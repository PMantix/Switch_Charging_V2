#ifndef SWITCHING_CIRCUIT_V2_INA226_H
#define SWITCHING_CIRCUIT_V2_INA226_H

#include <stdbool.h>
#include <stdint.h>

#include "config.h"

// One sensor's reading in float volts/amps.
typedef struct {
    float bus_v;
    float current_a;
} ina226_reading_t;

// Bring up the I2C bus, scan for the four INA226s at the known addresses,
// and apply the current AVG/CT config to whichever ones answer. Safe to
// call multiple times (used by the R command). Returns the number of
// sensors that responded.
int ina226_scan(void);

// Re-program every present sensor with the current cached AVG.
void ina226_apply_all(void);

// Get/set the cached AVG count. Setter validates against the legal table
// (1, 4, 16, 64, 128, 256, 512, 1024) — returns false on bad input.
uint16_t ina226_get_avg(void);
bool     ina226_set_avg(uint16_t avg);

// CNVR (alert pin → conversion-ready) plumbing. Off by default per the
// task brief; the N command toggles it but the hot path doesn't care.
bool ina226_get_cnvr_enabled(void);
void ina226_set_cnvr_enabled(bool enabled);

// One-shot read of all four sensors (always reads bus + shunt). Missing
// sensors come back as 0.0 / 0.0. Used by I and the burst path.
void ina226_read_all_fast(ina226_reading_t out[INA226_NUM_SENSORS]);

// Streaming-path read. Always reads shunt; reads bus only when read_bus
// is true. When read_bus is false, the cached bus voltage from the last
// successful bus read fills the slot so the D-line schema stays fixed.
void ina226_read_all_streaming(bool read_bus,
                               ina226_reading_t out[INA226_NUM_SENSORS]);

// Per-sensor presence flag, indexed by sensor_idx_t.
bool ina226_is_present(sensor_idx_t which);

// Sensor name table (used for R reply formatting).
const char *ina226_name(sensor_idx_t which);
uint8_t     ina226_address(sensor_idx_t which);

#endif  // SWITCHING_CIRCUIT_V2_INA226_H
