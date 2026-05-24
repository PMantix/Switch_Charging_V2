# PCB V3 — Design Changes from V2 Bring-Up

Running list of issues found during V2 board bring-up (2026-05-12).
All items here should be addressed before the next PCB order.

## Status

- [ ] All changes below reviewed
- [ ] Schematic / gen_schematic.py updated
- [ ] KiCad footprints updated
- [ ] DRC / ERC clean
- [ ] New board ordered

---

## 1. B0512S-1WR3 footprint pin swap (CRITICAL)

The GDHUIZHT-brand B0512S-1WR3 modules have a different pinout from the
Mornsun datasheet that the V2 footprint was based on:

| Pin | Mornsun (V2 assumption) | GDHUIZHT (actual) |
|-----|-------------------------|-------------------|
| 1   | Vin+                    | **Vin-**          |
| 2   | Vin-                    | **Vin+**          |
| 3   | Vout+                   | **Vout-**         |
| 4   | Vout-                   | **Vout+**         |

V2 PCB routes pin 1→+5V and pin 2→GND, which reverse-powers the converter
and causes instant overcurrent / voltage collapse on the 5V rail.

**V2 workaround:** cross-wire adjacent pins (1↔2, 3↔4) when installing.

**V3 fix:** If continuing with GDHUIZHT parts, update the footprint pin
mapping to match. If switching to genuine Mornsun parts, keep the V2
footprint. **Add a silkscreen note on the PCB indicating which brand the
footprint matches**, so the correct part is installed.

`gen_schematic.py` pin_nets for PS1/PS2 already updated for GDHUIZHT.
Datasheet images: `pcb/specs/GDHUIZHT_B0512S_1WR3_*.png`

## 2. Low-side UCC5304 VDD needs 12V, not 5V

V2 PCB routes U3/U4 (low-side drivers) pin 8 (VDD) to `+5V`. The UCC5304
has a 5V UVLO threshold — running at exactly 5V is unreliable (breadboard
testing confirmed this, 2026-04-29).

**V3 fix:** Add a **third B0512S-1WR3** (PS3) to supply a shared,
ground-referenced 12V rail for the low-side drivers. Wire:
- PS3 Vin+/Vin- → +5V / GND
- PS3 Vout+/Vout- → new net `VCC2_LS` / `GND`
- U3 pin 8 → `VCC2_LS` (instead of `+5V`)
- U4 pin 8 → `VCC2_LS` (instead of `+5V`)
- Add 4.7µF input cap and 2.2µF output cap for PS3

This matches the breadboard architecture that was validated working.

**Decoupling (CRITICAL):** The hand-wired V2 bodge (B0512D-2WR3 for LS 12V)
failed after continuous switching over a weekend (2026-05-19, SW1 board).
5V input present, 0V output — DC-DC died from gate charge transient stress
without adequate decoupling. V3 MUST include:
- PS3 input: 4.7µF + 100nF ceramic close to Vin pins (per Mornsun datasheet Table 1)
- PS3 output: 2.2µF + 100nF ceramic close to Vout pins (per datasheet Table 1)
- Additional 100nF ceramic at each UCC5304 pin 8 (VDD)
- **Resolved (2026-05-23):** The UCC5304 VDD bulk caps (2×10µF on PS3)
  provide ~22.5µF total on the PS3 output, satisfying the original intent
  for bulk capacitance. No separate electrolytic needed.

Same decoupling standard should apply to PS1/PS2 (high-side supplies).

## 3. Connector silkscreen — polarity and side labels

V2 PCB lacks clear silkscreen markings on J1 and J2 screw terminals.
During assembly it's ambiguous which terminal is +/- and which side is
cycler vs. cell.

**V3 fix:** Add silkscreen text adjacent to each terminal:

J1 (cycler input, 2-pos):
- Pin 1: `CYCLER +`
- Pin 2: `CYCLER -`

J2 (cell output, 4-pos):
- Pin 1: `CELL B-` (N2)
- Pin 2: `CELL A-` (N1)
- Pin 3: `CELL B+` (P2)
- Pin 4: `CELL A+` (P1)

Also add a board-level label: `CYCLER SIDE` near J1 and `CELL SIDE` near J2.

## 4. Current sensor upgrade

The INA226 (TI) works but has limitations for this application:
- 10-bit shunt ADC (2.5µV LSB) limits low-current resolution
- Maximum sample rate ~1 kHz per channel with 4 sensors on one bus
- External shunt resistor adds board area and power dissipation

**V3 consideration:** Evaluate alternative current sensors for higher
resolution and/or faster sampling. Candidates to investigate:
- **INA228** — 20-bit delta-sigma ADC, same I2C interface, drop-in upgrade
- **INA229** — SPI version of INA228, faster readout with dedicated bus
- **ACS723** — Hall-effect, galvanically isolated, analog output (needs ADC)
- **INA4226** — Quad INA226 in single package (saves board space, same resolution)

Document selection criteria and chosen part here before V3 layout.

## 5. UCC5304 footprint too narrow — pads too close together

The custom SOIC-8 DWV footprint (`SOIC-8_DWV_7.5x11.5mm_P1.27mm`) has pads
that are too close together, making hand soldering difficult and increasing
the risk of solder bridges.

**V3 fix:** Widen the footprint pad span by 1–1.5mm. The DWV package body is
7.5mm wide; the current pad-to-pad span should be increased to give more
clearance. Check the TI recommended land pattern in the UCC5304 datasheet
(SLUSDV5B) and use that as the baseline, then add extra margin for hand
soldering if needed.
