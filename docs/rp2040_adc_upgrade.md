# RP2040 + INA226 Upgrade: BOM & Circuit Design

## Overview

Modify the existing H-bridge controller to:
1. Replace Pi GPIO → UCC5304 gate drive with RP2040-Zero → UCC5304 gate drive
2. Add per-path voltage and current sensing via INA226 I2C power monitors
3. Pi becomes orchestrator only, communicating with RP2040 over USB serial
4. Physical fallback interface: rotary dials + 7-segment display on RP2040

---

## Bill of Materials (Ordered Components)

### Microcontroller

| Ref | Component | Qty | Description |
|-----|-----------|-----|-------------|
| U5 | hiBCTR RP2040-Zero | 6 | RP2040-based board, compact form factor. 1 primary + 5 spares. |

> The RP2040-Zero has the same RP2040 chip as the Pico but in a smaller
> package. Fewer exposed pins than the Pico, but sufficient for this
> design: 4 gate drive GPIOs, I2C bus (2 pins), USB, and physical
> interface I/O.

### Power/Current/Voltage Sensing

| Ref | Component | Qty | Description |
|-----|-----------|-----|-------------|
| U6-U11 | D-FLIFE INA226 breakout | 6 | 16-bit I2C power monitor. Built-in shunt. Measures bus voltage (0-36V) and shunt current simultaneously. 4 active + 2 spare. |

> The INA226 replaces the entire analog front end from the original plan
> (ADS131M08/ADS1256 ADC + MCP6004 op-amp + shunt resistors + voltage
> dividers). Each module measures both bus voltage and current through
> its built-in shunt — plug and wire.

### Passive Components

| Component | Qty | Description |
|-----------|-----|-------------|
| Monolithic ceramic capacitor set | 480 pcs | Assorted values for decoupling and filtering |

### Physical Interface (Fallback)

| Component | Qty | Description |
|-----------|-----|-------------|
| Rotary dials | TBD | Parameter adjustment (duty cycle, frequency, mode select) |
| 7-segment display | 1 | Status/value readout — operates independently of TUI |

### Existing Components (Retained)

| Ref | Component | Qty | Notes |
|-----|-----------|-----|-------|
| U1-U4 | UCC5304 gate driver | 4 | Now driven by RP2040-Zero GPIO instead of Pi GPIO |
| Q1-Q4 | N-channel MOSFETs | 4 | P1, P2 (high-side), N1, N2 (low-side) |
| - | Raspberry Pi | 1 | Orchestrator only — no longer drives MOSFETs |

---

## INA226 Sense Point Topology

Each INA226 module sits in-line with a current path and measures:
- **Bus voltage**: voltage at the load-side terminal (0-36V, 16-bit, 1.25mV LSB)
- **Shunt voltage**: voltage across the built-in shunt (±81.92mV, 16-bit, 2.5µV LSB)

4 INA226 modules are placed on the 4 MOSFET paths. 2 spares available
for additional measurement points (e.g., battery terminals, VCC rail).

```
                    VCC Rail
                      │
          ┌───────────┼───────────┐
          │                       │
       [INA226_P1]            [INA226_P2]
       (U6) I2C addr 0x40     (U7) I2C addr 0x41
          │                       │
       ┌──┴──┐                ┌──┴──┐
       │ P1  │  UCC5304       │ P2  │  UCC5304
       │MOSFET│  (U1)         │MOSFET│  (U2)
       └──┬──┘                └──┬──┘
          │                       │
     Node A (midpoint)       Node B (midpoint)
          │                       │
     ┌────┴────────┐        ┌────┴────────┐
     │  +A    -A   │        │  +B    -B   │
     │             │        │             │
     │  POUCH CELL LOAD (4 terminals)     │
     │             │        │             │
     └────┬────────┘        └────┬────────┘
          │                       │
       ┌──┴──┐                ┌──┴──┐
       │ N1  │  UCC5304       │ N2  │  UCC5304
       │MOSFET│  (U3)         │MOSFET│  (U4)
       └──┬──┘                └──┬──┘
          │                       │
       [INA226_N1]            [INA226_N2]
       (U8) I2C addr 0x44     (U9) I2C addr 0x45
          │                       │
          └───────────┼───────────┘
                      │
                    GND Rail

    Spare INA226 modules (U10, U11) available for:
    - Battery terminal voltage/current
    - VCC rail monitoring
    - Any additional sense point needed
```

