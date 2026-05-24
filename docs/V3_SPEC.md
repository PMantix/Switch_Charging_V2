# Switching Circuit V3 — Design Specification

Source of truth for the V3 board. Covers topology, component selection,
pin assignments, analog front-end, physical UI, firmware architecture,
and binary data format. When a value or connection is wrong, fix it here
first.

---

## 1. Design goals

V2 is a three-tier system (RP2040 MicroPython → Raspberry Pi 5 →
laptop TUI). V3 collapses this to a **standalone embedded instrument**:

| Goal | V2 baseline | V3 target |
|---|---|---|
| Sensor sample rate | ~245 Hz (I²C bottleneck) | ≥4 kSPS (SPI, simultaneous) |
| Resolution | 16-bit (INA226) | 24-bit (2× ADS131M04) |
| Current range | ±819 mA (0.1 Ω shunt) | ±3 A (0.01 Ω shunt) |
| Host dependency | Pi + TUI required | Standalone; USB optional |
| Data storage | TCP stream → laptop CSV | Onboard microSD card |
| User interface | Textual TUI over WiFi | OLED + 3 buttons on PCB |
| Data format | ASCII text lines | Binary packed frames |

The switching topology (4 independent MOSFET paths, UCC5304 drivers,
B0512S isolated supplies) is retained. The power-in protection circuit
is retained. Gate resistors and pulldowns are retained.

---

## 2. Topology overview

Same as V2 — **NOT a traditional H-bridge.** Four independent FETs
switch four cell tabs to supply/ground rails.

```
      +HV rail                                     +HV rail
          │                                            │
     [R_SH_P1]                                    [R_SH_P2]
     0.01Ω 3W                                     0.01Ω 3W
          │                                            │
      ┌───┴───┐                                    ┌───┴───┐
      │ Q1 P1 │                                    │ Q2 P2 │
      │SOT-23 │                                    │SOT-23 │
      └───┬───┘                                    └───┬───┘
          │                                            │
     CELL_A_POS ── J2a pin 1         J2a pin 2 ── CELL_B_POS
     ─ ─ ─ ─ ─ ─  pouch cell interior ─ ─ ─ ─ ─ ─ ─
     CELL_A_NEG ── J2b pin 1         J2b pin 2 ── CELL_B_NEG
          │                                            │
      ┌───┴───┐                                    ┌───┴───┐
      │ Q3 N1 │                                    │ Q4 N2 │
      │SOT-23 │                                    │SOT-23 │
      └───┬───┘                                    └───┬───┘
          │                                            │
     [R_SH_N1]                                    [R_SH_N2]
     0.01Ω 3W                                     0.01Ω 3W
          │                                            │
         GND                                          GND
```

State definitions (same as V2 `server/config.py`):

| State | P1 | P2 | N1 | N2 | Current path |
|---|---|---|---|---|---|
| 0 | ON | off | ON | off | +HV → CELL_A_POS → [cell] → CELL_A_NEG → GND |
| 1 | ON | off | off | ON | +HV → CELL_A_POS → [cell] → CELL_B_NEG → GND |
| 2 | off | ON | ON | off | +HV → CELL_B_POS → [cell] → CELL_A_NEG → GND |
| 3 | off | ON | off | ON | +HV → CELL_B_POS → [cell] → CELL_B_NEG → GND |
| 4 | ON | ON | ON | ON | All tabs connected (transparent) |
| 5 | off | off | off | off | All off (idle) |

8 switching sequences (hardcoded in firmware, same as V2):

| Seq | Steps (state indices) | Description |
|---|---|---|
| 1 | 5, 5, 5, 5 | All-off (idle) |
| 2 | 0, 1, 2, 3 | Full rotation |
| 3 | 0, 1, 3, 2 | Rotation variant |
| 4 | 0, 2, 1, 3 | Rotation variant |
| 5 | 0, 2, 3, 1 | Rotation variant |
| 6 | 0, 3, 1, 2 | Rotation variant |
| 7 | 0, 3, 2, 1 | Rotation variant |
| 8 | 4, 4, 4, 4 | All-on (transparent) |

Pulse charge sequence: [0, 3] (alternates P1+N1 and P2+N2).

---

## 3. Power rails

| Net | Source | Voltage | Purpose |
|---|---|---|---|
| `+HV` | Cycler through clip pads + protection | 2.65–10 V | FET drain rails (P1/P2) |
| `CYCLER_IN-` | Cycler negative through clip pads | ~0 V | Tied to `GND` at pad only |
| `GND` | Star ground | 0 V | System reference |
| `+5V` | Pico 2 VBUS pin (or optional buck from `+HV`) | 5 V | LS driver VDD, B0512S input, WS2812B VDD (via D_NEO diode drop to ~4.3 V) |
| `+3V3` | Pico 2 3V3(OUT) pin | 3.3 V | UCC5304 VCCI, ADS131M04 DVDD, OLED, I²C pullups, LEDs |
| `+3V3_A` | Filtered from `+3V3` (ferrite bead + 10 µF) | 3.3 V | ADS131M04 AVDD (analog supply, isolated from digital noise) |
| `VCC2_P1` | B0512S #1 Vout+ (floating) | ~12 V above CELL_A_POS | U1 VDD |
| `VCC2_P2` | B0512S #2 Vout+ (floating) | ~12 V above CELL_B_POS | U2 VDD |
| `VCC2_LS` | B0512S #3 Vout+ (GND-referenced) | ~12 V | U3/U4 VDD (low-side drivers) |

---

## 4. Connectors

