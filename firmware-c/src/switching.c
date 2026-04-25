#include "switching.h"

#include <stdint.h>
#include <string.h>

#include "hardware/irq.h"
#include "hardware/sync.h"
#include "hardware/timer.h"
#include "pico/stdlib.h"
#include "pico/time.h"

#include "config.h"

// ---------------------------------------------------------------------------
// FET state. Pin order matches main.py SENSOR_ORDER + the set_fets()
// signature (P1, P2, N1, N2). Cached pin numbers so the ISR doesn't
// re-resolve them.
// ---------------------------------------------------------------------------
static const uint8_t k_fet_pins[4] = {PIN_P1, PIN_P2, PIN_N1, PIN_N2};

// Packed cycle (each byte 0..15: bit3=P1, bit2=P2, bit1=N1, bit0=N2).
// Volatile because the ISR reads it concurrently with main-thread writes.
static volatile uint8_t  s_seq[CYCLE_MAX_STATES];
static volatile uint8_t  s_seq_len = 0;
static volatile uint8_t  s_seq_idx = 0;

static volatile uint32_t s_period_us = 0;
static volatile bool     s_running   = false;
static volatile uint32_t s_last_start_ticks_us = 0;

// Repeating timer state. The Pico SDK's add_repeating_timer_us API hands
// us a callback at a fixed interval; that callback runs in alarm-IRQ
// context (default alarm pool), which is what we want for jitter-free
// switching independent of the main loop.
static struct repeating_timer s_timer;
static bool                   s_timer_armed = false;

// ---------------------------------------------------------------------------
// Pin I/O
// ---------------------------------------------------------------------------
static inline void apply_packed_inline(uint8_t b) {
    // Bit order: P1<<3 | P2<<2 | N1<<1 | N2 (matches main.py _apply_packed).
    gpio_put(PIN_P1, (b >> 3) & 1u);
    gpio_put(PIN_P2, (b >> 2) & 1u);
    gpio_put(PIN_N1, (b >> 1) & 1u);
    gpio_put(PIN_N2,  b       & 1u);
}

void switching_apply_packed(uint8_t packed) {
    apply_packed_inline(packed);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
void switching_init(void) {
    for (int i = 0; i < 4; ++i) {
        gpio_init(k_fet_pins[i]);
        gpio_set_dir(k_fet_pins[i], GPIO_OUT);
        gpio_put(k_fet_pins[i], 0);
    }
}

void switching_all_off(void) {
    for (int i = 0; i < 4; ++i) gpio_put(k_fet_pins[i], 0);
}

// ---------------------------------------------------------------------------
// Direct control + state introspection
// ---------------------------------------------------------------------------
void switching_set_direct(uint8_t p1, uint8_t p2, uint8_t n1, uint8_t n2) {
    if (s_running) switching_halt();
    gpio_put(PIN_P1, p1 ? 1 : 0);
    gpio_put(PIN_P2, p2 ? 1 : 0);
    gpio_put(PIN_N1, n1 ? 1 : 0);
    gpio_put(PIN_N2, n2 ? 1 : 0);
}

void switching_get_states(uint8_t out[4]) {
    out[0] = (uint8_t)gpio_get(PIN_P1);
    out[1] = (uint8_t)gpio_get(PIN_P2);
    out[2] = (uint8_t)gpio_get(PIN_N1);
    out[3] = (uint8_t)gpio_get(PIN_N2);
}

uint8_t switching_get_packed(void) {
    uint8_t s[4];
    switching_get_states(s);
    return (uint8_t)((s[0] << 3) | (s[1] << 2) | (s[2] << 1) | s[3]);
}

// ---------------------------------------------------------------------------
// Cycle program / period
// ---------------------------------------------------------------------------
bool switching_program_cycle(const uint8_t *states, uint8_t n) {
    if (n < 1 || n > CYCLE_MAX_STATES) return false;
    for (uint8_t i = 0; i < n; ++i) {
        if (states[i] > 15) return false;
    }
    // Halt BEFORE swapping the buffer so a stale ISR tick can't land
    // mid-update. Matches the C-command race-fix in main.py.
    switching_halt();
    uint32_t prim = save_and_disable_interrupts();
    for (uint8_t i = 0; i < n; ++i) s_seq[i] = states[i];
    s_seq_len = n;
    s_seq_idx = 0;
    restore_interrupts(prim);
    return true;
}

uint8_t switching_cycle_len(void) { return s_seq_len; }

bool switching_set_period_us(uint32_t period_us) {
    if (period_us < 50) return false;
    s_period_us = period_us;
    if (s_running) {
        // Re-arm at the new period; preserve s_seq_idx so the cycle
        // continues from where it was. (Matches the F branch in main.py.)
        uint32_t anchor = 0;
        switching_start(&anchor);
        (void)anchor;
    }
    return true;
}

uint32_t switching_get_period_us(void) { return s_period_us; }

// ---------------------------------------------------------------------------
// ISR
// ---------------------------------------------------------------------------
//
// Fired by repeating_timer at s_period_us. Allocation-free, no division,
// no float — just packed-state read + 4 GPIO writes + index increment.
// Returning true keeps the alarm armed.
static bool tick_isr(struct repeating_timer *t) {
    (void)t;
    uint8_t n = s_seq_len;
    if (n == 0) return true;
    uint8_t idx = s_seq_idx + 1;
    if (idx >= n) idx = 0;
    s_seq_idx = idx;
    apply_packed_inline(s_seq[idx]);
    return true;
}

// ---------------------------------------------------------------------------
// Start / halt
// ---------------------------------------------------------------------------
bool switching_start(uint32_t *out_anchor_ticks_us) {
    if (s_seq_len == 0 || s_period_us == 0) return false;

    // Apply state 0 (or whatever index we're holding) FIRST, then capture
    // the anchor ticks_us as close as possible to that pin write — see
    // the long comment on _switching_start in main.py for why this
    // ordering matters for Pi-side step labelling.
    apply_packed_inline(s_seq[s_seq_idx]);
    uint32_t anchor = (uint32_t)(time_us_64() & 0xFFFFFFFFull);
    s_last_start_ticks_us = anchor;

    if (s_timer_armed) {
        cancel_repeating_timer(&s_timer);
        s_timer_armed = false;
    }
    // add_repeating_timer_us treats negative delay as period-from-completion
    // (zero phase drift) and positive as period-from-start. We want strict
    // periodicity, so pass a negative delay.
    int64_t delay_us = -(int64_t)s_period_us;
    if (!add_repeating_timer_us(delay_us, tick_isr, NULL, &s_timer)) {
        return false;
    }
    s_timer_armed = true;
    s_running     = true;
    if (out_anchor_ticks_us) *out_anchor_ticks_us = anchor;
    return true;
}

void switching_halt(void) {
    if (s_timer_armed) {
        cancel_repeating_timer(&s_timer);
        s_timer_armed = false;
    }
    s_running = false;
    switching_all_off();
}

bool switching_running(void) { return s_running; }

int switching_step_once(void) {
    if (s_seq_len == 0) return -1;
    uint8_t idx = s_seq_idx + 1;
    if (idx >= s_seq_len) idx = 0;
    s_seq_idx = idx;
    apply_packed_inline(s_seq[idx]);
    return (int)idx;
}
