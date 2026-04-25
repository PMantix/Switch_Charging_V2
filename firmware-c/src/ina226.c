#include "ina226.h"

#include <stdint.h>
#include <string.h>

#include "hardware/i2c.h"
#include "pico/stdlib.h"

#include "config.h"

// ---------------------------------------------------------------------------
// Per-sensor static metadata. Order MUST match sensor_idx_t / SENSOR_ORDER
// in the MicroPython firmware so the D-line column layout is unchanged.
// ---------------------------------------------------------------------------
static const char *const k_names[INA226_NUM_SENSORS] = {"P1", "P2", "N1", "N2"};
static const uint8_t k_addrs[INA226_NUM_SENSORS] = {
    INA226_ADDR_P1, INA226_ADDR_P2, INA226_ADDR_N1, INA226_ADDR_N2,
};

// AVG sample-count table (8 INA226 codes).
static const uint16_t k_avg_values[8] = {1, 4, 16, 64, 128, 256, 512, 1024};

// ---------------------------------------------------------------------------
// Mutable state
// ---------------------------------------------------------------------------
static bool     s_present[INA226_NUM_SENSORS] = {false, false, false, false};
static uint16_t s_avg          = 4;     // matches main.py default
static bool     s_cnvr_enabled = false; // off by default (CNVR isn't on hot path)
static float    s_last_bus_v[INA226_NUM_SENSORS] = {0.0f, 0.0f, 0.0f, 0.0f};
static bool     s_i2c_ready    = false;

// ---------------------------------------------------------------------------
// Low-level I2C helpers. Pico SDK i2c funcs return PICO_ERROR_TIMEOUT (-2)
// or the byte count on success. We treat anything negative as a bus error.
// ---------------------------------------------------------------------------
static bool i2c_write_reg(uint8_t addr, uint8_t reg, uint16_t value) {
    uint8_t buf[3] = {reg, (uint8_t)((value >> 8) & 0xFFu),
                      (uint8_t)(value & 0xFFu)};
    int n = i2c_write_blocking(I2C_PORT, addr, buf, sizeof(buf), false);
    return n == (int)sizeof(buf);
}

static bool i2c_read_reg(uint8_t addr, uint8_t reg, uint16_t *out) {
    uint8_t r = reg;
    int n = i2c_write_blocking(I2C_PORT, addr, &r, 1, true /* nostop */);
    if (n != 1) return false;
    uint8_t buf[2];
    n = i2c_read_blocking(I2C_PORT, addr, buf, 2, false);
    if (n != 2) return false;
    *out = (uint16_t)((buf[0] << 8) | buf[1]);
    return true;
}

static bool i2c_read_two_bytes(uint8_t addr, uint8_t reg, uint16_t *out) {
    return i2c_read_reg(addr, reg, out);
}

// ---------------------------------------------------------------------------
// CONFIG word builder. Encodes the cached AVG with the fixed VSHCT/VBUSCT/MODE.
// ---------------------------------------------------------------------------
static uint16_t avg_to_code(uint16_t avg) {
    for (uint16_t i = 0; i < 8; ++i) {
        if (k_avg_values[i] == avg) return i;
    }
    return 1;  // fallback to AVG=4
}

static uint16_t build_config(uint16_t avg) {
    uint16_t avg_code = avg_to_code(avg);
    return (uint16_t)(((avg_code & 0x07u) << 9)
                    | ((INA226_VBUSCT_CODE & 0x07u) << 6)
                    | ((INA226_VSHCT_CODE  & 0x07u) << 3)
                    | (INA226_MODE_CONT    & 0x07u));
}

// ---------------------------------------------------------------------------
// I2C bus init (idempotent)
// ---------------------------------------------------------------------------
static void ensure_i2c_ready(void) {
    if (s_i2c_ready) return;
    i2c_init(I2C_PORT, I2C_BAUD_HZ);
    gpio_set_function(PIN_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_SCL, GPIO_FUNC_I2C);
    // Internal pull-ups — the breadboard relies on these per the
    // MicroPython firmware comment; add external 2.2 kΩ if 1 MHz looks
    // glitchy on a scope.
    gpio_pull_up(PIN_SDA);
    gpio_pull_up(PIN_SCL);
    s_i2c_ready = true;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
int ina226_scan(void) {
    ensure_i2c_ready();
    int found = 0;
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        uint16_t die_id = 0;
        s_present[i] = false;
        if (!i2c_read_reg(k_addrs[i], INA226_REG_DIE_ID, &die_id)) {
            continue;
        }
        if (!i2c_write_reg(k_addrs[i], INA226_REG_CONFIG, build_config(s_avg))) {
            continue;
        }
        s_present[i] = true;
        ++found;
    }
    return found;
}