### INA226 I2C Address Assignments

Each INA226 breakout has A0/A1 address pins. With 4 active modules:

| Module | Position | A1 | A0 | I2C Address | Measurements |
|--------|----------|----|----|-------------|--------------|
| U6 | P1 (high-side left) | GND | GND | 0x40 | V/I through P1 path |
| U7 | P2 (high-side right) | GND | VS | 0x41 | V/I through P2 path |
| U8 | N1 (low-side left) | VS | GND | 0x44 | V/I through N1 path |
| U9 | N2 (low-side right) | VS | VS | 0x45 | V/I through N2 path |
| U10 | Spare 1 | SDA | GND | 0x48 | TBD |
| U11 | Spare 2 | SDA | VS | 0x49 | TBD |

### Reconstructing Path Measurements

With INA226 on each MOSFET path:
- **A→A (State 0):** V = U6.Vbus, I = U6.Ishunt (or cross-check with U8)
- **A→B (State 1):** V = U6.Vbus − U9.Vbus, I = U6.Ishunt (or U9)
- **B→A (State 2):** V = U7.Vbus − U8.Vbus, I = U7.Ishunt (or U8)
- **B→B (State 3):** V = U7.Vbus, I = U7.Ishunt (or cross-check with U9)

---

## RP2040-Zero Pin Assignments

```
                          USB to Raspberry Pi
                                 │
                      ┌──────────┴──────────┐
                      │   RP2040-Zero       │
                      │                     │
          Gate Drive  │  GP2 ─────► UCC5304 (U1) ──► P1 Gate
          Outputs     │  GP3 ─────► UCC5304 (U2) ──► P2 Gate
                      │  GP4 ─────► UCC5304 (U3) ──► N1 Gate
                      │  GP5 ─────► UCC5304 (U4) ──► N2 Gate
                      │                     │
          I2C Bus     │  GP6 (SDA) ◄──────► INA226 × 4 (shared bus)
          (INA226s)   │  GP7 (SCL) ────────► INA226 × 4 (shared bus)
                      │                     │
          Built-in    │  GP26 (ADC0) ◄───── V sense: Node A divider
          ADC         │  GP27 (ADC1) ◄───── V sense: Node B divider
          (transient  │  GP28 (ADC2) ◄───── I sense: MCP6004 or direct
          capture)    │                     │
          Physical    │  GP8  ◄──── Rotary dial A (CLK)
          Interface   │  GP9  ◄──── Rotary dial A (DT)
                      │  GP10 ◄──── Rotary dial A (SW)
                      │  GP11 ◄──── Rotary dial B (CLK) [if needed]
                      │  GP12 ◄──── Rotary dial B (DT)  [if needed]
                      │  GP13 ◄──── Rotary dial B (SW)  [if needed]
                      │                     │
          7-Seg       │  GP14 ────► 7-seg CLK (TM1637 or similar)
          Display     │  GP15 ────► 7-seg DIO
                      │                     │
          Power       │  3V3 OUT ──► INA226 VCC (all modules)
                      │  GND ──────► INA226 GND (all modules)
                      │                     │
                      └─────────────────────┘
```

> Pin assignments are tentative — adjust based on RP2040-Zero pinout
> and physical layout. The key constraint is that all INA226 modules
> share a single I2C bus (2 pins), freeing GPIOs for the physical
> interface.
>
> The 3 built-in ADC pins (GP26-28) are reserved for high-speed
> transient capture. These are independent of the I2C bus and can
> sample at up to 500 kSPS using DMA.

