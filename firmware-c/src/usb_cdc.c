#include "usb_cdc.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "pico/stdio.h"
#include "pico/stdio_usb.h"
#include "pico/stdlib.h"

// ---------------------------------------------------------------------------
// Line-buffered stdin. The MicroPython firmware reads one byte at a time
// from sys.stdin and accumulates until '\n'. We mirror that here so the
// host's command framing (one command per \n, see server/gpio_driver.py
// _send) keeps working unchanged.
// ---------------------------------------------------------------------------
#define LINE_BUF_CAP 256u
static char    s_line_buf[LINE_BUF_CAP];
static size_t  s_line_len  = 0;
static bool    s_overflow  = false;

void usb_cdc_init(void) {
    stdio_init_all();
    // Wait briefly for USB enumeration so the boot banner ("OK READY")
    // actually reaches the host. If nothing is connected within ~2 s,
    // proceed anyway — switching may run headless.
    absolute_time_t deadline = make_timeout_time_ms(2000);
    while (!stdio_usb_connected()) {
        if (time_reached(deadline)) break;
        sleep_ms(10);
    }
}

size_t usb_cdc_read_line(char *out_buf, size_t out_cap) {
    if (out_cap == 0) return 0;
    // getchar_timeout_us(0) returns PICO_ERROR_TIMEOUT (-1) when no byte
    // is available — we use that as our non-blocking poll.
    while (true) {
        int c = getchar_timeout_us(0);
        if (c == PICO_ERROR_TIMEOUT) return 0;
        if (c < 0) return 0;
        char ch = (char)c;
        if (ch == '\r') continue;  // tolerate CRLF; main.py does the same
        if (ch == '\n') {
            size_t n = s_line_len;
            if (s_overflow) {
                // Caller gets nothing for this round; emit ERR so the
                // host's serial reader sees a response and doesn't time
                // out waiting for one.
                printf("ERR line too long (>%u bytes)\n", (unsigned)LINE_BUF_CAP - 1);
                s_line_len = 0;
                s_overflow = false;
                continue;
            }
            // Copy up to out_cap-1 chars and NUL-terminate.
            if (n >= out_cap) n = out_cap - 1;
            memcpy(out_buf, s_line_buf, n);
            out_buf[n] = '\0';
            s_line_len = 0;
            return n;
        }
        if (s_line_len < LINE_BUF_CAP - 1) {
            s_line_buf[s_line_len++] = ch;
        } else {
            s_overflow = true;
        }
    }
}

void usb_cdc_write_str(const char *s) {
    if (!s) return;
    fputs(s, stdout);
    // No fflush — pico stdio_usb flushes on \n by default; the streaming
    // formatter always ends in '\n'.
}

int usb_cdc_vprintf(const char *fmt, va_list ap) {
    return vprintf(fmt, ap);
}

int usb_cdc_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = vprintf(fmt, ap);
    va_end(ap);
    return n;
}

void usb_cdc_println(const char *s) {
    if (!s) return;
    puts(s);  // appends '\n' and flushes the line on USB CDC
}
