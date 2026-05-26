# Switching Circuit V3 — Schematic Spec

Source of truth for the flat KiCad 10 schematic. Every part, every net,
every pin-to-net mapping. When a value or connection is wrong, fix it
here first, then regenerate the schematic.

Companion to `docs/V3_SPEC.md` (design spec) and `docs/V3_BOM.md` (BOM).

---

## 1. Topology overview

Same as V2 — **NOT a traditional H-bridge.** Four independent N-ch FETs
switch four cell tabs to supply/ground rails. 0.01 Ω shunts on all
four paths. High-side shunts read through INA180A1 current-sense
amplifiers (common-mode too high for direct ADC connection). Low-side
shunts connect directly to ADS131M04.

```
      +HV rail                                     +HV rail
          │                                            │
     [R_SH_P1]                                    [R_SH_P2]
     0.01Ω 3W                                     0.01Ω 3W
          │── INA180 IN+/IN- ──→ CSA_P1_OUT           │── INA180 ──→ CSA_P2_OUT
      [Q1 P1]                                      [Q2 P2]
      HS-N-ch                                      HS-N-ch
          │                                            │
     CELL_A_POS ── J2a pin 1        J2a pin 2 ── CELL_B_POS
     ─ ─ ─ ─ ─ ─  pouch cell interior ─ ─ ─ ─ ─ ─ ─
     CELL_A_NEG ── J2b pin 1        J2b pin 2 ── CELL_B_NEG
          │                                            │
      [Q3 N1]                                      [Q4 N2]
      LS-N-ch                                      LS-N-ch
          │                                            │
     [R_SH_N1]                                    [R_SH_N2]
     0.01Ω 3W                                     0.01Ω 3W
          │                                            │
         GND                                          GND
```

---

## 2. Power rails

| Net | Source | Voltage | Purpose |
|---|---|---|---|
| `+HV` | Cycler through clip pads + protection | 2.65–10 V | FET drain rails (P1/P2) |
| `CYCLER_IN-` | Cycler negative through clip pads | ~0 V | Tied to `GND` at pad only |
| `GND` | Star ground | 0 V | System reference, digital ground |
| `AGND` | Analog ground | 0 V | ADS131M04 AGND, tied to GND at one point |
| `+5V` | Pico 2 VBUS pin | 5 V | B0512S input, NeoPixel VDD (via D_NEO) |
| `+3V3` | Pico 2 3V3(OUT) pin | 3.3 V | UCC5304 VCCI, ADS DVDD, OLED, I²C pullups, LEDs, INA180 VS |
| `+3V3_A` | Filtered from `+3V3` (ferrite bead) | 3.3 V | ADS131M04 AVDD (analog supply) |
| `VCC2_P1` | B0512S PS1 Vout+ (floating) | ~12 V above CELL_A_POS | U1 VDD |
| `VCC2_P2` | B0512S PS2 Vout+ (floating) | ~12 V above CELL_B_POS | U2 VDD |
| `VCC2_LS` | B0512S PS3 Vout+ (GND-referenced) | ~12 V | U3/U4 VDD (low-side drivers) |

---

## 3. Connectors

| Ref | Part | Nets | Notes |
|---|---|---|---|
| J1 | Kelvin clip pads (PCB copper, 4 pads) | `CYCLER_IN+`, `CYCLER_V+`, `CYCLER_IN-`, `CYCLER_V-` | 22×8 mm ENIG pads at board edge. Current pads (IN±) use ≥1 mm traces. Sense pads (V±) thin traces. `CYCLER_IN+` → F1. `CYCLER_IN-` → 0R to `GND`. `CYCLER_V+` and `CYCLER_V-` are sense-only (route to voltage divider if needed, otherwise connect to corresponding current net). |
| J2a | DB128L-5.08-2P screw terminal | pin 1: `CELL_A_POS`, pin 2: `CELL_B_POS` | Cell positive tabs |
| J2b | DB128L-5.08-2P screw terminal | pin 1: `CELL_A_NEG`, pin 2: `CELL_B_NEG` | Cell negative tabs |
| J3 | JST B4B-XH-A 4-pin vertical | pin 1: `+3V3`, pin 2: `GND`, pin 3: `SDA`, pin 4: `SCL` | OLED display (SSD1306, I²C, addr 0x3C) |
| J4 | TF-PUSH microSD socket | See §13 | Push-push, SPI mode |

Silkscreen labels per `PCB_V3_CHANGELIST.md` item #3:
- J2a: `CELL A+` (pin 1), `CELL B+` (pin 2)
- J2b: `CELL A-` (pin 1), `CELL B-` (pin 2)

---

## 4. Power-in protection

