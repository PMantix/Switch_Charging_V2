# Switching Circuit V2 — Schematic Spec

Source of truth for the flat KiCad 10 schematic. Every part, every net, every pin-to-net mapping. When a value or connection is wrong, fix it here first, then regenerate the schematic.

## Topology overview

**NOT a traditional H-bridge.** The device under test is a special 4-terminal pouch cell with four independent tabs: `CELL_A_POS`, `CELL_A_NEG`, `CELL_B_POS`, `CELL_B_NEG`. Each tab is switched directly to a supply rail by one dedicated FET.

```
      +HV rail                                     +HV rail
          │                                            │
          │                                            │
     [R_SH_P1]                                    [R_SH_P2]
          │                                            │
      [Q1 P1]                                      [Q2 P2]
      HS-N-ch                                      HS-N-ch
          │                                            │
     CELL_A_POS ── J2 pin 1          J2 pin 3 ── CELL_B_POS
     ─ ─ ─ ─ ─ ─  pouch cell interior ─ ─ ─ ─ ─ ─ ─
     CELL_A_NEG ── J2 pin 2          J2 pin 4 ── CELL_B_NEG
          │                                            │
      [Q3 N1]                                      [Q4 N2]
      LS-N-ch                                      LS-N-ch
          │                                            │
     [R_SH_N1]                                    [R_SH_N2]
          │                                            │
         GND                                          GND
```

Firmware states `STATE_DEFS` in `switching_circuit_v2.py` map to the four independent charge/discharge paths:
- State 0 (P1+N1): current `+HV → CELL_A_POS → [cell] → CELL_A_NEG → GND`
- State 1 (P1+N2): current `+HV → CELL_A_POS → [cell] → CELL_B_NEG → GND`
- State 2 (P2+N1): current `+HV → CELL_B_POS → [cell] → CELL_A_NEG → GND`
- State 3 (P2+N2): current `+HV → CELL_B_POS → [cell] → CELL_B_NEG → GND`
- State 4 (all on): all four tabs simultaneously to their rails
- State 5 (all off): idle

## Power rails

| Net | Source | Expected voltage | Purpose |
|---|---|---|---|
| `+HV` | Cycler through J1 + protection | 2.65–10 V typical | FET drain rails for P1/P2 |
| `CYCLER_IN-` | Cycler negative through J1 | ~0 V | Kept separate from system GND (not every cycler is earth-referenced); tied to `GND` only at J1 |
| `GND` | Star ground | 0 V | System reference |
| `+5V` | RP2040-Zero USB VBUS pin | 5 V | U3/U4 VDD, 2× B0512S-1WR3 Vin, peripherals |
| `+3V3` | RP2040-Zero 3V3 pin | 3.3 V | UCC5304 VCCI (all 4), INA226 VS, I²C pullups, LEDs |
| `VCC2_P1` | B0512S #1 Vout+ (floating, ref to `CELL_A_POS`) | ~12 V above CELL_A_POS | U1 VDD |
| `VCC2_P2` | B0512S #2 Vout+ (floating, ref to `CELL_B_POS`) | ~12 V above CELL_B_POS | U2 VDD |

## Connectors

| Ref | Part | Nets |
|---|---|---|
| J1 | 5.08 mm 2-pos screw terminal (cycler in) | `CYCLER_IN+`, `CYCLER_IN-` |
| J2 | 5.08 mm 4-pos screw terminal (cell out) | `CELL_A_POS`, `CELL_A_NEG`, `CELL_B_POS`, `CELL_B_NEG` |
| J3 | JST-XH 5-pin vertical (rotary encoder) | `+3V3`, `GND`, `ENC_CLK`, `ENC_DT`, `ENC_SW` |
| J4 | JST-XH 4-pin vertical (TM1637 display) | `+3V3`, `GND`, `DISPLAY_CLK`, `DISPLAY_DIO` |

No external 12 V input — all board power comes via the RP2040-Zero's USB-C (from Pi) plus the cycler via J1.

## Power-in protection