---

## INA226 Wiring Detail

All 4 (or 6) INA226 modules share the same I2C bus. Each module is
differentiated by its I2C address (set via A0/A1 pins on the breakout).

```
    RP2040-Zero                    INA226 Breakout (×4-6)
    ┌───────────┐                  ┌──────────────────┐
    │           │                  │                  │
    │  GP6 (SDA)├──────┬──────────┤ SDA              │
    │           │      │          │                  │
    │  GP7 (SCL)├──────┼─┬────────┤ SCL              │
    │           │      │ │        │                  │
    │  3V3 OUT  ├──────┼─┼─┬──────┤ VCC              │
    │           │      │ │ │      │                  │
    │  GND      ├──────┼─┼─┼─┬────┤ GND              │
    │           │      │ │ │ │    │                  │
    └───────────┘      │ │ │ │    │  VIN+ ◄── from source (drain/VCC)
                       │ │ │ │    │  VIN- ──► to load (MOSFET/GND)
                       │ │ │ │    │                  │
                       │ │ │ │    │  A0, A1 ── set per module
                       │ │ │ │    │  ALERT (optional) │
                       │ │ │ │    └──────────────────┘
                       │ │ │ │
                       ├─┼─┼─┼──── (daisy-chain to next INA226)
                       │ │ │ │
                  4.7kΩ pullups on SDA and SCL (one pair, not per module)
```

Each INA226 module's VIN+/VIN- terminals go in series with the
current path being measured. The built-in shunt resistor sits
between VIN+ and VIN-. Bus voltage is measured at VIN-.

---

## Full System Block Diagram

```
    ┌─────────────────┐       USB Serial         ┌─────────────────────┐
    │  Raspberry Pi   │◄════════════════════════►│  RP2040-Zero        │
    │                 │   Commands + Telemetry    │                     │
    │  - Orchestrator │                           │  - Gate drive (PIO) │
    │  - TUI server   │                           │  - INA226 polling   │
    │  - TCP to host  │                           │  - Safety interlocks│
    │                 │                           │  - Rotary + 7-seg   │
    └─────────────────┘                           │                     │
           │ TCP                                  │  GP2-5: Gate out    │
           ▼                                      │  GP6-7: I2C bus     │
    ┌─────────────────┐                           │  GP8-13: Rotary     │
    │   Host TUI      │                           │  GP14-15: 7-seg     │
    │   (Textual)     │                           └──┬──────────┬──────┘
    └─────────────────┘                              │          │
                                           Gate Drive  I2C Bus  Built-in ADC
                                                │        │      (GP26-28)
                                                │        │         │
                                          ┌──────────┐ ┌────────┐  │
                                          │ UCC5304  │ │INA226  │  │
                                          │ ×4       │ │× 4     │  │
                                          └────┬─────┘ │(+2 spr)│  │
                                               │       └───┬────┘  │
                                               ▼           │       │
                                          ┌──────────┐     │       │
                                          │ H-Bridge │◄────┘       │
                                          │ MOSFETs  │  steady-    │
                                          │ P1,P2    │  state V+I  │
                                          │ N1,N2    │◄────────────┘
                                          └────┬─────┘  transient
                                               │        capture
                                                    │
                                                    ▼
                                               ┌──────────┐
                                               │  Pouch   │
                                               │  Cell    │
                                               │  Load    │
                                               └──────────┘

    Physical fallback interface (on RP2040-Zero directly):

    ┌──────────────┐     ┌──────────────┐
    │ Rotary Dials │────►│  RP2040-Zero │────► 7-Segment Display
    │ (input)      │     │  (standalone) │     (status output)
    └──────────────┘     └──────────────┘

    The physical interface allows basic operation (mode select,
    duty cycle adjustment, status monitoring) without the Pi or
    TUI running — useful for bench testing and as a safety fallback.
```

---

## Sampling Budget