| Ref | Part | Value | Footprint | Connection |
|---|---|---|---|---|
| F1 | PPTC polyfuse | 3 A hold | 1812 | Series: `CYCLER_IN+` → F1 pin 1 → F1 pin 2 → `+HV_PREFUSE` |
| Q_RP | AO3401A P-ch MOSFET | SOT-23 | SOT-23 | G (pin 1): `CYCLER_IN-`, S (pin 2): `+HV_PREFUSE`, D (pin 3): `+HV` |
| R_RP | 10 kΩ 5% | 0603 | 0603 | pin 1: `+HV_PREFUSE`, pin 2: `CYCLER_IN-` (Q_RP gate-source pulldown) |
| D_TVS | SMBJ12CA bidir TVS | SMB | SMB | pin 1 (A/K): `GND`, pin 2 (A/K): `+HV` |
| C_BULK1 | 10 µF X5R 50 V | 1206 | 1206 | pin 1: `+HV`, pin 2: `GND` |
| C_BULK2 | 100 nF X7R 50 V | 0603 | 0603 | pin 1: `+HV`, pin 2: `GND` |
| C_5V1 | 10 µF X7R 10 V | 0805 | 0805 | pin 1: `+5V`, pin 2: `GND` |
| C_5V2 | 100 nF X7R 50 V | 0603 | 0603 | pin 1: `+5V`, pin 2: `GND` |
| R_CYC_GND | 0 Ω | 0805 | 0805 | pin 1: `CYCLER_IN-`, pin 2: `GND` (DNP to lift tie for floating cyclers) |

---

## 5. Power stage — 4× MOSFETs + 4× shunts

### MOSFETs — AO3400A SOT-23

Q_NMOS_GSD pin mapping: **1=Gate, 2=Source, 3=Drain**

| Ref | Position | Pin 1 (G) | Pin 2 (S) | Pin 3 (D) |
|---|---|---|---|---|
| Q1 | P1 (HS → CELL_A_POS) | `GATE_P1_OUT` | `CELL_A_POS` | `+HV_P1` |
| Q2 | P2 (HS → CELL_B_POS) | `GATE_P2_OUT` | `CELL_B_POS` | `+HV_P2` |
| Q3 | N1 (LS ← CELL_A_NEG) | `GATE_N1_OUT` | `GND_N1` | `CELL_A_NEG` |
| Q4 | N2 (LS ← CELL_B_NEG) | `GATE_N2_OUT` | `GND_N2` | `CELL_B_NEG` |

### Shunts — 0.01 Ω 1% 3 W 2512 (TA-I RLP25FEGR010)

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| R_SH_P1 | `+HV` | `+HV_P1` |
| R_SH_P2 | `+HV` | `+HV_P2` |
| R_SH_N1 | `GND_N1` | `GND` |
| R_SH_N2 | `GND_N2` | `GND` |

### Gate pulldowns — 10 kΩ 0603

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| Rgpd_1 | `GATE_P1_OUT` | `CELL_A_POS` |
| Rgpd_2 | `GATE_P2_OUT` | `CELL_B_POS` |
| Rgpd_3 | `GATE_N1_OUT` | `GND_N1` |
| Rgpd_4 | `GATE_N2_OUT` | `GND_N2` |

---

## 6. High-side current-sense amplifiers — 2× INA180A1

The ADS131M04 abs max for analog inputs is AVDD + 0.3 V = 3.6 V.
High-side shunt nodes (+HV, +HV_P1/P2) run at 2.65–10 V. Two INA180A1
(TI, SOT-23-5, gain = 20 V/V, CM range −0.2 to +26 V) convert each
high-side shunt voltage to a ground-referenced signal.

INA180A1IDBVR (C122228) Pinout A (TI SBOS741H): **1=OUT, 2=GND, 3=IN+, 4=IN−, 5=VS**

| Ref | Pin 1 (OUT) | Pin 2 (GND) | Pin 3 (IN+) | Pin 4 (IN−) | Pin 5 (VS) |
|---|---|---|---|---|---|
| U_CSA_P1 | `CSA_P1_OUT` | `GND` | `+HV` | `+HV_P1` | `+3V3` |
| U_CSA_P2 | `CSA_P2_OUT` | `GND` | `+HV` | `+HV_P2` | `+3V3` |

Decoupling (VS to GND, close to IC):

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| C_CSA_P1 | `+3V3` | `GND` |
| C_CSA_P2 | `+3V3` | `GND` |

At 3 A: Vout = 20 × 30 mV = 600 mV. Unidirectional — clips at 0 V if
current reverses (acceptable: each high-side FET conducts in one
direction only).

---

