"""
DOE: sweep TUI broadcast rates against the Pi server and report the
throughput + latency envelope. Run from the Mac while the Pi service
is up. Lightweight — just opens a TCP subscribe, changes sensor_rate,
and tallies incoming state frames.

    python3 tools/rate_sweep.py               # default host 10.42.0.1
    python3 tools/rate_sweep.py --host X.X.X.X
    python3 tools/rate_sweep.py --rates 2,5,10,20,30,60,100

For each requested rate we report:
  actual_fps   — state frames/sec actually delivered
  mean_bytes   — average payload size per frame
  kB/s         — outbound throughput
  net p50/p95/max — one-way Pi-emit → Mac-recv latency (ms)
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import sys
import threading
import time
from collections import deque


def measure_offset(sock: socket.socket, rfile, wfile) -> int:
    """Pre-subscribe ping-pong to estimate clock offset (mac_ns - pi_ns).
    Min-RTT/2 over 5 pings."""
    best = None
    for _ in range(5):
        t_send = time.monotonic_ns()
        wfile.write(json.dumps({"cmd": "ping", "t_client_ns": t_send}) + "\n")
        wfile.flush()
        line = rfile.readline()
        t_recv = time.monotonic_ns()
        if not line:
            break
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            continue
        t_server = int(resp.get("t_server_ns", 0))
        if t_server == 0:
            continue
        rtt = t_recv - t_send
        offset = (t_send + rtt // 2) - t_server
        if best is None or abs(offset) < abs(best):
            best = offset
        time.sleep(0.05)
    return best or 0


def run_sweep(
    host: str, port: int, rates: list[float], window_s: float, verbose: bool,
) -> list[dict]:
    """Single sweep across `rates`. Returns a list of per-rate result dicts."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((host, port))
    sock.settimeout(3.0)
    rfile = sock.makefile("r", encoding="utf-8")
    wfile = sock.makefile("w", encoding="utf-8")

    offset_ns = measure_offset(sock, rfile, wfile)
    wfile.write(json.dumps({"cmd": "subscribe"}) + "\n")
    wfile.flush()
    rfile.readline()

    frames: deque[tuple[int, int, int]] = deque(maxlen=100_000)
    stop = threading.Event()
    sock.settimeout(None)

    def recv_loop() -> None:
        while not stop.is_set():
            try:
                line = rfile.readline()
            except OSError:
                break
            if not line:
                break
            t_recv = time.monotonic_ns()
            nbytes = len(line)
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("event") == "state":
                frames.append((t_recv, int(data.get("t_emit_ns", 0)), nbytes))

    t = threading.Thread(target=recv_loop, daemon=True, name="recv")
    t.start()

    results: list[dict] = []
    for rate in rates:
        wfile.write(json.dumps({"cmd": "set_sensor_rate", "rate": float(rate)}) + "\n")
        wfile.flush()
        time.sleep(1.0)
        frames.clear()
        time.sleep(window_s)
        snap = list(frames)

        if len(snap) < 2:
            results.append({
                "rate": rate, "actual": 0.0, "bytes": 0.0, "kbps": 0.0,
                "p50": None, "p95": None, "max": None, "status": "DEAD",
            })
            if verbose:
                print(f"{rate:>6.1f}   DEAD")
            continue

        t0 = snap[0][0]
        tN = snap[-1][0]
        elapsed = (tN - t0) / 1e9
        actual = len(snap) / elapsed if elapsed > 0 else 0
        sizes = [s[2] for s in snap]
        mean_bytes = statistics.mean(sizes)
        kbps = mean_bytes * actual / 1024

        nets = [
            (t_recv - t_emit - offset_ns) / 1e6
            for t_recv, t_emit, _ in snap
            if t_emit > 0 and -100 < (t_recv - t_emit - offset_ns) / 1e6 < 10_000
        ]
        if nets:
            nets_sorted = sorted(nets)
            p50 = statistics.median(nets_sorted)
            p95 = nets_sorted[min(len(nets_sorted) - 1, int(0.95 * len(nets_sorted)))]
            pmax = nets_sorted[-1]
        else:
            p50 = p95 = pmax = float("nan")

        status = "ok" if actual >= 0.9 * min(rate, 30) else "BEHIND"
        results.append({
            "rate": rate, "actual": actual, "bytes": mean_bytes, "kbps": kbps,
            "p50": p50, "p95": p95, "max": pmax, "status": status,
        })
        if verbose:
            print(f"{rate:>6.1f} {actual:>7.1f} {mean_bytes:>7.0f} {kbps:>7.1f} "
                  f"{p50:>6.1f} {p95:>6.1f} {pmax:>6.1f}  {status}")

    stop.set()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
    return results


