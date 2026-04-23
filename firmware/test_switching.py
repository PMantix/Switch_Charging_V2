"""
Interactive sanity test for the RP2040 switching-timer protocol.

Assumes the systemd server has been stopped and firmware/main.py has
been uploaded to the RP2040. Run from the Pi:

    sudo systemctl stop switching-circuit
    python3 firmware/upload.py /dev/ttyACM0 firmware/main.py main.py
    sleep 2
    python3 firmware/test_switching.py

Watches LEDs at slow and fast switching rates and checks the ERR paths.
"""

import sys
import time

import serial


def run(port: str = "/dev/ttyACM0", baud: int = 115200) -> None:
    s = serial.Serial(port, baud, timeout=2)
    time.sleep(1.0)
    s.reset_input_buffer()

    def cmd(c: str, wait: float = 0.15) -> str:
        s.write((c + "\n").encode())
        time.sleep(wait)
        out = s.read(s.in_waiting or 1).decode(errors="replace").strip()
        print(f">> {c}")
        for line in out.splitlines():
            print(f"   {line}")
        return out

    print("=== basic probe ===")
    cmd("P")

    print("\n=== slow switching (500 ms/step, 2-state cycle) — watch LEDs ===")
    cmd("C 2 9 6")        # state 9 = P1+N2, state 6 = P2+N1
    cmd("F 500000")       # 500 ms per step
    cmd("G")
    time.sleep(4)         # watch alternation
    cmd("H")

    print("\n=== debug step after halt, then manual override ===")
    cmd("K")              # advance idx by one
    cmd("S 0 0 0 0")      # manual override (auto-halts first)

    print("\n=== fast switching (1666 us/step ≈ 300 Hz per-FET) ===")
    cmd("C 2 9 6")
    cmd("F 1666")
    print("\n    >>> LOOK AT THE LEDs NOW — should appear SOLID (no blink).")
    print("    >>> 10 seconds starting in 3...")
    time.sleep(1); print("    >>> 2...")
    time.sleep(1); print("    >>> 1...")
    time.sleep(1); print("    >>> GO!\n")
    cmd("G")
    time.sleep(10)
    cmd("H")
    print("    >>> done — LEDs should now be off.")

    print("\n=== error paths ===")
    cmd("F 10")           # expect ERR (below 50 us floor)
    cmd("C 2 9")          # expect ERR (count says 2 but only 1 state given)
    cmd("G")              # should still work (earlier C+F still valid)
    time.sleep(1)
    cmd("H")

    s.close()
    print("\n=== done ===")


if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
    run(port)
