# PCB V3 — Component Placement Guide

Functional-block placement map for KiCad layout. Check off each group
as you finish placing and routing it.

---

## Block 1: Power Input & Protection

Place clustered at the cycler-side board edge near J1.

- [ ] **J1** — Kelvin clip pads (4 pads), board edge
- [ ] **F1** — 3A polyfuse (1812), inline between J1 and Q_RP
- [ ] **Q_RP** — AO3401A reverse-polarity FET, between F1 and +HV rail
- [ ] **R_RP** — 10k gate pulldown (0603), tight to Q_RP gate-source
- [ ] **D_TVS** — SMBJ12CA TVS (SMB), close to +HV/GND after Q_RP
- [ ] **C_BULK1** — 10uF 50V (1206), close to D_TVS on +HV/GND
- [ ] **C_BULK2** — 100nF 50V (0603), adjacent to C_BULK1
- [ ] **R_CYC_GND** — 0R (0805), at J1 only (CYCLER_IN- to GND tie)

---

## Block 2: Power Stage — 4 FET Channels

Organize as two mirrored halves (Cell A left, Cell B right), between
J1 and J2.

### Cell A path (P1 high-side + N1 low-side)

- [ ] **R_SH_P1** — 0.01R 3W shunt (2512), on +HV rail feeding Q1 drain
- [ ] **Q1** — AO3400A (SOT-23), between R_SH_P1 and J2a pin 1
- [ ] **R_G_U1** — 10R gate resistor (0603), between U1 pin 7 and Q1 gate
- [ ] **Rgpd_1** — 10k gate pulldown (0603), close to Q1 gate-source
- [ ] **Q3** — AO3400A (SOT-23), between J2b pin 1 and R_SH_N1
- [ ] **R_SH_N1** — 0.01R 3W shunt (2512), between Q3 source and GND
- [ ] **R_G_U3** — 10R gate resistor (0603), between U3 pin 7 and Q3 gate
- [ ] **Rgpd_3** — 10k gate pulldown (0603), close to Q3 gate-source

### Cell B path (P2 high-side + N2 low-side)

- [ ] **R_SH_P2** — 0.01R 3W shunt (2512), on +HV rail feeding Q2 drain
- [ ] **Q2** — AO3400A (SOT-23), between R_SH_P2 and J2a pin 2
- [ ] **R_G_U2** — 10R gate resistor (0603), between U2 pin 7 and Q2 gate
- [ ] **Rgpd_2** — 10k gate pulldown (0603), close to Q2 gate-source
- [ ] **Q4** — AO3400A (SOT-23), between J2b pin 2 and R_SH_N2
- [ ] **R_SH_N2** — 0.01R 3W shunt (2512), between Q4 source and GND
- [ ] **R_G_U4** — 10R gate resistor (0603), between U4 pin 7 and Q4 gate
- [ ] **Rgpd_4** — 10k gate pulldown (0603), close to Q4 gate-source

---

## Block 3: Gate Drivers + Isolated Supplies

Each UCC5304 sits close to its FET. Its B0512S supply sits adjacent.

### U1 cluster (drives Q1, high-side P1)

- [ ] **U1** — UCC5304 (SOIC-8 DWV), near Q1
- [ ] **PS1** — B0512S-1WR3 (SIP-4), adjacent to U1
- [ ] **C_VCCI_U1** — 100nF (0603), U1 pins 2/3 to pin 4
- [ ] **C_VDD_U1_1** — 10uF 50V (1206), U1 pin 8 to pins 5/6
- [ ] **C_VDD_U1_2** — 100nF (0603), adjacent to C_VDD_U1_1
- [ ] **C_PS1_IN1** — 4.7uF (0805), PS1 input (+5V/GND)
- [ ] **C_PS1_IN2** — 100nF (0603), PS1 input (+5V/GND)
- [ ] **C_PS1_OUT1** — 2.2uF (0805), PS1 output (VCC2_P1/CELL_A_POS)
- [ ] **C_PS1_OUT2** — 100nF (0603), PS1 output (VCC2_P1/CELL_A_POS)
- [ ] **R_BLEED_PS1** — 1.2k (0805), across PS1 output

### U2 cluster (drives Q2, high-side P2)