| Ref | Part | Nets | Notes |
|---|---|---|---|
| J1 | Kelvin clip pads (PCB copper, 4 pads) | `CYCLER_IN+` (current), `CYCLER_V+` (sense), `CYCLER_IN-` (current), `CYCLER_V-` (sense) | Cycler input — 4-wire Kelvin sense. Each pad 22×8 mm, ENIG finish, board edge placement. Current pads use wide traces (≥1 mm for 3 A). Sense pads can use thin traces. |
| J2a | 5.08 mm 2-pos screw terminal | `CELL_A_POS`, `CELL_B_POS` | Cell positive tabs |
| J2b | 5.08 mm 2-pos screw terminal | `CELL_A_NEG`, `CELL_B_NEG` | Cell negative tabs |
| J3 | JST-XH 4-pin (OLED) | `+3V3`, `GND`, `SDA`, `SCL` | SSD1306 128×64 OLED module |
| J4 | microSD card socket | `SD_MISO`, `SD_CS`, `SD_SCK`, `SD_MOSI`, `+3V3`, `GND` | Push-push socket, SPI mode |

No external 12 V input. Board powered by Pico 2 USB-C + cycler via clip pads.

---

## 5. Power-in protection (retained from V2)

| Ref | Part | Value | Connection |
|---|---|---|---|
| F1 | PPTC polyfuse | 3 A hold, 1812 | Series: `CYCLER_IN+` → F1 → `+HV_PREFUSE` |
| Q_RP | P-ch MOSFET | AO3401A SOT-23 | Reverse-polarity: S=`+HV_PREFUSE`, D=`+HV`, G=`CYCLER_IN-` |
| R_RP | 10 kΩ | 0603 | Q_RP gate-source pulldown |
| D_TVS | Bidir TVS | SMBJ12CA SMB | Across `+HV` / `CYCLER_IN-` |
| C_BULK1 | 10 µF MLCC | 1206 X5R 50 V | `+HV` to `GND` |
| C_BULK2 | 100 nF MLCC | 0603 X7R 50 V | `+HV` to `GND` |
| C_5V1 | 10 µF MLCC | 0805 X7R 10 V | `+5V` to `GND` |
| C_5V2 | 100 nF MLCC | 0603 X7R 50 V | `+5V` to `GND` |

### Optional 5 V buck converter (DNP by default)

Footprint included on the PCB but **not populated** for initial build.
The USB 500 mA budget is expected to be sufficient (~420 mA estimated).
If bench testing shows the 5 V rail sagging or USB overcurrent, populate
these components to derive 5 V from `+HV` instead of USB VBUS.

| Ref | Part | Value | Package | Connection | Notes |
|---|---|---|---|---|---|
| U_BUCK | TPS5430DDAR (TI) or AP3429K-ADJTRG1 | — | SOIC-8 or SOT-23-5 | IN=`+HV`, OUT=`+5V_BUCK`, EN=tied high | DNP. Choose part at layout time based on LCSC stock. |
| L_BUCK | 10 µH inductor | ≥1 A sat | — | SW → `+5V_BUCK` | DNP |
| D_BUCK | Schottky diode | SS34 or equiv | SMA | SW → GND (async topology) | DNP |
| C_BUCK_IN | 10 µF MLCC | 1206 50 V | 1206 | `+HV` to `GND` | DNP. Shares C_BULK1 if close enough. |
| C_BUCK_OUT | 22 µF MLCC | 0805 10 V | 0805 | `+5V_BUCK` to `GND` | DNP |
| R_FB1/R_FB2 | Feedback divider | per datasheet | 0603 | Sets output to 5.0 V | DNP |

When populated, `+5V_BUCK` connects to `+5V` via a solder jumper
(SJ_5V, normally bridged to VBUS). Cut the VBUS bridge and bridge
the buck output instead. This isolates the 5 V rail from USB current
limits entirely.

Minimum `+HV` for buck operation: ~6.5 V (TPS5430 dropout + margin).
Below that, fall back to USB VBUS.

---

## 6. Power stage — 4× MOSFETs + 4× shunts

### MOSFETs — AO3400A SOT-23 (retained from V2)

Each FET: **AO3400A, N-channel SOT-23, 5.7 A, ≤26.5 mΩ R_DS(on) (typ 18 mΩ), 30 V.**
JLCPCB basic part (same as V2). Max continuous current: **3 A** (hard limit).

Thermal check at 3 A continuous (worst-case R_DS(on)):
- P = I²R = 9 × 0.0265 = 0.24 W per FET
- SOT-23 θ_JA = 125 °C/W (steady-state, 1 in² FR-4, 2 oz Cu) → ΔT ≈ 30 °C
- T_J = 25 + 30 = 55 °C — well under 150 °C max

| Ref | Position | Drain | Source | Gate |
|---|---|---|---|---|
| Q1 | P1 (HS → CELL_A_POS) | `+HV_P1` (after shunt) | `CELL_A_POS` | `GATE_P1_OUT` |
| Q2 | P2 (HS → CELL_B_POS) | `+HV_P2` (after shunt) | `CELL_B_POS` | `GATE_P2_OUT` |
| Q3 | N1 (LS ← CELL_A_NEG) | `CELL_A_NEG` | `GND_N1` (to shunt) | `GATE_N1_OUT` |
| Q4 | N2 (LS ← CELL_B_NEG) | `CELL_B_NEG` | `GND_N2` (to shunt) | `GATE_N2_OUT` |

Gate pulldowns: **10 kΩ** 0603 from gate to source (Rgpd_1..4).

### Shunts — 0.01 Ω for ±3 A range

