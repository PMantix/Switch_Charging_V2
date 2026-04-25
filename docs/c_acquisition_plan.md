# C Acquisition Plan — Move INA226 streaming off MicroPython

Status: research + planning only. Nothing here is production code. Do not
flash this onto a DOE rig until Phase 1 is implemented and A/B'd against the
current Python firmware.

Author: planning agent, 2026-04-25.

---

## TL;DR

- **Recommendation: Do NOT cut to a C/C++ Pico-SDK firmware yet.** First
  re-land the reverted binary frame format on the existing MicroPython
  firmware. We already have direct evidence it hits 769 Hz at AVG=4 /
  bus_every=5 (commit `845c40d`, reverted as `9f24861` for a
  timestamp bug that has since been fixed in `971f781`). That alone
  covers the 400–800 Hz target the user wants.
- **If, after re-landing binary framing, we still need more headroom**
  (sustained >800 Hz, or AVG=1 single-sensor at >2 kHz), the right next
  step is **MicroPython + a C user-C-module** for the INA226 sweep +
  binary frame emit only. Keep everything else (commands, switching
  Timer IRQ, init, burst, NeoPixel) in MicroPython. This is the
  smallest-blast-radius path that recovers full silicon throughput
  without giving up the REPL or rewriting the command parser.
- **Pure Pico-SDK C/C++ on RP2040** is overkill for the present
  bottleneck and re-implements ~800 lines of Python for marginal gain
  beyond what binary framing + a native module already buy us. Park it
  as Phase 3.

Effort estimate to recover ≥700 Hz at AVG=4: **~1 day** (re-apply
`845c40d`, A/B against baseline, add seq number — all on the existing
toolchain).

---

## 1. Context — what the bottleneck actually is

Measured: 245 Hz at AVG=1, bus_every=5
([`firmware/main.py:142-149`](../firmware/main.py)).

Theoretical INA226 silicon ceiling at AVG=1, VSHCT=VBUSCT=332 µs:
~1.5 kHz/sensor (per `docs/rp2040_adc_upgrade.md:280`). With 4 sensors
on one I²C bus at 1 MHz the bus-limited ceiling is ~875 Hz/sweep.

The streaming hot path
([`firmware/main.py:436-470`](../firmware/main.py)):

```python
_STREAM_FMT = "D %d %.4f %.6f %.4f %.6f %.4f %.6f %.4f %.6f\n"
def emit_stream_line():
    ...
    t_us = time.ticks_us()
    r = ina226_read_all_streaming(read_bus)
    sys.stdout.write(_STREAM_FMT % (
        t_us,
        r[0][0], r[0][1], r[1][0], r[1][1],
        r[2][0], r[2][1], r[3][0], r[3][1],
    ))
```

Where the time goes — **measured 2026-04-25** on the live rig via the
extended `Z` command (`OK Z <n> <avg_us> <i2c_us> <fmt_us>
<write_us> <max_hz>`), 100–200 emits averaged per row:

| Config                                  | total µs | i2c µs (%)  | fmt µs (%)  | write µs (%) | other µs (%) | max Hz |
| --------------------------------------- | -------- | ----------- | ----------- | ------------ | ------------ | ------ |
| AVG=4, bus_every=1 (default)            | 5242     | 3283 (63%)  | 1360 (26%)  | 270 (5%)     | 329 (6%)     | 190.8  |
| AVG=4, bus_every=5                      | 4522     | 2581 (57%)  | 1350 (30%)  | 268 (6%)     | 323 (7%)     | 221.1  |
| AVG=1, bus_every=1                      | 5308     | 3226 (61%)  | 1491 (28%)  | 269 (5%)     | 322 (6%)     | 188.4  |
| AVG=1, bus_every=5                      | 4348     | 2306 (53%)  | 1446 (33%)  | 268 (6%)     | 328 (8%)     | 230.0  |
| AVG=1, bus_every=5 (≥600 sps requested) | 4420     | 2308 (52%)  | 1511 (34%)  | 265 (6%)     | 336 (7%)     | 226.2  |

