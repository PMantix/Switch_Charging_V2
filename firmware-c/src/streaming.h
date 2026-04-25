#ifndef SWITCHING_CIRCUIT_V2_STREAMING_H
#define SWITCHING_CIRCUIT_V2_STREAMING_H

#include <stdbool.h>
#include <stdint.h>

#include "config.h"

// Bus-decimation: read bus voltage every Nth shunt sweep. 0 = never.
// Matches main.py's _bus_every (range 0..1000).
void     streaming_set_bus_every(uint16_t every);
uint16_t streaming_get_bus_every(void);

// Set the streaming rate. 0 disables streaming. The actual rate is
// clamped to the current max_hz (which depends on AVG and bus_every and,
// once profile_emit() has run, the measured per-emit time). Returns the
// effective rate in Hz.
float    streaming_set_rate_hz(float hz);
float    streaming_get_rate_hz(void);

// Theoretical / measured ceiling on streaming rate.
float    streaming_max_hz(void);

// Tick: called from the main loop on every iteration. Emits a D-line if
// the streaming interval has elapsed. Cheap when not streaming (one
// time_us_64 + compare).
void     streaming_tick(void);

// One-shot: emit a single D-line synchronously. Used by Z (profiling)
// and as a building block for any future test paths. Always reads bus
// (read_bus=true) so the line carries fresh data regardless of decimation.
void     streaming_emit_one(void);

// Profile emit_stream_line() over `n` iterations and update the
// measured-per-emit cost (which feeds streaming_max_hz()). Returns the
// measured average µs per emit. Suspends streaming for the duration.
//
// Out-params (any may be NULL): per-stage µs estimates so the Z reply
// can carry the breakdown the host wants. We measure total time and
// approximate the i2c / format / write splits using a one-shot
// no-output path; values are µs averaged across n.
uint32_t streaming_profile(uint32_t n,
                           uint32_t *out_avg_us,
                           uint32_t *out_i2c_us,
                           uint32_t *out_fmt_us,
                           uint32_t *out_write_us);

// Burst-recording API. The B command starts a capture, BD downloads it.
bool     streaming_burst_start(uint32_t target_samples);
void     streaming_burst_cancel(void);
bool     streaming_burst_active(void);
uint32_t streaming_burst_count(void);
// Drive burst capture from the main loop. Returns true if the buffer
// just filled (caller emits OK BURST_DONE).
bool     streaming_burst_tick(void);
// Stream the captured rows out as BR lines. Caller is expected to have
// already emitted "OK BD <n>".
void     streaming_burst_dump(void);

// Reset the seq/last_emit state (called when T starts a new stream).
void     streaming_reset(void);

#endif  // SWITCHING_CIRCUIT_V2_STREAMING_H
