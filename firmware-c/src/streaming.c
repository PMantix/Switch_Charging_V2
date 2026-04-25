#include "streaming.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/time.h"

#include "config.h"
#include "ina226.h"
#include "switching.h"
#include "usb_cdc.h"

// ---------------------------------------------------------------------------
// Streaming state
// ---------------------------------------------------------------------------
static float    s_stream_hz       = 0.0f;
static uint64_t s_stream_period_us = 0; // 0 when not streaming
static uint64_t s_last_emit_us    = 0;
static uint16_t s_bus_every       = 1;
static uint16_t s_bus_counter     = 0;
static uint32_t s_measured_emit_us = 0; // updated by streaming_profile()
static uint32_t s_seq             = 0;  // emit counter, wraps at 2^32; emitted as the second D-line field

// Theoretical defaults (mirror the MicroPython _I2C_READ_US / _EMIT_OVERHEAD_US).
// These are only used until the first Z command runs.
#define I2C_READ_US_EST     60u   // 1 MHz bus theoretical
#define EMIT_OVERHEAD_US_EST 200u // printf to USB CDC + main-loop poll

// ---------------------------------------------------------------------------
// Burst buffer (RAM-resident, downloaded by BD).
// Each row: 8 bytes timestamp + 4 floats v + 4 floats i + 1 byte fets
//          = 8 + 32 + 1 = 41 B/row -> ~123 KB at 3000 rows.
// RP2040 has 264 KB SRAM, plenty of headroom even with stack/.bss.
// ---------------------------------------------------------------------------
typedef struct __attribute__((packed)) {
    uint64_t        ts_us;
    ina226_reading_t r[INA226_NUM_SENSORS];
    uint8_t         fets_packed;
} burst_row_t;

static burst_row_t s_burst[BURST_MAX_SAMPLES];
static uint32_t    s_burst_target = 0;
static uint32_t    s_burst_count  = 0;
static bool        s_burst_active = false;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
static inline uint32_t ticks_us_now_u32(void) {
    return (uint32_t)(time_us_64() & 0xFFFFFFFFull);
}

static bool decide_read_bus(void) {
    if (s_bus_every == 0) return false;
    if (++s_bus_counter >= s_bus_every) {
        s_bus_counter = 0;
        return true;
    }
    return false;
}

// Format + write a D line. Stamping ticks_us BEFORE the I2C sweep matches
// the MicroPython behavior; this is the sample-capture timestamp the Pi
// anchors recorded rows to.
static void emit_d_line(bool read_bus) {
    ina226_reading_t r[INA226_NUM_SENSORS];
    uint32_t t_us = ticks_us_now_u32();
    ina226_read_all_streaming(read_bus, r);
    // Bump seq before formatting so the emitted value matches the row.
    // Wraps at 2^32 (the host parser handles wrap; ~5.4 hr at 100 Hz,
    // ~33 min at 800 Hz).
    s_seq = (s_seq + 1u) & 0xFFFFFFFFu;
    // Wire format MUST match server/gpio_driver.py:_handle_stream_line:
    //   D <t_us> <seq> <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>\n
    // 11 whitespace fields total, 4-decimal voltages, 6-decimal currents.
    printf("D %lu %lu %.4f %.6f %.4f %.6f %.4f %.6f %.4f %.6f\n",
           (unsigned long)t_us,
           (unsigned long)s_seq,
           (double)r[0].bus_v, (double)r[0].current_a,
           (double)r[1].bus_v, (double)r[1].current_a,
           (double)r[2].bus_v, (double)r[2].current_a,
           (double)r[3].bus_v, (double)r[3].current_a);
}

// ---------------------------------------------------------------------------
// Public: bus decimation
// ---------------------------------------------------------------------------
void streaming_set_bus_every(uint16_t every) {
    s_bus_every   = every;
    s_bus_counter = 0;
}
uint16_t streaming_get_bus_every(void) { return s_bus_every; }

