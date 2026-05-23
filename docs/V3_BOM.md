# Switching Circuit V3 — Bill of Materials

Companion to `docs/V3_SPEC.md`. All LCSC part numbers verified in-stock
as of 2026-05-23. Max continuous current: **3 A** (hard limit).

---

## Summary

| Category | Part count | Est. cost |
|---|---|---|
| MCU (Pico 2 module) | 1 | ~$5 |
| ADC (2× ADS131M04) | 2 | ~$5.66 |
| MOSFETs (AO3400A, V2 carry-over) | 4 | ~$0.12 |
| Gate drivers (UCC5304, hand-solder) | 4 | existing stock |
| Isolated supplies (B0512S-1WR3) | 2 | existing stock |
| Shunt resistors | 4 | ~$0.19 |
| Protection (polyfuse, TVS, MOSFET) | 3 | existing stock |
| Voltage dividers + clamps | ~16 | ~$0.40 |
| Passives (caps, resistors, ferrite) | ~35 | ~$2.00 |
| Connectors (terminals, headers) | 5 | ~$1.30 |
| microSD socket | 1 | ~$0.07 |
| OLED display module | 1 | ~$3 |
| Buttons + debounce | 3 + passives | ~$0.10 |
| LEDs + resistors | 9 | ~$0.50 |
| **Total (new parts only)** | **~85** | **~$18** |

Cost excludes parts already in stock from V2 (UCC5304, B0512S-1WR3,
protection components, Pico 2 if already purchased).

---

## 1. MCU

| Ref | Part | Package | Qty | Source | Unit price | Notes |
|---|---|---|---|---|---|---|
| A1 | Raspberry Pi Pico 2 (RP2350) | TH module | 1 | Raspberry Pi official, DigiKey, Adafruit | ~$5 | Socketed via 2×20 pin headers. Future: bare RP2350 QFN-60 for JLCPCB assembly. |

---

## 2. Analog-to-digital converter

Changed from 1× ADS131M08 to **2× ADS131M04**. Same M-series register
map, same SPI frame format, better LCSC stock, cheaper total cost, and
actually faster (64 kSPS max vs 32 kSPS). Requires 2 SPI chip-selects
and 2 DRDY lines from RP2350.

