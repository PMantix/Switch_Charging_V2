"""
DOE grid plot: one tile per (switching_freq × sampling_freq) condition.

    python3 tools/plot_doe_grid.py 'glob/of/pi_charge_*.csv' --save doe.png

Each tile overlays:
  - Commanded P1-ON state   (step function, light grey background)
  - Sensed P1 current       (line, scaled to same axis)

Tile annotations:
  - samples/cycle           (Nyquist indicator — <2 means aliased)
  - on/off current ratio    (fidelity indicator — near 1 = can't discriminate)

Columns = sampling rates ascending, rows = switching rates ascending.
Filenames encode both (eg `pi_charge_seq2_100p0Hz_50sps_...csv`).
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


_FNAME_RE = re.compile(r"_(\d+p\d+)Hz_(\d+)sps_")


def parse_rates(path: Path) -> tuple[float, int] | None:
    m = _FNAME_RE.search(path.name)
    if not m:
        return None
    sw = float(m.group(1).replace("p", "."))
    sp = int(m.group(2))
    return sw, sp


def fidelity(df: pd.DataFrame, fet: str = "p1") -> tuple[float, float, float]:
    """Per-condition summary: samples/cycle, on-mean, off-mean. Uses current
    through `fet`. A healthy recording has on-mean >> off-mean."""
    n = len(df)
    dur = df["elapsed_s"].iloc[-1] - df["elapsed_s"].iloc[0]
    sps = (n - 1) / dur if dur > 0 else 0
    sw = df["frequency_hz"].iloc[0]
    samples_per_cycle = sps / sw if sw > 0 else float("inf")
    i_col = f"{fet}_current_a"
    on_col = f"{fet}_on"
    if i_col not in df or on_col not in df:
        return samples_per_cycle, 0.0, 0.0
    on_mask = df[on_col].astype(bool)
    i_on = df.loc[on_mask, i_col].mean() if on_mask.any() else 0.0
    i_off = df.loc[~on_mask, i_col].mean() if (~on_mask).any() else 0.0
    return samples_per_cycle, float(i_on), float(i_off)


def plot_tile(ax, df: pd.DataFrame, sw: float, sp: int, fet: str) -> None:
    t = df["elapsed_s"].to_numpy()
    on = df[f"{fet}_on"].astype(int).to_numpy()
    i = df[f"{fet}_current_a"].to_numpy()

    # Scale the commanded trace to the current's range so both share the axis
    i_max = max(i.max(), 1e-6)
    scaled_on = on * i_max

    ax.fill_between(t, 0, scaled_on, step="post", alpha=0.15, color="black",
                    label="commanded ON" if ax.get_subplotspec().is_first_col()
                    and ax.get_subplotspec().is_first_row() else None)
    ax.plot(t, i, color="tab:red", linewidth=0.6,
            label=f"sensed {fet.upper()} I"
            if ax.get_subplotspec().is_first_col()
            and ax.get_subplotspec().is_first_row() else None)

    spc, i_on, i_off = fidelity(df, fet)
    discrim = (i_on - i_off) / i_max if i_max > 0 else 0
    # colour-code the title by fidelity: green=discriminates, red=aliased
    if spc >= 2 and discrim > 0.5:
        colour = "green"
    elif spc >= 2 or discrim > 0.3:
        colour = "orange"
    else:
        colour = "red"
    ax.set_title(
        f"{sw:g} Hz × {sp} sps  |  {spc:.1f} s/cyc  |  Δ={discrim:.2f}",
        fontsize=8, color=colour,
    )
    ax.tick_params(labelsize=6)
    ax.grid(True, alpha=0.3)


def build_grid(files: list[Path], fet: str, save: Path | None) -> None:
    cells: dict[tuple[float, int], Path] = {}
    for f in files:
        parsed = parse_rates(f)
        if not parsed:
            continue
        cells[parsed] = f
    if not cells:
        print("no DOE files matched", file=sys.stderr)
        sys.exit(1)

    switchings = sorted({k[0] for k in cells})
    samplings = sorted({k[1] for k in cells})
    nrows, ncols = len(switchings), len(samplings)
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(3 * ncols, 1.8 * nrows),
        sharex=False, sharey=False,
    )
    if nrows == 1 and ncols == 1:
        axes = np.array([[axes]])
    elif nrows == 1:
        axes = axes[np.newaxis, :]
    elif ncols == 1:
        axes = axes[:, np.newaxis]

    for r, sw in enumerate(switchings):
        for c, sp in enumerate(samplings):
            ax = axes[r, c]
            path = cells.get((sw, sp))
            if path is None:
                ax.axis("off")
                continue
            try:
                df = pd.read_csv(path)
                if len(df) < 2:
                    ax.text(0.5, 0.5, "empty", transform=ax.transAxes,
                            ha="center", va="center")
                    ax.set_xticks([]); ax.set_yticks([])
                    continue
                plot_tile(ax, df, sw, sp, fet)
            except Exception as exc:
                ax.text(0.5, 0.5, f"err\n{exc}", transform=ax.transAxes,
                        ha="center", va="center", fontsize=7)
                ax.set_xticks([]); ax.set_yticks([])

    # Row / column headers
    for r, sw in enumerate(switchings):
        axes[r, 0].set_ylabel(f"{sw:g} Hz", fontsize=9, fontweight="bold")
    for c, sp in enumerate(samplings):
        axes[0, c].annotate(
            f"{sp} sps", xy=(0.5, 1.15), xycoords="axes fraction",
            ha="center", va="bottom", fontsize=9, fontweight="bold",
        )
    fig.suptitle(
        f"DOE grid — sensor {fet.upper()}  "
        f"(row=switching, col=sampling, green=clean, orange=marginal, red=aliased)",
        fontsize=10,
    )
    fig.supxlabel("time (s)", fontsize=9)
    fig.supylabel("switching frequency", fontsize=9)
    fig.tight_layout(rect=(0.02, 0.02, 1, 0.96))

    if save:
        fig.savefig(save, dpi=120, bbox_inches="tight")
        print(f"saved {save}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="CSV files or globs")
    parser.add_argument("--fet", default="p1", choices=["p1", "p2", "n1", "n2"],
                        help="which sensor to compare against (default p1)")
    parser.add_argument("--save", type=Path, default=None,
                        help="save PNG instead of opening window")
    args = parser.parse_args()

    files: list[Path] = []
    for p in args.paths:
        matches = sorted(Path(f) for f in glob.glob(p))
        if not matches and Path(p).exists():
            matches = [Path(p)]
        files.extend(matches)
    if not files:
        print("no files matched", file=sys.stderr)
        sys.exit(1)
    build_grid(files, args.fet, args.save)


if __name__ == "__main__":
    main()