// ---------------------------------------------------------------------------
// Public: rate
// ---------------------------------------------------------------------------
void streaming_invalidate_measurement(void) {
    s_measured_emit_us = 0;
}

float streaming_max_hz(void) {
    if (s_measured_emit_us > 0) {
        return 1000000.0f / (float)s_measured_emit_us;
    }
    uint32_t conv_us = (uint32_t)ina226_get_avg() * (INA226_VSHCT_US + INA226_VBUSCT_US);
    uint32_t i2c_us  = 4u * I2C_READ_US_EST;
    if (s_bus_every > 0) {
        i2c_us += (4u * I2C_READ_US_EST) / s_bus_every;
    }
    uint32_t period = (conv_us > i2c_us ? conv_us : i2c_us) + EMIT_OVERHEAD_US_EST;
    if (period == 0) period = 1;
    return 1000000.0f / (float)period;
}

float streaming_set_rate_hz(float hz) {
    if (hz <= 0.0f) {
        s_stream_hz        = 0.0f;
        s_stream_period_us = 0;
        return 0.0f;
    }
    float cap = streaming_max_hz();
    if (hz > cap) hz = cap;
    s_stream_hz        = hz;
    s_stream_period_us = (uint64_t)(1000000.0f / hz);
    if (s_stream_period_us == 0) s_stream_period_us = 1;
    s_last_emit_us = time_us_64();
    s_seq          = 0;
    return hz;
}

float streaming_get_rate_hz(void) { return s_stream_hz; }

void streaming_reset(void) {
    s_seq          = 0;
    s_last_emit_us = time_us_64();
    s_bus_counter  = 0;
}

// ---------------------------------------------------------------------------
// Public: tick (called every main-loop iteration)
// ---------------------------------------------------------------------------
void streaming_tick(void) {
    if (s_stream_period_us == 0) return;
    uint64_t now = time_us_64();
    if (now - s_last_emit_us < s_stream_period_us) return;
    s_last_emit_us = now;
    emit_d_line(decide_read_bus());
}

void streaming_emit_one(void) {
    emit_d_line(decide_read_bus());
}

// ---------------------------------------------------------------------------
// Public: profiling
// ---------------------------------------------------------------------------
uint32_t streaming_profile(uint32_t n,
                           uint32_t *out_avg_us,
                           uint32_t *out_i2c_us,
                           uint32_t *out_fmt_us,
                           uint32_t *out_write_us) {
    if (n < 10)  n = 10;
    if (n > 500) n = 500;
    // Suspend streaming so we don't double-emit while measuring.
    float prev_hz = s_stream_hz;
    s_stream_hz        = 0.0f;
    s_stream_period_us = 0;

    // 1) Full emit (i2c + format + write). This is what the live stream
    //    actually does, so it's the authoritative cost.
    uint64_t t0 = time_us_64();
    for (uint32_t i = 0; i < n; ++i) {
        emit_d_line(decide_read_bus());
    }
    uint64_t t1 = time_us_64();
    uint32_t total_avg_us = (uint32_t)((t1 - t0) / n);

    // 2) i2c-only path: read all sensors n times, no format, no write.
    uint64_t t2 = time_us_64();
    for (uint32_t i = 0; i < n; ++i) {
        ina226_reading_t r[INA226_NUM_SENSORS];
        ina226_read_all_streaming(decide_read_bus(), r);
        // Suppress unused-variable warning under -Werror.
        (void)r;
    }
    uint64_t t3 = time_us_64();
    uint32_t i2c_avg_us = (uint32_t)((t3 - t2) / n);

    // 3) format-only path: snprintf into a scratch buffer, no write.
    char scratch[160];
    uint64_t t4 = time_us_64();
    for (uint32_t i = 0; i < n; ++i) {
        snprintf(scratch, sizeof(scratch),
                 "D %lu %lu %.4f %.6f %.4f %.6f %.4f %.6f %.4f %.6f\n",
                 (unsigned long)ticks_us_now_u32(),
                 (unsigned long)i,
                 1.0, 0.001, 1.0, 0.001, 1.0, 0.001, 1.0, 0.001);
    }
    uint64_t t5 = time_us_64();
    uint32_t fmt_avg_us = (uint32_t)((t5 - t4) / n);

    // 4) write residual = total - i2c - fmt (clamped to >=0).
    uint32_t write_avg_us = 0;
    if (total_avg_us > i2c_avg_us + fmt_avg_us) {
        write_avg_us = total_avg_us - i2c_avg_us - fmt_avg_us;
    }

    s_measured_emit_us = total_avg_us;
    if (out_avg_us)   *out_avg_us   = total_avg_us;
    if (out_i2c_us)   *out_i2c_us   = i2c_avg_us;
    if (out_fmt_us)   *out_fmt_us   = fmt_avg_us;
    if (out_write_us) *out_write_us = write_avg_us;

    // Restore (re-clamp to new honest cap).
    if (prev_hz > 0.0f) {
        streaming_set_rate_hz(prev_hz);
    }
    return total_avg_us;
}

