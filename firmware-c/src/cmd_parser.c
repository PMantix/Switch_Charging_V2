#include "cmd_parser.h"

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>  // strncasecmp

#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"
#include "pico/time.h"
#include "pico/unique_id.h"

#include "config.h"
#include "ina226.h"
#include "neopixel.h"
#include "streaming.h"
#include "switching.h"
#include "usb_cdc.h"

// ---------------------------------------------------------------------------
// Tiny tokenizer. We avoid strtok_r-with-state because it mutates the input
// in-place and we want to keep our caller's buffer pristine. Instead, walk
// the line and copy tokens into a fixed-size scratch array.
// ---------------------------------------------------------------------------
#define MAX_TOKENS 70u   // C requires up to 1 + 1 + 64 = 66 tokens; leave headroom

typedef struct {
    const char *p;     // start of token within the (mutated) buffer
    size_t      len;
} token_t;

// Mutates `buf` (replaces whitespace runs with NULs). Returns token count.
static size_t tokenize(char *buf, token_t *toks, size_t max_toks) {
    size_t n = 0;
    char  *s = buf;
    while (*s && n < max_toks) {
        while (*s && isspace((unsigned char)*s)) ++s;
        if (!*s) break;
        toks[n].p = s;
        while (*s && !isspace((unsigned char)*s)) ++s;
        toks[n].len = (size_t)(s - toks[n].p);
        if (*s) {
            *s = '\0';
            ++s;
        }
        ++n;
    }
    return n;
}

static int parse_int_tok(const token_t *t, int *out) {
    char *end = NULL;
    long  v   = strtol(t->p, &end, 10);
    if (end == t->p || (end && *end != '\0')) return -1;
    if (v < INT32_MIN || v > INT32_MAX) return -1;
    *out = (int)v;
    return 0;
}

static int parse_float_tok(const token_t *t, float *out) {
    char *end = NULL;
    double v  = strtod(t->p, &end);
    if (end == t->p || (end && *end != '\0')) return -1;
    *out = (float)v;
    return 0;
}

// ---------------------------------------------------------------------------
// Command implementations. Each returns nothing — replies are printed
// directly so we control exact formatting.
// ---------------------------------------------------------------------------
static void cmd_S(size_t nt, const token_t *tk) {
    if (nt != 5) {
        puts("ERR S requires 4 args: S <P1> <P2> <N1> <N2>");
        return;
    }
    int v[4];
    for (int i = 0; i < 4; ++i) {
        if (parse_int_tok(&tk[i + 1], &v[i]) != 0) {
            puts("ERR S args must be 0/1");
            return;
        }
        v[i] &= 1;
    }
    switching_set_direct((uint8_t)v[0], (uint8_t)v[1], (uint8_t)v[2], (uint8_t)v[3]);
    if (v[0] || v[1] || v[2] || v[3]) {
        neopixel_set_rgb(0, 4, 0);
    } else {
        neopixel_set_rgb(0, 0, 2);
    }
    uint8_t s[4];
    switching_get_states(s);
    printf("OK S %u %u %u %u\n", s[0], s[1], s[2], s[3]);
}

static void cmd_Q(void) {
    uint8_t s[4];
    switching_get_states(s);
    printf("OK Q %u %u %u %u\n", s[0], s[1], s[2], s[3]);
}

static void cmd_J(void) {
    pico_unique_board_id_t uid;
    pico_get_unique_board_id(&uid);
    char hex[2 * sizeof(uid.id) + 1];
    for (size_t i = 0; i < sizeof(uid.id); ++i) {
        snprintf(hex + 2 * i, 3, "%02x", uid.id[i]);
    }
    printf("OK J %s %s %s %s\n",
           FW_BUILD_NAME, FW_BUILD_VARIANT, FW_BUILD_VERSION, hex);
}

static void cmd_I(void) {
    ina226_reading_t r[INA226_NUM_SENSORS];
    ina226_read_all_fast(r);
    // Match main.py: OK I <json>. Keep keys in SENSOR_ORDER.
    // We only emit entries for sensors that are present (matches main.py).
    fputs("OK I {", stdout);
    bool first = true;
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        if (!ina226_is_present((sensor_idx_t)i)) continue;
        if (!first) fputs(", ", stdout);
        printf("\"%s\": {\"voltage\": %.4f, \"current\": %.6f}",
               ina226_name((sensor_idx_t)i),
               (double)r[i].bus_v, (double)r[i].current_a);
        first = false;
    }
    fputs("}\n", stdout);
}

