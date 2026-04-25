#ifndef SWITCHING_CIRCUIT_V2_SWITCHING_H
#define SWITCHING_CIRCUIT_V2_SWITCHING_H

#include <stdbool.h>
#include <stdint.h>

#include "config.h"

// Initialize the FET pins (GP2/3/4/5) as outputs, pulled low. Call once
// at boot before any S/G command.
void switching_init(void);

// Direct FET write. The four args mirror set_fets() in main.py:
//   set_fets(P1, P2, N1, N2)
// Auto-halts the periodic timer if it was running (matches the S
// command's behavior).
void switching_set_direct(uint8_t p1, uint8_t p2, uint8_t n1, uint8_t n2);

// All FETs off. Used by H, BOOT, and the cleanup path.
void switching_all_off(void);

// Pack the four current FET pin states into a 4-bit mask.
//   bits: P1<<3 | P2<<2 | N1<<1 | N2
uint8_t switching_get_packed(void);

// Read the four FET pin states into out (size 4). Order matches
// PIN_P1, PIN_P2, PIN_N1, PIN_N2.
void switching_get_states(uint8_t out[4]);

// Apply a packed 4-bit FET state directly. Safe to call from ISR
// context (no allocations, no locks).
void switching_apply_packed(uint8_t packed);

// Program the cycle from C. `n` must be 1..64; each byte must be 0..15.
// Halts any in-flight timer before swapping the buffer (matches main.py).
// Returns false on invalid input — caller emits ERR.
bool switching_program_cycle(const uint8_t *states, uint8_t n);

// Number of states currently programmed.
uint8_t switching_cycle_len(void);

// Set the per-step period in microseconds. Mirrors F. If switching is
// running, the timer is re-armed at the new rate (state index preserved).
// Returns false if the period is invalid (<50 µs).
bool switching_set_period_us(uint32_t period_us);
uint32_t switching_get_period_us(void);

// Start periodic switching. Returns false if cycle is empty or period
// is unset. On success:
//   - state 0 is applied immediately
//   - the firmware ticks_us at the moment state 0 went on the pins is
//     captured into *out_anchor_ticks_us (used by the OK G reply for
//     Pi-side clock anchoring; see main.py:_switching_start docstring).
bool switching_start(uint32_t *out_anchor_ticks_us);

// Halt periodic switching. All FETs go off. Idempotent.
void switching_halt(void);
bool switching_running(void);

// Advance one step manually (used by the K command). Returns the new
// _seq_idx, or -1 if no cycle is loaded.
int  switching_step_once(void);

#endif  // SWITCHING_CIRCUIT_V2_SWITCHING_H