def print_header() -> None:
    print(f"{'rate':>6} {'actual':>7} {'bytes':>7} {'kB/s':>7} "
          f"{'p50':>6} {'p95':>6} {'max':>6}  status")
    print(f"{'(Hz)':>6} {'(fps)':>7} {'/frame':>7} {'':>7} "
          f"{'(ms)':>6} {'(ms)':>6} {'(ms)':>6}")
    print("-" * 72)


def aggregate(all_runs: list[list[dict]], rates: list[float]) -> None:
    """Compile per-rate stats across multiple sweep repetitions."""
    print()
    print(f"=== aggregate over {len(all_runs)} runs ===")
    print()
    print(f"{'rate':>6} | {'actual fps':>18} | {'p50 ms':>16} | "
          f"{'p95 ms':>18} | {'max ms':>18} | {'dead':>4}")
    print(f"{'(Hz)':>6} | {'mean (min..max)':>18} | {'mean (min..max)':>16} | "
          f"{'mean (min..max)':>18} | {'mean (min..max)':>18} | runs")
    print("-" * 109)

    for rate in rates:
        rows = [r for run in all_runs for r in run if r["rate"] == rate]
        dead = sum(1 for r in rows if r["status"] == "DEAD" or r["actual"] == 0)
        alive = [r for r in rows if r["actual"] > 0 and r["p50"] is not None]
        if not alive:
            print(f"{rate:>6.1f} | {'-- all dead --':>18} | "
                  f"{'':>16} | {'':>18} | {'':>18} | {dead:>4}")
            continue
        actuals = [r["actual"] for r in alive]
        p50s = [r["p50"] for r in alive]
        p95s = [r["p95"] for r in alive]
        maxes = [r["max"] for r in alive]
        a_mean = statistics.mean(actuals)
        cell = lambda xs: f"{statistics.mean(xs):>5.1f} ({min(xs):>4.1f}..{max(xs):>5.1f})"
        print(f"{rate:>6.1f} | {a_mean:>5.1f} ({min(actuals):>4.1f}..{max(actuals):>5.1f}) "
              f"| {cell(p50s):>16} | {cell(p95s):>18} | {cell(maxes):>18} | {dead:>4}")

    print()
    print("notes:")
    print("  * 'mean (min..max)' shows the across-runs range for each metric.")
    print("  * tight ranges = stable; wide ranges = bursty / unreliable at that rate.")
    print("  * dead count: runs where no frames arrived within the window for")
    print("    that rate. Non-zero means the connection is breaking intermittently.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="10.42.0.1")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument(
        "--rates", default="2,5,10,15,20,30,50,100",
        help="comma-separated Hz values to sweep",
    )
    parser.add_argument(
        "--window", type=float, default=3.0,
        help="measurement window in seconds per rate",
    )
    parser.add_argument(
        "--repeats", type=int, default=1,
        help="number of full sweeps to perform and aggregate",
    )
    parser.add_argument(
        "--label", default="",
        help="optional label shown in output headers",
    )
    args = parser.parse_args()
    rates = [float(x) for x in args.rates.split(",") if x.strip()]

    try:
        tag = f" [{args.label}]" if args.label else ""
        print(f"connected target: {args.host}:{args.port}{tag}")
        print(f"rates: {rates}")
        print(f"window: {args.window}s per rate, repeats: {args.repeats}")
        print()

        if args.repeats == 1:
            print_header()
            run_sweep(args.host, args.port, rates, args.window, verbose=True)
        else:
            all_runs: list[list[dict]] = []
            for i in range(args.repeats):
                print(f"--- run {i+1}/{args.repeats} ---")
                print_header()
                res = run_sweep(
                    args.host, args.port, rates, args.window, verbose=True,
                )
                all_runs.append(res)
                print()
            aggregate(all_runs, rates)
    except ConnectionError as e:
        print(f"connection failed: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)


if __name__ == "__main__":
    main()