static void cmd_T(size_t nt, const token_t *tk) {
    if (nt < 2) { puts("ERR T requires 1 arg: T <hz>"); return; }
    float hz;
    if (parse_float_tok(&tk[1], &hz) != 0) {
        puts("ERR T hz must be numeric");
        return;
    }
    float actual = streaming_set_rate_hz(hz);
    if (actual <= 0.0f) {
        neopixel_set_rgb(0, 0, 2);
        puts("OK T 0");
    } else {
        neopixel_set_rgb(0, 4, 4);
        printf("OK T %.1f\n", (double)actual);
    }
}

static void cmd_A(size_t nt, const token_t *tk) {
    if (nt != 2) { puts("ERR A requires 1 arg: A <avg>"); return; }
    int avg;
    if (parse_int_tok(&tk[1], &avg) != 0) {
        puts("ERR A avg must be integer");
        return;
    }
    if (!ina226_set_avg((uint16_t)avg)) {
        puts("ERR A avg must be one of 1/4/16/64/128/256/512/1024");
        return;
    }
    streaming_invalidate_measurement();
    float cap = streaming_max_hz();
    if (streaming_get_rate_hz() > cap) streaming_set_rate_hz(cap);
    printf("OK A %u %.1f\n", (unsigned)ina226_get_avg(), (double)cap);
}

static void cmd_V(size_t nt, const token_t *tk) {
    if (nt != 2) { puts("ERR V requires 1 arg: V <every>"); return; }
    int every;
    if (parse_int_tok(&tk[1], &every) != 0) {
        puts("ERR V every must be integer");
        return;
    }
    if (every < 0 || every > 1000) {
        puts("ERR V every must be 0..1000");
        return;
    }
    streaming_set_bus_every((uint16_t)every);
    streaming_invalidate_measurement();
    float cap = streaming_max_hz();
    if (streaming_get_rate_hz() > cap) streaming_set_rate_hz(cap);
    printf("OK V %u %.1f\n", (unsigned)streaming_get_bus_every(), (double)cap);
}

static void cmd_M(void) {
    float cap = streaming_max_hz();
    printf("OK M %u %u %.1f\n",
           (unsigned)ina226_get_avg(),
           (unsigned)streaming_get_bus_every(),
           (double)cap);
}

static void cmd_Z(size_t nt, const token_t *tk) {
    int n = 50;
    if (nt == 2) {
        int parsed;
        if (parse_int_tok(&tk[1], &parsed) == 0) n = parsed;
    }
    uint32_t avg_us = 0, i2c_us = 0, fmt_us = 0, write_us = 0;
    streaming_profile((uint32_t)n, &avg_us, &i2c_us, &fmt_us, &write_us);
    float cap = streaming_max_hz();
    // New extended Z reply (per task brief): OK Z <n> <avg_us> <i2c_us>
    // <fmt_us> <write_us> <max_hz>. The host doesn't parse Z, so the
    // wider format is safe to introduce here.
    printf("OK Z %d %u %u %u %u %.1f\n",
           n, (unsigned)avg_us, (unsigned)i2c_us,
           (unsigned)fmt_us, (unsigned)write_us, (double)cap);
}

static void cmd_L(size_t nt, const token_t *tk) {
    if (nt != 4) { puts("ERR L requires 3 args: L <R> <G> <B>"); return; }
    int r, g, b;
    if (parse_int_tok(&tk[1], &r) != 0 ||
        parse_int_tok(&tk[2], &g) != 0 ||
        parse_int_tok(&tk[3], &b) != 0) {
        puts("ERR L components must be integer 0..255");
        return;
    }
    if (r < 0) r = 0; if (r > 255) r = 255;
    if (g < 0) g = 0; if (g > 255) g = 255;
    if (b < 0) b = 0; if (b > 255) b = 255;
    neopixel_set_rgb((uint8_t)r, (uint8_t)g, (uint8_t)b);
    puts("OK L");
}