| Ref | Location | Net in | Net out | Max V_shunt | Max P |
|---|---|---|---|---|---|
| R_SH_P1 | Q1 drain (high-side) | `+HV` | `+HV_P1` | 30 mV @ 3 A | 0.09 W |
| R_SH_P2 | Q2 drain (high-side) | `+HV` | `+HV_P2` | 30 mV @ 3 A | 0.09 W |
| R_SH_N1 | Q3 source (low-side) | `GND_N1` | `GND` | 30 mV @ 3 A | 0.09 W |
| R_SH_N2 | Q4 source (low-side) | `GND_N2` | `GND` | 30 mV @ 3 A | 0.09 W |

Part: TA-I RLP25FEGR010, 0.01 Ω, 1%, 3 W, 2512, TCR ±50 ppm.
Kelvin sense pads routed to ADS131M04 differential inputs.

---

## 7. Current/voltage sensing — 2× ADS131M04

Two ADS131M04 (WQFN-20, 3×3 mm) replace all 4× INA226 modules. Same
M-series register map and SPI frame format as ADS131M08, but 4 channels
per device. Both share the SPI1 bus with independent CS and DRDY lines.

### Key specs

| Parameter | Value |
|---|---|
| Resolution | 24-bit delta-sigma |
| Channels | 4 simultaneous differential (per device, 8 total) |
| Max data rate | 64 kSPS/channel |
| Target data rate | 4 kSPS (good noise vs throughput) |
| Interface | SPI @ 8 MHz (shared bus, 2× CS) |
| Input range (gain=1) | ±1.2 V |
| Input range (gain=16) | ±75 mV |
| AVDD | 2.7–3.6 V (use `+3V3_A`) |
| DVDD | 2.7–3.6 V external supply (use `+3V3`, also feeds internal 1.8 V LDO to CAP pin) |
| VREF | 1.2 V internal |

### Channel assignment

**U_ADC1 — shunt current channels:**

| Channel | Signal | Type | Gain | Input range | AINxP | AINxN |
|---|---|---|---|---|---|---|
| CH0 | P1 shunt current | Differential | 16 | ±75 mV | `+HV` (before R_SH_P1) | `+HV_P1` (after R_SH_P1) |
| CH1 | P2 shunt current | Differential | 16 | ±75 mV | `+HV` (before R_SH_P2) | `+HV_P2` (after R_SH_P2) |
| CH2 | N1 shunt current | Differential | 16 | ±75 mV | `GND_N1` (before R_SH_N1) | `GND` (after R_SH_N1) |
| CH3 | N2 shunt current | Differential | 16 | ±75 mV | `GND_N2` (before R_SH_N2) | `GND` (after R_SH_N2) |

**U_ADC2 — bus voltage channels:**

| Channel | Signal | Type | Gain | Input range | AINxP | AINxN |
|---|---|---|---|---|---|---|
| CH0 | +HV rail voltage | Single-ended | 1 | ±1.2 V | Divider from `+HV` | `AGND` |
| CH1 | +HV_P1 node voltage | Single-ended | 1 | ±1.2 V | Divider from `+HV_P1` | `AGND` |
| CH2 | +HV_P2 node voltage | Single-ended | 1 | ±1.2 V | Divider from `+HV_P2` | `AGND` |
| CH3 | GND_N1 node voltage | Single-ended | 1 | ±1.2 V | Divider from `GND_N1` | `AGND` |

### Shunt channel resolution

At gain=16, input range ±75 mV:
- LSB = 75 mV / 2²³ = **8.94 nV**
- Current LSB = 8.94 nV / 0.01 Ω = **0.894 µA/bit**
- At 3 A: code ≈ 3,355,443 → 21.7 effective bits
- At 100 mA: code ≈ 111,848 → 16.8 effective bits
- RMS noise @ 4 kSPS, gain=16: ~1.8 µV (datasheet: 1.82 µV) → ~182 µA

### Bus voltage channel — analog front-end

Each bus voltage channel uses an 11:1 resistive divider + anti-alias
filter + input clamp:

```
  +HV (0–10 V)
      │
   [R_DIV_H]  91 kΩ 0603 1%
      │
      ├──── AINxP (0–0.99 V after divider)
      │
   [R_DIV_L]  9.1 kΩ 0603 1%
      │
    AGND
```

- Divider ratio: (91k + 9.1k) / 9.1k = 11.0:1 Vin:Vout (calibrated in firmware)
- Gain=1: input range ±1.2 V → 10 V / 11.0 = 0.91 V max is within range
- Anti-alias: 100 nF across R_DIV_L → f_c = 1/(2π × 9.1 kΩ × 100 nF) ≈ 175 Hz
  (sufficient for DC bus voltage)
- Input protection: Nexperia BAV99,215 dual diode, clamp to AVDD/AGND

Voltage resolution: 1.2 V / 2²³ = 143 nV = 0.143 µV/bit at divider →
**1.57 µV/bit at bus** (after 11.0× scaling).

### ADS131M04 support components (per device, ×2)

| Ref | Part | Value | Connection |
|---|---|---|---|
| C_AVDD1 | 10 µF MLCC | 0805 X7R 10 V | `+3V3_A` to `AGND` |
| C_AVDD2 | 100 nF MLCC | 0603 X7R 50 V | `+3V3_A` to `AGND` (close to AVDD pin, per device) |
| C_DVDD | 1 µF MLCC | 0603 X5R 50 V | DVDD to DGND (external digital/IO supply, datasheet requires 1 µF) |
| C_CAP | 220 nF MLCC | 0603 X7R 25 V | CAP to DGND (internal 1.8 V LDO output, datasheet required) |
| L_AVDD | Ferrite bead | TAI-TECH HCB1608KF-601T20, 600 Ω @ 100 MHz, 0603 | `+3V3` → `+3V3_A` (shared, single ferrite for both devices) |

Pin connections (both devices share SPI1 bus):

