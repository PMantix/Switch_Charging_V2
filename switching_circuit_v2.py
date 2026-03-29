#!/usr/bin/env python3
"""
Switching Circuit V2 - H-Bridge Controller

Ported from V1 (switch_charging_sequence_final.py) with the following change:
  All 4 MOSFET outputs use active_high=True because V2 has MOSFET driver ICs
  (V1 used active_high=False for P-channel high-side GPIOs 17 and 27).

GPIO assignments:
  17, 27  = High-side MOSFETs (P1, P2)
  22, 23  = Low-side MOSFETs (N1, N2)
  18, 24  = TM1637 display CLK / DIO
  16, 20, 21 = Rotary encoder CLK / DT / BTN

Controls:
  Rotate          - Adjust frequency (normal mode) or select sequence (sequence mode)
  Short press     - Toggle fine/coarse frequency step (normal) or detail view (sequence)
  Long press      - Toggle between frequency mode and sequence-selection mode
"""

from gpiozero import RotaryEncoder, Button, OutputDevice
from tm1637 import TM1637
from time import sleep, time

# ---------------------------------------------------------------------------
# Frequency limits
# ---------------------------------------------------------------------------
MAX_FREQ = 300.0   # Hz
MIN_FREQ = 0.1     # Hz

# ---------------------------------------------------------------------------
# MOSFET outputs  (V2: all active_high=True — driver ICs handle inversion)
# ---------------------------------------------------------------------------
P1 = OutputDevice(17, active_high=True)   # High-side A
P2 = OutputDevice(27, active_high=True)   # High-side B
N1 = OutputDevice(22, active_high=True)   # Low-side A
N2 = OutputDevice(23, active_high=True)   # Low-side B

# ---------------------------------------------------------------------------
# H-bridge state definitions
#   Index:  (P1,    P2,    N1,    N2)
#   0: +A / -A   (P1+N1)
#   1: +A / -B   (P1+N2)
#   2: +B / -A   (P2+N1)
#   3: +B / -B   (P2+N2)
#   4: All ON
#   5: All OFF
# ---------------------------------------------------------------------------
STATE_DEFS = [
    (True,  False, True,  False),   # 0
    (True,  False, False, True),    # 1
    (False, True,  True,  False),   # 2
    (False, True,  False, True),    # 3
    (True,  True,  True,  True),    # 4  all-on
    (False, False, False, False),   # 5  all-off
]

# ---------------------------------------------------------------------------
# 8 selectable switching sequences (each is 4 steps of state indices)
# ---------------------------------------------------------------------------
SEQUENCES = [
    [5, 5, 5, 5],   # 1: all-off (idle)
    [0, 1, 2, 3],   # 2
    [0, 1, 3, 2],   # 3
    [0, 2, 1, 3],   # 4
    [0, 2, 3, 1],   # 5
    [0, 3, 1, 2],   # 6
    [0, 3, 2, 1],   # 7
    [4, 4, 4, 4],   # 8: all-on
]

# ---------------------------------------------------------------------------
# TM1637 4-digit 7-segment display
# ---------------------------------------------------------------------------
CLK_PIN = 18
DIO_PIN = 24
display = TM1637(clk=CLK_PIN, dio=DIO_PIN)
display.set_brightness(3)

# Extend segment table: index 10 = dash (blank/dash), then 10..19 = digits with DP
if len(display._segments) < 11:
    display._segments.append(0x40)          # index 10 = dash/blank
BLANK = 10
DECIMAL_START = len(display._segments)
for i in range(10):
    display._segments.append(display._segments[i] | 0x80)  # digits with decimal point

# ---------------------------------------------------------------------------
# Rotary encoder + push-button
# ---------------------------------------------------------------------------
ROTARY_CLK = 16
ROTARY_DT  = 20
ROTARY_BTN = 21

rotary = RotaryEncoder(ROTARY_CLK, ROTARY_DT, max_steps=1000, wrap=False)
button = Button(ROTARY_BTN)

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
frequency   = 1.0
period      = 1.0 / frequency
step_time   = period / 2.0
step_rate   = 1          # 1 = fine (0.1 Hz steps), 10 = coarse (1 Hz steps)

freq_base   = frequency
dial_offset = rotary.steps

last_step_time    = time()
current_step      = 0
last_rotary_steps = rotary.steps

# Sequence selection
switch_sequence_mode      = False
sequence_sel              = 1          # 1-based index into SEQUENCES
sequence_mode_base        = 0
sequence_display_detailed = False

# Button timing
press_start              = None
last_freq_toggle_time    = 0
freq_toggle_hysteresis   = 1.5