The system uses two complementary sensing tiers:

### Tier 1: INA226 (Steady-State Monitoring)

The INA226 has configurable conversion time and averaging:

| Conversion Time | Averaging | Effective Rate (per module) | 4-Module Poll Rate |
|----------------|-----------|---------------------------|-------------------|
| 140µs (V+I) | 1 | ~3.5 kHz | ~875 Hz |
| 332µs (V+I) | 1 | ~1.5 kHz | ~375 Hz |
| 1.1ms (V+I) | 4 | ~227 Hz | ~57 Hz |
| 2.116ms (V+I) | 16 | ~30 Hz | ~7.5 Hz |

At 100 Hz switching frequency:
- Fastest config (140µs, no averaging): ~8-9 samples per cycle per module
- With 4 modules on one I2C bus at 400kHz: ~2-3 samples per cycle per module

Best for: DC averages, power calculation, fault detection, long-term logging.

### Tier 2: RP2040 Built-in ADC (Transient Capture)

| Parameter | Value |
|-----------|-------|
| Resolution | 12-bit (4096 steps) |
| Max sample rate | 500 kSPS (total, round-robin across channels) |
| Usable channels | 3 (GP26/ADC0, GP27/ADC1, GP28/ADC2) |
| Input range | 0 – 3.3V (use voltage dividers for higher voltages) |
| Samples/cycle at 100Hz | 5000 total, ~1667 per channel with 3 active |

Best for: switching edge capture, transient ringing, inrush profiles,
waveform visualization in the TUI.

The built-in ADC can be driven by DMA in the background — the CPU
sets up a circular buffer, DMA fills it at 500 kSPS, and firmware
reads completed buffers without blocking gate drive or I2C polling.

### Comparison

| | INA226 | RP2040 ADC | ADS131M08 (if needed later) |
|---|--------|------------|---------------------------|
| Resolution | 16-bit | 12-bit | 24-bit |
| Samples/cycle (100Hz) | 2-3 per module | ~1667 per channel | 320 per channel |
| Channels | 4-6 (V+I each) | 3 | 8 simultaneous |
| Extra hardware | None (modules) | Voltage dividers (~$1) | $25 breakout + front end |
| Interface | I2C (shared bus) | On-chip (DMA) | SPI |
| Best for | DC monitoring, faults | Transient capture | Precision transient capture |

### Recommended Approach

1. **Start with INA226 only** — validate circuit, confirm switching states work,
   measure steady-state V/I, set up ALERT-based overcurrent protection
2. **Add built-in ADC** — wire 2-3 voltage dividers to GP26-28, enable DMA-driven
   capture, visualize switching transients in the TUI
3. **External ADC only if needed** — if 12-bit resolution proves insufficient for
   a specific measurement (unlikely for most prototyping), add an ADS1256 module
   (~$8) or MCP3208 (~$4) on SPI

---

## Interface Modes

The system supports two independent control/monitoring interfaces:

### 1. TUI (Primary — via Pi)
- Host machine runs Textual TUI, connects to Pi over TCP
- Pi forwards commands to RP2040-Zero over USB serial
- Full telemetry display, logging, waveform visualization

### 2. Physical Interface (Fallback — on RP2040-Zero)
- Rotary dials for parameter input (duty cycle, mode, frequency)
- 7-segment display for status readout (voltage, current, mode, errors)
- Operates independently of Pi — RP2040 runs standalone if USB disconnected
- Useful for bench testing, quick adjustments, and safety-critical operation

The RP2040 firmware arbitrates between USB commands and local rotary
input. Local input takes priority when USB is disconnected. When both
are active, USB commands take precedence but local display always
reflects current state.

---

## Design Notes

1. **I2C BUS SPEED:** The RP2040 supports I2C at 400kHz (Fast Mode).
   All INA226 modules share the bus. Keep I2C wires short (<15cm) on the
   breadboard and use 4.7kΩ pullups on SDA/SCL.