static void cmd_P(void) {
    // OK P <ticks_us>. ticks_us is u32-wrapped time_us_64 to match the
    // MicroPython firmware's time.ticks_us() semantics.
    uint32_t t = (uint32_t)(time_us_64() & 0xFFFFFFFFull);
    printf("OK P %lu\n", (unsigned long)t);
}

static void cmd_B(size_t nt, const token_t *tk) {
    if (nt < 2) { puts("ERR B requires 1 arg: B <count>"); return; }
    int count;
    if (parse_int_tok(&tk[1], &count) != 0) {
        puts("ERR B count must be integer");
        return;
    }
    if (count <= 0) {
        streaming_burst_cancel();
        puts("OK B 0");
        return;
    }
    if ((uint32_t)count > BURST_MAX_SAMPLES) count = (int)BURST_MAX_SAMPLES;
    streaming_burst_start((uint32_t)count);
    neopixel_set_rgb(10, 0, 4);
    printf("OK B %d\n", count);
}

static void cmd_BD(void) {
    if (streaming_burst_active()) {
        puts("ERR burst still recording");
        return;
    }
    uint32_t n = streaming_burst_count();
    if (n == 0) {
        puts("ERR no burst data");
        return;
    }
    printf("OK BD %lu\n", (unsigned long)n);
    streaming_burst_dump();
}

static void cmd_R(void) {
    int found = ina226_scan();
    (void)found;
    // Build "P1@0x40, P2@0x41, ..." — only present sensors.
    char buf[128];
    size_t off = 0;
    bool first = true;
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        if (!ina226_is_present((sensor_idx_t)i)) continue;
        if (!first) {
            int w = snprintf(buf + off, sizeof(buf) - off, ", ");
            if (w < 0) break;
            off += (size_t)w;
        }
        int w = snprintf(buf + off, sizeof(buf) - off, "%s@0x%02X",
                         ina226_name((sensor_idx_t)i),
                         (unsigned)ina226_address((sensor_idx_t)i));
        if (w < 0) break;
        off += (size_t)w;
        first = false;
        if (off >= sizeof(buf) - 1) break;
    }
    printf("OK R %s\n", buf);
}

static void cmd_X(void) {
    // Soft reset via watchdog (matches main.py machine.reset()).
    puts("OK X");
    sleep_ms(20);  // give the host time to drain the OK before we vanish
    watchdog_reboot(0, 0, 1);
    while (1) tight_loop_contents();
}

static void cmd_X_BOOTSEL(void) {
    // Reboot to USB BOOTSEL — handy for in-field re-flash without the BOOT
    // button. Not in main.py but called for explicitly in the brief.
    puts("OK X BOOTSEL");
    sleep_ms(20);
    reset_usb_boot(0, 0);
}

static void cmd_N(size_t nt, const token_t *tk) {
    if (nt != 2) { puts("ERR N requires 1 arg: N <0|1>"); return; }
    int v;
    if (parse_int_tok(&tk[1], &v) != 0) {
        puts("ERR N arg must be 0 or 1");
        return;
    }
    bool en = (v != 0);
    ina226_set_cnvr_enabled(en);
    printf("OK N %d\n", en ? 1 : 0);
}

static void cmd_C(size_t nt, const token_t *tk) {
    if (nt < 2) { puts("ERR C requires count: C <n> <s1> ... <sn>"); return; }
    int n;
    if (parse_int_tok(&tk[1], &n) != 0) {
        puts("ERR C count must be integer");
        return;
    }
    if (n < 1 || n > (int)CYCLE_MAX_STATES) {
        printf("ERR C count must be 1..%u\n", (unsigned)CYCLE_MAX_STATES);
        return;
    }
    if ((int)nt != 2 + n) {
        printf("ERR C expected %d states, got %d\n", n, (int)nt - 2);
        return;
    }
    uint8_t states[CYCLE_MAX_STATES];
    for (int i = 0; i < n; ++i) {
        int v;
        if (parse_int_tok(&tk[2 + i], &v) != 0) {
            printf("ERR C state %d not numeric\n", i);
            return;
        }
        if (v < 0 || v > 15) {
            printf("ERR C state %d out of range 0..15\n", i);
            return;
        }
        states[i] = (uint8_t)v;
    }
    if (!switching_program_cycle(states, (uint8_t)n)) {
        puts("ERR C cycle program failed");
        return;
    }
    printf("OK C %d\n", n);
}

