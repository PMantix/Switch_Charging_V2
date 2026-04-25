"""
Run a small DOE sweep twice — once with CNVR disabled, once with CNVR
enabled — and tag the recordings so they can be paired up for A/B
comparison.

    /usr/bin/python3 tools/cnvr_ab_sweep.py \
        --host 10.42.0.1 \
        --switching 1,5,20,100 \
        --sampling 50,100,200 \
        --duration 4 \
        --avg 1 --bus-every 5

Files land in ~/SwitchingCircuitV2_logs as
``pi_charge_seq2_<sw>Hz_<sps>sps_cnvr<off|on>_<ts>.csv`` so plot_cnvr_ab
can pair them up trivially.

Server prerequisites: the post-2026-04-25 build with the ``set_cnvr``
TCP command (firmware ``N`` command). The recorder also needs the
new schema (sample_pi_s, fw_ticks_us, fw_seq, recv_elapsed_s columns)
for the stale-repeat detection in plot_cnvr_ab to work cleanly.
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
    def __init__(self, host: str, port: int = AP_PORT, timeout: float = 8.0):
        self.sock = socket.socket()
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


def run_pass(
    c: Client, switching_hz, sampling_hz, duration_s, sequence_idx, mode,
    cnvr_label,
) -> list[str]:
    """One full pass through the (sw × sp) grid. Returns list of remote
    paths recorded during this pass."""
    paths = []
    for sp in sampling_hz:
        for sw in switching_hz:
            print(f"=== CNVR={cnvr_label}  sw={sw:g} Hz  sp={sp:g} sps "
                  f"dur={duration_s:.2f}s ===")
            print(c.send({"cmd": "set_frequency", "frequency": sw}))
            print(c.send({"cmd": "set_sensor_rate", "rate": sp}))
            time.sleep(0.25)
            max_samples = int(duration_s * sp) + 50
            resp = c.send({
                "cmd": "pi_record_start",
                "max_samples": max_samples,
                "rec_mode": mode,
                "rec_freq": sw,
                "rec_seq": sequence_idx,
                "rec_sensor_hz": sp,
            })
            print(resp)
            path = resp.get("path")
            if path:
                paths.append(path)
            time.sleep(duration_s + 0.3)
            print(c.send({"cmd": "pi_record_stop"}))
            time.sleep(0.3)
    return paths


def run_sweep(
    host: str, switching_hz, sampling_hz, duration_s, sequence_idx,
    mode, ina_avg, bus_every,
):
    c = Client(host)
    print(f"connected to {host}:{AP_PORT}")
    try:
        if ina_avg is not None:
            print("→ set_ina226_avg", ina_avg, c.send({"cmd": "set_ina226_avg", "avg": ina_avg}))
        if bus_every is not None:
            print("→ set_bus_every", bus_every, c.send({"cmd": "set_bus_every", "every": bus_every}))

        print("→ set_mode idle"); print(c.send({"cmd": "set_mode", "mode": "idle"}))
        print("→ set_sequence", sequence_idx); print(c.send({"cmd": "set_sequence", "sequence": sequence_idx}))
        print("→ set_mode", mode); print(c.send({"cmd": "set_mode", "mode": mode}))
        time.sleep(0.5)

        # Pass 1: CNVR off
        print()
        print("=" * 60)
        print("PASS 1 of 2  —  CNVR DISABLED  (legacy blind-poll path)")
        print("=" * 60)
        print("→ set_cnvr off:", c.send({"cmd": "set_cnvr", "enabled": False}))
        time.sleep(0.3)
        off_paths = run_pass(c, switching_hz, sampling_hz, duration_s,
                             sequence_idx, mode, "off")

        # Pass 2: CNVR on
        print()
        print("=" * 60)
        print("PASS 2 of 2  —  CNVR ENABLED  (ALERT-driven, fresh-only)")
        print("=" * 60)
        print("→ set_cnvr on:", c.send({"cmd": "set_cnvr", "enabled": True}))
        time.sleep(0.3)
        on_paths = run_pass(c, switching_hz, sampling_hz, duration_s,
                            sequence_idx, mode, "on")

        print()
        print(c.send({"cmd": "set_mode", "mode": "idle"}))
    finally:
        c.close()
    return off_paths, on_paths


def transfer(host: str, off_paths, on_paths) -> tuple[list[Path], list[Path]]:
    """SCP everything back, renaming with a cnvroff/cnvron tag so the
    A/B pairs are obvious from the filename."""
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    local_off, local_on = [], []
    print()
    print("=== transferring recordings ===")
    for tag, remote_paths, sink in (
        ("cnvroff", off_paths, local_off),
        ("cnvron",  on_paths,  local_on),
    ):
        for remote in remote_paths:
            name = Path(remote).name
            # insert tag before timestamp: ..._sps_cnvron_YYYYMMDD_...
            # original is ..._<ts>.csv ; we splice _cnvr<tag> in
            stem = Path(name).stem
            ext = Path(name).suffix
            # naive splice: insert before the trailing _YYYYMMDD_HHMMSS
            parts = stem.rsplit("_", 2)
            if len(parts) == 3:
                local_name = f"{parts[0]}_{tag}_{parts[1]}_{parts[2]}{ext}"
            else:
                local_name = f"{stem}_{tag}{ext}"
            local = DEFAULT_LOG_DIR / local_name
            target = f"pi@{host}:{remote}"
            r = subprocess.run(
                ["scp", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 target, str(local)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                print(f"  ✓ {local.name}")
                sink.append(local)
                subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no",
                     "-o", "BatchMode=yes",
                     f"pi@{host}", f"rm {remote}"],
                    capture_output=True, timeout=5,
                )
            else:
                err = (r.stderr or "unknown").strip()
                print(f"  ✗ {local.name}  ({err[:80]})")
    return local_off, local_on


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="10.42.0.1")
    p.add_argument("--switching", default="1,5,20,100")
    p.add_argument("--sampling", default="50,100,200")
    p.add_argument("--duration", type=float, default=4.0)
    p.add_argument("--sequence", type=int, default=1)
    p.add_argument("--mode", default="charge")
    p.add_argument("--avg", type=int, default=1, choices=[1, 4, 16, 64, 128, 256, 512, 1024])
    p.add_argument("--bus-every", type=int, default=5)
    args = p.parse_args()

    sw = [float(x) for x in args.switching.split(",") if x.strip()]
    sp = [float(x) for x in args.sampling.split(",") if x.strip()]
    total_one_pass = len(sw) * len(sp) * args.duration
    print(f"DOE A/B: 2 passes × {len(sw)}×{len(sp)} = {len(sw) * len(sp) * 2} runs,"
          f" {args.duration}s each, ~{total_one_pass * 2:.0f}s total")
    print(f"switching: {sw}")
    print(f"sampling:  {sp}")
    print()

    off_paths, on_paths = run_sweep(
        args.host, sw, sp, args.duration, args.sequence,
        args.mode, args.avg, args.bus_every,
    )
    local_off, local_on = transfer(args.host, off_paths, on_paths)

    print()
    print(f"done. {len(local_off)} CNVR-off + {len(local_on)} CNVR-on files in {DEFAULT_LOG_DIR}")
    if local_off and local_on:
        # Suggest a pairing for plot_cnvr_ab
        first_off = local_off[0]
        first_on = local_on[0]
        print()
        print("plot one pair with:")
        print(f"  /usr/bin/python3 tools/plot_cnvr_ab.py \\\n      {first_off} \\\n      {first_on}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
