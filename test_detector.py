#!/usr/bin/env python3
"""
Phase 0 Test Script — Cycler State Detector Validation.

Run this on the Pi while the server is running in DISCHARGE mode (all FETs
on = transparent).  It connects to the RP2040 directly (or via the server's
GPIO driver) and feeds sensor data to the CyclerDetector, printing the
classified state in real time.

Usage (on Pi, standalone):
    python test_detector.py --serial /dev/ttyACM0

Usage (on Pi, using server's GPIO driver):
    python test_detector.py --server

Usage (on Mac, via TCP to server):
    python test_detector.py --tcp 192.168.1.100

The script logs all readings and classifications to a CSV file for
post-analysis.

Test procedure:
  1. Set circuit to DISCHARGE mode (all FETs on / transparent)
  2. Run this script
  3. Manually control the Arbin through:
     REST -> CC Charge -> CV Charge -> REST -> Discharge -> REST
  4. Observe detector output — each state should be identified within ~1s
  5. Review the CSV log for any false transitions
"""

import argparse
import csv
import json
import socket
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from server.cycler_detector import CyclerDetector, DetectionThresholds


def sensor_source_serial(port: str, baud: int = 115200):
    """Yield sensor snapshots from RP2040 serial (direct connection)."""
    import serial
    ser = serial.Serial(port, baud, timeout=1.0)
    # Start streaming at 15 Hz
    ser.write(b"T 15\n")
    ser.flush()
    print(f"Connected to RP2040 on {port}, streaming at 15 Hz")

    names = ["P1", "P2", "N1", "N2"]
    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("D "):
                continue
            parts = line.split()
            if len(parts) != 9:
                continue
            vals = [float(x) for x in parts[1:]]
            data = {}
            for i, name in enumerate(names):
                data[name] = {
                    "voltage": round(vals[i * 2], 4),
                    "current": round(vals[i * 2 + 1], 6),
                }
            yield data
    finally:
        ser.write(b"T 0\n")
        ser.flush()
        ser.close()


def sensor_source_tcp(host: str, port: int = 5555):
    """Yield sensor snapshots from the Pi server via TCP subscription."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    sock.settimeout(5.0)
    # Subscribe
    sock.sendall(b'{"cmd":"subscribe"}\n')
    print(f"Connected to server at {host}:{port}, subscribed to state updates")

    buf = ""
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("event") == "state" and "sensors" in msg:
                    yield msg["sensors"]
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="Cycler state detector test")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serial", metavar="PORT", help="RP2040 serial port (e.g., /dev/ttyACM0)")
    group.add_argument("--tcp", metavar="HOST", help="Pi server hostname/IP")
    parser.add_argument("--rest-threshold", type=float, default=0.005)
    parser.add_argument("--charge-min", type=float, default=0.008)
    parser.add_argument("--discharge-min", type=float, default=0.008)
    parser.add_argument("--output", "-o", default="detector_test.csv", help="CSV output path")
    args = parser.parse_args()

    thresholds = DetectionThresholds(
        rest_threshold=args.rest_threshold,
        charge_min=args.charge_min,
        discharge_min=args.discharge_min,
    )
    detector = CyclerDetector(thresholds)

    if args.serial:
        source = sensor_source_serial(args.serial)
    else:
        source = sensor_source_tcp(args.tcp)

    csv_path = Path(args.output)
    print(f"Logging to {csv_path}")
    print(f"Thresholds: rest={thresholds.rest_threshold}A, "
          f"charge={thresholds.charge_min}A, discharge={thresholds.discharge_min}A")
    print()
    print(f"{'Time':>8s}  {'State':<12s}  {'Conf':>5s}  "
          f"{'Avg I (mA)':>10s}  {'Avg V':>7s}  {'P1 I':>8s}  {'P2 I':>8s}  "
          f"{'N1 I':>8s}  {'N2 I':>8s}")
    print("-" * 95)

    last_state = None
    start = time.monotonic()

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "elapsed_s", "detected_state", "confidence",
            "avg_current_a", "avg_voltage_v",
            "p1_voltage", "p1_current_a", "p2_voltage", "p2_current_a",
            "n1_voltage", "n1_current_a", "n2_voltage", "n2_current_a",
        ])

        try:
            for sensor_data in source:
                result = detector.feed(sensor_data)
                elapsed = time.monotonic() - start

                # Extract individual sensor values for logging
                vals = {}
                for name in ("P1", "P2", "N1", "N2"):
                    s = sensor_data.get(name, {})
                    vals[f"{name}_v"] = s.get("voltage", 0.0)
                    vals[f"{name}_i"] = s.get("current", 0.0)

                writer.writerow([
                    f"{elapsed:.3f}",
                    result.state.value,
                    f"{result.confidence:.2f}",
                    f"{result.avg_current:.6f}",
                    f"{result.avg_voltage:.4f}",
                    f"{vals['P1_v']:.4f}", f"{vals['P1_i']:.6f}",
                    f"{vals['P2_v']:.4f}", f"{vals['P2_i']:.6f}",
                    f"{vals['N1_v']:.4f}", f"{vals['N1_i']:.6f}",
                    f"{vals['N2_v']:.4f}", f"{vals['N2_i']:.6f}",
                ])

                # Print state changes prominently, regular updates compactly
                state_marker = " " if result.state == last_state else "*"
                p1i = vals["P1_i"] * 1000
                p2i = vals["P2_i"] * 1000
                n1i = vals["N1_i"] * 1000
                n2i = vals["N2_i"] * 1000

                print(f"{elapsed:7.1f}s {state_marker}{result.state.value:<12s}  "
                      f"{result.confidence:5.2f}  "
                      f"{result.avg_current * 1000:9.3f}   "
                      f"{result.avg_voltage:7.4f}  "
                      f"{p1i:7.3f}  {p2i:7.3f}  {n1i:7.3f}  {n2i:7.3f}",
                      end="\r" if result.state == last_state else "\n")

                last_state = result.state

        except KeyboardInterrupt:
            print(f"\n\nStopped after {time.monotonic() - start:.1f}s")
            print(f"Results written to {csv_path}")


if __name__ == "__main__":
    main()
