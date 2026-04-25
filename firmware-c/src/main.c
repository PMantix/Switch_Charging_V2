// switching_circuit_v2 — C port of firmware/main.py for RP2040 + Pico SDK.
//
// Wire format and command set mirror the MicroPython firmware (see
// firmware/main.py and server/gpio_driver.py). The point of the port is
// to remove MicroPython interpreter overhead from the I²C hot path so we
// can hit ~1–2 kHz/sweep cleanly for high-frequency switching DOEs.
//
// Boot sequence:
//   1. Init USB CDC stdio (printf goes out the CDC port).
//   2. Init NeoPixel + run startup blink.
//   3. Init FET pins (all low).
//   4. Init I²C @ 1 MHz, scan for INA226 sensors, apply default config.
//   5. Print "OK READY" — host driver waits for this token.
//   6. Enter main loop: poll stdin → cmd_parser; tick streaming/burst.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/time.h"

#include "cmd_parser.h"
#include "config.h"
#include "ina226.h"
#include "neopixel.h"
#include "streaming.h"
#include "switching.h"
#include "usb_cdc.h"

int main(void) {
    usb_cdc_init();
    neopixel_init();
    neopixel_startup();
    switching_init();
    switching_all_off();

    int found = ina226_scan();
    if (found > 0) {
        // Print which sensors responded — purely informational; the
        // host's _wait_for_ready ignores everything until "OK READY".
        printf("INA226 found:");
        for (int i = 0; i < INA226_NUM_SENSORS; ++i) {
            if (ina226_is_present((sensor_idx_t)i)) {
                printf(" %s@0x%02X",
                       ina226_name((sensor_idx_t)i),
                       (unsigned)ina226_address((sensor_idx_t)i));
            }
        }
        printf("\n");
    } else {
        printf("INA226: none found on I2C bus\n");
    }

    neopixel_set_rgb(0, 0, 2);  // blue = idle
    printf("OK READY\n");

    char line_buf[512];
    while (true) {
        // 1) Service incoming command lines (non-blocking).
        size_t n = usb_cdc_read_line(line_buf, sizeof(line_buf));
        if (n > 0) {
            cmd_parser_handle_line(line_buf, n);
        }

        // 2) Drive burst capture (greedy — pulls samples as fast as I²C
        //    will allow, mirrors main.py's burst-priority loop).
        if (streaming_burst_active()) {
            if (streaming_burst_tick()) {
                neopixel_set_rgb(10, 4, 0);
                printf("OK BURST_DONE %u\n",
                       (unsigned)streaming_burst_count());
            }
            continue;
        }

        // 3) Stream tick — emits a D-line if the configured interval has
        //    elapsed. Cheap when streaming is off.
        streaming_tick();

        // 4) When idle, give the CPU a tiny sleep so we don't busy-spin
        //    the USB CDC poll. 100 µs matches the MicroPython firmware.
        if (streaming_get_rate_hz() <= 0.0f) {
            sleep_us(100);
        }
    }
    return 0;
}
