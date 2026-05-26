# PCB V2 — Firmware & Documentation Changelist

Reviewed 2026-04-28. PCB design is approved for ordering as-is.
These changes apply **after PCBs arrive** and the breadboard is retired.

## Status

- [x] PCBs received (2026-05-10)
- [x] Firmware changes applied (N1 addr, ALERT pin)
- [ ] B0512S footprint fix verified on next PCB revision
- [ ] All remaining changes below applied
- [ ] Firmware deployed to V2 board
- [ ] INA226 sensors verified at correct addresses (0x40, 0x41, 0x49, 0x45)
- [ ] ALERT interrupt verified on GP29

---

## 1. INA226 N1 address: 0x43 → 0x49

The V2 PCB hardwires U_INA_N1 address pins as A1=SDA, A0=VS, which produces
I2C address 0x49 (not 0x43 used on the breadboard where A1=GND, A0=SCL).

| File | Line | Old | New |
|------|------|-----|-----|
| `firmware/main.py` | 115 | `"N1": 0x43,` | `"N1": 0x49,` |
| `firmware-c/src/config.h` | 32 | `#define INA226_ADDR_N1 0x43u` | `#define INA226_ADDR_N1 0x49u` |
| `pcb/SCHEMATIC_SPEC.md` | 100 | `P1=0x40, P2=0x41, N1=0x43, N2=0x45` | `P1=0x40, P2=0x41, N1=0x49, N2=0x45` |
| `pcb/SCHEMATIC_SPEC.md` | 106 | `U_INA_N1 \| 0x43 \| SDA \| VS` | `U_INA_N1 \| 0x49 \| SDA \| VS` |

## 2. ALERT pin: GP27 → GP29

The V2 PCB routes the INA226 shared ALERT line to GP29.
The breadboard uses GP27.

| File | Line | Old | New |
|------|------|-----|-----|
| `firmware/main.py` | 107 | `PIN_ALERT = 27` | `PIN_ALERT = 29` |
| `firmware-c/src/config.h` | 21 | `#define PIN_INA_ALERT 27u` | `#define PIN_INA_ALERT 29u` |

## 3. J2 connector comment (gen_schematic.py)

Line 374 comment says `1=CELL_A_POS, 2=CELL_A_NEG, 3=CELL_B_POS, 4=CELL_B_NEG`.
Actual code (and intended wiring) is:

| J2 Pin | Net | Label |
|--------|-----|-------|
| 1 | CELL_B_NEG | N2 |
| 2 | CELL_A_NEG | N1 |
| 3 | CELL_B_POS | P2 |
| 4 | CELL_A_POS | P1 |

Update the comment on line 374 to match.

## 4. SCHEMATIC_SPEC.md topology diagram — J2 pin order

The ASCII diagram shows `J2 pin 1 = CELL_A_POS` etc. Update to match the
actual pin ordering in item 3 above.

## 5. SCHEMATIC_SPEC.md LED table — polarity description

The table says: Anode = `+3V3`, Cathode → 1k → `GATE_xx_IN` (LED on when gate LOW).

Actual circuit (gen_schematic.py): Anode → `GATE_xx_IN`, Cathode → 1k → `GND`
(LED on when gate HIGH — correct behavior).

Update the table and the sentence below it to match.

## 6. B0512S-1WR3 footprint pin swap (CRITICAL — PCB trace error)

The actual parts are GDHUIZHT brand (not genuine Mornsun). The GDHUIZHT pinout
differs from the Mornsun datasheet — both input and output pairs are swapped:

| Pin | Mornsun (old assumption) | GDHUIZHT (actual) |
|-----|--------------------------|-------------------|
| 1   | Vin+                     | **Vin-**          |
| 2   | Vin-                     | **Vin+**          |
| 3   | Vout+                    | **Vout-**         |
| 4   | Vout-                    | **Vout+**         |

The V2 PCB traces route pin 1→+5V and pin 2→GND, which reverse-powers the
converter and causes USB overcurrent / voltage collapse on the 5V rail.

**V2 board workaround:** cross-wire adjacent pins (1↔2, 3↔4) when installing.

**Fix applied to `gen_schematic.py`** — pin_nets for PS1 and PS2 updated to
match GDHUIZHT pinout. Next PCB revision will have correct traces.

Datasheet images saved to `pcb/specs/GDHUIZHT_B0512S_1WR3_*.png`.
