"""
Switching Circuit V2 - Sensor Timeseries Plot Widget.

Two display modes:
  Compact — two overlaid plots (Voltage, Current) with all 4 sensors.
  Expanded — 8 individual plots in a 2-column grid (Voltage | Current)
             with rows +A (P1), +B (P2), -A (N1), -B (N2).

The app toggles `expanded` based on available terminal width.
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

SENSOR_LABELS = {
    "P1": "+A",
    "P2": "+B",
    "N1": "-A",
    "N2": "-B",
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

# Bit flags for colors — used in the bytearray braille grid to avoid set() allocations
COLOR_BITS = {"green": 1, "cyan": 2, "yellow": 4, "magenta": 8}
BIT_TO_COLOR = {1: "green", 2: "cyan", 4: "yellow", 8: "magenta"}


class SensorPlot(Widget):
    """Rolling timeseries plots with compact and expanded modes."""

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
    expanded: reactive[bool] = reactive(False)
    ina_avg: reactive[int] = reactive(4)           # INA226 sample averaging
    # bus_every default 5 mirrors firmware default (2026-04-24 sweep). The
    # rig is format-blocked at ~4 ms/emit regardless of AVG/CT, so the only
    # rate-relevant knob the user touches with `k` is bus_every. Header
    # initial state matches what the firmware actually reports on first M.
    bus_every: reactive[int] = reactive(5)         # bus-voltage decimation
    max_hz: reactive[float] = reactive(244.0)      # firmware-computed cap

    # Expanded plot sizing is driven by the available_width / available_height
    # reactives set by the app's on_resize handler.
    available_width: reactive[int] = reactive(0)
    available_height: reactive[int] = reactive(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plot_width = 50
        self._plot_height = 6
        self._history: dict[str, deque] = {
            name: deque(maxlen=300)
            for name in ["P1", "P2", "N1", "N2"]
        }
        self._last_update = 0.0
        self._last_render_time = 0.0
        self._min_render_interval = 1.0 / 15  # cap plot redraws at ~15 fps

    def push_data(self, sensors: dict) -> None:
        """Compat shim: ingest + rate-limited refresh in one call.

        Prefer append_data() + commit() from inside the app's batch_update()
        so the plot's refresh is coalesced with the other widget updates.
        """
        self.append_data(sensors)
        self.commit()

    def append_data(self, sensors: dict) -> None:
        """Ingest sensor data only. No refresh — pure deque append."""
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

    def commit(self) -> None:
        """Rate-limited refresh. Call inside batch_update() to coalesce with
        other widget updates in the same tick."""
        now = monotonic()
        if now - self._last_render_time >= self._min_render_interval:
            self._last_render_time = now
            self.refresh()

    def cycle_mode(self) -> None:
        idx = VIZ_MODES.index(self.viz_mode)
        self.viz_mode = VIZ_MODES[(idx + 1) % len(VIZ_MODES)]

    # -- Braille plot engine ---------------------------------------------------

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
        """Build a braille plot using a flat bytearray grid (no set() allocations)."""
        data_width = width * 2
        dot_rows = height * 4
        mn, mx = self._calc_range(all_series, data_width)
        rng = mx - mn

        # Flat grid: grid[col * dot_rows + row] = color bit mask
        grid = bytearray(data_width * dot_rows)

        for color, vals in all_series.items():
            bit = COLOR_BITS.get(color, 1)
            series = vals[-data_width:]
            offset = data_width - len(series)

            prev_row = None
            for i, v in enumerate(series):
                col = offset + i
                norm = (v - mn) / rng
                row = int((1.0 - norm) * (dot_rows - 1) + 0.5)
                row = max(0, min(dot_rows - 1, row))
                grid[col * dot_rows + row] |= bit

                if connect and prev_row is not None and col > 0:
                    prev_col = col - 1
                    r0, r1 = prev_row, row
                    if r0 != r1:
                        step = 1 if r1 > r0 else -1
                        for r in range(r0 + step, r1, step):
                            grid[prev_col * dot_rows + r] |= bit

                prev_row = row

        # Render to braille characters with color
        rows = []
        for char_row in range(height):
            row_chars = []
            for char_col in range(width):
                pattern = BRAILLE_BASE
                cell_bits = 0
                for sub_col in range(2):
                    dc = char_col * 2 + sub_col
                    if dc >= data_width:
                        continue
                    base = dc * dot_rows
                    for sub_row in range(4):
                        dr = char_row * 4 + sub_row
                        if dr >= dot_rows:
                            continue
                        b = grid[base + dr]
                        if b:
                            pattern |= BRAILLE_DOT[sub_col][sub_row]
                            cell_bits |= b

                if pattern == BRAILLE_BASE:
                    row_chars.append((" ", "dim"))
                else:
                    # Pick the lowest-bit color (priority: green > cyan > yellow > magenta)
                    c = "dim"
                    for bit_val, color_name in BIT_TO_COLOR.items():
                        if cell_bits & bit_val:
                            c = color_name
                            break
                    row_chars.append((chr(pattern), c))
            rows.append(row_chars)

        return rows, (mn, mx)

    def _build_bar_plot(self, all_series: dict[str, list[float]],
                        width: int, height: int):
        """Build a bar/sparkline plot with each sensor as a separate row."""
        data_width = width
        mn, mx = self._calc_range(all_series, data_width)
        rng = mx - mn

        rows_out = []
        for color, vals in all_series.items():
            series = vals[-data_width:]
            pad = data_width - len(series)
            row_chars = []
            for _ in range(pad):
                row_chars.append((" ", "dim"))
            for v in series:
                norm = (v - mn) / rng
                level = int(norm * 8)
                level = max(0, min(8, level))
                row_chars.append((BAR_BLOCKS[level], color))
            rows_out.append((color, row_chars))

        return rows_out, (mn, mx)

    # -- Render helpers --------------------------------------------------------

    def _render_plot_header(self, t: Text, label: str, style: str,
                            mn: float, mx: float, unit: str) -> None:
        t.append(f" {label}", style=style)
        t.append(f"  {mn:.2f}{unit} \u2014 {mx:.2f}{unit}\n", style="dim")

    def _render_braille_rows(self, t: Text, rows: list, width: int) -> None:
        for row in rows:
            t.append(" \u2502", style="dim")
            for ch, color in row:
                t.append(ch, style=color)
            t.append("\u2502\n", style="dim")
        t.append(" \u2514", style="dim")
        t.append("\u2500" * width, style="dim")
        t.append("\u2518\n", style="dim")

    def _render_bar_rows(self, t: Text, bar_data: list, width: int) -> None:
        for color, row_chars in bar_data:
            name = [n for n, c in SENSOR_COLORS.items() if c == color]
            label = name[0] if name else "?"
            t.append(f" {label} ", style=f"bold {color}")
            t.append("\u2502", style="dim")
            for ch, c in row_chars:
                t.append(ch, style=c)
            t.append("\u2502\n", style="dim")
        t.append("    \u2514", style="dim")
        t.append("\u2500" * width, style="dim")
        t.append("\u2518\n", style="dim")

    # -- Compact render (original: 2 overlaid plots) --------------------------

    def _render_compact_from(self, history: dict = None) -> Text:
        if history is None:
            history = self._history
        t = Text()
        pw = self._plot_width
        ph = self._plot_height
        mode = self.viz_mode

        # Header with legend
        t.append(" SENSORS ", style="bold cyan underline")
        t.append(f"[{self.sensor_rate:.0f}/{self.max_hz:.0f}Hz] ", style="dim")
        t.append(f"avg={self.ina_avg}", style="bold yellow")
        t.append(" ", style="dim")
        bus_tag = "v=off" if self.bus_every == 0 else f"v÷{self.bus_every}"
        t.append(bus_tag, style="bold yellow")
        t.append(f"  [{mode}]", style="dim cyan")
        t.append("  ")
        t.append("/", style="bold white on dark_blue")
        t.append("- ", style="dim")
        t.append("*", style="bold white on dark_blue")
        t.append("+ ", style="dim")
        t.append("j", style="bold white on dark_blue")
        t.append("avg ", style="dim")
        t.append("k", style="bold white on dark_blue")
        t.append("vdec ", style="dim")
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
            hist = history.get(name, [])
            if hist:
                v_series[SENSOR_COLORS[name]] = [v for _, v, _ in hist]
                i_series[SENSOR_COLORS[name]] = [i * 1000 for _, _, i in hist]

        # -- Voltage --
        if mode in ("line", "dot"):
            connect = (mode == "line")
            v_rows, (v_mn, v_mx) = self._build_braille_plot(v_series, pw, ph, connect)
            self._render_plot_header(t, "VOLTAGE", "bold yellow", v_mn, v_mx, "V")
            self._render_braille_rows(t, v_rows, pw)
        else:
            bar_data, (v_mn, v_mx) = self._build_bar_plot(v_series, pw, 1)
            self._render_plot_header(t, "VOLTAGE", "bold yellow", v_mn, v_mx, "V")
            self._render_bar_rows(t, bar_data, pw)

        # -- Current --
        if mode in ("line", "dot"):
            i_rows, (i_mn, i_mx) = self._build_braille_plot(i_series, pw, ph, connect)
            self._render_plot_header(t, "CURRENT", "bold green", i_mn, i_mx, "mA")
            self._render_braille_rows(t, i_rows, pw)
        else:
            bar_data, (i_mn, i_mx) = self._build_bar_plot(i_series, pw, 1)
            self._render_plot_header(t, "CURRENT", "bold green", i_mn, i_mx, "mA")
            self._render_bar_rows(t, bar_data, pw)

        return t

    # -- Expanded render (8 individual plots: 2 cols x 4 rows) ----------------

    def _expanded_dims(self) -> tuple:
        """Calculate per-plot width and height from available space.

        Layout overhead per row:
          "  │" + col_w + "│ │" + col_w + "│"  => col_w*2 + 8 chars
        So col_w = (avail_width - 8) // 2

        Height overhead: 2 lines header + 4 sensors * (1 label + ph plot + 1 border)
          => total rows = 2 + 4*(ph+2)   => ph = (avail_height - 2) / 4 - 2
        """
        aw = self.available_width if self.available_width > 0 else 70
        ah = self.available_height if self.available_height > 0 else 30

        col_w = max(16, (aw - 8) // 2)
        ph = max(3, (ah - 2) // 4 - 2)

        return col_w, ph

    def _render_expanded_from(self, history: dict = None) -> Text:
        if history is None:
            history = self._history
        t = Text()
        mode = self.viz_mode
        connect = (mode == "line")

        col_w, ph = self._expanded_dims()

        # Header
        t.append(" SENSORS ", style="bold cyan underline")
        t.append(f"[{self.sensor_rate:.0f}/{self.max_hz:.0f}Hz] ", style="dim")
        t.append(f"avg={self.ina_avg}", style="bold yellow")
        t.append(" ", style="dim")
        bus_tag = "v=off" if self.bus_every == 0 else f"v÷{self.bus_every}"
        t.append(bus_tag, style="bold yellow")
        t.append(f"  [{mode}]", style="dim cyan")
        t.append("  ")
        t.append("j", style="bold white on dark_blue")
        t.append("avg ", style="dim")
        t.append("k", style="bold white on dark_blue")
        t.append("vdec ", style="dim")
        t.append("v", style="bold white on dark_blue")
        t.append("viz\n", style="dim")

        # Column headers — align to plot positions
        v_header = "VOLTAGE"
        i_header = "CURRENT"
        pad = col_w - len(v_header) + 2
        t.append(f"      {v_header}", style="bold yellow")
        t.append(" " * max(1, pad), style="dim")
        t.append(f"  {i_header}\n", style="bold green")

        sensor_order = ["P1", "P2", "N1", "N2"]

        for sensor in sensor_order:
            hist = history.get(sensor, [])
            color = SENSOR_COLORS[sensor]
            label = SENSOR_LABELS[sensor]

            # Extract voltage and current series
            v_vals = [v for _, v, _ in hist] if hist else []
            i_vals = [i * 1000 for _, _, i in hist] if hist else []

            v_series = {color: v_vals}
            i_series = {color: i_vals}

            if mode == "bar":
                # Bar mode: use block characters
                v_bar, (v_mn, v_mx) = self._build_bar_plot(v_series, col_w, 1)
                i_bar, (i_mn, i_mx) = self._build_bar_plot(i_series, col_w, 1)

                # Sensor label + range header
                t.append(f" {label} ", style=f"bold {color}")
                t.append(f"{v_mn:.2f}-{v_mx:.2f}V", style="dim")
                range_gap = col_w - 10
                t.append(" " * max(1, range_gap), style="dim")
                t.append(f"  {i_mn:.1f}-{i_mx:.1f}mA\n", style="dim")

                # Render bar rows side-by-side
                for (v_color, v_chars), (i_color, i_chars) in zip(v_bar, i_bar):
                    t.append("  \u2502", style="dim")
                    for ch, c in v_chars:
                        t.append(ch, style=c)
                    t.append("\u2502 \u2502", style="dim")
                    for ch, c in i_chars:
                        t.append(ch, style=c)
                    t.append("\u2502\n", style="dim")

                # Bottom border
                t.append("  \u2514", style="dim")
                t.append("\u2500" * col_w, style="dim")
                t.append("\u2518", style="dim")
                t.append(" ", style="dim")
                t.append("\u2514", style="dim")
                t.append("\u2500" * col_w, style="dim")
                t.append("\u2518\n", style="dim")
            else:
                # Line/dot mode: use braille characters
                v_rows, (v_mn, v_mx) = self._build_braille_plot(
                    v_series, col_w, ph, connect)
                i_rows, (i_mn, i_mx) = self._build_braille_plot(
                    i_series, col_w, ph, connect)

                # Sensor label + range header
                t.append(f" {label} ", style=f"bold {color}")
                t.append(f"{v_mn:.2f}-{v_mx:.2f}V", style="dim")
                range_gap = col_w - 10
                t.append(" " * max(1, range_gap), style="dim")
                t.append(f"  {i_mn:.1f}-{i_mx:.1f}mA\n", style="dim")

                # Render braille rows side-by-side
                for row_idx in range(ph):
                    t.append("  \u2502", style="dim")
                    for ch, c in v_rows[row_idx]:
                        t.append(ch, style=c)
                    t.append("\u2502", style="dim")

                    t.append(" ", style="dim")

                    t.append("\u2502", style="dim")
                    for ch, c in i_rows[row_idx]:
                        t.append(ch, style=c)
                    t.append("\u2502\n", style="dim")

                # Bottom border
                t.append("  \u2514", style="dim")
                t.append("\u2500" * col_w, style="dim")
                t.append("\u2518", style="dim")
                t.append(" ", style="dim")
                t.append("\u2514", style="dim")
                t.append("\u2500" * col_w, style="dim")
                t.append("\u2518\n", style="dim")

        return t

    # -- Main render -----------------------------------------------------------

    def render(self) -> Text:
        if self.expanded:
            return self._render_expanded_from()
        return self._render_compact_from()

    def watch_sensor_rate(self, _: float) -> None:
        self._dirty = True

    def watch_ina_avg(self, _: int) -> None:
        self._dirty = True

    def watch_bus_every(self, _: int) -> None:
        self._dirty = True

    def watch_max_hz(self, _: float) -> None:
        self._dirty = True

    def watch_viz_mode(self, _: str) -> None:
        self._dirty = True

    def watch_expanded(self, _: bool) -> None:
        self._dirty = True

    def watch_available_width(self, _: int) -> None:
        self._dirty = True

    def watch_available_height(self, _: int) -> None:
        self._dirty = True
