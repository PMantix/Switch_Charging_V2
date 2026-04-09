"""
Upload a file to the RP2040's MicroPython filesystem over serial.
Usage: python3 upload.py <serial_port> <local_file> <remote_name>
"""

import sys
import time
import serial


def upload(port, local_path, remote_name):
    with open(local_path, "r") as f:
        content = f.read()

    ser = serial.Serial(port, 115200, timeout=2)
    time.sleep(0.2)

    # Interrupt any running program
    ser.write(b"\x03\x03")
    time.sleep(0.5)
    ser.read(ser.in_waiting)  # flush

    # Enter raw REPL mode
    ser.write(b"\x01")  # Ctrl-A
    time.sleep(0.2)
    ser.read(ser.in_waiting)

    # Build the write script
    script = (
        f"f = open('{remote_name}', 'w')\n"
        f"f.write({content!r})\n"
        f"f.close()\n"
        f"print('UPLOADED {remote_name}', len({content!r}), 'bytes')\n"
    )

    # Send via raw REPL: Ctrl-A already sent, now paste + Ctrl-D to execute
    ser.write(script.encode("utf-8"))
    ser.write(b"\x04")  # Ctrl-D = execute
    time.sleep(1.0)

    # Read response
    resp = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
    print(resp)

    # Exit raw REPL
    ser.write(b"\x02")  # Ctrl-B = normal REPL
    time.sleep(0.2)

    # Soft reset to run main.py
    ser.write(b"\x04")  # Ctrl-D = soft reset
    time.sleep(2.0)
    resp = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
    print(resp)

    ser.close()
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <port> <local_file> <remote_name>")
        sys.exit(1)
    upload(sys.argv[1], sys.argv[2], sys.argv[3])