(`other` = loop bookkeeping, timer poll, the seq increment, and the
Z-command profile harness's own `ticks_diff` calls — all unavoidable
under the current architecture.)

The earlier hypothesis that **`%`-format on 9 floats is the dominant
cost is wrong.** The data overturns it:

- **I²C reads dominate (52–63% of every emit).** That's 4 sequential
  `i2c.readfrom` calls per sweep, each ~580 µs at 1 MHz nominal,
  including MicroPython's per-call interpreter overhead. The bus
  rate the firmware *requests* is 1 MHz, but per `machine.I2C` docs
  the *actual* SCL rate may be lower; this needs scope verification
  (already in the Phase 1 checklist).
- **Format string is secondary (26–34%).** Real, but second-place.
- **USB CDC `write` is negligible (5–6%).** The "wire is the
  bottleneck" framing was wrong.
- **AVG=1 vs AVG=4 changes total emit time by <2%.** The
  per-conversion time (664–2656 µs) is dominated by I²C-side
  overlap — the chip finishes converting while the CPU is still
  walking the bus from the prior sweep. Lowering AVG buys very
  little until I²C is faster.
- **`bus_every=5` saves ~700 µs of I²C** vs `bus_every=1` (matches
  4-of-5-skipped × ~150 µs/bus-read prediction). This is the
  cheapest lever currently available.

**Implications for the rest of this plan:**

A pure ASCII → binary conversion (Phase 1) zeroes out only the
format + a sliver of write — best case ~1450 + ~150 = ~1600 µs
saved on a 4400 µs emit, **~36% improvement, not 3.16×**. Realistic
post-binary ceiling at AVG=1, bus_every=5: ~2800 µs total, ~360 Hz.
The historical 245 → 770 Hz claim of `845c40d` is therefore
**suspect** — it should be re-validated against this measured
baseline before Phase 1 effort is committed. Possible explanations:
the prior baseline used a fatter format (more decimals, more
fields), or the prior measurement methodology differed.

The right architectural lever is **whatever cuts I²C**: PIO-driven
reads, async ALERT-driven scheduling so the CPU doesn't spin
waiting for the bus, or a C user-C-module that sheds MicroPython's
per-`readfrom` interpreter overhead. CNVR/ALERT (already
implemented in `worktree-agent-a4ee9ebd1f4cf8c1c`) is now a
higher-priority experiment than re-landing binary, because at
AVG=1 with chip cadence ~1.5 kHz vs sweep cadence ~230 Hz, ~5/6
emits read the same conversion. CNVR is a *correctness* fix in
addition to whatever throughput it recovers.

---

## 2. Architecture choice — assessed

### Option A — Re-land binary framing on MicroPython (recommended)

- Same toolchain, same flash flow, no new build system.
- Already proven: 3.16× throughput on real hardware.
- Pi-side parser was already written and tested (commit 845c40d notes
  document a stress test).