- [ ] **U2** — UCC5304 (SOIC-8 DWV), near Q2
- [ ] **PS2** — B0512S-1WR3 (SIP-4), adjacent to U2
- [ ] **C_VCCI_U2** — 100nF (0603), U2 pins 2/3 to pin 4
- [ ] **C_VDD_U2_1** — 10uF 50V (1206), U2 pin 8 to pins 5/6
- [ ] **C_VDD_U2_2** — 100nF (0603), adjacent to C_VDD_U2_1
- [ ] **C_PS2_IN1** — 4.7uF (0805), PS2 input (+5V/GND)
- [ ] **C_PS2_IN2** — 100nF (0603), PS2 input (+5V/GND)
- [ ] **C_PS2_OUT1** — 2.2uF (0805), PS2 output (VCC2_P2/CELL_B_POS)
- [ ] **C_PS2_OUT2** — 100nF (0603), PS2 output (VCC2_P2/CELL_B_POS)
- [ ] **R_BLEED_PS2** — 1.2k (0805), across PS2 output

### U3/U4 cluster (low-side, shared PS3)

> **V2 lesson:** PS3 decoupling is critical — the V2 bodge DC-DC died
> from inadequate decoupling during continuous switching. Keep all PS3
> caps within 5mm of pins.

- [ ] **U3** — UCC5304 (SOIC-8 DWV), near Q3
- [ ] **U4** — UCC5304 (SOIC-8 DWV), near Q4
- [ ] **PS3** — B0512S-1WR3 (SIP-4), between U3 and U4
- [ ] **C_VCCI_U3** — 100nF (0603), U3 pins 2/3 to pin 4
- [ ] **C_VCCI_U4** — 100nF (0603), U4 pins 2/3 to pin 4
- [ ] **C_VDD_U3_1** — 10uF 50V (1206), U3 VDD to GND
- [ ] **C_VDD_U3_2** — 100nF (0603), adjacent to C_VDD_U3_1
- [ ] **C_VDD_U4_1** — 10uF 50V (1206), U4 VDD to GND
- [ ] **C_VDD_U4_2** — 100nF (0603), adjacent to C_VDD_U4_1
- [ ] **C_PS3_IN1** — 4.7uF (0805), PS3 input (+5V/GND)
- [ ] **C_PS3_IN2** — 100nF (0603), PS3 input (+5V/GND)
- [ ] **C_PS3_OUT1** — 2.2uF (0805), PS3 output (VCC2_LS/GND)
- [ ] **C_PS3_OUT2** — 100nF (0603), PS3 output (VCC2_LS/GND)
- [ ] **R_BLEED_PS3** — 1.2k (0805), across PS3 output

---

## Block 4: Current Sense Amplifiers

Place between the high-side shunts and U_ADC1.

- [ ] **U_CSA_P1** — INA180A1 (SOT-23-5), between R_SH_P1 and U_ADC1
- [ ] **C_CSA_P1** — 100nF (0603), tight to U_CSA_P1 VS pin
- [ ] **U_CSA_P2** — INA180A1 (SOT-23-5), between R_SH_P2 and U_ADC1
- [ ] **C_CSA_P2** — 100nF (0603), tight to U_CSA_P2 VS pin

---

## Block 5: ADCs + Analog Front-End

Place both ADS131M04s in a dedicated analog zone, away from switching
noise. Route AGND as a local plane tied to GND at one star point.

### U_ADC1 (shunt current sensing)

- [ ] **U_ADC1** — ADS131M04 (WQFN-20), analog zone, near INA180 outputs
- [ ] **C_AVDD2_U1** — 100nF AVDD decoupling, tight to pin 19
- [ ] **C_DVDD_U1** — 1uF DVDD decoupling, tight to pin 18
- [ ] **C_CAP_U1** — 220nF CAP pin, tight to pin 16

### U_ADC2 (bus voltage sensing)

- [ ] **U_ADC2** — ADS131M04 (WQFN-20), adjacent to U_ADC1
- [ ] **C_AVDD2_U2** — 100nF AVDD decoupling, tight to pin 19
- [ ] **C_DVDD_U2** — 1uF DVDD decoupling, tight to pin 18
- [ ] **C_CAP_U2** — 220nF CAP pin, tight to pin 16

### Shared analog supply

- [ ] **L_AVDD** — Ferrite bead (0603), between +3V3 and +3V3_A
- [ ] **C_AVDD1** — 10uF bulk (0805), after L_AVDD on +3V3_A/AGND

### Voltage dividers + clamps (4 sets, row adjacent to U_ADC2 inputs)

- [ ] **Set 1 (VDIV_HV):** R_DIV_H1 (91k), R_DIV_L1 (9.1k), C_AA1 (100nF), D_CLAMP1 (BAV99)
- [ ] **Set 2 (VDIV_HV_P1):** R_DIV_H2, R_DIV_L2, C_AA2, D_CLAMP2
- [ ] **Set 3 (VDIV_HV_P2):** R_DIV_H3, R_DIV_L3, C_AA3, D_CLAMP3
- [ ] **Set 4 (VDIV_GND_N1):** R_DIV_H4, R_DIV_L4, C_AA4, D_CLAMP4

---

## Block 6: MCU — Pico 2