# Display cache (avoid redundant I2C writes)
cached_display = None

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def compute_display_digits():
    """Return a list of 4 segment indices for the TM1637."""
    if not switch_sequence_mode:
        # Frequency mode: show freq * 10 as 4 digits (implied single decimal)
        freq_disp = max(int(MIN_FREQ * 10), int(frequency * 10))
        return [int(d) for d in f"{freq_disp:04}"]
    else:
        if not sequence_display_detailed:
            # Show sequence number on rightmost digit with decimal point
            return [BLANK, BLANK, BLANK, DECIMAL_START + sequence_sel]
        else:
            # Show the 4 state indices (+1 for human-readable)
            if sequence_sel == 1:
                return [0, 0, 0, 0]
            elif sequence_sel == len(SEQUENCES):
                return [1, 1, 1, 1]
            else:
                return [s + 1 for s in SEQUENCES[sequence_sel - 1]]


def update_display_if_changed():
    """Push new digits to the display only when they differ from cache."""
    global cached_display
    new_digits = compute_display_digits()
    if new_digits != cached_display:
        display.display(new_digits)
        cached_display = new_digits

# ---------------------------------------------------------------------------
# Apply an H-bridge state to the MOSFET outputs
# ---------------------------------------------------------------------------
def apply_state(state):
    """Set MOSFETs according to a 4-tuple (P1, P2, N1, N2)."""
    P1.value = state[0]
    P2.value = state[1]
    N1.value = state[2]
    N2.value = state[3]


def all_off():
    """Convenience: turn every MOSFET off."""
    P1.off(); P2.off(); N1.off(); N2.off()

# ---------------------------------------------------------------------------
# Button callbacks
# ---------------------------------------------------------------------------
def button_pressed():
    global press_start
    press_start = time()


def button_released():
    global press_start, sequence_display_detailed, step_rate
    global last_freq_toggle_time, freq_base, dial_offset

    freq_base   = frequency
    dial_offset = rotary.steps

    duration = (time() - press_start) if press_start is not None else 0

    # Short press (< 0.5 s) with hysteresis guard
    if duration < 0.5 and (time() - last_freq_toggle_time) >= freq_toggle_hysteresis:
        if switch_sequence_mode:
            sequence_display_detailed = not sequence_display_detailed
            print("Sequence display detail toggled:", sequence_display_detailed)
        else:
            step_rate = 10 if step_rate == 1 else 1
            print(f"Frequency adjustment factor set to x{step_rate}")
        last_freq_toggle_time = time()

    press_start = None


def toggle_mode():
    """Long-press handler: switch between frequency and sequence-selection modes."""
    global switch_sequence_mode, sequence_mode_base, dial_offset, freq_base

    switch_sequence_mode = not switch_sequence_mode
    print("Dial_offset set to rotary.steps:", rotary.steps)

    if switch_sequence_mode:
        print("Entered sequence selector mode")
        sequence_mode_base = (rotary.steps - dial_offset) - (sequence_sel - 1)
        all_off()
    else:
        print("Exited sequence selector mode")

    freq_base   = frequency
    dial_offset = rotary.steps


button.hold_time    = 0.5
button.when_pressed  = button_pressed
button.when_released = button_released
button.when_held     = toggle_mode

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
print("Switching Circuit V2 running. Frequency:", frequency, "Hz")

while True:
    now = time()

    if not switch_sequence_mode:
        # --- Frequency mode ---

        # Check for rotary changes and update frequency
        if rotary.steps != last_rotary_steps:
            last_rotary_steps = rotary.steps
            increment = 0.1 if step_rate == 1 else 1.0
            raw_frequency = freq_base + (rotary.steps - dial_offset) * increment
            frequency = max(min(raw_frequency, MAX_FREQ), MIN_FREQ)
            print("Frequency set to:", frequency)

            period    = 1.0 / frequency
            step_time = period / 2.0

        # Step through the selected sequence at the correct rate
        if now - last_step_time >= step_time:
            seq = SEQUENCES[sequence_sel - 1]
            state_index = seq[current_step]
            apply_state(STATE_DEFS[state_index])

            last_step_time = now
            current_step = (current_step + 1) % 4

    else:
        # --- Sequence selection mode ---

        if rotary.steps != last_rotary_steps:
            last_rotary_steps = rotary.steps
            sequence_sel = 1 + (rotary.steps - dial_offset - sequence_mode_base) % len(SEQUENCES)
            if sequence_sel == 0:
                sequence_sel = len(SEQUENCES)
            print(f"Sequence set to: {sequence_sel} -> {SEQUENCES[sequence_sel - 1]}")

        sleep(0.01)

    # Update display every iteration (only writes on change)
    update_display_if_changed()
