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
    mode: str = "charge",
    ina_avg: int = None,
    bus_every: int = None,
) -> list[Path]:
    c = Client(host)
    print(f"connected to {host}:{AP_PORT}")

    try:
        # Apply sensor profile up front. Echoed max_hz tells the rest of the
        # sweep how high we can legitimately push --sampling.
        max_hz = None
        if ina_avg is not None:
            print(f"→ set_ina226_avg {ina_avg}")
            resp = c.send({"cmd": "set_ina226_avg", "avg": ina_avg})
            print(resp)
            if resp.get("ok"):
                max_hz = resp.get("max_hz")
        if bus_every is not None:
            print(f"→ set_bus_every {bus_every}")
            resp = c.send({"cmd": "set_bus_every", "every": bus_every})
            print(resp)
            if resp.get("ok"):
                max_hz = resp.get("max_hz")
        if max_hz is not None:
            requested_max = max(sampling_hz)
            if requested_max > max_hz:
                print(f"WARNING: requested --sampling max {requested_max:.0f} Hz"
                      f" exceeds firmware cap {max_hz:.0f} Hz — firmware will clamp")

        # Clean starting state.
        print("→ set_mode idle")
        print(c.send({"cmd": "set_mode", "mode": "idle"}))
        print(f"→ set_sequence {sequence_idx}")
        print(c.send({"cmd": "set_sequence", "sequence": sequence_idx}))
        print(f"→ set_mode {mode}")
        print(c.send({"cmd": "set_mode", "mode": mode}))
        time.sleep(0.5)  # let the engine programme C+F+G

        pi_paths: list[str] = []
        # Inner loop is switching so the visual effect on the circuit
        # (LED blink rate) cycles fast — more interesting to watch on video
        # than if the outer loop varied switching and the LEDs held still
        # for minutes at a time while only the sample rate changed.
        for sp_hz in sampling_hz:
            for sw_hz in switching_hz:
                # One full sequence cycle spans two step_times at the current
                # step_time formula ((1/f)/2 for 4-step, doubled for 2-step),
                # so actual cycle period = 2/f seconds regardless of mode.
                # Multiply by 2 so --cycles N produces N real cycles.
                if cycles > 0:
                    cond_duration = max(2.0 * cycles / sw_hz, min_duration_s)
                else:
                    cond_duration = duration_s
                print()
                actual_cycles = cond_duration * sw_hz / 2.0
                print(f"=== switching={sw_hz} Hz, sampling={sp_hz} Hz,"
                      f" mode={mode}, duration={cond_duration:.2f}s"
                      f" (≈{actual_cycles:.1f} cycles) ===")
                print(c.send({"cmd": "set_frequency", "frequency": sw_hz}))
                print(c.send({"cmd": "set_sensor_rate", "rate": sp_hz}))
                # Wait past the engine's 150ms debounce so the F command
                # actually lands on the RP2040 (and _resume_time gets re-
                # synced) before recording begins — otherwise the recording
                # captures the tail end of the previous frequency.
                time.sleep(0.25)
                max_samples = int(cond_duration * sp_hz) + 50
                resp = c.send({
                    "cmd": "pi_record_start",
                    "max_samples": max_samples,
                    "rec_mode": mode,
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
    parser.add_argument(
        "--mode", default="charge",
        choices=["charge", "discharge", "pulse_charge"],
        help="circuit mode during the DOE (default charge; use"
             " pulse_charge to exercise PULSE_CHARGE_SEQUENCE)",
    )
    parser.add_argument(
        "--avg", type=int, default=None,
        choices=[1, 4, 16, 64, 128, 256, 512, 1024],
        help="INA226 averaging count (fewer = faster but noisier). Leave"
             " unset to keep whatever the firmware currently has."
    )
    parser.add_argument(
        "--bus-every", type=int, default=None,
        help="Bus-voltage decimation: read bus once per N shunt sweeps."
             " 1 = every sample (default on firmware), 0 = never. Higher"
             " values raise the sample-rate cap.",
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
        mode=args.mode,
        ina_avg=args.avg, bus_every=args.bus_every,
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
