#ifndef SWITCHING_CIRCUIT_V2_USB_CDC_H
#define SWITCHING_CIRCUIT_V2_USB_CDC_H

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Initialize stdio over USB CDC. Returns once a host has enumerated the
// CDC interface (or after a short timeout — the rest of init proceeds even
// without a host so onboard switching can run untethered).
void usb_cdc_init(void);

// Drain pending stdin without blocking. If a complete line (\n-terminated)
// is available, copy it (without the newline) into out_buf (NUL-terminated)
// and return its length; otherwise return 0. The internal line buffer is
// 256 bytes; longer lines get truncated and a one-line ERR is emitted.
size_t usb_cdc_read_line(char *out_buf, size_t out_cap);

// Send a string verbatim (no implicit newline). Used by the streaming
// path so we control the exact wire format including the trailing \n.
void usb_cdc_write_str(const char *s);

// printf-style write to USB CDC. Routes through stdio_usb (line-buffered
// disabled) so output is flushed promptly. Returns bytes written.
int  usb_cdc_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
int  usb_cdc_vprintf(const char *fmt, va_list ap);

// Convenience: write s + "\n".
void usb_cdc_println(const char *s);

#endif  // SWITCHING_CIRCUIT_V2_USB_CDC_H