void ina226_apply_all(void) {
    uint16_t cfg = build_config(s_avg);
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        if (!s_present[i]) continue;
        // Best-effort — silently skip on bus errors; main loop's R command
        // can re-scan if a sensor drops off.
        (void)i2c_write_reg(k_addrs[i], INA226_REG_CONFIG, cfg);
    }
}

uint16_t ina226_get_avg(void) { return s_avg; }

bool ina226_set_avg(uint16_t avg) {
    for (uint16_t i = 0; i < 8; ++i) {
        if (k_avg_values[i] == avg) {
            s_avg = avg;
            ina226_apply_all();
            return true;
        }
    }
    return false;
}

bool ina226_get_cnvr_enabled(void) { return s_cnvr_enabled; }

void ina226_set_cnvr_enabled(bool enabled) {
    s_cnvr_enabled = enabled;
    // Update MASK_ENABLE on every present sensor. We only touch the CNVR
    // bit; other bits stay at INA226 defaults (0). CNVR is OFF by default
    // per the task brief — flipping this bit doesn't change the hot path,
    // it only governs whether the ALERT pin pulses on conversion-ready.
    uint16_t mask = enabled ? INA226_MASK_CNVR_BIT : 0;
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        if (!s_present[i]) continue;
        (void)i2c_write_reg(k_addrs[i], INA226_REG_MASK_EN, mask);
    }
}

static inline float shunt_raw_to_amps(uint16_t raw) {
    int16_t signed_raw = (int16_t)raw;  // INA226 shunt is two's-complement
    return ((float)signed_raw * INA226_SHUNT_V_LSB) / SHUNT_RESISTOR_OHM;
}

static inline float bus_raw_to_volts(uint16_t raw) {
    return (float)raw * INA226_BUS_V_LSB;
}

void ina226_read_all_fast(ina226_reading_t out[INA226_NUM_SENSORS]) {
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        out[i].bus_v = 0.0f;
        out[i].current_a = 0.0f;
        if (!s_present[i]) continue;
        uint16_t bv = 0, sv = 0;
        if (!i2c_read_two_bytes(k_addrs[i], INA226_REG_BUS_V, &bv)) continue;
        if (!i2c_read_two_bytes(k_addrs[i], INA226_REG_SHUNT_V, &sv)) continue;
        out[i].bus_v = bus_raw_to_volts(bv);
        out[i].current_a = shunt_raw_to_amps(sv);
        s_last_bus_v[i] = out[i].bus_v;
    }
}

void ina226_read_all_streaming(bool read_bus,
                               ina226_reading_t out[INA226_NUM_SENSORS]) {
    for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
        out[i].bus_v = 0.0f;
        out[i].current_a = 0.0f;
        if (!s_present[i]) continue;
        uint16_t sv = 0;
        if (!i2c_read_two_bytes(k_addrs[i], INA226_REG_SHUNT_V, &sv)) continue;
        out[i].current_a = shunt_raw_to_amps(sv);
        if (read_bus) {
            uint16_t bv = 0;
            if (i2c_read_two_bytes(k_addrs[i], INA226_REG_BUS_V, &bv)) {
                s_last_bus_v[i] = bus_raw_to_volts(bv);
            }
        }
        out[i].bus_v = s_last_bus_v[i];
    }
}

bool ina226_is_present(sensor_idx_t which) {
    if ((int)which < 0 || (int)which >= INA226_NUM_SENSORS) return false;
    return s_present[which];
}

const char *ina226_name(sensor_idx_t which) {
    if ((int)which < 0 || (int)which >= INA226_NUM_SENSORS) return "??";
    return k_names[which];
}

uint8_t ina226_address(sensor_idx_t which) {
    if ((int)which < 0 || (int)which >= INA226_NUM_SENSORS) return 0;
    return k_addrs[which];
}
