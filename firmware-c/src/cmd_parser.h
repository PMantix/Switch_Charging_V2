#ifndef SWITCHING_CIRCUIT_V2_CMD_PARSER_H
#define SWITCHING_CIRCUIT_V2_CMD_PARSER_H

#include <stddef.h>

// Process a single line (no trailing newline). Replies (OK / ERR) are
// emitted directly to stdout; the caller doesn't need to forward
// anything. Lines that look like "BD" emit a multi-line BR dump and
// suppress the OK line, since BD's header is sent inline.
//
// Long-running side effects (X reset, BOOTSEL reboot) never return.
void cmd_parser_handle_line(const char *line, size_t len);

#endif  // SWITCHING_CIRCUIT_V2_CMD_PARSER_H