**U_ADC1 (shunt currents):**
- DRDY → `ADS1_DRDY` (GP14, falling-edge IRQ)
- CS → `ADS1_CS` (GP13)
- SCLK → `ADS_SCK` (GP10, SPI1, shared)
- DIN → `ADS_MOSI` (GP11, SPI1, shared)
- DOUT → `ADS_MISO` (GP12, SPI1, shared)
- RESET → `ADS_RESET` (GP15, shared)

**U_ADC2 (bus voltages):**
- DRDY → `ADS2_DRDY` (GP21)
- CS → `ADS2_CS` (GP26)
- SCLK/DIN/DOUT/RESET → shared with U_ADC1

Both devices:
- AVDD → `+3V3_A`
- DVDD → internal LDO (decouple with C_DVDD)
- DVDD → `+3V3`
- CAP → 220 nF to DGND (internal 1.8 V LDO output)

---

## 8. Gate drivers — 4× UCC5304DWVR (retained from V2)

| Ref | Drives | Pin 1 IN | Pin 2/3 VCCI | Pin 4 GND | Pin 5/6 VSS | Pin 7 OUT | Pin 8 VDD |
|---|---|---|---|---|---|---|---|
| U1 | Q1 (P1, HS) | `GATE_P1_IN` | `+3V3` | `GND` | `CELL_A_POS` | `GATE_P1_OUT` | `VCC2_P1` |
| U2 | Q2 (P2, HS) | `GATE_P2_IN` | `+3V3` | `GND` | `CELL_B_POS` | `GATE_P2_OUT` | `VCC2_P2` |
| U3 | Q3 (N1, LS) | `GATE_N1_IN` | `+3V3` | `GND` | `GND` | `GATE_N1_OUT` | `VCC2_LS` |
| U4 | Q4 (N2, LS) | `GATE_N2_IN` | `+3V3` | `GND` | `GND` | `GATE_N2_OUT` | `VCC2_LS` |

Hand-solder from stock (DNP_JLC=TRUE, same as V2). Use TI recommended
land pattern (SLUSDV5B) with extra pad margin for hand soldering —
V2 pads were too narrow and caused solder bridges.

**V2 lesson:** Low-side VDD was +5V in V2, which sits right at the
UCC5304 UVLO threshold (5V). Breadboard testing (2026-04-29) confirmed
this was unreliable. V3 uses `VCC2_LS` (12V from PS3) for both LS
drivers, matching the validated breadboard architecture.

### Per-driver support components

- `C_VCCI_U1..4`: 100 nF 0603 (VCCI to GND)
- `C_VDD_U1..4_1`: 10 µF 1206 X5R 50 V (VDD to VSS, bulk — 12 V across VDD-VSS requires 50 V rating)
- `C_VDD_U1..4_2`: 100 nF 0603 (VDD to VSS, close to pin 8)
- `R_G_U1..4`: **10 Ω** 0603 (OUT to FET gate)

---

## 9. Isolated gate supplies — 3× B0512S-1WR3

| Ref | Pin 1 Vin+ | Pin 2 Vin- | Pin 3 Vout+ | Pin 4 Vout- | Feeds |
|---|---|---|---|---|---|
| PS1 | `+5V` | `GND` | `VCC2_P1` | `CELL_A_POS` | U1 VDD/VSS (high-side, floating) |
| PS2 | `+5V` | `GND` | `VCC2_P2` | `CELL_B_POS` | U2 VDD/VSS (high-side, floating) |
| PS3 | `+5V` | `GND` | `VCC2_LS` | `GND` | U3/U4 VDD (low-side, ground-referenced 12 V) |

PS3 is new for V3. V2 used +5V directly for LS driver VDD, which sat
at the UCC5304 UVLO threshold and was unreliable. PS3 provides a clean
12V rail for both LS drivers, matching the validated breadboard fix.

**V2 failure lesson (2026-05-19, SW1):** A B0512S used for the LS 12V
bodge died after continuous switching over a weekend — gate charge
transient stress without adequate decoupling. V3 decoupling is
significantly upgraded from V2.

Decoupling per converter (all three), per YLPTEC datasheet Table 1:
- Input: 4.7 µF 0805 + 100 nF 0603, close to Vin pins
- Output: 2.2 µF 0805 + 100 nF 0603, close to Vout pins
- Additional 100 nF 0603 at each UCC5304 pin 8 (VDD), close to the driver
- Max capacitive load for 12 V output: 560 µF (total decoupling incl. UCC5304 VDD caps: PS1/PS2 ≈ 12.4 µF, PS3 ≈ 22.5 µF — safe)

**Minimum load:** B0512S requires ≥9 mA output (10% of 84 mA). UCC5304
IVDD is only ~1–2.5 mA per driver (quiescent 1.0 mA, operating 2.5 mA
@ 500 kHz with COUT=100 pF). AO3400A Qg = 7 nC adds negligible
average current at our switching frequencies (≤2 kHz). Each converter
needs a 1.2 kΩ bleeder resistor (VDD to VSS) to bring total load to
~12 mA. P_bleeder = 120 mW per resistor (0805, 125 mW rated).

**B0512S pin mapping note:** Verify pinout matches your actual parts.
GDHUIZHT-brand modules have pins 1↔2 and 3↔4 swapped vs Mornsun
datasheet. Add silkscreen note on PCB indicating which brand the
footprint matches. See `pcb/PCB_V3_CHANGELIST.md` item #1.

---

## 10. MCU — Raspberry Pi Pico 2 (RP2350)