## 7. Current/voltage sensing — 2× ADS131M04

ADS131M04IRUKR (C5121509), WQFN-20 (3×3 mm).

### WQFN-20 pin mapping

| Pin | Name | U_ADC1 net | U_ADC2 net |
|---|---|---|---|
| 1 | AIN0P | `CSA_P1_OUT` | Divider from `+HV` → `VDIV_HV` |
| 2 | AIN0N | `AGND` | `AGND` |
| 3 | AIN1N | `AGND` | `AGND` |
| 4 | AIN1P | `CSA_P2_OUT` | Divider from `+HV_P1` → `VDIV_HV_P1` |
| 5 | AIN2P | `GND_N1` | Divider from `+HV_P2` → `VDIV_HV_P2` |
| 6 | AIN2N | `GND` | `AGND` |
| 7 | AIN3N | `GND` | `AGND` |
| 8 | AIN3P | `GND_N2` | Divider from `GND_N1` → `VDIV_GND_N1` |
| 9 | SYNC/RESET | `ADS_RESET` | `ADS_RESET` |
| 10 | CS | `ADS1_CS` | `ADS2_CS` |
| 11 | DRDY | `ADS1_DRDY` | `ADS2_DRDY` |
| 12 | SCLK | `ADS_SCK` | `ADS_SCK` |
| 13 | DOUT | `ADS_MISO` | `ADS_MISO` |
| 14 | DIN | `ADS_MOSI` | `ADS_MOSI` |
| 15 | CLKIN | `ADS_CLKIN` | `ADS_CLKIN` |
| 16 | CAP | `CAP1` (→ C_CAP1 → GND) | `CAP2` (→ C_CAP2 → GND) |
| 17 | DGND | `GND` | `GND` |
| 18 | DVDD | `+3V3` | `+3V3` |
| 19 | AVDD | `+3V3_A` | `+3V3_A` |
| 20 | AGND | `AGND` | `AGND` |
| TP | Thermal pad | `AGND` | `AGND` |

### Channel assignment summary

**U_ADC1 — shunt current:**

| CH | AINxP (pin) | AINxN (pin) | Gain | Signal |
|---|---|---|---|---|
| 0 | CSA_P1_OUT (1) | AGND (2) | 1 | P1 high-side current (via INA180) |
| 1 | CSA_P2_OUT (4) | AGND (3) | 1 | P2 high-side current (via INA180) |
| 2 | GND_N1 (5) | GND (6) | 16 | N1 low-side current (direct) |
| 3 | GND_N2 (8) | GND (7) | 16 | N2 low-side current (direct) |

**U_ADC2 — bus voltage (all single-ended, gain=1):**

| CH | AINxP (pin) | AINxN (pin) | Gain | Signal |
|---|---|---|---|---|
| 0 | VDIV_HV (1) | AGND (2) | 1 | +HV rail voltage |
| 1 | VDIV_HV_P1 (4) | AGND (3) | 1 | +HV_P1 node voltage |
| 2 | VDIV_HV_P2 (5) | AGND (6) | 1 | +HV_P2 node voltage |
| 3 | VDIV_GND_N1 (8) | AGND (7) | 1 | GND_N1 node voltage |

### Per-device support components (×2)

| Ref pattern | Part | Pin 1 | Pin 2 |
|---|---|---|---|
| C_AVDD1 | 10 µF 0805 X7R 10 V | `+3V3_A` | `AGND` |
| C_AVDD2_U* | 100 nF 0603 (per device) | `+3V3_A` | `AGND` |
| C_DVDD_U* | 1 µF 0603 X5R 50 V (per device) | `+3V3` | `GND` |
| C_CAP_U* | 220 nF 0603 (per device) | `CAP*` | `GND` |

C_AVDD1 is shared (single ferrite + bulk cap for both). C_AVDD2, C_DVDD,
C_CAP are per-device (qty 2 each).

### Analog supply filter

| Ref | Part | Pin 1 | Pin 2 |
|---|---|---|---|
| L_AVDD | Ferrite bead HCB1608KF-601T20, 600 Ω @ 100 MHz, 0603 | `+3V3` | `+3V3_A` |

---

## 8. Bus voltage analog front-end — 4× divider + clamp

Each bus voltage channel uses an 11:1 resistive divider + anti-alias
cap + BAV99 input clamp.

```
  +HV (0–10 V)
      │
   [R_DIV_H]  91 kΩ 0603 1%
      │
      ├──── [BAV99 pin 3] ──── AINxP
      │         │    │
   [R_DIV_L]   pin 2 pin 1
   9.1 kΩ      │    │
      │      +3V3_A AGND
    AGND
      │
   [C_AA] 100 nF
      │
    AGND
```