2. **I2C BUS SPLITTING:** If 4 modules on one bus is too slow, split
   into 2 modules per I2C bus (RP2040 has two I2C peripherals: I2C0
   and I2C1). This doubles effective polling rate.

3. **HIGH-SIDE vs LOW-SIDE PLACEMENT:** The INA226 common-mode input
   range is 0V to 36V. For high-side sensing (P1, P2), VIN+ connects
   to the VCC rail side and VIN- to the MOSFET drain. For low-side
   sensing (N1, N2), VIN+ connects to the MOSFET source and VIN- to
   GND rail. Both are within the INA226's common-mode range as long
   as VCC ≤ 36V.

4. **SHUNT RESISTOR VALUE:** The D-FLIFE INA226 breakout likely has a
   built-in shunt (commonly 0.1Ω or 0.01Ω). Check the module —
   this determines the current range and resolution:
   - 0.1Ω shunt: max ±819.2mA, LSB = 25µA
   - 0.01Ω shunt: max ±8.192A, LSB = 250µA
   Calibration register must be programmed accordingly.

5. **DECOUPLING:** Place a 100nF ceramic cap (from the cap assortment)
   between VCC and GND on each INA226 module, close to the power pins.
   Also decouple the RP2040-Zero 3V3 rail.

6. **SAFETY:** The RP2040 firmware enforces dead-time and shoot-through
   protection independently of the Pi. If USB comms are lost, the RP2040
   defaults to all-off (idle) state. The physical interface (rotary +
   7-seg) remains operational in this mode.

7. **ALERT PINS:** Each INA226 has an open-drain ALERT pin that can
   trigger on over-current or over-voltage. These can be wired to a
   shared GPIO interrupt on the RP2040 for hardware-level protection
   (e.g., immediate MOSFET shutdown on overcurrent).

8. **RP2040-ZERO FORM FACTOR:** The Zero is smaller than the Pico —
   verify it has enough exposed GPIOs for all connections:
   - 4 gate drive (GP2-5)
   - 2 I2C (GP6-7)
   - 3 built-in ADC (GP26-28)
   - Up to 6 rotary (GP8-13)
   - 2 display (GP14-15)
   - Total: 17 GPIOs needed. The RP2040-Zero typically exposes 20+ GPIOs.

9. **BUILT-IN ADC USAGE:** The RP2040's ADC inputs (GP26-28) accept
   0-3.3V only. For measuring voltages above 3.3V, use a resistive
   divider (e.g., 75kΩ/10kΩ for ±10V → ±1.18V, same topology as the
   original plan's voltage sense). For current transient capture, either:
   - Tap the INA226 module's shunt voltage output (if accessible)
   - Add a single op-amp channel (one section of an MCP6004, ~$1.50)
   - Use a simple shunt + resistor divider for low-side paths
   Keep ADC input wires short and away from gate drive traces to
   minimize noise coupling. Use 100nF caps from the assortment on
   the ADC AVDD pin (GP26-28 share an analog power domain).

10. **DMA-DRIVEN SAMPLING:** Use the RP2040's DMA engine to drive ADC
    reads into a circular buffer. This decouples transient capture from
    the main firmware loop (gate drive timing, I2C polling, USB comms).
    The PIO can trigger ADC captures synchronized to switching edges
    for phase-aligned transient data.

11. **UPGRADE PATH:** Once prototyping validates the topology, the INA226
    modules can be replaced with bare INA226 chips on a custom PCB. If
    12-bit ADC resolution is insufficient, add an ADS1256 module (~$8)
    or MCP3208 (~$4) on SPI — both are breadboard-friendly. The
    ADS131M08 ($25) is only justified if simultaneous 24-bit capture
    across all channels is needed.

10. **7-SEGMENT PROTOCOL:** If using a TM1637-based display, it needs
    only 2 GPIOs (CLK + DIO) using a proprietary serial protocol.
    MicroPython and C libraries exist for the RP2040.