Socketed via TH headers (same approach as V2's RP2040-Zero). Future
revision may use bare RP2350 QFN-60 for JLCPCB full assembly.

### Why RP2350 over RP2040

| | RP2040 (V2) | RP2350 (V3) |
|---|---|---|
| RAM | 264 KB | 520 KB |
| CPU | Dual M0+ @ 133 MHz | Dual M33 @ 150 MHz |
| FPU | None (software float) | Hardware single-precision |
| Flash | 2 MB (on Zero) | 4 MB (on Pico 2) |
| PIO | 2 × 4 SM | 3 × 4 SM |

### Pin assignments

| GPIO | Net | Function |
|---|---|---|
| GP0 | `LED_AUTO` | Auto-follow engaged indicator (yellow LED) |
| GP1 | `NEOPIXEL` | WS2812 RGB status LED |
| GP2 | `GATE_P1_IN` | → UCC5304 U1 → Q1 (P1 high-side) |
| GP3 | `GATE_P2_IN` | → UCC5304 U2 → Q2 (P2 high-side) |
| GP4 | `GATE_N1_IN` | → UCC5304 U3 → Q3 (N1 low-side) |
| GP5 | `GATE_N2_IN` | → UCC5304 U4 → Q4 (N2 low-side) |
| GP6 | `SDA` | I²C0 SDA → OLED display |
| GP7 | `SCL` | I²C0 SCL → OLED display |
| GP8 | `BTN_A` | Button A: mode / auto-follow (input, pullup) |
| GP9 | `BTN_B` | Button B: start-stop / record (input, pullup) |
| GP10 | `ADS_SCK` | SPI1 SCK → ADS131M04 SCLK (shared) |
| GP11 | `ADS_MOSI` | SPI1 TX → ADS131M04 DIN (shared) |
| GP12 | `ADS_MISO` | SPI1 RX ← ADS131M04 DOUT (shared) |
| GP13 | `ADS1_CS` | GPIO → U_ADC1 CS (active low) |
| GP14 | `ADS1_DRDY` | GPIO input ← U_ADC1 DRDY (falling-edge IRQ) |
| GP15 | `ADS_RESET` | GPIO output → ADS131M04 RESET (shared, active low) |
| GP16 | `SD_MISO` | SPI0 RX ← microSD MISO |
| GP17 | `SD_CS` | GPIO → microSD CS (active low) |
| GP18 | `SD_SCK` | SPI0 SCK → microSD CLK |
| GP19 | `SD_MOSI` | SPI0 TX → microSD MOSI |
| GP20 | `BTN_C` | Button C: sequence / freq adjust (input, pullup) |
| GP21 | `ADS2_DRDY` | GPIO input ← U_ADC2 DRDY (falling-edge IRQ) |
| GP22 | `LED_REC` | Recording active indicator (green LED) |
| GP25 | (onboard) | Pico 2 onboard LED (heartbeat) |
| GP26 | `ADS2_CS` | GPIO → U_ADC2 CS (active low) |
| GP27 | `ADC1` | Future: transient capture channel 1 |
| GP28 | `ADC2` | Future: transient capture channel 2 |

Gate drive indicator LEDs (no extra GPIO — driven from GATE_*_IN nets):

| Ref | Anode | Cathode → 1 kΩ → | Purpose |
|---|---|---|---|
| D_LED_P1 | `+3V3` | `GATE_P1_IN` | P1 FET idle (LED on when gate LOW) |
| D_LED_P2 | `+3V3` | `GATE_P2_IN` | P2 FET idle (LED on when gate LOW) |
| D_LED_N1 | `+3V3` | `GATE_N1_IN` | N1 FET idle (LED on when gate LOW) |
| D_LED_N2 | `+3V3` | `GATE_N2_IN` | N2 FET idle (LED on when gate LOW) |
| D_LED_PWR | `+3V3` | `GND` | Board power on |

---

## 11. OLED display — SSD1306 128×64

Module: 0.96" or 1.3" SSD1306 I²C OLED (4-pin: VCC, GND, SDA, SCL).
Address: 0x3C. Connected via J3 header.

Pullups: `R_SDA` 4.7 kΩ and `R_SCL` 4.7 kΩ to `+3V3`.

### Display layout (4 lines, 16px font)

```
┌──────────────────────────────┐
│ CHARGE  SEQ:3   10.0Hz      │  Mode, sequence, frequency
│ I: 0.523A    V: 3.42V       │  KCL current, bus voltage
│ Auto:ON  Step:2/4           │  Auto-follow, current step
│ ■REC 00:12:34    SD:OK      │  Recording status, SD health
└──────────────────────────────┘
```

Sequence preview (shown for 2 s after BTN_C press):
```
┌──────────────────────────────┐
│ ► SEQ 3                     │
│ Step 1: +A / -B  (P1+N2)   │
│ Step 2: +A / -A  (P1+N1)   │
│ Step 3: +B / -B  (P2+N2)   │
└──────────────────────────────┘
```

Frequency adjust mode (BTN_C long press):
```
┌──────────────────────────────┐
│ FREQUENCY ADJUST            │
│                             │
│     ► 10.0 Hz ◄             │
│ [A]=down  [C]=up  [B]=set  │
└──────────────────────────────┘
```

Update rate: ~10 Hz (avoid I²C bus contention with high-rate rendering).

---

## 12. microSD card

Push-push microSD socket. SPI mode (not SDIO — simpler, sufficient
bandwidth).

| Socket pin | Net | Connection |
|---|---|---|
| DAT0/MISO | `SD_MISO` | GP16 (SPI0 RX) |
| CMD/MOSI | `SD_MOSI` | GP19 (SPI0 TX) |
| CLK | `SD_SCK` | GP18 (SPI0 SCK) |
| DAT3/CS | `SD_CS` | GP17 (GPIO) |
| VDD | `+3V3` | Via 10 µF + 100 nF decoupling |
| VSS | `GND` | |

Filesystem: FAT32 via FatFS (included in Pico SDK `lib/tinyusb`).

### Recording file format

Binary files: `REC_YYYYMMDD_HHMMSS.bin` (timestamps from USB-set RTC
or monotonic counter if no host connection).

Each file starts with a 64-byte header:
```
offset  bytes  field
  0      4     magic        "SCV3" (0x53435633)
  4      2     version      1
  6      2     frame_size   38
  8      4     sample_rate  data rate in SPS (e.g., 4000)
 12      4     shunt_ohms   shunt resistance × 1e6 (e.g., 10000 = 0.01Ω)
 16      8     start_time   Unix timestamp if available, else 0
 24     40     reserved     zero-filled
```

Followed by a continuous stream of 38-byte binary frames (§14).

Write strategy:
- 2048-sample ring buffer (~66 KB) in RAM (33 bytes/entry, see §15)
- Core 0 drains buffer → 512-byte aligned SD writes
- At 4 kSPS × 38 bytes = 152 KB/s sustained write
- 32 GB card ≈ 58 hours of continuous recording

---

## 13. Physical UI — 3 buttons

Tact switches with hardware debounce (100 nF cap + 10 kΩ pullup to
3V3 per button, active low).

| Ref | GPIO | Short press | Long press (>1 s) |
|---|---|---|---|
| SW_A | GP8 (`BTN_A`) | Cycle mode: IDLE → CHARGE → DISCHARGE → PULSE_CHARGE | Toggle auto-follow on/off |
| SW_B | GP9 (`BTN_B`) | Start/stop switching | Start/stop SD recording |
| SW_C | GP20 (`BTN_C`) | Cycle sequence 1–8 | Enter frequency adjust mode |

In frequency adjust mode:
- SW_A = decrease frequency (steps: 0.1, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000 Hz)
- SW_C = increase frequency
- SW_B = confirm and exit freq mode

---

## 14. Binary frame format (SD + USB telemetry)

```
offset  bytes  field
  0      2     sync         0xAA 0x55
  2      1     type         'D' (0x44)
  3      1     length       33 (payload bytes after length, before xor)
  4      4     ticks_us     uint32 (RP2350 timer at DRDY, µs)
  8      4     seq_no       uint32 (monotonic, wraps at 2³²)
 12      3     ch0_raw      int24 (P1 shunt, signed, big-endian)
 15      3     ch1_raw      int24 (P2 shunt)
 18      3     ch2_raw      int24 (N1 shunt)
 21      3     ch3_raw      int24 (N2 shunt)
 24      3     ch4_raw      int24 (+HV bus voltage)
 27      3     ch5_raw      int24 (+HV_P1 node voltage)
 30      3     ch6_raw      int24 (+HV_P2 node voltage)
 33      3     ch7_raw      int24 (GND_N1 node voltage)
 36      1     mode_flags   uint8 [mode:3 | seq_idx:3 | auto:1 | rec:1]
 37      1     xor          uint8 (XOR of bytes 0..36)
            ────
total: 38 bytes per frame
```

At 4 kSPS: 152 KB/s. USB Full Speed (12 Mbit/s) and SD SPI both
handle this easily.

Host-side conversion (Python):
```python
import struct

SHUNT_OHM = 0.01
ADS_LSB_75MV = 75e-3 / (2**23)       # gain=16, ±75 mV range
ADS_LSB_1V2 = 1.2 / (2**23)          # gain=1, ±1.2 V range
BUS_DIVIDER = (91.0 + 9.1) / 9.1      # 91k/9.1k divider → 11.0:1

def decode_frame(buf):
    sync, typ, length = struct.unpack_from('<HBB', buf, 0)
    ticks_us, seq_no = struct.unpack_from('<II', buf, 4)
    raw = []
    for i in range(8):
        b = buf[12 + i*3 : 15 + i*3]
        val = int.from_bytes(b, 'big', signed=True)
        raw.append(val)
    mode_flags = buf[36]
    # Convert shunt channels to amps
    currents = [r * ADS_LSB_75MV / SHUNT_OHM for r in raw[:4]]
    # Convert bus channels to volts (undo divider)
    voltages = [r * ADS_LSB_1V2 * BUS_DIVIDER for r in raw[4:]]
    return ticks_us, seq_no, currents, voltages, mode_flags
```

---

## 15. Firmware architecture (C, Pico SDK)

### Dual-core split

**Core 1 — Real-time acquisition (interrupt-driven):**
- ADS1 DRDY falling edge → SPI read 4 shunt channels, then CS-swap
  and read 4 bus voltage channels from ADS2 → pack 8-channel sample
  into lock-free SPSC ring buffer
- Timer alarm callback for FET switching (`_tick`, same concept as
  V2 `firmware/main.py:293-303`)
- No allocations, no stdio, no SD/USB I/O on this core

**Core 0 — Application logic (main loop, ~100 Hz poll):**
- Mode controller state machine (5 modes, ported from
  `server/mode_controller.py`)
- Auto-follow controller (KCL hysteresis, ported from
  `server/auto_follow.py`)
- Sequence engine (8 hardcoded sequences + pulse mode, ported from
  `server/sequence_engine.py` + `server/config.py`)
- Button handler (debounce + short/long press detection)
- OLED display refresh (~10 Hz via I²C)
- SD card writer (drain ring buffer → FAT32 binary file, 512 B blocks)
- USB CDC handler (command parser + optional binary streaming)

### Ring buffer (core 1 → core 0)

Lock-free single-producer single-consumer ring:
- Entry size: 33 bytes (timestamp + seq + 8 × int24 + flags = 4+4+24+1)
- Buffer depth: 2048 entries ≈ 66 KB
- At 4 kSPS: ~0.5 s of buffering (handles SD write latency spikes)
- Core 1 writes head pointer, core 0 reads tail pointer
- Memory barrier via `__dmb()` on ARM M33

### Mode controller (from `server/mode_controller.py`)

5 modes: IDLE, CHARGE, DISCHARGE, PULSE_CHARGE, DEBUG.

Transition rules:
- DISCHARGE ↔ CHARGE/PULSE_CHARGE: skip dead-time (FETs always
  conducting, no shoot-through risk)
- All other transitions: enforce 2 ms all-off dead-time
- Auto-follow integration: if auto-follow is enabled and user selects
  CHARGE or PULSE_CHARGE, the request sets the auto-follow target
  rather than forcing immediate transition
- Manual IDLE/DISCHARGE/DEBUG: disables auto-follow first

### Auto-follow (from `server/auto_follow.py`)

Runs at ~15 Hz (every ~267th DRDY interrupt):
1. Compute KCL current = Σ(high-side shunts) - Σ(low-side shunts)
2. Hysteresis:
   - `avg_i > i_enter` AND not active → engage target switching mode
   - `avg_i < i_exit` AND active → disengage to DISCHARGE (transparent)
3. Direction-aware: only positive (charging) current triggers engagement
4. Thresholds configurable via USB command (default i_enter=50 mA,
   i_exit=20 mA)
5. CC setpoint recommendation: enter = 0.35 × setpoint, exit = 0.15 × setpoint

### USB serial command set

Retained from V2 (for tool compatibility):
```
S <P1> <P2> <N1> <N2>    Set FET states (halts switching)
Q                         Query FET states
T <hz>                    Start/stop binary streaming over USB
P                         Ping (heartbeat + ticks_us)
C <n> <s1> ... <sn>       Program switching cycle
F <period_us>             Set step period
G                         Go — start switching
H                         Halt switching
K                         Debug: advance one step
Z [n]                     Profile acquisition loop
```

New commands:
```
MODE <idle|charge|discharge|pulse_charge|debug>   Set mode
SEQ <1-8>                 Set sequence (1-based)
AF <0|1> [enter_mA] [exit_mA]   Auto-follow config
REC <0|1>                 Start/stop SD recording
STATUS                    Full JSON status dump
CAL <ch> <gain> <offset>  Per-channel calibration trim
```

---

## 16. Status LEDs

| Ref | GPIO / Net | Color | Purpose |
|---|---|---|---|
| D_LED_PWR | `+3V3` → 1 kΩ → GND | Green | Board power on |
| D_LED_P1 | `+3V3` → 1 kΩ → `GATE_P1_IN` | Green | P1 FET idle (on when gate LOW) |
| D_LED_P2 | `+3V3` → 1 kΩ → `GATE_P2_IN` | Green | P2 FET idle (on when gate LOW) |
| D_LED_N1 | `+3V3` → 1 kΩ → `GATE_N1_IN` | Green | N1 FET idle (on when gate LOW) |
| D_LED_N2 | `+3V3` → 1 kΩ → `GATE_N2_IN` | Green | N2 FET idle (on when gate LOW) |
| D_LED_REC | GP22 → 1 kΩ → GND | Green | SD recording active |
| D_LED_AUTO | GP0 → 1 kΩ → GND | Yellow | Auto-follow engaged |
| NeoPixel | GP1 (WS2812 data), VDD = `+5V` via D_NEO | RGB | Mode status color (level-shifted, see below) |

### NeoPixel level shift — series diode on VDD

The WS2812B-MINI-X2 requires VIH = 0.65 x VDD. At VDD = 5.0 V, VIH =
3.25 V — only 50 mV above the RP2350 3.3 V GPIO output, which is
unreliable across temperature and voltage tolerance.

**Fix:** A 1N4148W silicon diode (D_NEO, SOD-123) in series with the
NeoPixel VDD line drops the effective supply voltage. Datasheet Vf
(Semtech, max values): 0.715 V @ 1 mA, 0.855 V @ 10 mA, 1.0 V @ 50 mA.

At nominal 5 V USB, 10 mA NeoPixel current (moderate brightness):
- VDD = 5.0 - 0.855 = **4.15 V** (within 3.7–5.3 V)
- VIH = 0.65 × 4.15 = 2.70 V → **600 mV margin** over 3.3 V GPIO

```
+5V ──[D_NEO 1N4148W]──┬── NEOPIXEL VDD (~4.15 V @ 10 mA)
                        │
GP1 ────────────────────┤── NEOPIXEL DIN
                        │
GND ────────────────────┴── NEOPIXEL GND
```

At minimum USB voltage (4.5 V), VDD depends on NeoPixel current:
- 1 mA (dim): VDD = 4.5 - 0.715 = 3.79 V (above 3.7 V min, VIH margin = 840 mV)
- 10 mA: VDD = 4.5 - 0.855 = 3.65 V (**below 3.7 V min**)

**Firmware constraint:** limit NeoPixel brightness so total current
stays ≤5 mA. As a single-pixel status indicator, this is adequate —
full white is not needed. At 5 mA, Vf ≈ 0.8 V (interpolated), giving
VDD = 4.5 - 0.8 = 3.7 V (at minimum).

NeoPixel color map:
- Blue = IDLE
- Green = CHARGE
- Cyan = DISCHARGE (transparent)
- Magenta = PULSE_CHARGE
- Red = fault/error
- Purple = DEBUG
- Orange = recording active (overrides mode color)

---

## 17. Net summary

| Net | Approx pin count |
|---|---|
| `GND` / `AGND` | ~35 |
| `+HV` | 6 (protection, 2× shunt, 2× ADS divider, bulk cap) |
| `+HV_P1` / `+HV_P2` | 4 each (shunt, FET drain, ADS shunt-, ADS bus divider) |
| `GND_N1` / `GND_N2` | 4 each (FET source, shunt, ADS shunt+, ADS bus divider) |
| `CELL_A_POS` / `CELL_B_POS` | 3 each (FET source, J2a, PS Vout-/U VSS) |
| `CELL_A_NEG` / `CELL_B_NEG` | 2 each (FET drain, J2b) |
| `+5V` | 6+ |
| `+3V3` | 15+ |
| `+3V3_A` | 5 (ferrite out, 2× ADS AVDD, 2× ADS decoupling) |
| `VCC2_P1` / `VCC2_P2` | 2 each |
| `SDA` / `SCL` | 3 each (MCU, pullup, OLED header) |
| SPI1 nets (`ADS_*`) | 3 each (MCU + 2× ADS131M04) |
| SPI0 nets (`SD_*`) | 2 each (MCU + SD socket) |
| `GATE_P1_IN`..`GATE_N2_IN` | 3 each (MCU + driver + LED) |
| `GATE_P1_OUT`..`GATE_N2_OUT` | 3 each (driver + FET gate + pulldown) |
| `BTN_A` / `BTN_B` / `BTN_C` | 2 each (MCU + switch) |

---

## 18. Estimated component count

~91 parts (vs ~70 in V2). Net additions:
- +2 ADS131M04 + 12 support passives (replaces 4× INA226 + 8 passives → net +6)
- +1 microSD socket + 2 passives
- +1 OLED header (replaces TM1637 header)
- +3 tact switches + 9 passives (replaces rotary encoder header)
- +4 voltage dividers (8 resistors + 4 caps + 4 diodes)
- +2 indicator LEDs + 2 resistors
- +1 NeoPixel VDD level-shift diode (D_NEO, 1N4148W)
- +1 ferrite bead
- −4 INA226 chips, −2 I²C pullup resistors, −1 ALERT pullup
- −1 screw terminal (cycler input replaced by PCB clip pads)

---

## 19. Verification checklist

- [ ] ADS131M04 AVDD/DVDD voltage levels match RP2350 3.3 V domain (no IOVDD pin — DVDD is I/O supply)
- [ ] SPI1 clock ≤ 25 MHz (ADS131M04 max SCLK)
- [ ] Both ADS131M04 CS/DRDY lines routed to correct GPIO (GP13/14 and GP26/21)
- [ ] Shunt voltage at 3 A (30 mV) within gain=16 input range (±75 mV)
- [ ] Bus voltage at 10 V through 11.0:1 divider (0.91 V) within gain=1 range (±1.2 V)
- [ ] MOSFET SOA check: 3 A continuous at ambient + enclosure temp (AO3400A SOT-23, R_DS(on) max 26.5 mΩ, θ_JA 125 °C/W → ΔT ≈ 30 °C)
- [ ] UCC5304 UVLO: VDD rising threshold 5.0–5.9 V — 5 V supply fails on typical/worst-case units. V3 uses 12 V from PS3 (resolved).
- [ ] B0512S-1WR3 output floats above CELL_x_POS — verify isolation rating
- [ ] SPI0 (SD) and SPI1 (ADS) on separate peripherals — no bus contention
- [ ] I²C0 (OLED) does not share bus with any high-speed device
- [ ] Total 3.3 V rail current: RP2350 (~50 mA) + 2× ADS131M04 (~9 mA, datasheet IAVDD+IDVDD max 4.5 mA/device) + OLED (~20 mA) + LEDs (~10 mA) + drivers VCCI (~6 mA, 4×1.5 mA typ) = ~95 mA — within Pico 2 3V3 regulator (300 mA)
- [ ] 5 V rail: 3× B0512S input current (each ~12 mA output load at 12 V, Mornsun 83% efficiency: I_in ≈ 12×0.012/(5×0.83) ≈ 35 mA per converter = ~105 mA total) + misc (~80 mA) = ~185 mA typical — within USB 500 mA.
- [ ] Optional 5 V buck: footprint present, DNP, solder jumper SJ_5V defaults to VBUS
- [ ] Kelvin sense routing: shunt voltage traces go directly to ADS131M04 inputs, not through power traces
- [ ] Star ground: AGND and DGND tied at one point under ADS131M04 devices
- [ ] Kelvin clip pads: 22×8 mm per pad, ENIG finish, board edge, current/sense paths separated, ≥1 mm traces on current pads
- [ ] B0512S minimum load: each converter needs ≥9 mA. UCC5304 draws ~1–2.5 mA → 1.2 kΩ bleeder required per converter (10 mA + driver ≈ 12 mA, 33% margin)
- [ ] UCC5304 VDD bulk caps: all four see ~12 V across VDD-VSS — use 50 V rated (1206), not 10 V
- [ ] ADS131M04 CAP pin: 220 nF to DGND per device (internal LDO output, datasheet required)
- [ ] ADS131M04 DVDD decoupling: 1 µF minimum per datasheet (not 100 nF)
- [ ] ADS131M04 has no REFP/REFN pins — internal 1.2 V reference requires no external decoupling (do NOT carry over C_VREF from M08)
- [ ] Gate drive LEDs on input nets (system-GND referenced), not floating output nets
- [ ] NeoPixel VDD level shift: D_NEO (1N4148W) in series with +5V → VDD ≈ 4.15 V @ 10 mA (Vf max 0.855 V per datasheet), VIH = 2.70 V, margin ≥600 mV. At min USB (4.5 V), limit NeoPixel brightness to ≤5 mA to keep VDD ≥3.7 V.