### Voltage dividers

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| R_DIV_H1 | `+HV` | `VDIV_HV` |
| R_DIV_L1 | `VDIV_HV` | `AGND` |
| R_DIV_H2 | `+HV_P1` | `VDIV_HV_P1` |
| R_DIV_L2 | `VDIV_HV_P1` | `AGND` |
| R_DIV_H3 | `+HV_P2` | `VDIV_HV_P2` |
| R_DIV_L3 | `VDIV_HV_P2` | `AGND` |
| R_DIV_H4 | `GND_N1` | `VDIV_GND_N1` |
| R_DIV_L4 | `VDIV_GND_N1` | `AGND` |

### Anti-alias capacitors (across R_DIV_L, f_c ≈ 175 Hz)

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| C_AA1 | `VDIV_HV` | `AGND` |
| C_AA2 | `VDIV_HV_P1` | `AGND` |
| C_AA3 | `VDIV_HV_P2` | `AGND` |
| C_AA4 | `VDIV_GND_N1` | `AGND` |

### Input clamps — BAV99,215 (Nexperia, SOT-23)

Pin mapping: **1=A1 (anode D1), 2=K2 (cathode D2), 3=K1/A2 (signal)**

| Ref | Pin 1 (A1) | Pin 2 (K2) | Pin 3 (K1/A2) |
|---|---|---|---|
| D_CLAMP1 | `AGND` | `+3V3_A` | `VDIV_HV` |
| D_CLAMP2 | `AGND` | `+3V3_A` | `VDIV_HV_P1` |
| D_CLAMP3 | `AGND` | `+3V3_A` | `VDIV_HV_P2` |
| D_CLAMP4 | `AGND` | `+3V3_A` | `VDIV_GND_N1` |

---

## 9. Gate drivers — 4× UCC5304DWVR

