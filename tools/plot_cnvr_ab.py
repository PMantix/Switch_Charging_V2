"""
A/B compare two recordings of the same DOE condition with and without
CNVR-driven sample scheduling.

    python3 tools/plot_cnvr_ab.py <off.csv> <on.csv>
    python3 tools/plot_cnvr_ab.py <off.csv> <on.csv> --save out.png

The two CSVs should be the same switching/sampling configuration with
CNVR disabled (left) and enabled (right). Filenames don't have to
match a convention; the plot reads ``frequency_hz`` and the recorded
duration directly.

Three panels per side:

  1. P1 current trace vs elapsed_s (firmware-anchored time).
  2. Inter-sample dt distribution. With CNVR off, expect a cluster
     around 1/sensor_rate but also a heavy tail of *zero or
     near-zero* gaps where multiple emits read the same conversion.
     With CNVR on, the distribution should tighten.
  3. Stale-repeat fraction — % of rows whose 8 raw V/I values are
     bit-identical to the previous row. This is the key
     correctness-fix metric. CNVR-off should be high (~5/6 = 83% at
     AVG=1 according to the design note); CNVR-on should be ~0%.

Each panel is annotated with:

  - Expected sample count = nominal_sps × duration
  - Actual sample count = number of rows in the CSV
  - Achieved sps = (n - 1) / duration

Stale-repeats are computed on the floats as written to the CSV
(``p1_voltage``, ``p1_current_a``, ..., ``n2_current_a``). A repeat
means every one of those 8 values matches the prior row exactly.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


VAL_COLS = (
    "p1_voltage", "p1_current_a",
    "p2_voltage", "p2_current_a",
    "n1_voltage", "n1_current_a",
    "n2_voltage", "n2_current_a",
)
_FNAME_RE = re.compile(r"_(\d+p\d+)Hz_(\d+)sps_")


def parse_nominal_sps(path: Path) -> int | None:
    """Extract requested sps from a filename like
    ``pi_charge_seq2_50p0Hz_400sps_20260424_062825.csv``. The recorder
    encodes the *requested* (not achieved) rate. Returns None on
    fail."""
    m = _FNAME_RE.search(path.name)
    if not m:
        return None
    try:
        return int(m.group(2))
    except ValueError:
        return None


def stale_repeat_mask(df: pd.DataFrame) -> np.ndarray:
    """True for rows where every column in VAL_COLS exactly matches
    the previous row. The first row is False (no previous)."""
    if not all(c in df.columns for c in VAL_COLS):
        missing = [c for c in VAL_COLS if c not in df.columns]
        raise ValueError(f"missing columns for stale-repeat: {missing}")
    cur = df[list(VAL_COLS)].to_numpy()
    prev = np.roll(cur, 1, axis=0)
    eq = np.all(cur == prev, axis=1)
    eq[0] = False
    return eq


def summarize(df: pd.DataFrame, nominal_sps: int | None) -> dict:
    n = len(df)
    elapsed_col = "elapsed_s" if "elapsed_s" in df.columns else None
    if elapsed_col is None:
        return {"error": "no elapsed_s column"}
    duration = float(df[elapsed_col].iloc[-1] - df[elapsed_col].iloc[0])
    achieved_sps = (n - 1) / duration if duration > 0 else 0.0
    expected_n = (
        int(round(nominal_sps * duration)) if nominal_sps else None
    )
    stale = stale_repeat_mask(df)
    stale_n = int(stale.sum())
    stale_pct = 100.0 * stale_n / n if n else 0.0
    sw = float(df["frequency_hz"].iloc[0]) if "frequency_hz" in df else 0.0
    return {
        "n": n, "expected_n": expected_n,
        "duration_s": duration,
        "achieved_sps": achieved_sps,
        "nominal_sps": nominal_sps,
        "switching_hz": sw,
        "stale_n": stale_n, "stale_pct": stale_pct,
    }


def plot_pair(off_csv: Path, on_csv: Path, save: Path | None) -> None:
    df_off = pd.read_csv(off_csv)
    df_on = pd.read_csv(on_csv)
    s_off = summarize(df_off, parse_nominal_sps(off_csv))
    s_on = summarize(df_on, parse_nominal_sps(on_csv))

    fig, axes = plt.subplots(3, 2, figsize=(14, 10),
                             gridspec_kw={"height_ratios": [1.2, 1.0, 0.4]})

    # --- row 1: signal traces ------------------------------------------
    for ax, df, label, s, csv in (
        (axes[0, 0], df_off, "CNVR off", s_off, off_csv),
        (axes[0, 1], df_on,  "CNVR on",  s_on,  on_csv),
    ):
        t = df["elapsed_s"].to_numpy()
        i = df["p1_current_a"].to_numpy()
        ax.plot(t, i, color="tab:red", linewidth=0.7)
        ax.set_xlabel("elapsed_s (s)")
        ax.set_ylabel("P1 current (A)")
        ax.grid(True, alpha=0.3)
        sw = s.get("switching_hz", 0.0) or 0.0
        ax.set_title(
            f"{label}  ·  {sw:g} Hz × {s['nominal_sps']} sps requested  "
            f"·  {s['n']} rows over {s['duration_s']:.2f} s",
            fontsize=10, loc="left",
        )

    # --- row 2: dt histograms ------------------------------------------
    for ax, df, label, s in (
        (axes[1, 0], df_off, "CNVR off", s_off),
        (axes[1, 1], df_on,  "CNVR on",  s_on),
    ):
        dt_ms = np.diff(df["elapsed_s"].to_numpy()) * 1000.0
        med = max(np.median(dt_ms), 0.5)
        hi = max(med * 4, 1.0)
        bins = np.linspace(0, hi, 60)
        ax.hist(np.clip(dt_ms, 0, hi), bins=bins, color="tab:blue", alpha=0.7)
        nominal_dt = 1000.0 / s["nominal_sps"] if s["nominal_sps"] else None
        if nominal_dt:
            ax.axvline(nominal_dt, color="black", linestyle="--",
                       linewidth=1, label=f"nominal {nominal_dt:.2f} ms")
            ax.legend(loc="upper right", fontsize=8)
        ax.set_xlabel("inter-sample dt (ms, clipped)")
        ax.set_ylabel("count")
        ax.grid(True, alpha=0.3)
        ax.set_title(
            f"{label}  ·  dt mean {dt_ms.mean():.2f} ± {dt_ms.std():.2f} ms"
            f"  ·  achieved {s['achieved_sps']:.1f} sps",
            fontsize=10, loc="left",
        )

    # --- row 3: text annotation summarizing expected-vs-actual ---------
    for ax, label, s in (
        (axes[2, 0], "CNVR off", s_off),
        (axes[2, 1], "CNVR on",  s_on),
    ):
        ax.axis("off")
        exp = s["expected_n"]
        if exp is None:
            exp_txt = "n/a (couldn't parse nominal sps from filename)"
        else:
            shortfall = s["n"] - exp
            shortfall_pct = 100.0 * shortfall / exp if exp else 0
            exp_txt = (
                f"expected (nominal × duration): {exp}\n"
                f"actual:                        {s['n']}\n"
                f"delta:                         {shortfall:+d}  ({shortfall_pct:+.1f}%)"
            )
        text = (
            f"{label}\n"
            f"\n"
            f"sample count:\n"
            f"{exp_txt}\n"
            f"\n"
            f"stale-repeat rows: {s['stale_n']} / {s['n']}  "
            f"({s['stale_pct']:.1f}%)\n"
            f"  (rows whose 8 raw V/I values exactly match the prior row)"
        )
        ax.text(0.02, 0.95, text, transform=ax.transAxes,
                fontsize=9, family="monospace", va="top", ha="left")

    fig.suptitle(
        "CNVR A/B — same DOE condition, blind-poll vs ALERT-driven",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    if save:
        fig.savefig(save, dpi=140, bbox_inches="tight")
        print(f"saved {save}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("off_csv", type=Path, help="recording with CNVR disabled")
    parser.add_argument("on_csv", type=Path, help="recording with CNVR enabled")
    parser.add_argument("--save", type=Path, default=None,
                        help="save PNG instead of opening window")
    args = parser.parse_args()
    if not args.off_csv.exists():
        print(f"missing: {args.off_csv}", file=sys.stderr)
        sys.exit(1)
    if not args.on_csv.exists():
        print(f"missing: {args.on_csv}", file=sys.stderr)
        sys.exit(1)
    plot_pair(args.off_csv, args.on_csv, args.save)


if __name__ == "__main__":
    main()