| Ref | Part | Value | Connection |
|---|---|---|---|
| F1 | PPTC polyfuse | 3 A hold, 1812 | Series: `CYCLER_IN+` → F1 → `+HV_PREFUSE` |
| Q_RP | P-ch MOSFET | AO3401A SOT-23 | Reverse-polarity: S=`+HV_PREFUSE`, D=`+HV`, G=`CYCLER_IN-` with 10 kΩ S-G |
| R_RP | 10 kΩ | 0603 | Q_RP gate-source pulldown |
| D_TVS | Bidir TVS | SMBJ12CA SMB | Across `+HV` / `CYCLER_IN-`; 12 V standoff allows ±10 V cycler with headroom |
| C_BULK1 | 10 µF MLCC | 0805 X5R 25V | `+HV` to `GND` |
| C_BULK2 | 100 nF MLCC | 0603 X7R 25V | `+HV` to `GND` |
| C_5V1 | 10 µF MLCC | 0805 X5R 16V | `+5V` to `GND` |
| C_5V2 | 100 nF MLCC | 0603 X7R 25V | `+5V` to `GND` |

`CYCLER_IN-` connects to `GND` with a single wire directly at J1 (schematic-level short, physically realized as a small trace at the terminal pad).

## Power stage — 4× MOSFETs + 4× shunts

Each FET is an **AO3400A** (N-ch, SOT-23, 20 mΩ, 5.8 A, JLC Basic Part).

| Ref | Position | Drain | Source | Gate |
|---|---|---|---|---|
| Q1 | P1 (HS → CELL_A_POS) | `+HV_P1` (after shunt) | `CELL_A_POS` | `GATE_P1_OUT` |
| Q2 | P2 (HS → CELL_B_POS) | `+HV_P2` (after shunt) | `CELL_B_POS` | `GATE_P2_OUT` |
| Q3 | N1 (LS ← CELL_A_NEG) | `CELL_A_NEG` | `GND_N1` (to shunt) | `GATE_N1_OUT` |
| Q4 | N2 (LS ← CELL_B_NEG) | `CELL_B_NEG` | `GND_N2` (to shunt) | `GATE_N2_OUT` |

Each FET gate also gets a **10 kΩ pulldown** from gate to source (`Rgpd_1..4`) to hold off when the driver is floating.

**Shunts** — 0.05 Ω 1% 1 W 2512 metal-strip:

| Ref | Location | Net in | Net out |
|---|---|---|---|
| R_SH_P1 | Q1 drain (high-side) | `+HV` | `+HV_P1` |
| R_SH_P2 | Q2 drain (high-side) | `+HV` | `+HV_P2` |
| R_SH_N1 | Q3 source (low-side) | `GND_N1` | `GND` |
| R_SH_N2 | Q4 source (low-side) | `GND_N2` | `GND` |

## Current sensing — 4× INA226

Bare INA226 (VSSOP-10, stock `Sensor_Energy:INA226`, `Package_SO:TSSOP-10_3x3mm_P0.5mm`). I²C addresses match the firmware `INA226_ADDRS` dict (P1=0x40, P2=0x41, N1=0x43, N2=0x45).

| Ref | Addr | A1 | A0 | Vin+ | Vin- | Vbus | Decoupling |
|---|---|---|---|---|---|---|---|
| U_INA_P1 | 0x40 | GND | GND | `+HV` | `+HV_P1` | `+HV_P1` | `C_INA_P1` 100 nF 0603 |
| U_INA_P2 | 0x41 | GND | VS  | `+HV` | `+HV_P2` | `+HV_P2` | `C_INA_P2` 100 nF 0603 |
| U_INA_N1 | 0x43 | SDA | VS  | `GND_N1` | `GND` | `GND_N1` | `C_INA_N1` 100 nF 0603 |
| U_INA_N2 | 0x45 | VS  | VS  | `GND_N2` | `GND` | `GND_N2` | `C_INA_N2` 100 nF 0603 |

All INA226: pin 6 VS → `+3V3`, pin 7 GND → `GND`, pin 4 SDA → `SDA`, pin 5 SCL → `SCL`, pin 3 ~ALERT → `ALERT` (wired-OR, all 4 share).

