#ifndef SWITCHING_CIRCUIT_V2_CONFIG_H
#define SWITCHING_CIRCUIT_V2_CONFIG_H

#include <stdint.h>

// ---------------------------------------------------------------------------
// Pin assignments — match firmware/main.py exactly so the Pi-side parser
// (server/gpio_driver.py) sees identical electrical behavior. The MEMORY.md
// task brief mentions GP0/GP1 for P1/N1, but the live firmware (and the
// schematic-on-disk) uses GP2/GP3/GP4/GP5. We follow the live firmware.
// ---------------------------------------------------------------------------
#define PIN_P1        2u   // GP2 → UCC5304 U1 → P1 high-side
#define PIN_P2        3u   // GP3 → UCC5304 U2 → P2 high-side
#define PIN_N1        4u   // GP4 → UCC5304 U3 → N1 low-side
#define PIN_N2        5u   // GP5 → UCC5304 U4 → N2 low-side

#define PIN_SDA       6u   // GP6 → INA226 I2C SDA (i2c1)
#define PIN_SCL       7u   // GP7 → INA226 I2C SCL (i2c1)

#define PIN_NEOPIXEL  16u  // GP16 → onboard WS2812
#define PIN_INA_ALERT 27u  // GP27 → wired-OR INA226 ALERT (open-drain)

// I2C bus selection (i2c1 because GP6/GP7 are on i2c1).
#define I2C_PORT      i2c1
#define I2C_BAUD_HZ   1000000u   // 1 MHz Fm+ — matches MicroPython firmware

// ---------------------------------------------------------------------------
// INA226 addresses (7-bit). These match the schematic + main.py.
// ---------------------------------------------------------------------------
#define INA226_ADDR_P1 0x40u
#define INA226_ADDR_P2 0x41u
#define INA226_ADDR_N1 0x43u
#define INA226_ADDR_N2 0x45u

#define INA226_NUM_SENSORS 4

// Sensor index order (matches SENSOR_ORDER in main.py).
typedef enum {
    SENSOR_P1 = 0,
    SENSOR_P2 = 1,
    SENSOR_N1 = 2,
    SENSOR_N2 = 3,
} sensor_idx_t;

// INA226 register addresses (TI SBOS547A).
#define INA226_REG_CONFIG  0x00u
#define INA226_REG_SHUNT_V 0x01u
#define INA226_REG_BUS_V   0x02u
#define INA226_REG_MASK_EN 0x06u
#define INA226_REG_DIE_ID  0xFFu

// INA226 LSB constants.
#define INA226_BUS_V_LSB    1.25e-3f   // V/bit
#define INA226_SHUNT_V_LSB  2.5e-6f    // V/bit
#define SHUNT_RESISTOR_OHM  0.1f       // 100 mΩ

// CONFIG register field codes.
//   AVG    bits [11:9]  (1, 4, 16, 64, 128, 256, 512, 1024)
//   VBUSCT bits [8:6]   (140, 204, 332, 588, 1100, 2116, 4156, 8244 µs)
//   VSHCT  bits [5:3]   (same codes)
//   MODE   bits [2:0]   (0b111 = continuous shunt+bus)
#define INA226_VSHCT_CODE   0b010u   // 332 µs
#define INA226_VBUSCT_CODE  0b010u   // 332 µs
#define INA226_VSHCT_US     332u
#define INA226_VBUSCT_US    332u
#define INA226_MODE_CONT    0b111u

// MASK_ENABLE bits.
#define INA226_MASK_CNVR_BIT  (1u << 10)
#define INA226_MASK_CVRF_BIT  (1u << 3)

// Build identifier — printed by the J command.
#define FW_BUILD_NAME    "switching_circuit_v2_fw"
#define FW_BUILD_VARIANT "pico-sdk-c"
#define FW_BUILD_VERSION "0.1.0"

// Burst-recording capacity (matches MicroPython _burst_target cap).
#define BURST_MAX_SAMPLES 3000u

// Switching cycle capacity (1..64 packed states, matches main.py).
#define CYCLE_MAX_STATES 64u

#endif  // SWITCHING_CIRCUIT_V2_CONFIG_H