SOIC-8 DWV package (7.5×11.5 mm). Hand-solder from stock (DNP_JLC=TRUE).
Use TI recommended land pattern with extra pad margin (V2 pads were
too narrow — `PCB_V3_CHANGELIST.md` item #5).

Pin mapping: **1=IN, 2=VCCI, 3=VCCI, 4=GND, 5=VSS, 6=VSS, 7=OUT, 8=VDD**

| Ref | Drives | Pin 1 | Pin 2/3 | Pin 4 | Pin 5/6 | Pin 7 | Pin 8 |
|---|---|---|---|---|---|---|---|
| U1 | Q1 (P1, HS) | `GATE_P1_IN` | `+3V3` | `GND` | `CELL_A_POS` | `GATE_P1_OUT_PRE` | `VCC2_P1` |
| U2 | Q2 (P2, HS) | `GATE_P2_IN` | `+3V3` | `GND` | `CELL_B_POS` | `GATE_P2_OUT_PRE` | `VCC2_P2` |
| U3 | Q3 (N1, LS) | `GATE_N1_IN` | `+3V3` | `GND` | `GND` | `GATE_N1_OUT_PRE` | `VCC2_LS` |
| U4 | Q4 (N2, LS) | `GATE_N2_IN` | `+3V3` | `GND` | `GND` | `GATE_N2_OUT_PRE` | `VCC2_LS` |

### Gate series resistors — 10 Ω 0603

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| R_G_U1 | `GATE_P1_OUT_PRE` | `GATE_P1_OUT` |
| R_G_U2 | `GATE_P2_OUT_PRE` | `GATE_P2_OUT` |
| R_G_U3 | `GATE_N1_OUT_PRE` | `GATE_N1_OUT` |
| R_G_U4 | `GATE_N2_OUT_PRE` | `GATE_N2_OUT` |

### VCCI decoupling — 100 nF 0603 (pin 2/3 to pin 4)

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| C_VCCI_U1 | `+3V3` | `GND` |
| C_VCCI_U2 | `+3V3` | `GND` |
| C_VCCI_U3 | `+3V3` | `GND` |
| C_VCCI_U4 | `+3V3` | `GND` |

### VDD-VSS decoupling — 10 µF 1206 50 V + 100 nF 0603

| Ref | Part | Pin 1 | Pin 2 |
|---|---|---|---|
| C_VDD_U1_1 | 10 µF | `VCC2_P1` | `CELL_A_POS` |
| C_VDD_U1_2 | 100 nF | `VCC2_P1` | `CELL_A_POS` |
| C_VDD_U2_1 | 10 µF | `VCC2_P2` | `CELL_B_POS` |
| C_VDD_U2_2 | 100 nF | `VCC2_P2` | `CELL_B_POS` |
| C_VDD_U3_1 | 10 µF | `VCC2_LS` | `GND` |
| C_VDD_U3_2 | 100 nF | `VCC2_LS` | `GND` |
| C_VDD_U4_1 | 10 µF | `VCC2_LS` | `GND` |
| C_VDD_U4_2 | 100 nF | `VCC2_LS` | `GND` |

---

## 10. Isolated gate supplies — 3× B0512S-1WR3

Mornsun B0512S-1WR3, SIP-4, 5 V → 12 V, 1 W, 1.5 kV isolation.

Pin mapping (Mornsun): **1=Vin-(GND), 2=Vin+, 3=Vout-(0V), 4=Vout+(+Vo)**

| Ref | Pin 1 (Vin-) | Pin 2 (Vin+) | Pin 3 (Vout-) | Pin 4 (Vout+) | Feeds |
|---|---|---|---|---|---|
| PS1 | `GND` | `+5V` | `CELL_A_POS` | `VCC2_P1` | U1 (P1 HS driver) |
| PS2 | `GND` | `+5V` | `CELL_B_POS` | `VCC2_P2` | U2 (P2 HS driver) |
| PS3 | `GND` | `+5V` | `GND` | `VCC2_LS` | U3/U4 (LS drivers, shared 12 V) |

### Decoupling per converter (×3)

| Ref pattern | Part | Pin 1 | Pin 2 |
|---|---|---|---|
| C_PS*_IN1 | 4.7 µF 0805 | `+5V` | `GND` |
| C_PS*_IN2 | 100 nF 0603 | `+5V` | `GND` |
| C_PS*_OUT1 | 2.2 µF 0805 | PS* Vout+ net | PS* Vout- net |
| C_PS*_OUT2 | 100 nF 0603 | PS* Vout+ net | PS* Vout- net |

Expanded:

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| C_PS1_IN1 | `+5V` | `GND` |
| C_PS1_IN2 | `+5V` | `GND` |
| C_PS1_OUT1 | `VCC2_P1` | `CELL_A_POS` |
| C_PS1_OUT2 | `VCC2_P1` | `CELL_A_POS` |
| C_PS2_IN1 | `+5V` | `GND` |
| C_PS2_IN2 | `+5V` | `GND` |
| C_PS2_OUT1 | `VCC2_P2` | `CELL_B_POS` |
| C_PS2_OUT2 | `VCC2_P2` | `CELL_B_POS` |
| C_PS3_IN1 | `+5V` | `GND` |
| C_PS3_IN2 | `+5V` | `GND` |
| C_PS3_OUT1 | `VCC2_LS` | `GND` |
| C_PS3_OUT2 | `VCC2_LS` | `GND` |

**Mornsun pinout verified.** GDHUIZHT/YLPTEC brands have pins 1↔2 and
3↔4 swapped — do NOT install those brands without cross-wiring.
Add silkscreen note "MORNSUN PINOUT" on PCB.

### Bleeder resistors — 1.2 kΩ 0805 (minimum load)

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| R_BLEED_PS1 | `VCC2_P1` | `CELL_A_POS` |
| R_BLEED_PS2 | `VCC2_P2` | `CELL_B_POS` |
| R_BLEED_PS3 | `VCC2_LS` | `GND` |

---

## 11. MCU — Raspberry Pi Pico 2 (RP2350)

Socketed via 2×20 TH headers. Physical pin numbers 1–40.

### Pin assignments

| Phys pin | Pad name | Net | Function |
|---|---|---|---|
| 1 | GP0 | `LED_AUTO` | Auto-follow indicator (yellow LED) |
| 2 | GP1 | `NEOPIXEL` | WS2812 data |
| 3 | GND | `GND` | |
| 4 | GP2 | `GATE_P1_IN` | → UCC5304 U1 → Q1 |
| 5 | GP3 | `GATE_P2_IN` | → UCC5304 U2 → Q2 |
| 6 | GP4 | `GATE_N1_IN` | → UCC5304 U3 → Q3 |
| 7 | GP5 | `GATE_N2_IN` | → UCC5304 U4 → Q4 |
| 8 | GND | `GND` | |
| 9 | GP6 | `SDA` | I²C0 SDA → OLED |
| 10 | GP7 | `SCL` | I²C0 SCL → OLED |
| 11 | GP8 | `BTN_A` | Button A (input, ext pullup) |
| 12 | GP9 | `BTN_B` | Button B (input, ext pullup) |
| 13 | GND | `GND` | |
| 14 | GP10 | `ADS_SCK` | SPI1 SCK → ADS131M04 |
| 15 | GP11 | `ADS_MOSI` | SPI1 TX → ADS131M04 |
| 16 | GP12 | `ADS_MISO` | SPI1 RX ← ADS131M04 |
| 17 | GP13 | `ADS1_CS` | GPIO → U_ADC1 CS |
| 18 | GND | `GND` | |
| 19 | GP14 | `ADS1_DRDY` | GPIO ← U_ADC1 DRDY |
| 20 | GP15 | `ADS_RESET` | GPIO → ADS SYNC/RESET (shared) |
| 21 | GP16 | `SD_MISO` | SPI0 RX ← microSD |
| 22 | GP17 | `SD_CS` | GPIO → microSD CS |
| 23 | GND | `GND` | |
| 24 | GP18 | `SD_SCK` | SPI0 SCK → microSD |
| 25 | GP19 | `SD_MOSI` | SPI0 TX → microSD |
| 26 | GP20 | `BTN_C` | Button C (input, ext pullup) |
| 27 | GP21 | `ADS2_DRDY` | GPIO ← U_ADC2 DRDY |
| 28 | GND | `GND` | |
| 29 | GP22 | `LED_REC` | Recording indicator (green LED) |
| 30 | RUN | NC | (pulled high internally) |
| 31 | GP26 | `ADS2_CS` | GPIO → U_ADC2 CS |
| 32 | GP27 | `ADS_CLKIN` | PIO → ADS131M04 CLKIN (8.192 MHz, shared) |
| 33 | AGND | `AGND` | |
| 34 | GP28 | `TP_GP28` | Test pad (future ADC) |
| 35 | ADC_VREF | NC | |
| 36 | 3V3(OUT) | `+3V3` | Regulator output |
| 37 | 3V3_EN | NC | (pulled high internally) |
| 38 | GND | `GND` | |
| 39 | VSYS | NC | (connected to VBUS internally via Schottky on Pico 2) |
| 40 | VBUS | `+5V` | USB VBUS source |

GP25 = onboard LED (heartbeat), not on header — no schematic connection.

### Test pads

| Ref | Pin 1 | Net |
|---|---|---|
| TP1 | `TP_GP28` | Future ADC input |

---

## 12. OLED display — SSD1306 (via J3)

Module connected via J3 header. I²C address 0x3C.

### I²C pullups — 4.7 kΩ 0603

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| R_SDA | `+3V3` | `SDA` |
| R_SCL | `+3V3` | `SCL` |

---

## 13. microSD card (J4)

TF-PUSH socket (C393941), SPI mode. Standard SD pin mapping:

| Socket pin | SD function | SPI function | Net |
|---|---|---|---|
| 1 | DAT2 | NC | (no connect) |
| 2 | CD/DAT3 | CS | `SD_CS` |
| 3 | CMD | MOSI | `SD_MOSI` |
| 4 | VDD | VDD | `+3V3` |
| 5 | CLK | SCK | `SD_SCK` |
| 6 | VSS | GND | `GND` |
| 7 | DAT0 | MISO | `SD_MISO` |
| 8 | DAT1 | NC | (no connect) |
| Cd | Card detect | NC | (detect via SPI init) |

### SD power decoupling

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| C_SD1 | `+3V3` | `GND` |
| C_SD2 | `+3V3` | `GND` |

---

## 14. Buttons — 3× tact switches

XKB TS-1187A (C318884), SPST-NO, SMD. Each has hardware RC debounce:
10 kΩ pullup to +3V3 + 100 nF to GND (τ ≈ 1 ms). Active low.

Switch: one side to GPIO net, other side to GND.

| Ref | GPIO side | GND side |
|---|---|---|
| SW_A | `BTN_A` | `GND` |
| SW_B | `BTN_B` | `GND` |
| SW_C | `BTN_C` | `GND` |

### Pullup resistors — 10 kΩ 0603

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| R_BTN_A | `+3V3` | `BTN_A` |
| R_BTN_B | `+3V3` | `BTN_B` |
| R_BTN_C | `+3V3` | `BTN_C` |

### Debounce capacitors — 100 nF 0603

| Ref | Pin 1 | Pin 2 |
|---|---|---|
| C_BTN_A | `BTN_A` | `GND` |
| C_BTN_B | `BTN_B` | `GND` |
| C_BTN_C | `BTN_C` | `GND` |

---

## 15. Status LEDs

### Gate drive indicators (no GPIO — driven by GATE_*_IN nets)

LED anode at +3V3, cathode through 1 kΩ to GATE_*_IN. LED ON when
gate is LOW (FET idle). Device:LED pin mapping: **1=K (cathode), 2=A (anode)**.

| Ref LED | Ref R | LED pin 2 (A) | LED pin 1 (K) → R → | R other end |
|---|---|---|---|---|
| D_LED_P1 | R_LED_P1 | `+3V3` | `D_LED_P1_K` | `GATE_P1_IN` |
| D_LED_P2 | R_LED_P2 | `+3V3` | `D_LED_P2_K` | `GATE_P2_IN` |
| D_LED_N1 | R_LED_N1 | `+3V3` | `D_LED_N1_K` | `GATE_N1_IN` |
| D_LED_N2 | R_LED_N2 | `+3V3` | `D_LED_N2_K` | `GATE_N2_IN` |

### Power LED

| Ref LED | Ref R | LED pin 2 (A) | LED pin 1 (K) → R → | R other end |
|---|---|---|---|---|
| D_LED_PWR | R_LED_PWR | `+3V3` | `PWR_LED_K` | `GND` |

### GPIO-driven LEDs

| Ref LED | Ref R | LED pin 2 (A) → R → | R other end | LED pin 1 (K) |
|---|---|---|---|---|
| D_LED_REC | R_LED_REC | `LED_REC_A` | `LED_REC` (GP22) | `GND` |
| D_LED_AUTO | R_LED_AUTO | `LED_AUTO_A` | `LED_AUTO` (GP0) | `GND` |

Note: GPIO-driven LEDs have anode through resistor to GPIO, cathode to
GND. LED on when GPIO HIGH.

### NeoPixel — WS2812B-MINI-X2 (3535)

Pin mapping: **1=VDD, 2=DOUT, 3=GND, 4=DIN**

| Pin | Net |
|---|---|
| 1 (VDD) | `NEO_VDD` (from D_NEO cathode) |
| 2 (DOUT) | NC |
| 3 (GND) | `GND` |
| 4 (DIN) | `NEOPIXEL` (GP1) |

### NeoPixel VDD level shift — 1N4148W (D_NEO, SOD-123)

Series diode drops VDD to improve VIH margin for 3.3 V GPIO.

| Pin | Net |
|---|---|
| Anode (A) | `+5V` |
| Cathode (K) | `NEO_VDD` |

---

## 16. PWR_FLAG symbols

Tell ERC that externally-driven nets have a source.

| Ref | Net |
|---|---|
| PWR_FLAG_HV | `+HV` |
| PWR_FLAG_GND | `GND` |
| PWR_FLAG_5V | `+5V` |
| PWR_FLAG_CAN | `CELL_A_NEG` |
| PWR_FLAG_CBN | `CELL_B_NEG` |
| PWR_FLAG_AGND | `AGND` |

---

## 17. Net summary

| Net | Approx pin count |
|---|---|
| `GND` | ~40 |
| `AGND` | ~15 |
| `+HV` | 7 (protection, 2× shunt, 2× INA180 IN+, divider, bulk cap) |
| `+HV_P1` / `+HV_P2` | 5 each (shunt, FET drain, INA180 IN-, divider, ADS or cap) |
| `GND_N1` / `GND_N2` | 4 each (FET source, shunt, ADS AINxP, divider) |
| `CELL_A_POS` / `CELL_B_POS` | 4 each (FET source, J2a, PS Vout-, U VSS, caps) |
| `CELL_A_NEG` / `CELL_B_NEG` | 2 each (FET drain, J2b) |
| `+5V` | 8+ |
| `+3V3` | 20+ |
| `+3V3_A` | 8 (ferrite out, 2× ADS AVDD, caps, 4× BAV99 clamp) |
| `VCC2_P1` / `VCC2_P2` | 3 each (PS Vout+, U VDD, caps) |
| `VCC2_LS` | 5 (PS3 Vout+, U3 VDD, U4 VDD, caps) |
| `CSA_P1_OUT` / `CSA_P2_OUT` | 2 each (INA180 OUT, ADS AINxP) |
| `ADS_CLKIN` | 3 (MCU GP27, 2× ADS CLKIN) |
| `ADS_RESET` | 3 (MCU GP15, 2× ADS SYNC/RESET) |
| `SDA` / `SCL` | 3 each (MCU, pullup, J3) |
| SPI1 (`ADS_SCK/MOSI/MISO`) | 3 each (MCU + 2× ADS) |
| SPI0 (`SD_*`) | 2 each (MCU + SD socket) |
| `GATE_*_IN` | 3 each (MCU + driver + LED) |
| `GATE_*_OUT` | 3 each (gate R + FET gate + pulldown) |
| `BTN_A/B/C` | 3 each (MCU + switch + pullup + cap) |
| `VDIV_*` | 4 each (divider junction + anti-alias + clamp + ADS AINxP) |

---

## 18. Component count

| Category | Count |
|---|---|
| MCU (Pico 2) | 1 |
| ADC (ADS131M04) | 2 |
| Current-sense amp (INA180A1) | 2 |
| MOSFETs (AO3400A) | 4 (+1 Q_RP) |
| Gate drivers (UCC5304) | 4 |
| DC-DC converters (B0512S) | 3 |
| Shunt resistors | 4 |
| Protection (polyfuse, TVS) | 2 |
| Connectors (screw, JST, SD) | 5 |
| Tact switches | 3 |
| LEDs (green + yellow + NeoPixel) | 8 |
| Diodes (BAV99 + 1N4148W) | 5 |
| Ferrite bead | 1 |
| Resistors (various) | 33 |
| Capacitors (various) | 38 |
| Test pads | 1 |
| PWR_FLAG | 6 |
| **Total** | **~122 (incl. passives + flags)** |

---

## 19. Verification checklist

- [x] High-side shunt CM (up to 10 V) exceeds ADS131M04 abs max (3.6 V) — resolved by 2× INA180A1
- [x] INA180A1 output at 3 A (600 mV) within ADS131M04 gain=1 range (±1.2 V)
- [x] ADS131M04 CLKIN provided: GP27 PIO 8.192 MHz clock (shared)
- [x] UCC5304 = SOIC-8 DWV (8 pins, ganged VCCI and VSS)
- [x] Topology: 4 independent cell-tab nets, not bridge midpoints
- [x] 3× B0512S-1WR3: 2 HS (floating), 1 LS (ground-referenced 12 V)
- [x] LS driver VDD = VCC2_LS (12 V from PS3), not +5V (UVLO fix)
- [x] Gate series resistors 10 Ω
- [x] 10 µF 50 V + 100 nF on each driver VDD-VSS
- [x] CYCLER_IN- separate from GND, tied via R_CYC_GND at J1 only
- [x] Status LEDs on driver input (GND-referenced), not floating output
- [x] Shunt direction consistent with sensing conventions
- [x] BAV99 pin 3 (K1/A2) = signal input for bidirectional clamping
- [x] ADS131M04 DVDD decoupling: 1 µF per device (not 100 nF)
- [x] ADS131M04 CAP pin: 220 nF to DGND per device
- [x] ADS131M04 has no REFP/REFN pins — internal reference
- [x] ADS131M04 thermal pad → AGND
- [x] SPI0 (SD) and SPI1 (ADS) on separate peripherals
- [x] I²C0 (OLED) on dedicated bus, no contention
- [x] NeoPixel VDD via 1N4148W (D_NEO): VDD ≈ 4.15 V @ 10 mA
- [x] B0512S bleeder resistors (1.2 kΩ) on all three converters
- [ ] B0512S pin mapping: verify Mornsun vs GDHUIZHT pinout for ordered parts
- [ ] TS-1187A tact switch: verify pin mapping (image-only datasheet)
- [ ] TF-PUSH microSD: verify SPI pin mapping (image-only drawing)
- [ ] Kelvin clip pads: verify dimensions and ENIG finish at layout time
- [ ] Green LED brightness: at Vf_max=3.10 V, only 0.2 mA through 1 kΩ — consider 680 Ω
- [ ] J2a/J2b silkscreen polarity labels: address during KiCad layout

---

## 20. Custom symbols required

| Symbol name | Package | Source | Notes |
|---|---|---|---|
| `switching_circuit_v3:UCC5304` | SOIC-8 DWV | Carried from V2 lib | 8 pins, ganged VCCI (2/3) and VSS (5/6) |
| `switching_circuit_v3:B0512S_1WR3` | SIP-4 | Carried from V2 lib | 4 pins (Mornsun pinout) |
| `switching_circuit_v3:Pico2` | TH module 2×20 | **New** | 40 header pins by name |
| `switching_circuit_v3:ADS131M04` | WQFN-20 | **New** | 20 pins + thermal pad |
| `switching_circuit_v3:INA180A1` | SOT-23-5 | **New** | 5 pins: OUT, VS, GND, IN+, IN- |
| `switching_circuit_v3:WS2812B_MINI` | 3535 | **New** | 4 pins: VDD, DOUT, GND, DIN |
| `switching_circuit_v3:TF_PUSH` | SMD microSD | **New** | 9 pins (8 + Cd) |

Stock KiCad symbols used: `Device:R`, `Device:C`, `Device:LED`,
`Device:Polyfuse_Small`, `Device:D_TVS`, `Device:D`,
`Transistor_FET:Q_NMOS_GSD`, `Transistor_FET:Q_PMOS_GSD`,
`Connector:Screw_Terminal_01x02`, `Connector:TestPoint`,
`Connector_Generic:Conn_01x04`, `power:PWR_FLAG`.
