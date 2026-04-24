"""
Detailed per-recording "commanded vs sensed" overlay.

    python3 tools/plot_cmd_vs_sense.py <path.csv>                 # interactive
    python3 tools/plot_cmd_vs_sense.py <path.csv> --save out.png  # file

One subplot per FET (P1, P2, N1, N2). In each:
  * grey shaded band = commanded ON state (full height when 1, 0 when 0)
  * red line         = sensed current through that sensor
  * blue line        = sensed voltage through that sensor (second y-axis)

Title annotations give:
  * samples/cycle — Nyquist indicator
  * on-mean / off-mean / discrimination Δ
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


SENSORS = ("p1", "p2", "n1", "n2")
LABELS = ("P1 / +A (high-side A)", "P2 / +B (high-side B)",
          "N1 / -A (low-side A)", "N2 / -B (low-side B)")


def summarize(df: pd.DataFrame, fet: str) -> dict:
    i_col = f"{fet}_current_a"
    on_col = f"{fet}_on"
    n = len(df)
    dur = df["elapsed_s"].iloc[-1] - df["elapsed_s"].iloc[0]
    sps = (n - 1) / dur if dur > 0 else 0
    sw = df["frequency_hz"].iloc[0] if "frequency_hz" in df else 0
    samples_per_cycle = sps / sw if sw > 0 else float("inf")
    on_mask = df[on_col].astype(bool)
    i_on = df.loc[on_mask, i_col].mean() if on_mask.any() else 0.0
    i_off = df.loc[~on_mask, i_col].mean() if (~on_mask).any() else 0.0
    i_max = df[i_col].max() or 1e-9
    return {
        "samples_per_cycle": samples_per_cycle,
        "i_on": i_on, "i_off": i_off,
        "delta": (i_on - i_off) / i_max if i_max > 0 else 0,
        "sps": sps, "switching_hz": sw,
    }


def plot_one(path: Path, save: Path | None) -> None:
    df = pd.read_csv(path)
    if len(df) < 2:
        print(f"{path.name}: empty, skipping", file=sys.stderr)
        return

    t = df["elapsed_s"].to_numpy()
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

    for ax, fet, label in zip(axes, SENSORS, LABELS):
        i = df[f"{fet}_current_a"].to_numpy()
        v = df[f"{fet}_voltage"].to_numpy()
        on = df[f"{fet}_on"].astype(int).to_numpy()

        # Current axis (primary)
        i_max = max(i.max(), 1e-6)
        ax.fill_between(t, 0, on * i_max, step="post", alpha=0.15, color="black",
                        label="commanded ON")
        ax.plot(t, i, color="tab:red", linewidth=0.9, label="sensed current (A)")
        ax.set_ylabel("current (A)", color="tab:red")
        ax.tick_params(axis="y", labelcolor="tab:red")
        ax.grid(True, alpha=0.3)

        # Voltage axis (secondary) on the right
        ax2 = ax.twinx()
        ax2.plot(t, v, color="tab:blue", linewidth=0.7, alpha=0.7,
                 label="sensed voltage (V)")
        ax2.set_ylabel("voltage (V)", color="tab:blue")
        ax2.tick_params(axis="y", labelcolor="tab:blue")

        s = summarize(df, fet)
        ax.set_title(
            f"{label}   |   {s['samples_per_cycle']:.1f} samples/cycle   |   "
            f"I_on={s['i_on']*1000:.2f} mA,  I_off={s['i_off']*1000:.2f} mA,  "
            f"Δ={s['delta']:.2f}",
            fontsize=9, loc="left",
        )

    axes[-1].set_xlabel("elapsed (s)")
    axes[0].legend(loc="upper right", fontsize=7)

    sw = df["frequency_hz"].iloc[0] if "frequency_hz" in df else 0
    n = len(df)
    dur = df["elapsed_s"].iloc[-1] - df["elapsed_s"].iloc[0]
    sps = (n - 1) / dur if dur > 0 else 0
    fig.suptitle(
        f"{path.name}\n"
        f"switching {sw:g} Hz  |  sampling ≈ {sps:.0f} sps  |  "
        f"{n} samples over {dur:.2f} s",
        fontsize=10,
    )
    fig.tight_layout()

    if save:
        fig.savefig(save, dpi=140, bbox_inches="tight")
        print(f"saved {save}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="CSV file")
    parser.add_argument("--save", type=Path, default=None,
                        help="save PNG instead of opening window")
    args = parser.parse_args()
    plot_one(args.path, args.save)


if __name__ == "__main__":
    main()