static void cmd_F(size_t nt, const token_t *tk) {
    if (nt != 2) { puts("ERR F requires 1 arg: F <period_us>"); return; }
    int us;
    if (parse_int_tok(&tk[1], &us) != 0) {
        puts("ERR F period_us must be integer");
        return;
    }
    if (us < 50) {
        puts("ERR F period_us must be >= 50");
        return;
    }
    if (!switching_set_period_us((uint32_t)us)) {
        puts("ERR F set_period failed");
        return;
    }
    printf("OK F %d\n", us);
}

static void cmd_G(size_t nt, const token_t *tk) {
    (void)nt; (void)tk;  // optional ticks_us hint per brief; firmware
                          // ignores it (no use today, would be advisory).
    uint32_t anchor = 0;
    if (!switching_start(&anchor)) {
        puts("ERR G requires C and F first");
        return;
    }
    neopixel_set_rgb(4, 0, 4);
    // OK G <period_us> <n_states> <anchor_ticks_us>  (matches main.py).
    printf("OK G %lu %u %lu\n",
           (unsigned long)switching_get_period_us(),
           (unsigned)switching_cycle_len(),
           (unsigned long)anchor);
}

static void cmd_H(void) {
    switching_halt();
    neopixel_set_rgb(0, 0, 2);
    puts("OK H");
}

static void cmd_K(void) {
    // K is two things in different parts of the brief: a per-step debug
    // tick (main.py) AND a clock-sync ping (task brief). main.py's
    // behavior is the only one the host parser cares about today; we
    // preserve it so debug stepping keeps working. The clock-sync use
    // case is already covered by P (which already returns ticks_us).
    int idx = switching_step_once();
    if (idx < 0) {
        puts("ERR K requires C first");
        return;
    }
    printf("OK K %d\n", idx);
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------
void cmd_parser_handle_line(const char *line, size_t len) {
    if (len == 0) return;
    // Local mutable copy for tokenize().
    static char  scratch[512];
    if (len >= sizeof(scratch)) len = sizeof(scratch) - 1;
    memcpy(scratch, line, len);
    scratch[len] = '\0';

    token_t toks[MAX_TOKENS];
    size_t  nt = tokenize(scratch, toks, MAX_TOKENS);
    if (nt == 0) return;

    // Uppercase the verb in place.
    char *verb = (char *)toks[0].p;
    for (size_t i = 0; i < toks[0].len; ++i) {
        verb[i] = (char)toupper((unsigned char)verb[i]);
    }

    // Two-letter verbs first (BD).
    if (toks[0].len == 2 && verb[0] == 'B' && verb[1] == 'D') { cmd_BD();   return; }

    if (toks[0].len != 1) {
        printf("ERR unknown command: %s\n", verb);
        return;
    }

    switch (verb[0]) {
        case 'S': cmd_S(nt, toks); return;
        case 'Q': cmd_Q();         return;
        case 'J': cmd_J();         return;
        case 'I': cmd_I();         return;
        case 'T': cmd_T(nt, toks); return;
        case 'A': cmd_A(nt, toks); return;
        case 'V': cmd_V(nt, toks); return;
        case 'M': cmd_M();         return;
        case 'Z': cmd_Z(nt, toks); return;
        case 'L': cmd_L(nt, toks); return;
        case 'P': cmd_P();         return;
        case 'B': cmd_B(nt, toks); return;
        case 'R': cmd_R();         return;
        case 'X':
            // Optional payload: `X BOOTSEL` reboots into BOOTSEL mode.
            if (nt >= 2 && toks[1].len == 7 &&
                strncasecmp(toks[1].p, "BOOTSEL", 7) == 0) {
                cmd_X_BOOTSEL();
            } else {
                cmd_X();
            }
            return;
        case 'N': cmd_N(nt, toks); return;
        case 'C': cmd_C(nt, toks); return;
        case 'F': cmd_F(nt, toks); return;
        case 'G': cmd_G(nt, toks); return;
        case 'H': cmd_H();         return;
        case 'K': cmd_K();         return;
        default:
            printf("ERR unknown command: %c\n", verb[0]);
            return;
    }
}
