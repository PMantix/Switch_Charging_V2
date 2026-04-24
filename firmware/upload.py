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

    # Chunk the file — a single f.write({content!r}) of a >20 KB string
    # triggers MemoryError on the RP2040 (can't allocate one ~28 KB repr
    # plus the GC/parse overhead on a 264 KB heap). Writing in ~2 KB
    # blocks stays well inside what the heap can hold at once.
    def _exec(script: str, wait: float = 0.2) -> str:
        ser.write(script.encode("utf-8"))
        ser.write(b"\x04")
        time.sleep(wait)
        out = b""
        while ser.in_waiting:
            out += ser.read(ser.in_waiting)
            time.sleep(0.02)
        return out.decode("utf-8", errors="replace")

    resp = _exec(f"f = open('{remote_name}', 'w')\n", 0.2)
    if "Error" in resp or "error" in resp:
        print(resp)

    CHUNK = 2048
    total = len(content)
    for i in range(0, total, CHUNK):
        block = content[i:i + CHUNK]
        # repr() escapes newlines / quotes; safe to inline into the f.write call.
        resp = _exec(f"f.write({block!r})\n", 0.1)
        if "Error" in resp:
            print(f"chunk @{i} failed: {resp}")
            break

    resp = _exec(
        f"f.close()\nprint('UPLOADED {remote_name}', {total}, 'bytes')\n", 0.3
    )
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