| Ref | Part | Package | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| U_ADC1 | ADS131M04IRUKR | WQFN-20 (3×3 mm) | 1 | [C5121509](https://www.lcsc.com/product-detail/C5121509.html) | ~$2.83 | 24-bit 4-ch simultaneous delta-sigma. Shunt current channels (CH0–CH3). |
| U_ADC2 | ADS131M04IRUKR | WQFN-20 (3×3 mm) | 1 | [C5121509](https://www.lcsc.com/product-detail/C5121509.html) | ~$2.83 | Bus voltage channels (CH0–CH3 on second device). |

Stock: 3,520 units (extended part).

**Eval option:** TI ADS131M04EVM for SPI bring-up before committing to
the PCB layout.

---

## 3. Power MOSFETs

Retained from V2: **AO3400A** in SOT-23. Adequate for the 3 A max
current target (rated 5.7 A continuous). JLCPCB basic part.

| Ref | Part | Package | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| Q1–Q4 | AO3400A | SOT-23 | 4 | JLCPCB basic (same as V2) | ~$0.03 | N-ch, 30 V, 5.7 A, R_DS(on) ≤26.5 mΩ (typ 18 mΩ) @ V_GS=10 V. |

Thermal at 3 A (worst-case): P = 3² × 0.0265 = 0.24 W. SOT-23
θ_JA = 125 °C/W (steady-state, 1 in² FR-4, 2 oz Cu) → ΔT ≈ 30 °C —
comfortable at lab ambient. θ_JA measured per datasheet Note A.

---

## 4. Shunt resistors

| Ref | Part | Package | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| R_SH_P1–N2 | RLP25FEGR010 (TA-I Technology) | 2512 | 4 | [C393070](https://www.lcsc.com/product-detail/C393070.html) | ~$0.048 | 0.01 Ω, 1%, 3 W. TCR ±50 ppm. |

At 3 A: V_shunt = 30 mV, P = 0.09 W (well under 3 W rating).
Kelvin-sense routing to ADS131M04 differential inputs required.

Stock: 77,240 units (extended part).

---

## 5. ADS131M04 analog front-end

### Voltage dividers (bus voltage channels)

Divider ratio: 91 kΩ / (91 kΩ + 9.1 kΩ) = 10.01:1. Calibrated in
firmware. Anti-alias f_c = 1/(2π × 9.1 kΩ × 100 nF) ≈ 175 Hz.

| Ref | Part | Package | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| R_DIV_H1–H4 | 91 kΩ 1% 0603 | 0603 | 4 | [C23265](https://www.lcsc.com/product-detail/C23265.html) | ~$0.001 | UNI-ROYAL. High-side of 10:1 divider. Basic part. 29,100 stock. |
| R_DIV_L1–L4 | 9.1 kΩ 1% 0603 | 0603 | 4 | [C114639](https://www.lcsc.com/product-detail/C114639.html) | ~$0.002 | YAGEO. Low-side of divider. 37,900 stock. |
| C_AA1–AA4 | 100 nF X7R 0603 | 0603 | 4 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | ~$0.003 | Anti-alias across R_DIV_L. Basic part. |

### Input protection (bus voltage channels)

| Ref | Part | Package | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| D_CLAMP1–4 | BAV99,215 (Nexperia) | SOT-23 | 4 | [C2500](https://www.lcsc.com/product-detail/C2500.html) | ~$0.009 | Dual series diode, clamp to AVDD/AGND. **Basic part.** 1,656,928 stock. |

### Power supply filtering

| Ref | Part | Package | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| L_AVDD | TAI-TECH HCB1608KF-601T20 | 0603 | 1 | [C304319](https://www.lcsc.com/product-detail/C304319.html) | Ferrite bead 600 Ω @ 100 MHz, DCR=100 mΩ, 2 A. 28,400 stock. |
| C_AVDD1 | 10 µF X7R 10 V | 0805 | 1 | [C86038](https://www.lcsc.com/product-detail/C86038.html) | Murata. +3V3_A bulk decoupling. 153,220 stock. |
| C_AVDD2 | 100 nF X7R 50 V | 0603 | 1 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | +3V3_A close to AVDD pin. Basic part. |
| C_DVDD | 1 µF X5R 50 V | 0603 | 1 | [C15849](https://www.lcsc.com/product-detail/C15849.html) | Samsung. DVDD to DGND (external digital/IO supply, 2.7–3.6 V). Datasheet requires 1 µF. 3,740,750 stock. |
| C_CAP | 220 nF X7R 25 V | 0603 | 1 | [C21120](https://www.lcsc.com/product-detail/C21120.html) | Samsung. CAP to DGND (internal 1.8 V LDO output from DVDD). Datasheet required. 1,237,500 stock. |
| C_VREF | 100 nF X7R 50 V | 0603 | 1 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | REFP to REFN (internal reference). |

---

## 6. Gate drivers (retained from V2 — hand-solder stock)

| Ref | Part | Package | Qty | Notes |
|---|---|---|---|---|
| U1–U4 | UCC5304DWVR | SOIC-8 DWV (7.5×11.5 mm) | 4 | Existing stock. DNP for JLCPCB, hand-solder after. |

### Per-driver passives (×4)

| Ref | Part | Package | Qty total | LCSC # | Notes |
|---|---|---|---|---|---|
| C_VCCI_U1–4 | 100 nF X7R 50 V | 0603 | 4 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | VCCI to GND |
| C_VDD_U1–4_1 | 10 µF X5R 50 V | 1206 | 4 | [C100122](https://www.lcsc.com/product-detail/C100122.html) | VDD to VSS (bulk). All drivers see ~12 V across VDD-VSS — 10 V cap insufficient. Use 1206 50 V for all four. |
| C_VDD_U1–4_2 | 100 nF X7R 50 V | 0603 | 4 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | VDD to VSS (HF) |
| R_G_U1–4 | 10 Ω 1% 0603 | 0603 | 4 | [C109318](https://www.lcsc.com/product-detail/C109318.html) | YAGEO. Gate series resistor. 3,793,600 stock. |

---

## 7. Isolated gate supplies — 3× B0512S-1WR3

PS1/PS2 supply floating 12 V for high-side drivers. PS3 is new for V3:
supplies ground-referenced 12 V for low-side drivers (V2 used +5V
which was unreliable at the UCC5304 UVLO threshold).

| Ref | Part | Package | Qty | Notes |
|---|---|---|---|---|
| PS1, PS2, PS3 | B0512S-1WR3 (Mornsun) | SIP-4 | 3 | 5 V → 12 V, 1 W, 1.5 kV isolation. Existing stock (need 1 more for PS3). |

Decoupling per converter (×3) — upgraded from V2 per SW1 failure
(2026-05-19: B0512S died from gate charge transient stress without
adequate decoupling). Values match YLPTEC datasheet Table 1
(Cin=4.7 µF for 5 V input, Cout=2.2 µF for 12 V output):

| Ref | Part | Package | Qty total | LCSC # | Notes |
|---|---|---|---|---|---|
| C_PS*_IN1 | 4.7 µF X5R 25 V | 0805 | 3 | [C1779](https://www.lcsc.com/product-detail/C1779.html) | Samsung. Input bulk, close to Vin pins. Datasheet recommended. |
| C_PS*_IN2 | 100 nF X7R 50 V | 0603 | 3 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | Input HF, close to Vin pins. |
| C_PS*_OUT1 | 2.2 µF X7R 25 V | 0805 | 3 | [C19110](https://www.lcsc.com/product-detail/C19110.html) | Samsung. Output bulk, close to Vout pins. Datasheet recommended. |
| C_PS*_OUT2 | 100 nF X7R 50 V | 0603 | 3 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | Output HF, close to Vout pins. |

Max capacitive load for B0512S 12 V output: **560 µF**. Total per
converter (2.2 µF + 0.1 µF) = 2.3 µF — well under limit.

**Minimum load warning:** B0512S-1WR3 requires ≥9 mA output (10% of
84 mA rated) for proper regulation. UCC5304 VDD draw is only ~1–2.5 mA
per driver. Add a bleeder resistor on each converter output:

| Ref | Part | Package | Qty total | LCSC # | Notes |
|---|---|---|---|---|---|
| R_BLEED_PS1–3 | 1.5 kΩ 1% 0603 | 0603 | 3 | [C22843](https://www.lcsc.com/product-detail/C22843.html) | VDD to VSS per converter. I_bleed = 12 V / 1.5 kΩ = 8 mA. P = 96 mW (0603 rated 100 mW, use 0805 if margin needed). Total load with driver: ~10 mA. |

**Pin mapping warning:** GDHUIZHT-brand B0512S modules have pins 1↔2
and 3↔4 swapped vs Mornsun datasheet. Add silkscreen note on PCB.
See `pcb/PCB_V3_CHANGELIST.md` item #1.

---

## 8. Power-in protection (retained from V2)

| Ref | Part | Value | Package | Qty | Notes |
|---|---|---|---|---|---|
| F1 | PPTC polyfuse | 3 A hold | 1812 | 1 | existing stock |
| Q_RP | AO3401A | P-ch MOSFET | SOT-23 | 1 | existing stock |
| R_RP | 10 kΩ 5% 0603 | — | 0603 | 1 | [C99198](https://www.lcsc.com/product-detail/C99198.html) YAGEO. |
| D_TVS | SMBJ12CA | Bidir TVS 12 V | SMB | 1 | existing stock |
| C_BULK1 | 10 µF X5R 50 V | 1206 | 1 | [C100122](https://www.lcsc.com/product-detail/C100122.html) YAGEO. +HV decoupling (1206 for voltage margin). 251,470 stock. |
| C_BULK2 | 100 nF X7R 50 V | 0603 | 1 | [C14663](https://www.lcsc.com/product-detail/C14663.html) +HV decoupling |
| C_5V1 | 10 µF X7R 10 V | 0805 | 1 | [C86038](https://www.lcsc.com/product-detail/C86038.html) Murata. +5V decoupling. |
| C_5V2 | 100 nF X7R 50 V | 0603 | 1 | [C14663](https://www.lcsc.com/product-detail/C14663.html) +5V decoupling |

### Optional 5 V buck converter (DNP by default)

Footprint on PCB but not populated. Selectable via solder jumper SJ_5V
(default: bridged to VBUS). Populate only if USB 500 mA budget is
insufficient under measured load.

| Ref | Part | Value | Package | Qty | Notes |
|---|---|---|---|---|---|
| U_BUCK | TPS5430DDAR or AP3429K | 5 V buck | SOIC-8 / SOT-23-5 | 1 | DNP. Select at layout time based on LCSC stock. |
| L_BUCK | Inductor | 10 µH, ≥1 A sat | — | 1 | DNP |
| D_BUCK | Schottky | SS34 or equiv | SMA | 1 | DNP |
| C_BUCK_OUT | 22 µF MLCC | 0805 10 V | 0805 | 1 | DNP |
| R_FB1, R_FB2 | Feedback divider | per datasheet | 0603 | 2 | DNP |
| SJ_5V | Solder jumper | — | — | 1 | Default: VBUS→+5V. Cut and bridge buck output if populated. |

---

## 9. OLED display

| Ref | Part | Interface | Qty | Source | Unit price | Notes |
|---|---|---|---|---|---|---|
| OLED1 | SSD1306 128×64 0.96" OLED module | I²C, 4-pin (VCC/GND/SDA/SCL) | 1 | AliExpress, Amazon, Adafruit | ~$2–4 | Connects via J3 (JST-XH 4-pin header). Addr 0x3C. |

### I²C pullups

| Ref | Part | Package | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| R_SDA | 4.7 kΩ 1% 0603 | 0603 | 1 | [C99782](https://www.lcsc.com/product-detail/C99782.html) | YAGEO. SDA to +3V3. 2,371,600 stock. |
| R_SCL | 4.7 kΩ 1% 0603 | 0603 | 1 | [C99782](https://www.lcsc.com/product-detail/C99782.html) | YAGEO. SCL to +3V3. |

---

## 10. microSD card socket

| Ref | Part | Type | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| J4 | SHOU HAN TF PUSH | Push-push microSD, SMD | 1 | [C393941](https://www.lcsc.com/product-detail/C393941.html) | ~$0.065 | Has Cd (card-detect) pin — wire to GPIO or leave unconnected (detect via SPI init). 201,663 stock. |

### SD power decoupling

| Ref | Part | Package | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| C_SD1 | 10 µF X7R 10 V | 0805 | 1 | [C86038](https://www.lcsc.com/product-detail/C86038.html) | Murata. SD VDD bulk. |
| C_SD2 | 100 nF X7R 50 V | 0603 | 1 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | SD VDD HF. |

---

## 11. Buttons

| Ref | Part | Type | Qty | LCSC # | Unit price | Notes |
|---|---|---|---|---|---|---|
| SW_A, SW_B, SW_C | XKB TS-1187A-B-A-B | SPST-NO, 5.1×5.1×1.5 mm, SMD | 3 | [C318884](https://www.lcsc.com/product-detail/C318884.html) | ~$0.016 | 1.6 N force, 100K cycles. 1,017,340 stock. |

### Per-button debounce (×3)

| Ref | Part | Package | Qty total | LCSC # | Notes |
|---|---|---|---|---|---|
| R_BTN_A–C | 10 kΩ 5% 0603 | 0603 | 3 | [C99198](https://www.lcsc.com/product-detail/C99198.html) | YAGEO. Pullup to +3V3. |
| C_BTN_A–C | 100 nF X7R 50 V | 0603 | 3 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | Hardware debounce (RC ≈ 1 ms). |

---

## 12. LEDs and indicators

### Gate drive indicators (same as V2, no GPIO needed)

| Ref | Part | Package | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| D_LED_P1–N2 | Lite-On LTST-C193TGKT-5A (green) | 0603 | 4 | [C12065](https://www.lcsc.com/product-detail/C12065.html) | 525 nm peak, 28–280 mcd (binned), Vf=2.50–3.10 V. 83,280 stock. |
| D_LED_PWR | Lite-On LTST-C193TGKT-5A (green) | 0603 | 1 | [C12065](https://www.lcsc.com/product-detail/C12065.html) | Power-on indicator. |
| R_LED_P1–N2, R_LED_PWR | 1 kΩ 1% 0603 | 0603 | 5 | [C22548](https://www.lcsc.com/product-detail/C22548.html) | YAGEO. LED current limit. 3,046,400 stock. |

### Status indicators (GPIO-driven)

| Ref | Part | Package | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| D_LED_REC | Lite-On LTST-C193TGKT-5A (green) | 0603 | 1 | [C12065](https://www.lcsc.com/product-detail/C12065.html) | GP22, recording active. |
| D_LED_AUTO | NATIONSTAR NCD0603Y5 (yellow) | 0603 | 1 | [C7429912](https://www.lcsc.com/product-detail/C7429912.html) | GP0, auto-follow engaged. 595 nm peak, 40–180 mcd, Vf=1.5–2.6 V. 26,360 stock. |
| R_LED_REC, R_LED_AUTO | 1 kΩ 1% 0603 | 0603 | 2 | [C22548](https://www.lcsc.com/product-detail/C22548.html) | YAGEO. Current limit. |
| NEOPIXEL | WS2812B-MINI-X2 | 3535 | 1 | [C4154873](https://www.lcsc.com/product-detail/C4154873.html) | GP1, RGB mode status. 37,690 stock. |

---

## 13. FET gate pulldowns

| Ref | Part | Package | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| Rgpd_1–4 | 10 kΩ 5% 0603 | 0603 | 4 | [C99198](https://www.lcsc.com/product-detail/C99198.html) | YAGEO. Gate-to-source pulldown per MOSFET. 2,549,100 stock. |

---

## 14. Connectors

| Ref | Part | Pitch / Type | Qty | LCSC # | Notes |
|---|---|---|---|---|---|
| J1 | Kelvin clip pads (PCB copper) | — | 4 pads | N/A | Cycler input: +A, +V, −A, −V. Each pad 22×8 mm, ENIG finish, board edge. Current pads (±A) use ≥1 mm traces. No BOM part. |
| J2a | DORABO DB128L-5.08-2P-GN-S | 5.08 mm screw | 1 | [C395868](https://www.lcsc.com/product-detail/C395868.html) | Cell positive tabs (CELL_A_POS, CELL_B_POS). 37,312 stock. |
| J2b | DORABO DB128L-5.08-2P-GN-S | 5.08 mm screw | 1 | [C395868](https://www.lcsc.com/product-detail/C395868.html) | Cell negative tabs (CELL_A_NEG, CELL_B_NEG). |
| J3 | JST B4B-XH-A(LF)(SN) | 2.5 mm, 4-pin vertical | 1 | [C144395](https://www.lcsc.com/product-detail/C144395.html) | OLED (VCC, GND, SDA, SCL). Genuine JST. 262,275 stock. |
| — | BOOMELE 2.54 mm 2×20P male header | 2.54 mm | 1 | [C50980](https://www.lcsc.com/product-detail/C50980.html) | Pico 2 socket (header side). 4,925 stock. |
| — | ZHOURI 2.54-2×20 female socket | 2.54 mm | 1 | [C2977589](https://www.lcsc.com/product-detail/C2977589.html) | Pico 2 socket (board side). 45,647 stock. |

---

## 15. Passive component summary

| Value | Package | Qty | LCSC # | Usage |
|---|---|---|---|---|
| 10 Ω 1% | 0603 | 4 | [C109318](https://www.lcsc.com/product-detail/C109318.html) | Gate series resistors (YAGEO, 3.8M stock) |
| 1 kΩ 1% | 0603 | 7 | [C22548](https://www.lcsc.com/product-detail/C22548.html) | LED current limit (YAGEO, 3M stock) |
| 4.7 kΩ 1% | 0603 | 2 | [C99782](https://www.lcsc.com/product-detail/C99782.html) | I²C pullups (YAGEO, 2.4M stock) |
| 9.1 kΩ 1% | 0603 | 4 | [C114639](https://www.lcsc.com/product-detail/C114639.html) | Voltage divider low-side (YAGEO, 37.9K stock) |
| 10 kΩ 5% | 0603 | 8 | [C99198](https://www.lcsc.com/product-detail/C99198.html) | Gate pulldowns (4), button pullups (3), R_RP (1) (YAGEO, 2.5M stock) |
| 91 kΩ 1% | 0603 | 4 | [C23265](https://www.lcsc.com/product-detail/C23265.html) | Voltage divider high-side (UNI-ROYAL, 29.1K stock, basic) |
| 100 nF X7R 50 V | 0603 | ~20 | [C14663](https://www.lcsc.com/product-detail/C14663.html) | Decoupling, anti-alias, debounce (YAGEO, 6.6M stock, basic) |
| 10 µF X7R 10 V | 0805 | ~6 | [C86038](https://www.lcsc.com/product-detail/C86038.html) | Bulk decoupling — 3V3, 5V, SD rails (Murata, 153K stock) |
| 10 µF X5R 50 V | 1206 | 1 | [C100122](https://www.lcsc.com/product-detail/C100122.html) | Bulk decoupling — +HV rail (YAGEO, 251K stock) |
| 4.7 µF X5R 25 V | 0805 | 3 | [C1779](https://www.lcsc.com/product-detail/C1779.html) | B0512S input bulk ×3 converters (Samsung, 2.5M stock, basic). Datasheet recommended Cin. |
| 2.2 µF X7R 25 V | 0805 | 3 | [C19110](https://www.lcsc.com/product-detail/C19110.html) | B0512S output bulk ×3 converters (Samsung, 872K stock). Datasheet recommended Cout for 12 V output. |
| 1.5 kΩ 1% | 0603 | 3 | [C22843](https://www.lcsc.com/product-detail/C22843.html) | B0512S bleeder resistors, minimum load (UNI-ROYAL, 650,600 stock) |

---

## 16. Ordering checklist

### JLCPCB-assembled (SMD)

- [ ] ADS131M04IRUKR × 2 — [C5121509](https://www.lcsc.com/product-detail/C5121509.html) (3,520 stock, extended)
- [ ] AO3400A × 4 — JLCPCB basic (same as V2)
- [ ] RLP25FEGR010 × 4 — [C393070](https://www.lcsc.com/product-detail/C393070.html) (77K stock)
- [ ] BAV99,215 × 4 — [C2500](https://www.lcsc.com/product-detail/C2500.html) (1.6M stock, basic)
- [ ] All 0603/0805/1206 passives (see §15 for full LCSC # list)
- [ ] microSD socket × 1 — [C393941](https://www.lcsc.com/product-detail/C393941.html) (201K stock)
- [ ] Tact switches × 3 — [C318884](https://www.lcsc.com/product-detail/C318884.html) (1M stock)
- [ ] Green LEDs × 6 — [C12065](https://www.lcsc.com/product-detail/C12065.html) (83K stock)
- [ ] Yellow LED × 1 — [C7429912](https://www.lcsc.com/product-detail/C7429912.html) (26K stock)
- [ ] WS2812B-MINI-X2 × 1 — [C4154873](https://www.lcsc.com/product-detail/C4154873.html) (37K stock)
- [ ] Ferrite bead × 1 — [C304319](https://www.lcsc.com/product-detail/C304319.html) (28K stock)
- [ ] Screw terminals 2P × 2 — [C395868](https://www.lcsc.com/product-detail/C395868.html) (37K stock)
- [ ] JST-XH 4-pin × 1 — [C144395](https://www.lcsc.com/product-detail/C144395.html) (262K stock)
- [ ] Pin sockets 2×20 × 1 — [C2977589](https://www.lcsc.com/product-detail/C2977589.html) (45K stock)

### Hand-solder after JLCPCB

- [ ] UCC5304DWVR × 4 (existing stock)
- [ ] B0512S-1WR3 × 2 (existing stock)

### Separate purchase

- [ ] Raspberry Pi Pico 2 × 1 (+ spare)
- [ ] SSD1306 OLED 128×64 module × 1 (+ spare)
- [ ] microSD card (32 GB, Class 10 or better)
- [ ] Pin headers 2×20 × 1 — [C50980](https://www.lcsc.com/product-detail/C50980.html) (4.9K stock)

---

## 17. Design changes from V2 BOM review (2026-05-23)

1. **Max current → 3 A** (was 5 A). AO3400A SOT-23 retained from V2
   (rated 5.7 A, 0.24 W worst-case at 3 A — within SOT-23 limits).

2. **ADC → 2× ADS131M04** (was 1× ADS131M08). Same M-series register
   map, 3,520 LCSC stock vs 163 for M08. Two extra GPIO needed (CS2,
   DRDY2). RP2350 has plenty.

3. **Voltage divider → 91 kΩ / 9.1 kΩ** (was 90 kΩ / 10 kΩ). Both
   values in stock; ratio is 10.01:1, calibrated in firmware. 10 kΩ 1%
   was out of stock across all brands at LCSC.

4. **Cycler input → Kelvin clip pads** (was 2-pos screw terminal).
   4 exposed copper pads on PCB edge (+A, +V, −A, −V) for alligator
   clip connection. 4-wire sense for accurate voltage measurement.

5. **Cell output → 2× 2-pos screw terminals** (was 1× 4-pos). Positive
   tabs (J2a) and negative tabs (J2b) on separate connectors.

6. **+HV bulk cap → 1206** (was 0805). 10 µF 0805 25 V+ out of stock
   across LCSC. YAGEO 1206 50 V has 251K stock.

---

## 18. Remaining open questions

1. **5 V rail budget** — calculate total draw from LS drivers + B0512S
   converters on the V2 bench before committing. If over 500 mA USB
   budget, add a separate 5 V buck from +HV.

2. **ADS131M04 dual-chip pin assignment** — allocate 2 additional GPIO
   on RP2350 for second CS and DRDY. Update V3_SPEC.md pin table.

3. **Clip pad design** — determine pad dimensions, edge placement, and
   ENIG vs HASL finish for Kelvin input pads. ENIG preferred for
   reliable alligator clip contact.

4. ~~**HS driver VDD decoupling**~~ — **Resolved:** all four UCC5304
   VDD-VSS bulk caps now use 10 µF 1206 X5R 50 V (C100122). See §6.
