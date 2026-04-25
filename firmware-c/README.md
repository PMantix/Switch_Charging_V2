# switching_circuit_v2 — RP2040 firmware (C port)

C / Pico SDK port of `firmware/main.py`. Same wire protocol, same pin map,
no MicroPython interpreter overhead on the I²C hot path. The host
(`server/gpio_driver.py`) does not need to change.

## Build

You need:

- The Pico SDK 2.x checked out somewhere (e.g. `~/pico/pico-sdk`),
  with submodules fetched.
- `arm-none-eabi-gcc`, `cmake`, `make` (or `ninja`).

The SDK is **not** vendored in this repo. Point `PICO_SDK_PATH` at your
local checkout, then build out-of-tree:

```sh
export PICO_SDK_PATH=/path/to/pico-sdk
cd firmware-c
mkdir -p build && cd build
cmake ..
make -j
```

That produces `switching_circuit_v2_fw.uf2` in `build/`. Drag-and-drop it
onto the RP2040's `RPI-RP2` mass-storage volume (hold BOOTSEL while
plugging in), or use `picotool load -fx`.

## Layout

| File | Purpose |
|------|---------|
| `src/main.c` | Entry point + main poll loop |
| `src/config.h` | Pin map, INA226 addresses, build constants |
| `src/usb_cdc.{c,h}` | USB CDC stdio wrappers + line buffer |
| `src/ina226.{c,h}` | INA226 I²C driver (scan, config, fast/streaming reads) |
| `src/streaming.{c,h}` | D-line emit, rate clamp, burst buffer, Z profiler |
| `src/cmd_parser.{c,h}` | Line-based command dispatch (S/Q/J/I/T/A/V/M/Z/L/P/B/BD/R/X/N/C/F/G/H/K) |
| `src/switching.{c,h}` | FET pin control + repeating-timer ISR for periodic switching |
| `src/neopixel.{c,h}` | WS2812 status LED via PIO |
| `src/ws2812.pio` | PIO program for WS2812 (compiled to `ws2812.pio.h` at build time) |

## Wire format

D lines are unchanged from the MicroPython firmware:

```
D <ticks_us> <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>\n
```

`ticks_us` is `time_us_64() & 0xFFFFFFFF` snapshotted *before* the I²C
sweep starts (sample-capture timestamp). All voltages are 4-decimal,
all currents 6-decimal. See
`server/gpio_driver.py:_handle_stream_line` for the host parser.

## Notable behaviour

- Switching ISR uses `add_repeating_timer_us` with a negative delay
  (period-from-completion), so it's strictly periodic and allocation-free.
- INA226 CONFIG is `0x4127` at AVG=4 / VSHCT=VBUSCT=332 µs / mode=continuous
  shunt+bus. The `A` command re-encodes the AVG bits.
- CNVR is **off** by default. The `N` command toggles the MASK_ENABLE
  register but the streaming loop doesn't poll the ALERT pin yet — that's
  Phase 2.
- Burst capture is RAM-resident at up to 3000 rows; `BD` dumps as text
  `BR` lines, matching the existing host parser.