// ---------------------------------------------------------------------------
// Burst recording
// ---------------------------------------------------------------------------
bool streaming_burst_start(uint32_t target_samples) {
    if (target_samples == 0) {
        s_burst_active = false;
        s_burst_count  = 0;
        s_burst_target = 0;
        return true;
    }
    if (target_samples > BURST_MAX_SAMPLES) target_samples = BURST_MAX_SAMPLES;
    s_burst_target = target_samples;
    s_burst_count  = 0;
    s_burst_active = true;
    return true;
}

void streaming_burst_cancel(void) {
    s_burst_active = false;
    s_burst_count  = 0;
    s_burst_target = 0;
}

bool     streaming_burst_active(void) { return s_burst_active; }
uint32_t streaming_burst_count(void)  { return s_burst_count; }

bool streaming_burst_tick(void) {
    if (!s_burst_active) return false;
    if (s_burst_count >= s_burst_target) return false;
    burst_row_t *row = &s_burst[s_burst_count];
    row->ts_us       = time_us_64();
    ina226_read_all_fast(row->r);
    row->fets_packed = switching_get_packed();
    s_burst_count++;
    if (s_burst_count >= s_burst_target) {
        s_burst_active = false;
        return true;
    }
    return false;
}

void streaming_burst_dump(void) {
    // Match main.py's BR row format:
    //   BR <ts_us> P1v P1i P2v P2i N1v N1i N2v N2i P1 P2 N1 N2
    // (8 floats followed by 4 FET state ints — main.py uses the live
    // get_fets() ints from the snapshot, which we recover from packed.)
    for (uint32_t i = 0; i < s_burst_count; ++i) {
        const burst_row_t *row = &s_burst[i];
        uint8_t p = row->fets_packed;
        uint8_t p1 = (p >> 3) & 1u;
        uint8_t p2 = (p >> 2) & 1u;
        uint8_t n1 = (p >> 1) & 1u;
        uint8_t n2 =  p       & 1u;
        // ts_us is a u64 captured by time_us_64; the host reads it as
        // an int. main.py uses time.ticks_us() which is u32-wrapping;
        // we widen here for monotonicity across ~71-minute boots. The
        // host parses with int(), which handles 64-bit fine.
        printf("BR %llu %.4f %.6f %.4f %.6f %.4f %.6f %.4f %.6f %u %u %u %u\n",
               (unsigned long long)row->ts_us,
               (double)row->r[0].bus_v, (double)row->r[0].current_a,
               (double)row->r[1].bus_v, (double)row->r[1].current_a,
               (double)row->r[2].bus_v, (double)row->r[2].current_a,
               (double)row->r[3].bus_v, (double)row->r[3].current_a,
               p1, p2, n1, n2);
    }
    // After dumping, drop the buffer (matches main.py "burst_buffer = None").
    s_burst_count = 0;
}