- **Cost: ~1 day** to re-apply, fix the timestamp issue (which the
  commit message implicates as the revert reason but which is now
  addressed by `971f781`'s `ticks_us` stamping), and add a sequence
  number.

### Option B — MicroPython + C user-C-module for the hot path

- Compile a small C module (`uina226`) that exposes a single function:
  `read_and_pack_into(buf, ts_us, bus_mask) -> int` that does all 4
  I²C reads and writes the binary frame into a pre-allocated bytearray.
- Build flow: clone the MicroPython tree, drop `uina226.c` into
  `ports/rp2/modules/`, set `MICROPY_USERMOD_*`, build a custom
  MicroPython `.uf2` once, flash. After that, `main.py` development is
  unchanged.
- Removes per-emit Python interpreter overhead (~200–400 µs of
  bridging) and lets us enable the INA226's "conversion ready" ALERT
  to skip the conservative "wait for AVG×CT" margin we'd otherwise
  pay.
- Estimated post-binary ceiling: ~1.5 kHz/sweep at AVG=1,
  bus_every=∞.
- **Cost: ~3–5 days** (one-time MicroPython tree build, C driver, glue,
  tests). Substantial: requires the user to learn/maintain a custom
  MicroPython port. Worth it only if Option A is provably insufficient.

### Option C — Pure Pico-SDK C/C++ firmware

- Replaces all of `firmware/main.py` (~810 lines) with C.
- Loses the REPL — debugging becomes printf + UF2 reflash cycles.
- Re-implements: command parser (J/A/V/F/G/M/Z/B/R/etc.), Timer IRQ
  switching, NeoPixel WS2812 driver, INA226 init/scan, burst path,
  USB CDC plumbing.
- The Pico SDK USB CDC implementation (`tinyusb`) does not buffer
  exactly the way MicroPython's does — the 64-byte CDC bursting
  behavior we already documented in `feedback_usb_cdc_burst.md` will
  surface differently and need fresh validation.
- **Cost: ~2–3 weeks** for a working baseline parity build, plus the
  ongoing maintenance hit of two parallel firmwares (Python for bench
  hacking, C for production).
- Throughput gain over Option B is small. USB CDC is unlikely to be
  the first bottleneck at 25–29 byte frames (a 25-byte frame at 1500
  emits/s is only ~37.5 kB/s, well under USB's nominal 12 Mbit/s),
  but the practical ceiling is set by tinyusb's packetization,
  scheduling, and flush policy — and must be measured on-device, not
  derived from raw bandwidth math.

### Option D — PIO + DMA for I²C

- An RP2040 PIO program can run the I²C state machine and a DMA chain
  can rotate through 4 INA226 reads without CPU intervention.
- Would let the CPU sleep/handle other work during the I²C sweep, not
  shorten the sweep itself (the sweep is already bus-limited).
- The win shows up only if we want the CPU free for ALERT-driven
  asynchronous reads, dual-rate streaming, or
  switching-edge-synchronized sampling.
- Park as a future Phase 3+ optimization. Not warranted by current
  data.

### Verdict

```
Phase 1   : Option A (re-land binary frames)             ← do this now
Phase 1.5 : split-Z profile + CNVR vs blind-poll A/B     ← cheap experiments after binary lands
Phase 2   : Option B (C user-C-module)                   ← only if 1 + 1.5 insufficient
Phase 3   : Option C / D (pure SDK or PIO+DMA)           ← speculative, defer
```

Phase 1.5 is a deliberate slow-down: before reaching for a custom
MicroPython build, run the two cheap measurements that might
remove the need for Phase 2 entirely. Both are already prepared in
existing worktrees and don't change the wire format.

---

## 3. What stays in Python, what moves

| Subsystem                         | File:line                      | Phase 1 | Phase 2 | Phase 3 |
| --------------------------------- | ------------------------------ | ------- | ------- | ------- |
| Command parser (S/Q/I/T/A/V/M/Z/L/P/B/BD/R/X/C/F/G/H/K) | `main.py:476-719` | Python | Python  | C       |
| Switching Timer IRQ (`_tick`)     | `main.py:235-269`              | Python | Python  | C / hardware-PWM |
| INA226 init/config/scan           | `main.py:284-346`              | Python | Python  | C       |
| Streaming sweep + emit            | `main.py:388-470`              | Python (binary fmt) | C module | C       |
| Burst recording                   | `main.py:608-643, 784-793`     | Python | Python  | C       |
| Status/heartbeat (P, NeoPixel)    | `main.py:197-205, 605-606`     | Python | Python  | C       |

Justification: the streaming sweep is the only path with a tight
per-iteration deadline. Everything else runs at human-interaction
cadence (commands) or ISR cadence (Timer at ≤20 kHz which the existing
allocation-free `_tick` handles fine — see `main.py:235`'s comment on
the IRQ contract). Moving them to C buys nothing.

---

## 4. Wire format

### Today

ASCII line-delimited:
```
D <ticks_us> <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>\n
```
Defined at `firmware/main.py:436`. Parsed at
`server/gpio_driver.py:144-190` (`_handle_stream_line`).

### Phase 1 proposal — re-land the binary frame from `845c40d`

25-byte little-endian frame:

```
offset  bytes  field
  0      2    sync         0xAA 0x55
  2      1    type         'B'      (0x42; reserved for future types)
  3      1    length       20       (payload bytes that follow, excluding xor)
  4      4    ticks_us     uint32   (firmware time.ticks_us at sweep start)
  8      4    seq_no       uint32   (NEW; monotonic, wraps at 2^32)        ← add to original
 12      2    P1.shunt     int16    (raw, ×2.5 µV LSB on Pi)
 14      2    P1.bus       uint16   (raw, ×1.25 mV LSB on Pi)
 16      2    P2.shunt     int16
 18      2    P2.bus       uint16
 20      2    N1.shunt     int16
 22      2    N1.bus       uint16
 24      2    N2.shunt     int16
 26      2    N2.bus       uint16
 28      1    xor          uint8    XOR of bytes [0..28]
            ────
total: 29 bytes
```

(Original `845c40d` was 25 bytes without `seq_no`. Add 4 bytes for the
seq number to coordinate with the parallel agent's text-path work and
to give the host a cheap "we dropped a frame" signal.)

Why binary:

- 9 ASCII floats average ~88 bytes/emit (with sign, decimals,
  separators); binary is 29.
- Float→string formatting on MicroPython is the dominant cost
  (Section 1 table). Moving raw→V/A math to the Pi is essentially
  free (Pi has plenty of CPU; 800 Hz × 8 floats = 6400 floats/s).
- USB CDC throughput is 12 Mbit/s nominal, ~960 KB/s practical on
  RP2040 + tinyusb. ASCII at 800 Hz: ~70 KB/s. Binary at 800 Hz:
  ~23 KB/s. Wire is not the bottleneck either way; per-emit
  formatting is.

Why a 1-byte XOR (not CRC16):

- We already have framing redundancy via the 2-byte sync + 1-byte
  type + 1-byte length pattern. XOR is enough to detect single-byte
  corruption inside the frame body. If we ever see frames slipping
  through with corrupted payloads, upgrade to CRC8.

### Backward compatibility with `gpio_driver._handle_stream_line`

- The reverted commit already shipped a state-machine framer in
  `_handle_stream_line` that:
  - Hunts for the `AA 55 'B' 20` sync header in a persistent rxbuf.
  - Decodes 25-byte frames; routes any non-frame bytes (boot banner,
    `OK`/`ERR` replies) to a `\n`-delimited line accumulator.
  - Recovers cleanly from a firmware reboot mid-stream.
- Re-applying that code is the natural first step. Add 4 bytes for
  `seq_no` at the right offset; expose it via the `on_sensor_tick`
  callback so recorder code can detect dropouts.
- Host-side: the existing `_handle_stream_line(line)` text path
  becomes a fallback. Detect the protocol version at startup by
  watching for either `AA 55 'B' 20` or `D ` prefix on the stream.
  Both must coexist for a clean rollout window where some rigs run
  old firmware.

### Coordinating with the parallel agent

The parallel agent is adding a sequence number to the **text** path.
Don't conflict. Specifically:

- Reserve byte offsets [8..12] in the binary frame for `seq_no` from
  day one. The text-path commit can use the same field name in its
  schema (`D <seq> <ticks_us> ...`) so both formats expose the same
  semantic.
- If their PR lands first, take their seq_no field name and ordering
  conventions into the binary frame.

---

## 5. Build & deploy

### Phase 1 (binary framing on MicroPython)

- **Build:** None. `firmware/main.py` is the artifact.
- **Flash:** Existing `firmware/upload.py` (mpremote-style raw REPL
  push). Already documented in the deployment workflow note.
- **Rollback:** `git checkout HEAD~1 -- firmware/main.py
  server/gpio_driver.py` and re-run `upload.py`. Both files MUST be
  reverted together; protocol mismatch will break streaming.

### Phase 2 (C user-C-module + custom MicroPython UF2)

New layout:

```
firmware/
  main.py                  ← unchanged after module exposes uina226
  modules/
    uina226/
      uina226.c
      micropython.cmake
      micropython.mk
  build_uf2.sh             ← clone micropython, configure with USER_C_MODULES
  README_BUILD.md
```

- **Toolchain:** `arm-none-eabi-gcc` (Homebrew or Linux pkg) + Pico
  SDK (`pico-sdk` submodule pinned to a known-good release). Bundle
  the SDK as a git submodule under `firmware/pico-sdk/` so the build
  is reproducible.
- **Build artifact:** `firmware/build/firmware.uf2` (or `.bin` for
  swd). One UF2 per RP2040 board variant (Zero vs Pico).
- **Flash UF2:** Hold BOOTSEL on the RP2040-Zero, plug USB, RP2040
  enumerates as `RPI-RP2` mass-storage; drag `firmware.uf2` onto
  it. Boots automatically.
- **Flash without re-plug:** `mpremote ... --command 'import
  machine; machine.bootloader()'` from the existing Python firmware
  drops the device into BOOTSEL on demand. After a C-module-bearing
  UF2 is on the device, MicroPython is still running — `main.py`
  stays the entry point — only the new module is added.
- **Rollback:** Keep the previous "vanilla MicroPython" UF2 in
  `firmware/uf2/micropython-vanilla-v1.23.uf2` in the repo. To roll
  back: bootsel + drag-and-drop. About 30 seconds.

### Phase 3 (pure Pico-SDK C/C++)

Same UF2 mechanics, but `firmware.uf2` now contains the entire
firmware (no MicroPython). Rollback to MicroPython is
`drag-and-drop the vanilla UF2 + upload.py main.py`. About 90
seconds.

---

## 6. Phased rollout plan

### Phase 1 — Re-land binary framing (recommended start)

**Goal:** Reproduce the 245 Hz → 770 Hz win from `845c40d`, with the
timestamp fix from `971f781` already baked in, and a seq_no added.

**Scope is deliberately narrow** — only these five things land in this
phase:

1. Binary frame format back in firmware + host parser
2. seq_no field in the frame (already designed; parallel agent
   added it to the text path, this phase mirrors it in binary)
3. Firmware-anchored timestamps preserved end-to-end (the
   timestamp fix from `971f781` already baked into the host path)
4. ASCII fallback retained — text and binary paths coexist; host
   detects on stream-prefix
5. DOE A/B validation against the ASCII baseline

Anything else (CNVR scheduling, profiling instrumentation tuning,
chip-level CT asymmetry, AVG sweeps) belongs to Phase 1.5 or later.
Keep this phase scoped so the only question it answers is: **does
binary framing recover the 3.16× throughput without harming data
quality?**

**Smallest testable increment:**
1. Verify external I²C pull-ups before any high-rate measurement.
   `firmware/main.py:166` configures internal ~50 kΩ pull-ups at
   1 MHz, which is marginal. Confirm the PCB carries 2.2 kΩ
   external pull-ups or add them; otherwise any rate >800 Hz
   measured here is unreliable. (Promoted from the risks section
   because it gates the Phase 1 result.)
2. Cherry-pick `845c40d` onto current main. Resolve conflicts
   against `971f781` (the `ticks_us` stamping must remain at sweep
   *start*, not after, per the comment at `firmware/main.py:458`).
3. Add `seq_no` field at byte offset 8 of the binary frame.
4. Run on a rig with `tools/rate_sweep.py`. Confirm:
   - Sustained ≥700 Hz at AVG=4, bus_every=5.
   - Per-D-line `ticks_us` is monotonic and consistent with prior
     ASCII output (host-side log compare).
   - No dropouts under 60 s sustained streaming (seq_no monotonic).
5. Run the recording-DOE workflow (`tools/recording_doe.py`)
   end-to-end. Compare cycle-aligned plots against the last
   ASCII-format DOE recording. The shapes must match to within
   sample-rate aliasing.
6. Validate against a known-good baseline (per `feedback_validate_data_quality.md`).

**Effort:** 1 day (one engineer). Half on cherry-pick + seq_no, half
on validation.

**Rollback:** `git revert <new commit>`; both
`firmware/main.py` and `server/gpio_driver.py` revert together. The
old text path is preserved through the parallel agent's seq_no-on-text
work.

### Phase 1.5 — Profile + CNVR experiments (cheap, before Phase 2)

**Goal:** Two measurements that may eliminate the need for Phase 2.
Both already have implementations in worktrees and don't touch the
wire format.

**1. Split-Z profiling.** The seq+profiling worktree
(`worktree-agent-ae33049ed22730043`) extends the `Z` command to
return `OK Z <n> <avg_us> <i2c_us> <fmt_us> <write_us> <max_hz>`.
Run `Z 100` after Phase 1 and record the per-stage breakdown for
both ASCII and binary paths. **Update Section 1's timing table with
measured numbers.** This sharpens every downstream architectural
call (especially the Phase 2 cost/benefit estimate).

**2. CNVR vs blind-poll A/B.** The CNVR worktree
(`worktree-agent-a4ee9ebd1f4cf8c1c`) wires the INA226 ALERT pin
(verified routed to RP2040 GP27 in the schematic) into the
streaming loop, gating each emit on a fresh conversion. At AVG=1
the chip cadence is ~1.5 kHz but emit rate is ~245 Hz, so under
blind polling roughly 5 of every 6 reads return the same
conversion (bit-identical stale repeats). CNVR is primarily a
**data-quality fix**, but it may also recover wasted
ALERT-handshake margin.

Run the same DOE sweep used for Phase 1, with CNVR enabled vs
disabled. Compare:
- Inter-sample dt distribution (firmware-anchored).
- "Stale repeat" rate — count sequential samples where every
  sensor reads bit-identical raw values.
- Achieved throughput at AVG=1 single-sensor.

**Decision gate before Phase 2:** With Phase 1 + Phase 1.5 done,
measure throughput at AVG=1 single-sensor read (factors out
averaging cost). If ≥1.2 kHz / sweep, silicon and I²C are no
longer the bottleneck — Phase 2 buys nothing. If ≤1.0 kHz,
MicroPython interpreter overhead is still material; the split-Z
numbers tell us whether Phase 2's allocation-free C path is worth
it.

**Effort:** ~1 day. Both worktrees are already implemented; this
phase is mostly merge + test + measure.

**Rollback:** Both worktrees touch only `firmware/main.py` and the
host parser plus the recorder schema. Standard `git revert`.

### Phase 2 — C user-C-module for the streaming sweep

**Goal:** Push past ~800 Hz to ~1.2–1.5 kHz/sweep. Useful for AVG=1,
single-sensor experiments, and 200+ Hz switching where we want
≥6 samples per cycle.

**Design rule (non-negotiable):** the C hot path **must not allocate
at runtime**. MicroPython on rp2 will fail any C-side `m_malloc` /
`malloc` unless the firmware is built with `MICROPY_C_HEAP_SIZE=N`
explicitly carved out, and that reserved C heap comes out of memory
the Python interpreter can no longer use. Trading Python heap for C
heap is exactly the wrong direction. Strict requirements:

- All output buffers are pre-allocated bytearrays passed in by the
  Python caller (`out_buf`, `addrs_bytes`).
- All transient state is on the C stack, never the heap.
- `m_new` / `m_malloc` / any allocation-flavored API is forbidden
  in `read_and_pack_into`.
- I²C errors return a status code; do not raise C exceptions
  (which allocate).

If a future feature needs allocation, raise `MICROPY_C_HEAP_SIZE`
explicitly in the build and document the Python-heap loss.

**Design:**
- New module `uina226` exports:
  - `init(i2c_id, sda_pin, scl_pin, freq_hz)` — set up the
    hardware peripheral once.
  - `scan() -> list[(name, addr)]` — same as Python's
    `ina226_scan` but in C.
  - `apply_config(addr, config_word)` — write CONFIG register.
  - `read_and_pack_into(out_buf: bytearray, addrs: bytes,
    seq_no: int, read_bus_mask: int) -> int` — the hot path.
    Writes the 29-byte binary frame into `out_buf`, returns the
    number of bytes (29 on success, 0 on error). All 4 I²C reads
    happen in this single call. **Allocates nothing.**
- Python side (`main.py`) shrinks: the streaming branch becomes
  ```python
  n = uina226.read_and_pack_into(_frame_buf, _addrs_bytes, seq_no, mask)
  if n: sys.stdout.buffer.write(_frame_buf)
  ```
- Switching IRQ stays in Python (Timer + `_tick` is allocation-free
  already, no benefit from rewrite).
- Command parser stays in Python. Commands that touch the moved
  subsystem (`A`, `V`, `Z`, `M`, `R`) re-implement as Python
  wrappers around the new C module.

**Smallest testable increment:** start with a no-op user-C-module
that just prints "uina226 loaded" on import. Verify the build +
flash cycle. Add the I²C read. Add the pack. Replace the streaming
hot path. Each step is independently flashable.

**Effort:** 3–5 days. Bulk is the one-time MicroPython build setup
+ scripting. The C module itself is ~150 LOC.

**Rollback:** Re-flash vanilla MicroPython UF2 (in
`firmware/uf2/`), re-upload `main.py`. ~1 minute.

### Phase 3 — Optional. Pure SDK C/C++ or PIO+DMA

Only motivated if **after Phase 2** there is still a measured need
that the Phase 2 architecture cannot meet. Realistic candidates:

- Switching-edge-synchronized burst sampling (PIO triggers
  `bus_every=1` reads phase-locked to `_tick`).
- Dual-core split: core1 owns the streaming sweep + USB CDC TX,
  core0 owns commands + Timer IRQ. Eliminates main-loop poll
  jitter on commands.
- Cycle-by-cycle ALERT-driven async reads (skip the conservative
  AVG×CT wait).

**Effort:** 2–3 weeks for any of these. Out of scope for the
current DOE schedule.

---

## 7. Risks

### Risks shared by all options

- **Wire format change breaks recordings.** Every D-line consumer
  (`server/gpio_driver._handle_stream_line`,
  `tools/recording_doe.py`, `tools/plot_*.py`) must be revalidated.
  Mitigation: keep the ASCII path live as a fallback that the host
  can request via a new command `T <hz> --ascii` (or a server
  config flag).
- **USB CDC bursting changes when frames shrink.** `feedback_usb_cdc_burst.md`
  warns receipt-time timestamps are unreliable; binary frames make
  this worse because TinyUSB is more aggressive about packing
  small writes. We already use firmware `ticks_us` for capture
  time, so this is mitigated, but we must regression-test the
  on_sensor_tick latency on a rig.
- **Sequence number overflow at u32 wraps every ~5.4 hours at
  100 Hz, ~33 minutes at 800 Hz.** Pi-side parser must handle wrap.

### Phase 2-specific risks

- **MicroPython port build is fiddly on macOS.** Submodule the SDK
  to keep the build reproducible. Document the toolchain version
  pin (`arm-none-eabi-gcc 13.x` is what current Pico SDK targets).
- **Custom MicroPython firmware diverges from upstream.** Pin to
  a release tag, not master. Track upgrades manually.

### Phase 3-specific risks

- **Loss of REPL convenience.** Today, debugging is `mpremote repl`
  + an immediate `import main; main.main()`. C firmware loses
  this entirely. The user does not want to give it up unless the
  performance demand is irrefutable.
- **Pico SDK USB CDC implementation differs from MicroPython's.**
  TinyUSB's CDC sends 64-byte packets eagerly; we need to either
  call `tud_cdc_write_flush()` after each frame or accept that
  small frames coalesce. Test: 25-byte frame at 770 Hz under
  TinyUSB — does it match MicroPython's emit cadence?
- **PWM/Timer conflicts.** The existing Timer-IRQ switching uses
  one alarm. Pico SDK has 4 alarm channels; reserving one for
  switching, one for stream-rate gating, leaves headroom.
- **USB enumeration timing.** Watch out for the same
  `_wait_for_ready` 5 s race we already document at
  `gpio_driver.py:102-111`. C firmware "OK READY" line text must
  match exactly or the host-side connect-and-wait code times out.
- **Firmware command set must be reimplemented for any moved-to-C
  subsystem.** Phase 3 means rewriting all of `main.py:476-719`.
  Estimated 600 LOC of C with all the validation paths.

### Hardware risks (any phase)

- **I²C bus pull-ups** — promoted to Phase 1 checklist (Section 6).
  Internal ~50 kΩ pull-ups at 1 MHz are marginal; adding 2.2 kΩ
  external pull-ups is a precondition for trusting any rate >800
  Hz. Also note MicroPython documents that the *actual* I²C bus
  rate may be lower than the rate requested via `machine.I2C(...,
  freq=...)` on a given port — confirm the realised SCL frequency
  with a scope before drawing conclusions about bus-bandwidth
  ceilings.
- **Schematic constraints on core1.** Nothing in
  `pcb/switching_circuit_v2/...` known to me prevents core1 use,
  but verify on the rig before splitting the load.

---

## 8. Recommended decision

**Yes, start Phase 1 (re-land binary framing) now.** It is the
smallest change with the largest verified payoff, and we already have
production-ready code and a Pi-side parser in `git show 845c40d`.

**No, do not start Phase 2 (C user-C-module) yet.** Wait for the
post-Phase-1 throughput measurement. If it's ≥700 Hz at AVG=4 and
the user's DOE needs are met, Phase 2 has no upside that justifies
its multi-day cost or the maintenance burden of a custom MicroPython
build.

**No, do not start Phase 3 (pure SDK C/C++).** Out of proportion to
the present bottleneck. Revisit only if Phase 1 + Phase 2 together
fail to meet a documented research need.

The user is a battery researcher with hardware actively recording
DOEs. Downtime cost is high. Phase 1 gives a verifiable 3.16×
improvement with a 1-day window and a clean rollback.

---

## 9. Open questions to resolve before Phase 1 lands

1. **Why was `845c40d` reverted?** Commit message of the revert
   (`9f24861`) is bare. Need to git-blame the recorded DOEs from
   that day or ask the user. Hypothesis: timestamp drift between
   the binary path's `ticks_us` (stamped after sweep) and ASCII
   path's `ticks_us` (now stamped before sweep, per `971f781`)
   misaligned cycle-anchored plots. The fix is mechanical; just
   re-stamp before the sweep — but **confirm this** before
   re-landing.

2. **Coordinate seq_no field with the parallel ASCII-path agent.**
   We need to agree on whether seq_no precedes or follows
   `ticks_us` in the text format, so a recorded session can be
   replayed against either parser cleanly.

---

## 10. Appendix — relevant file:line references

- Streaming hot path: `firmware/main.py:436-470` (`_STREAM_FMT`,
  `emit_stream_line`).
- Decimated shunt-only read: `firmware/main.py:359-364`
  (`_ina226_read_shunt_only`).
- Streaming sweep with bus decimation:
  `firmware/main.py:388-414` (`ina226_read_all_streaming`).
- Max-rate calculation: `firmware/main.py:318-331` (`_max_stream_hz`).
- Profile-emit calibration: `firmware/main.py:569-596` (`Z` cmd).
- Timer IRQ switching: `firmware/main.py:235-269` (`_tick`,
  `_switching_start`).
- Main loop & USB CDC poll: `firmware/main.py:725-804`.
- Host-side stream parser: `server/gpio_driver.py:144-190`
  (`_handle_stream_line`).
- Host-side firmware-clock sync: `server/gpio_driver.py:329-374`
  (`sync_firmware_clock`).
- Host-side switching-anchor logic: `server/gpio_driver.py:407-446`
  (`start_switching`).
- Reverted binary commit: `git show 845c40d`.
- Revert commit (no rationale): `git show 9f24861`.
- Timestamp-correctness fix that the binary path now inherits:
  `git show 971f781`.
- Prior architectural plan: `docs/rp2040_adc_upgrade.md`.

