#ifndef SWITCHING_CIRCUIT_V2_NEOPIXEL_H
#define SWITCHING_CIRCUIT_V2_NEOPIXEL_H

#include <stdint.h>

// Bring up the WS2812 PIO state machine on PIN_NEOPIXEL. Idempotent.
void neopixel_init(void);

// Set the single onboard LED. Components are 0..255 each. Mirrors the
// MicroPython firmware's set_led(r, g, b).
void neopixel_set_rgb(uint8_t r, uint8_t g, uint8_t b);

// Run the boot startup sequence (red → green → blue → off).
// Blocking but only ~400 ms total — runs once during init.
void neopixel_startup(void);

#endif  // SWITCHING_CIRCUIT_V2_NEOPIXEL_H