Pullups:
- `R_SDA` 4.7 kΩ 0603 from `SDA` to `+3V3`
- `R_SCL` 4.7 kΩ 0603 from `SCL` to `+3V3`
- `R_ALERT` 4.7 kΩ 0603 from `ALERT` to `+3V3` (ALERT wired to `GP29`)

## Gate drivers — 4× UCC5304DWVR

Each UCC5304 is the DWV (SOIC-8 wide) package, marked `DNP_JLC=TRUE` (you hand-solder from your stock after JLC assembly).

| Ref | Drives | Pin 1 IN | Pin 2/3 VCCI | Pin 4 GND | Pin 5/6 VSS | Pin 7 OUT | Pin 8 VDD |
|---|---|---|---|---|---|---|---|
| U1 | Q1 (P1, HS) | `GATE_P1_IN` | `+3V3` | `GND` | `CELL_A_POS` (floating) | `GATE_P1_OUT` | `VCC2_P1` |
| U2 | Q2 (P2, HS) | `GATE_P2_IN` | `+3V3` | `GND` | `CELL_B_POS` (floating) | `GATE_P2_OUT` | `VCC2_P2` |
| U3 | Q3 (N1, LS) | `GATE_N1_IN` | `+3V3` | `GND` | `GND` (ground-ref'd) | `GATE_N1_OUT` | `+5V` |
| U4 | Q4 (N2, LS) | `GATE_N2_IN` | `+3V3` | `GND` | `GND` (ground-ref'd) | `GATE_N2_OUT` | `+5V` |

Pins 2 and 3 (VCCI) are both ganged to `+3V3`. Pins 5 and 6 (VSS) are ganged to the same net (FET source for HS, GND for LS). UCC5304 UVLO is 5 V on VDD — LS drivers running on +5 V sit right at the edge, which matches Phillip's verified breadboard operation.

### Per-driver support components

**VCCI decoupling** (pin 2/3 to pin 4, close to driver):
- `C_VCCI_U1..4`: 100 nF 0603

**VDD-VSS decoupling** (pin 8 to pin 5/6, close to driver):
- `C_VDD_U1..4_1`: 10 µF 0805 X7R 25 V
- `C_VDD_U1..4_2`: 100 nF 0603 X7R 25 V

**Gate series resistors** (pin 7 OUT to FET gate):
- `R_G_U1..4`: **10 Ω** 0603 (not 100 Ω — matches the external V1 design doc)

## Isolated gate supplies — 2× B0512S-1WR3

Mornsun B0512S-1WR3 (SIP-4, 5 V → 12 V, 1 W, 1.5 kV isolation). Only required for high-side drivers.

| Ref | Pin 1 Vin+ | Pin 2 Vin- | Pin 3 Vout+ | Pin 4 Vout- | Feeds |
|---|---|---|---|---|---|
| PS1 | `+5V` | `GND` | `VCC2_P1` | `CELL_A_POS` | U1 VDD/VSS |
| PS2 | `+5V` | `GND` | `VCC2_P2` | `CELL_B_POS` | U2 VDD/VSS |

**Decoupling** (per Mornsun datasheet Table 1, 5 V input → 12 V output):
- Input side: `C_PS1_IN`, `C_PS2_IN` = 4.7 µF 0805 16 V (across Vin+/Vin-)
- Output side: `C_PS1_OUT`, `C_PS2_OUT` = 2.2 µF 0805 25 V (across Vout+/Vout-)

## MCU — RP2040-Zero

`A1`, `switching_circuit_v2:RP2040_Zero`. 23 TH pads:

| Pad | Net | Function |
|---|---|---|
| 5V | `+5V` | USB VBUS source |
| GND | `GND` | Common ground |
| 3V3 | `+3V3` | Regulator output |
| GP2 | `GATE_P1_IN` | To U1 pin 1 |
| GP3 | `GATE_P2_IN` | To U2 pin 1 |
| GP4 | `GATE_N1_IN` | To U3 pin 1 |
| GP5 | `GATE_N2_IN` | To U4 pin 1 |
| GP6 | `SDA` | I²C data |
| GP7 | `SCL` | I²C clock |
| GP8 | `ENC_CLK` | Encoder A |
| GP9 | `ENC_DT` | Encoder B |
| GP10 | `ENC_SW` | Encoder button |
| GP14 | `DISPLAY_CLK` | TM1637 clock |
| GP15 | `DISPLAY_DIO` | TM1637 data |
| GP29 | `ALERT` | INA226 overcurrent interrupt |
| GP0, GP1, GP11, GP12, GP13, GP26, GP27, GP28 | (test pads) | Each exposed as 1-pin 2.54 mm TH pad for future use |

## Status LEDs

5× green 0603 LEDs + 5× 1 kΩ 0603 resistors. Driven from the MCU **GATE_\*_IN** nets (referenced to system GND, not the floating driver outputs):

| Ref | Anode | Cathode → 1kΩ → | Purpose |
|---|---|---|---|
| D_LED_P1 | `+3V3` | `GATE_P1_IN` | P1 FET commanded indicator |
| D_LED_P2 | `+3V3` | `GATE_P2_IN` | P2 FET commanded indicator |
| D_LED_N1 | `+3V3` | `GATE_N1_IN` | N1 FET commanded indicator |
| D_LED_N2 | `+3V3` | `GATE_N2_IN` | N2 FET commanded indicator |
| D_LED_PWR | `+3V3` | `GND` | Board power indicator |

Anode at 3.3 V and cathode dragged to GND via 1 kΩ gives ~1.3 mA — dim but visible, doesn't load the MCU.

## Net summary

| Net | Approx pin count |
|---|---|
| `GND` | ~30 |
| `+HV` | 4 |
| `+HV_P1` / `+HV_P2` | 3 each |
| `GND_N1` / `GND_N2` | 3 each |
| `CELL_A_POS` | 3 (Q1 source, J2 pin 1, PS1 Vout-, U1 VSS) |
| `CELL_A_NEG` | 2 (Q3 drain, J2 pin 2) |
| `CELL_B_POS` | 3 (Q2 source, J2 pin 3, PS2 Vout-, U2 VSS) |
| `CELL_B_NEG` | 2 (Q4 drain, J2 pin 4) |
| `CYCLER_IN+` / `CYCLER_IN-` | 1+1 (tied to +HV_PREFUSE and GND respectively at J1) |
| `+5V` | 6+ |
| `+3V3` | 12+ |
| `VCC2_P1` / `VCC2_P2` | 2 each |
| `SDA` / `SCL` | 5 each |
| `ALERT` | 6 |
| `GATE_P1_IN`..`GATE_N2_IN` | 3 each (MCU + driver + LED) |
| `GATE_P1_OUT`..`GATE_N2_OUT` | 3 each (driver + FET gate + pulldown) |
| `ENC_CLK`/`ENC_DT`/`ENC_SW` | 2 each |
| `DISPLAY_CLK`/`DISPLAY_DIO` | 2 each |

## Component count

~70 parts. Custom symbols/footprints ready in `pcb/lib/switching_circuit_v2.{kicad_sym,pretty}`:
- `UCC5304` + `SOIC-8_DWV_7.5x11.5mm_P1.27mm`
- `B0512S_1WR3` + `B0512S_1WR3_SIP4`
- `RP2040_Zero` + `RP2040_Zero_SocketedTH`

Everything else uses stock libraries (`Device`, `Sensor_Energy`, `Connector_Generic`, `Connector`, `Package_TO_SOT_SMD`, `Package_SO`, `Resistor_SMD`, `Capacitor_SMD`, `LED_SMD`, `Diode_SMD`, `TestPoint`).

## Verification checklist

- [x] UCC5304 = SOIC-8 DWV (8 pins, ganged VCCI and VSS)
- [x] Topology: 4 independent cell-tab nets, not bridge midpoints
- [x] 2× B0512S-1WR3 for HS only, LS runs directly on +5 V
- [x] Gate series resistors 10 Ω
- [x] 100 nF + 10 µF on each driver VDD-VSS
- [x] CYCLER_IN- separate from GND, tied at J1 only
- [x] Unused MCU pins exposed as test pads
- [x] Status LEDs on driver input (GND-referenced), not on floating driver output
- [x] Shunt direction consistent with INA226 high-side / low-side sensing conventions
