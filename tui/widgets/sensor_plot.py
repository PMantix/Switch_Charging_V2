"""
Switching Circuit V2 - Sensor Timeseries Plot Widget.

Two stacked plots (Voltage, Current) with all 4 sensors overlaid
on shared axes. Three visualization modes: line, dot, bar.
Uses braille characters for sub-character resolution.
"""

from collections import deque
from time import monotonic

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


SENSOR_COLORS = {
    "P1": "green",
    "P2": "cyan",
    "N1": "yellow",
    "N2": "magenta",
}

VIZ_MODES = ["line", "dot", "bar"]

# Braille character encoding
# Each char is 2 columns x 4 rows of dots
# Left col bits:  row0=0x01 row1=0x02 row2=0x04 row3=0x40
# Right col bits: row0=0x08 row1=0x10 row2=0x20 row3=0x80
BRAILLE_BASE = 0x2800
BRAILLE_DOT = [
    [0x01, 0x02, 0x04, 0x40],  # left column
    [0x08, 0x10, 0x20, 0x80],  # right column
]

# Block elements for bar mode (8 levels, bottom-up)
BAR_BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


class SensorPlot(Widget):
    """Rolling timeseries plots with multiple visualization modes."""

    DEFAULT_CSS = """
    SensorPlot {
        width: 100%;
        height: auto;
        min-height: 14;
        padding: 0 2;
    }
    """

    sensor_rate: reactive[float] = reactive(2.0)
    viz_mode: reactive[str] = reactive("line")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plot_width = 50
        self._plot_height = 6
        self._history: dict[str, deque] = {
            name: deque(maxlen=300)
            for name in ["P1", "P2", "N1", "N2"]
        }
        self._last_update = 0.0

    def push_data(self, sensors: dict) -> None:
        if not sensors:
            return
        now = monotonic()
        for name in ["P1", "P2", "N1", "N2"]:
            data = sensors.get(name)
            if data and "error" not in data:
                v = data.get("voltage", 0.0)
                i = data.get("current", 0.0)
                self._history[name].append((now, v, i))
        self._last_update = now
        self.refresh()

    def cycle_mode(self) -> None:
        idx = VIZ_MODES.index(self.viz_mode)
        self.viz_mode = VIZ_MODES[(idx + 1) % len(VIZ_MODES)]

    # -- Plot builders -------------------------------------------------------

    def _calc_range(self, all_series: dict[str, list[float]], data_width: int):
        """Get min/max across all series for the visible window."""
        all_vals = []
        for vals in all_series.values():
            all_vals.extend(vals[-data_width:])
        if not all_vals:
            return 0.0, 1.0
        mn, mx = min(all_vals), max(all_vals)
        if mn == mx:
            mn -= 0.5
            mx += 0.5
        return mn, mx

    def _build_braille_plot(self, all_series: dict[str, list[float]],
                            width: int, height: int, connect: bool):
        """Build a braille plot. If connect=True, draw lines between points."""
        data_width = width * 2
        dot_rows = height * 4
        mn, mx = self._calc_range(all_series, data_width)
        rng = mx - mn

        # grid[col][row] = set of colors
        grid = [[set() for _ in range(dot_rows)] for _ in range(data_width)]

        for color, vals in all_series.items():
            series = vals[-data_width:]
            offset = data_width - len(series)

            prev_row = None
            for i, v in enumerate(series):
                col = offset + i
                norm = (v - mn) / rng
                row = int((1.0 - norm) * (dot_rows - 1) + 0.5)
                row = max(0, min(dot_rows - 1, row))
                grid[col][row].add(color)

                # Interpolate between previous and current point
                if connect and prev_row is not None and col > 0:
                    prev_col = col - 1
                    r0, r1 = prev_row, row
                    if r0 != r1:
                        step = 1 if r1 > r0 else -1
                        for r in range(r0 + step, r1, step):
                            grid[prev_col][r].add(color)

                prev_row = row

        # Render to braille characters with color
        rows = []
        for char_row in range(height):
            row_chars = []
            for char_col in range(width):
                pattern = BRAILLE_BASE
                colors_in_cell = set()
                for sub_col in range(2):
                    dc = char_col * 2 + sub_col
                    if dc >= data_width:
                        continue
                    for sub_row in range(4):
                        dr = char_row * 4 + sub_row
                        if dr >= dot_rows:
                            continue
                        if grid[dc][dr]:
                            pattern |= BRAILLE_DOT[sub_col][sub_row]
                            colors_in_cell.update(grid[dc][dr])

                if pattern == BRAILLE_BASE:
                    row_chars.append((" ", "dim"))
                else:
                    # When multiple colors share a cell, blend by picking
                    # the one with more dots, or first alphabetically
                    c = sorted(colors_in_cell)[0] if colors_in_cell else "dim"
                    row_chars.append((chr(pattern), c))
            rows.append(row_chars)

        return rows, (mn, mx)

    def _build_bar_plot(self, all_series: dict[str, list[float]],
                        width: int, height: int):
        """Build a bar/sparkline plot with each sensor as a separate row."""
        data_width = width
        mn, mx = self._calc_range(all_series, data_width)
        rng = mx - mn
        total_rows = height * 8  # 8 levels per character height

        rows_out = []
        for color, vals in all_series.items():
            series = vals[-data_width:]
            pad = data_width - len(series)
            row_chars = []
            # Pad left
            for _ in range(pad):
                row_chars.append((" ", "dim"))
            for v in series:
                norm = (v - mn) / rng
                level = int(norm * 8)
                level = max(0, min(8, level))
                row_chars.append((BAR_BLOCKS[level], color))
            rows_out.append((color, row_chars))

        return rows_out, (mn, mx)

    # -- Render --------------------------------------------------------------

    def _render_plot_header(self, t: Text, label: str, style: str,
                            mn: float, mx: float, unit: str) -> None:
        t.append(f" {label}", style=style)
        t.append(f"  {mn:.2f}{unit} \u2014 {mx:.2f}{unit}\n", style="dim")

    def _render_braille_rows(self, t: Text, rows: list) -> None:
        for row in rows:
            t.append(" \u2502", style="dim")
            for ch, color in row:
                t.append(ch, style=color)
            t.append("\u2502\n", style="dim")
        t.append(" \u2514", style="dim")
        t.append("\u2500" * self._plot_width, style="dim")
        t.append("\u2518\n", style="dim")

    def _render_bar_rows(self, t: Text, bar_data: list) -> None:
        for color, row_chars in bar_data:
            name = [n for n, c in SENSOR_COLORS.items() if c == color]
            label = name[0] if name else "?"
            t.append(f" {label} ", style=f"bold {color}")
            t.append("\u2502", style="dim")
            for ch, c in row_chars:
                t.append(ch, style=c)
            t.append("\u2502\n", style="dim")
        t.append("    \u2514", style="dim")
        t.append("\u2500" * self._plot_width, style="dim")
        t.append("\u2518\n", style="dim")

    def render(self) -> Text:
        t = Text()
        pw = self._plot_width
        ph = self._plot_height
        mode = self.viz_mode

        # Header with legend
        t.append(" SENSORS ", style="bold cyan underline")
        t.append(f"[{self.sensor_rate:.0f}Hz]", style="dim")
        t.append(f" [{mode}]", style="dim cyan")
        t.append("  ")
        t.append("/", style="bold white on dark_blue")
        t.append("- ", style="dim")
        t.append("*", style="bold white on dark_blue")
        t.append("+ ", style="dim")
        t.append("v", style="bold white on dark_blue")
        t.append("viz  ", style="dim")
        for name in ["P1", "P2", "N1", "N2"]:
            color = SENSOR_COLORS[name]
            t.append("\u2588", style=color)
            t.append(f"{name} ", style=f"bold {color}")
        t.append("\n")

        # Gather series data
        v_series = {}
        i_series = {}
        for name in ["P1", "P2", "N1", "N2"]:
            hist = self._history[name]
            if hist:
                v_series[SENSOR_COLORS[name]] = [v for _, v, _ in hist]
                i_series[SENSOR_COLORS[name]] = [i * 1000 for _, _, i in hist]

        # -- Voltage --
        if mode in ("line", "dot"):
            connect = (mode == "line")
            v_rows, (v_mn, v_mx) = self._build_braille_plot(v_series, pw, ph, connect)
            self._render_plot_header(t, "VOLTAGE", "bold yellow", v_mn, v_mx, "V")
            self._render_braille_rows(t, v_rows)
        else:
            bar_data, (v_mn, v_mx) = self._build_bar_plot(v_series, pw, 1)
            self._render_plot_header(t, "VOLTAGE", "bold yellow", v_mn, v_mx, "V")
            self._render_bar_rows(t, bar_data)

        # -- Current --
        if mode in ("line", "dot"):
            i_rows, (i_mn, i_mx) = self._build_braille_plot(i_series, pw, ph, connect)
            self._render_plot_header(t, "CURRENT", "bold green", i_mn, i_mx, "mA")
            self._render_braille_rows(t, i_rows)
        else:
            bar_data, (i_mn, i_mx) = self._build_bar_plot(i_series, pw, 1)
            self._render_plot_header(t, "CURRENT", "bold green", i_mn, i_mx, "mA")
            self._render_bar_rows(t, bar_data)

        return t

    def watch_sensor_rate(self, _: float) -> None:
        self.refresh()

    def watch_viz_mode(self, _: str) -> None:
        self.refresh()
