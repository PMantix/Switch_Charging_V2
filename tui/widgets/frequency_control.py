"""
Switching Circuit V2 - Frequency Control Widget.

Displays a horizontal bar showing frequency position and key hints.
"""

import math
from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


MIN_FREQ = 0.1
MAX_FREQ = 300.0
BAR_WIDTH = 30


class FrequencyControl(Widget):
    """Frequency display with a visual position bar."""

    DEFAULT_CSS = """
    FrequencyControl {
        width: 100%;
        min-height: 7;
        padding: 0 1;
    }
    """

    frequency: reactive[float] = reactive(1.0)

    def render(self) -> Text:
        t = Text()
        t.append("  FREQUENCY\n", style="bold cyan underline")
        t.append("\n")

        # Numeric display
        t.append("  ", style="dim")
        t.append(f"{self.frequency:6.1f} Hz", style="bold white on dark_blue")
        t.append("\n")

        # Bar: use log scale for better visual distribution
        log_min = math.log10(MIN_FREQ)
        log_max = math.log10(MAX_FREQ)
        log_val = math.log10(max(MIN_FREQ, min(MAX_FREQ, self.frequency)))
        ratio = (log_val - log_min) / (log_max - log_min)
        pos = int(ratio * (BAR_WIDTH - 1))

        t.append("  ", style="dim")
        t.append(f"{MIN_FREQ:.1f}", style="dim")
        t.append(" ", style="dim")
        for i in range(BAR_WIDTH):
            if i == pos:
                t.append("\u2588", style="bold green")  # filled block
            else:
                t.append("\u2591", style="dim")  # light shade
        t.append(" ", style="dim")
        t.append(f"{MAX_FREQ:.0f}", style="dim")
        t.append("\n")

        # Key hints
        t.append("\n")
        t.append("  ", style="dim")
        t.append("w/s", style="bold")
        t.append(" +/-0.1  ", style="dim")
        t.append("e/d", style="bold")
        t.append(" +/-1.0  ", style="dim")
        t.append("W/S", style="bold")
        t.append(" +/-10", style="dim")
        t.append("\n")

        return t

    def watch_frequency(self, _: float) -> None:
        self.refresh()
