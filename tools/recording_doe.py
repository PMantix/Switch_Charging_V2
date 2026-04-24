"""
Automated DOE: record a CSV for each combination of switching frequency
and sensor sampling rate. Files are auto-named by the Pi recorder and
SCP'd back to the Mac (same flow the TUI uses).

    python3 tools/recording_doe.py \
        --host 10.42.0.1 \
        --switching 1,10,100,300 \
        --sampling 2,10,50,100,200 \
        --duration 10

This connects over TCP, asks the server to:
  1. set_sequence (default 1 → [0,1,2,3] standard cycle)
  2. set_mode charge (starts switching)
  3. for each (switching_freq, sensor_rate) pair:
       - set_frequency + set_sensor_rate
       - pi_record_start
       - wait duration
       - pi_record_stop
  4. set_mode idle
  5. scp all the pi_*.csv recordings back

It deliberately does NOT subscribe to state events — this is a command-only
client, so it doesn't contend with the Pi broadcast loop.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path


AP_PORT = 5555
DEFAULT_LOG_DIR = Path.home() / "SwitchingCircuitV2_logs"


class Client:
    """Minimal JSON-line command client — no subscription, synchronous replies."""

    def __init__(self, host: str, port: int = AP_PORT, timeout: float = 5.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(timeout)
        self.sock.connect((host, port))
        self.rfile = self.sock.makefile("r", encoding="utf-8")
        self.wfile = self.sock.makefile("w", encoding="utf-8")

    def send(self, cmd: dict) -> dict:
        self.wfile.write(json.dumps(cmd) + "\n")
        self.wfile.flush()
        line = self.rfile.readline()
        if not line:
            raise ConnectionError("server closed connection")
        return json.loads(line)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def run_doe(
    host: str,
    switching_hz: list[float],
    sampling_hz: list[float],
    duration_s: float,
    sequence_idx: int,
    cycles: float = 0,
    min_duration_s: float = 1.0,
) -> list[Path]:
    c = Client(host)
    print(f"connected to {host}:{AP_PORT}")

    try:
        # Clean starting state.
        print("→ set_mode idle")
        print(c.send({"cmd": "set_mode", "mode": "idle"}))
        print(f"→ set_sequence {sequence_idx}")
        print(c.send({"cmd": "set_sequence", "sequence": sequence_idx}))
        # Enter charge so switching runs during the recording.
        print("→ set_mode charge")
        print(c.send({"cmd": "set_mode", "mode": "charge"}))
        time.sleep(0.5)  # let the engine programme C+F+G

        pi_paths: list[str] = []
        for sw_hz in switching_hz:
            for sp_hz in sampling_hz:
                # Per-condition duration: when --cycles is used, duration
                # scales inversely with switching freq so every recording
                # captures the same number of switching cycles. A minimum
                # floor prevents nonsensically short captures at high
                # switching rates.
                if cycles > 0:
                    cond_duration = max(cycles / sw_hz, min_duration_s)
                else:
                    cond_duration = duration_s
                print()
                print(f"=== switching={sw_hz} Hz, sampling={sp_hz} Hz,"
                      f" duration={cond_duration:.2f}s"
                      f" (≈{cond_duration * sw_hz:.1f} cycles) ===")
                print(c.send({"cmd": "set_frequency", "frequency": sw_hz}))
                print(c.send({"cmd": "set_sensor_rate", "rate": sp_hz}))
                max_samples = int(cond_duration * sp_hz) + 50
                resp = c.send({
                    "cmd": "pi_record_start",
                    "max_samples": max_samples,
                    "rec_mode": "charge",
                    "rec_freq": sw_hz,
                    "rec_seq": sequence_idx,
                    "rec_sensor_hz": sp_hz,
                })
                print(resp)
                path = resp.get("path")
                if path:
                    pi_paths.append(path)
                time.sleep(cond_duration + 0.3)
                print(c.send({"cmd": "pi_record_stop"}))
                # Small gap between runs so Pi flush + engine settle.
                time.sleep(0.3)

        print()
        print("→ set_mode idle")
        print(c.send({"cmd": "set_mode", "mode": "idle"}))
    finally:
        c.close()

    # SCP everything back to the Mac's log dir.
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    local_paths: list[Path] = []
    print()
    print("=== transferring recordings ===")
    for remote in pi_paths:
        name = Path(remote).name
        local = DEFAULT_LOG_DIR / name
        target = f"pi@{host}:{remote}"
        result = subprocess.run(
            ["scp", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             target, str(local)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"  ✓ {name}")
            local_paths.append(local)
            # Clean up on Pi so we don't accumulate.
            subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                 f"pi@{host}", f"rm {remote}"],
                capture_output=True, timeout=5,
            )
        else:
            err = (result.stderr or "unknown").strip()
            print(f"  ✗ {name}  ({err[:80]})")
    return local_paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="10.42.0.1")
    parser.add_argument(
        "--switching", default="1,10,100,300",
        help="comma-separated switching Hz values",
    )
    parser.add_argument(
        "--sampling", default="2,10,50,100,200",
        help="comma-separated sensor sample Hz values",
    )
    parser.add_argument(
        "--duration", type=float, default=10.0,
        help="seconds to record per combination (ignored if --cycles is set)",
    )
    parser.add_argument(
        "--cycles", type=float, default=0,
        help="record N switching cycles per condition instead of fixed time;"
             " actual duration varies with switching rate (0 disables)",
    )
    parser.add_argument(
        "--min-duration", type=float, default=1.0,
        help="floor on per-condition duration when --cycles is used,"
             " so high-frequency switching doesn't produce useless short captures",
    )
    parser.add_argument(
        "--sequence", type=int, default=1,
        help="sequence index (0=all-off idle, 1=[0,1,2,3] standard)",
    )
    args = parser.parse_args()

    sw = [float(x) for x in args.switching.split(",") if x.strip()]
    sp = [float(x) for x in args.sampling.split(",") if x.strip()]

    if args.cycles > 0:
        total = sum(
            max(args.cycles / s, args.min_duration) for s in sw
        ) * len(sp)
        print(f"DOE: {len(sw)}×{len(sp)} = {len(sw) * len(sp)} conditions,"
              f" {args.cycles:g} cycles each (min {args.min_duration}s),"
              f" ~{total:.0f}s total before transfer")
    else:
        total = len(sw) * len(sp) * args.duration
        print(f"DOE: {len(sw)}×{len(sp)} = {len(sw) * len(sp)} conditions,"
              f" {args.duration}s each, ~{total:.0f}s total before transfer")
    print(f"switching rates: {sw}")
    print(f"sampling rates:  {sp}")
    print()

    paths = run_doe(
        args.host, sw, sp, args.duration, args.sequence,
        cycles=args.cycles, min_duration_s=args.min_duration,
    )

    print()
    print(f"done. {len(paths)} files in {DEFAULT_LOG_DIR}")
    if paths:
        print()
        print("plot them all with:")
        print(f"  python3 tools/plot_recording.py {paths[0].parent}/pi_charge_seq*_*.csv --save")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