- [ ] **Pico 2** — 2x20 TH headers, central digital zone
- [ ] **C_5V1** — 10uF (0805), near Pico VBUS (pin 40)
- [ ] **C_5V2** — 100nF (0603), adjacent to C_5V1

Routing from Pico:
- SPI1 (pins 14-17) toward analog zone (ADCs)
- SPI0 (pins 21-25) toward J4 (microSD)
- I2C (pins 9-10) toward J3 (OLED)
- Gate signals (pins 4-7) toward UCC5304 inputs

---

## Block 7: Peripherals

### Buttons (near Pico, user-accessible edge)

- [ ] **SW_A** + R_BTN_A (10k) + C_BTN_A (100nF)
- [ ] **SW_B** + R_BTN_B (10k) + C_BTN_B (100nF)
- [ ] **SW_C** + R_BTN_C (10k) + C_BTN_C (100nF)

### OLED connector

- [ ] **J3** — JST 4-pin, near Pico I2C pins (GP6/GP7)
- [ ] **R_SDA** — 4.7k pullup (0603), between J3 and Pico
- [ ] **R_SCL** — 4.7k pullup (0603), between J3 and Pico

### microSD

- [ ] **J4** — TF-PUSH socket, near Pico SPI0 pins
- [ ] **C_SD1** — 10uF (0805), tight to J4 VDD pin
- [ ] **C_SD2** — 100nF (0603), tight to J4 VDD pin

### Cell terminals

- [ ] **J2a** — Screw terminal (cell positive tabs), cell-side board edge
- [ ] **J2b** — Screw terminal (cell negative tabs), cell-side board edge
- [ ] Silkscreen: `CELL A+`, `CELL B+` on J2a; `CELL A-`, `CELL B-` on J2b

---

## Block 8: LEDs & NeoPixel

### Gate drive indicator LEDs (near their UCC5304 input pins)

- [ ] **D_LED_P1** + R_LED_P1 — near U1
- [ ] **D_LED_P2** + R_LED_P2 — near U2
- [ ] **D_LED_N1** + R_LED_N1 — near U3
- [ ] **D_LED_N2** + R_LED_N2 — near U4

### Status LEDs

- [ ] **D_LED_PWR** + R_LED_PWR — near power input, visible edge
- [ ] **D_LED_REC** + R_LED_REC — near Pico GP22
- [ ] **D_LED_AUTO** + R_LED_AUTO — near Pico GP0

### NeoPixel

- [ ] **D_NEO** — 1N4148W level-shift diode, near +5V rail
- [ ] **LED_NEO** — WS2812B-MINI (3535), near Pico GP1 and D_NEO

---

## Floor Plan

```
 CYCLER SIDE (J1 clip pads)
 +----------------------------------------------+
 |  [Protection: F1, Q_RP, D_TVS, C_BULK]       |
 |                                               |
 |   +- Cell A --+       +- Cell B --+           |
 |   | PS1 -> U1 |       | PS2 -> U2 |           |
 |   | R_SH_P1   |       | R_SH_P2   |           |
 |   | Q1 <-CSA1 |       | Q2 <-CSA2 |           |
 |   |     |     |       |     |     |           |
 |   | Q3        |       | Q4        |           |
 |   | R_SH_N1   |       | R_SH_N2   |           |
 |   | U3 <- PS3 -> --- -> U4        |           |
 |   +-----------+       +-----------+           |
 |                                               |
 |  +-- Analog Zone -------------------------+   |
 |  | U_ADC1   U_ADC2   L_AVDD              |   |
 |  | Dividers + clamps (x4)                |   |
 |  | INA180 x2                              |   |
 |  +----------------------------------------+   |
 |                                               |
 |  +-- Pico 2 ------+  [J4 SD]   [J3 OLED]    |
 |  |  (2x20 TH)     |                          |
 |  +-----------------+  [SW_A] [SW_B] [SW_C]   |
 |                                               |
 |  [LEDs: PWR, REC, AUTO, NEO] [Gate LEDs x4]  |
 +----------------------------------------------+
 CELL SIDE (J2a / J2b screw terminals)
```

---

## Layout Principles

- [ ] Short, wide traces for +HV -> shunt -> FET -> cell tab (high current paths)
- [ ] AGND local plane under ADCs, single star-point tie to GND
- [ ] All B0512S decoupling caps within 5mm of converter pins
- [ ] Gate driver decoupling caps directly at UCC5304 VDD/VCCI pins
- [ ] Analog signals (CSA outputs, divider outputs) routed away from switching nodes
- [ ] Silkscreen: `MORNSUN PINOUT` near each B0512S
- [ ] Silkscreen: `CYCLER SIDE` near J1, `CELL SIDE` near J2
