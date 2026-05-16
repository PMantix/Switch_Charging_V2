"""
Switching Circuit V2 - E2E latency probe.

Measures per-frame latency from Pi emit to TUI render-thread completion, so
we can verify later TUI-latency fixes against a baseline rather than by feel.

Clock alignment: Pi and Mac each use time.monotonic_ns() with arbitrary
epochs. We estimate the offset (mac - pi) once at connect and refresh it
every 60s via a ping round-trip; min-RTT halved, assumes symmetric links.
A few ms of offset error is well below the signals we care about.

The probe is a no-op unless enabled (toggled via ConnectionBar `D` key),
so the zero-overhead path is a single bool check.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Iterable


WINDOW = 100  # rolling samples per timer
HZ_WINDOW = 30  # samples for Hz measurement


class LatencyProbe:
    """Collects timing samples for one TUI session and reports p50/p95/max."""

    def __init__(self) -> None:
        self._net: deque[int] = deque(maxlen=WINDOW)
        self._queue: deque[int] = deque(maxlen=WINDOW)
        self._apply: deque[int] = deque(maxlen=WINDOW)
        self._plot: deque[int] = deque(maxlen=WINDOW)
        self._total: deque[int] = deque(maxlen=WINDOW)

        self._offset_ns: int = 0  # mac_ns - pi_ns
        self._offset_set: bool = False
        self._enabled: bool = False
        self._lock = threading.Lock()

        # Hz / jitter / drop tracking (always active, cheap)
        self._inter_frame: deque[int] = deque(maxlen=HZ_WINDOW)
        self._last_recv_ns: int = 0
        self._frame_count: int = 0
        self._drop_count: int = 0
        self._last_seq: int = -1

    # -- control ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled

    def set_offset(self, offset_ns: int) -> None:
        with self._lock:
            self._offset_ns = offset_ns
            self._offset_set = True

    @property
    def offset_set(self) -> bool:
        return self._offset_set

    def reset(self) -> None:
        """Clear all accumulators (e.g. on reconnect)."""
        with self._lock:
            self._net.clear()
            self._queue.clear()
            self._apply.clear()
            self._plot.clear()
            self._total.clear()
            self._inter_frame.clear()
            self._last_recv_ns = 0
            self._frame_count = 0
            self._drop_count = 0
            self._last_seq = -1

    # -- recording ----------------------------------------------------------

    def record(
        self,
        t_emit_pi_ns: int,
        t_recv_mac_ns: int,
        t_apply_start_ns: int,
        t_apply_end_ns: int,
        t_plot_ns: int,
        seq: int = -1,
    ) -> None:
        """Record timers from one state event. Safe to call from UI thread."""
        with self._lock:
            # Hz / inter-frame tracking (always, even when display is off)
            self._frame_count += 1
            if self._last_recv_ns > 0:
                gap = t_recv_mac_ns - self._last_recv_ns
                if gap > 0:
                    self._inter_frame.append(gap)
            self._last_recv_ns = t_recv_mac_ns

            # Drop detection via seq gaps
            if seq >= 0 and self._last_seq >= 0:
                expected = (self._last_seq + 1) & 0xFFFFFFFF
                if seq != expected:
                    gap_size = (seq - self._last_seq) & 0xFFFFFFFF
                    if gap_size < 10000:
                        self._drop_count += int(gap_size - 1)
            if seq >= 0:
                self._last_seq = seq

        if not self._enabled:
            return

        with self._lock:
            offset = self._offset_ns
            ready = self._offset_set

        if ready and t_emit_pi_ns > 0:
            net = max(0, t_recv_mac_ns - t_emit_pi_ns - offset)
        else:
            net = 0
        q = max(0, t_apply_start_ns - t_recv_mac_ns)
        apply_d = max(0, t_apply_end_ns - t_apply_start_ns)
        total = net + q + apply_d

        with self._lock:
            self._net.append(net)
            self._queue.append(q)
            self._apply.append(apply_d)
            self._plot.append(t_plot_ns)
            self._total.append(total)

    # -- reporting ----------------------------------------------------------

    @staticmethod
    def _stats_ms(samples: Iterable[int]) -> tuple[float, float, float]:
        s = sorted(samples)
        if not s:
            return (0.0, 0.0, 0.0)
        p50 = s[len(s) // 2]
        p95 = s[min(len(s) - 1, int(0.95 * len(s)))]
        mx = s[-1]
        return (p50 / 1e6, p95 / 1e6, mx / 1e6)

    def summary(self) -> dict:
        """Return {timer: (p50_ms, p95_ms, max_ms)} plus hz/jitter/drops."""
        with self._lock:
            snap = {
                "net": list(self._net),
                "queue": list(self._queue),
                "apply": list(self._apply),
                "plot": list(self._plot),
                "total": list(self._total),
            }
            n = len(self._total)
            ready = self._offset_set
            inter = list(self._inter_frame)
            drops = self._drop_count
            frames = self._frame_count

        out = {k: self._stats_ms(v) for k, v in snap.items()}
        out["_count"] = n
        out["_offset_ready"] = ready
        out["_drops"] = drops
        out["_frames"] = frames

        # Effective Hz from inter-frame intervals
        if inter:
            mean_ns = sum(inter) / len(inter)
            out["_hz"] = 1e9 / mean_ns if mean_ns > 0 else 0.0
            sorted_inter = sorted(inter)
            p50_ns = sorted_inter[len(sorted_inter) // 2]
            p95_ns = sorted_inter[min(len(sorted_inter) - 1, int(0.95 * len(sorted_inter)))]
            out["_jitter_p50_ms"] = p50_ns / 1e6
            out["_jitter_p95_ms"] = p95_ns / 1e6
        else:
            out["_hz"] = 0.0
            out["_jitter_p50_ms"] = 0.0
            out["_jitter_p95_ms"] = 0.0

        return out
