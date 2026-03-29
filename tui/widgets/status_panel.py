"""
Switching Circuit V2 - Status Panel Widget.

Displays mode, sequence, frequency, period, step, and connection status.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


MODE_STYLES = {
    "charge": "bold green",
    "discharge": "bold yellow",
    "idle": "bold red",
}


class StatusPanel(Widget):
    """Compact status display for the switching circuit."""

    DEFAULT_CSS = """
    StatusPanel {
        width: 100%;
        min-height: 10;
        padding: 0 1;
    }
    """

    mode: reactive[str] = reactive("idle")
    sequence: reactive[int] = reactive(0)
    frequency: reactive[float] = reactive(1.0)
    step: reactive[int] = reactive(0)
    connected: reactive[bool] = reactive(False)
    conn_status: reactive[str] = reactive("Disconnected")

    def render(self) -> Text:
        t = Text()
        t.append("  STATUS\n", style="bold cyan underline")
        t.append("\n")

        # Mode
        mode_upper = self.mode.upper()
        style = MODE_STYLES.get(self.mode, "bold white")
        t.append("  Mode:       ", style="dim")
        t.append(f"  {mode_upper}  ", style=f"{style} reverse")
        t.append("\n")

        # Sequence
        t.append("  Sequence:   ", style="dim")
        t.append(f"  {self.sequence + 1}", style="bold white")
        t.append(f"  / 8\n", style="dim")

        # Frequency
        t.append("  Frequency:  ", style="dim")
        t.append(f"  {self.frequency:.1f} Hz\n", style="bold white")

        # Period
        period = 1.0 / self.frequency if self.frequency > 0 else float("inf")
        t.append("  Period:     ", style="dim")
        if period < 1.0:
            t.append(f"  {period * 1000:.1f} ms\n", style="white")
        else:
            t.append(f"  {period:.3f} s\n", style="white")

        # Step
        t.append("  Step:       ", style="dim")
        t.append(f"  {self.step + 1}", style="bold white")
        t.append(f"  / 4\n", style="dim")

        # Connection
        t.append("\n")
        t.append("  Connection: ", style="dim")
        if self.connected:
            t.append(f"  {self.conn_status}", style="bold green")
        else:
            t.append(f"  {self.conn_status}", style="bold red")
        t.append("\n")

        return t

    def watch_mode(self, _: str) -> None:
        self.refresh()

    def watch_sequence(self, _: int) -> None:
        self.refresh()

    def watch_frequency(self, _: float) -> None:
        self.refresh()

    def watch_step(self, _: int) -> None:
        self.refresh()

    def watch_connected(self, _: bool) -> None:
        self.refresh()

    def watch_conn_status(self, _: str) -> None:
        self.refresh()
