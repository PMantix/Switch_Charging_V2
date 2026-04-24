"""
Plot a Switching Circuit V2 recording CSV.

    python3 tools/plot_recording.py <path.csv>
    python3 tools/plot_recording.py <path.csv> --save out.png
    python3 tools/plot_recording.py ~/SwitchingCircuitV2_logs/charge_seq*.csv

Top panel: all four sensor voltages.
Middle panel: all four sensor currents.
Bottom panel: FET state traces (P1/P2/N1/N2), offset for readability.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


SENSORS = ("p1", "p2", "n1", "n2")
LABELS = ("P1 / +A", "P2 / +B", "N1 / -A", "N2 / -B")
COLORS = ("tab:green", "tab:blue", "tab:orange", "tab:red")


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Mac-tier logs have a `timestamp` ISO column; Pi-tier logs don't.
    # Both have `elapsed_s`, which is what we actually want to plot against.
    if "elapsed_s" not in df.columns:
        raise ValueError(f"{path}: no elapsed_s column — not a V2 recording?")
    return df


def title_from_file(df: pd.DataFrame, path: Path) -> str:
    n = len(df)
    if n == 0:
        return f"{path.name}\n(empty recording — 0 samples)"
    freq = df["frequency_hz"].iloc[0] if "frequency_hz" in df.columns else None
    mode = df["mode"].iloc[0] if "mode" in df.columns else ""
    dur = df["elapsed_s"].iloc[-1] - df["elapsed_s"].iloc[0]
    sample_hz = (n - 1) / dur if dur > 0 else 0
    bits = []
    if mode:
        bits.append(str(mode))
    if freq is not None:
        bits.append(f"{freq:.2f} Hz switching")
    bits.append(f"{n} samples over {dur:.2f} s ({sample_hz:.1f} sps)")
    return f"{path.name}\n{' • '.join(bits)}"


def plot_one(path: Path, save: Path | None = None) -> None:
    df = load(path)
    if len(df) == 0:
        print(f"skip {path.name}: empty recording", file=sys.stderr)
        return
    t = df["elapsed_s"].to_numpy()

    fig, (ax_v, ax_i, ax_f) = plt.subplots(
        3, 1, figsize=(12, 8), sharex=True,
        gridspec_kw={"height_ratios": [3, 3, 1]},
    )

    for name, label, color in zip(SENSORS, LABELS, COLORS):
        v_col = f"{name}_voltage"
        i_col = f"{name}_current_a"
        if v_col in df:
            ax_v.plot(t, df[v_col], label=label, color=color, linewidth=0.8)
        if i_col in df:
            ax_i.plot(t, df[i_col], label=label, color=color, linewidth=0.8)

    ax_v.set_ylabel("voltage (V)")
    ax_v.legend(loc="upper right", ncol=4, fontsize=8)
    ax_v.grid(True, alpha=0.3)

    ax_i.set_ylabel("current (A)")
    ax_i.legend(loc="upper right", ncol=4, fontsize=8)
    ax_i.grid(True, alpha=0.3)
    ax_i.axhline(0, color="k", linewidth=0.4, alpha=0.5)

    # FET states: offset each trace so they don't overlap — p1 at y=3, p2 at 2,
    # n1 at 1, n2 at 0. Values get vertical step plots so edges are crisp.
    for i, (name, label, color) in enumerate(
        zip(SENSORS, LABELS, COLORS)
    ):
        col = f"{name}_on"
        if col in df:
            offset = 3 - i
            ax_f.step(
                t, df[col].astype(int) + offset,
                where="post", color=color, linewidth=1.0, label=label,
            )
    ax_f.set_yticks([0, 1, 2, 3])
    ax_f.set_yticklabels(["N2", "N1", "P2", "P1"])
    ax_f.set_xlabel("elapsed (s)")
    ax_f.set_ylim(-0.2, 4.2)
    ax_f.grid(True, alpha=0.3)

    fig.suptitle(title_from_file(df, path), fontsize=10)
    fig.tight_layout()

    if save:
        fig.savefig(save, dpi=130, bbox_inches="tight")
        print(f"saved {save}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="one or more CSV files or globs")
    parser.add_argument(
        "--save", action="store_true",
        help="save a PNG next to each CSV instead of opening a window",
    )
    args = parser.parse_args()

    # Expand globs ourselves so shell-quoted globs also work.
    files: list[Path] = []
    for p in args.paths:
        matches = sorted(Path(f) for f in glob.glob(p))
        if not matches and Path(p).exists():
            matches = [Path(p)]
        files.extend(matches)
    if not files:
        print("no files matched", file=sys.stderr)
        sys.exit(1)

    for f in files:
        if args.save:
            plot_one(f, f.with_suffix(".png"))
        else:
            plot_one(f)


if __name__ == "__main__":
    main()
