#include "neopixel.h"

#include <stdint.h>

#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "pico/stdlib.h"

#include "config.h"
#include "ws2812.pio.h"  // generated at build time from ws2812.pio

// ---------------------------------------------------------------------------
// PIO state machine setup. We use pio0 / sm0; the firmware doesn't use any
// other PIO stuff today, so contention isn't a concern.
// ---------------------------------------------------------------------------
#define WS2812_PIO  pio0
#define WS2812_SM   0u
#define WS2812_FREQ 800000.0f

static bool s_inited = false;

static inline uint32_t pack_grb(uint8_t r, uint8_t g, uint8_t b) {
    // WS2812 expects G then R then B, MSB first. The PIO program shifts
    // out the top byte first, so we left-justify into a 32-bit word with
    // the chosen ordering.
    return ((uint32_t)g << 24) | ((uint32_t)r << 16) | ((uint32_t)b << 8);
}

void neopixel_init(void) {
    if (s_inited) return;
    uint offset = pio_add_program(WS2812_PIO, &ws2812_program);
    ws2812_program_init(WS2812_PIO, WS2812_SM, offset,
                        PIN_NEOPIXEL, WS2812_FREQ, false /* RGB, not RGBW */);
    s_inited = true;
}

void neopixel_set_rgb(uint8_t r, uint8_t g, uint8_t b) {
    if (!s_inited) neopixel_init();
    pio_sm_put_blocking(WS2812_PIO, WS2812_SM, pack_grb(r, g, b));
}

void neopixel_startup(void) {
    neopixel_set_rgb(10, 0, 0); sleep_ms(100);
    neopixel_set_rgb(0, 10, 0); sleep_ms(100);
    neopixel_set_rgb(0, 0, 10); sleep_ms(100);
    neopixel_set_rgb(0, 0, 0);  sleep_ms(100);
}
