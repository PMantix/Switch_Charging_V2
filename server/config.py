"""
Switching Circuit V2 - Configuration constants and definitions.

All pin assignments, state definitions, sequences, and tuning parameters
live here so every other module imports from a single source of truth.
"""

# ---------------------------------------------------------------------------
# GPIO pin assignments
# ---------------------------------------------------------------------------
PIN_P1 = 17          # High-side MOSFET A
PIN_P2 = 27          # High-side MOSFET B
PIN_N1 = 22          # Low-side MOSFET A
PIN_N2 = 23          # Low-side MOSFET B

PIN_TM1637_CLK = 18  # TM1637 display clock
PIN_TM1637_DIO = 24  # TM1637 display data

PIN_ROTARY_CLK = 16  # Rotary encoder clock
PIN_ROTARY_DT = 20   # Rotary encoder data
PIN_ROTARY_BTN = 21  # Rotary encoder push-button

# All MOSFET outputs are active-high (V2 uses MOSFET driver ICs)
MOSFET_ACTIVE_HIGH = True

# ---------------------------------------------------------------------------
# H-bridge state definitions
#   Each tuple: (P1, P2, N1, N2) — True = on, False = off
#
#   0: +A / -A   (P1 + N1)
#   1: +A / -B   (P1 + N2)
#   2: +B / -A   (P2 + N1)
#   3: +B / -B   (P2 + N2)
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
# 8 selectable switching sequences (each is 4 state-definition indices)
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

NUM_SEQUENCES = len(SEQUENCES)

# ---------------------------------------------------------------------------
# Pulse charge: alternates between state 0 (P1+N1) and state 3 (P2+N2)
# to pulse-charge two batteries connected on each half-bridge leg
# ---------------------------------------------------------------------------
PULSE_CHARGE_SEQUENCE = [0, 3]

# ---------------------------------------------------------------------------
# Frequency range and defaults
# ---------------------------------------------------------------------------
MIN_FREQ = 0.1       # Hz
MAX_FREQ = 300.0     # Hz
DEFAULT_FREQ = 1.0   # Hz

# ---------------------------------------------------------------------------
# Dead time inserted between mode transitions (seconds)
# ---------------------------------------------------------------------------
DEAD_TIME = 0.002    # 2 ms

# ---------------------------------------------------------------------------
# Command server
# ---------------------------------------------------------------------------
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5555
