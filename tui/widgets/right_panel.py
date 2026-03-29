"""
Switching Circuit V2 - Right Panel: Status, Frequency, Sequence, Mode.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


SEQUENCES = [
    [5, 5, 5, 5],
    [0, 1, 2, 3],
    [0, 1, 3, 2],
    [0, 2, 1, 3],
    [0, 2, 3, 1],
    [0, 3, 1, 2],
    [0, 3, 2, 1],
    [4, 4, 4, 4],
]

MODE_STYLES = {
    "idle": "bold red",
    "charge": "bold green",
    "discharge": "bold yellow",
    "pulse_charge": "bold magenta",
}


class RightPanel(Widget):
    """Status, frequency, sequence, and mode."""

    DEFAULT_CSS = """
    RightPanel {
        width: 100%;
        height: auto;
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

        # -- Status --
        t.append(" STATUS\n", style="bold cyan underline")
        t.append("\n")

        ms = MODE_STYLES.get(self.mode, "white")
        t.append(" Mode   ", style="dim")
        t.append(f" {self.mode.upper():<10} ", style=f"{ms} reverse")
        t.append("\n")

        t.append(" Freq   ", style="dim")
        t.append(f" {self.frequency:6.1f} Hz ", style="bold white on dark_blue")
        t.append("\n")

        period = 1.0 / self.frequency if self.frequency > 0 else 0
        t.append(" Period ", style="dim")
        if period < 1.0:
            t.append(f" {period*1000:.1f} ms", style="white")
        else:
            t.append(f" {period:.3f} s", style="white")
        t.append("\n")

        t.append(" Step   ", style="dim")
        t.append(f" {self.step + 1}", style="bold white")
        t.append(" / 4", style="dim")
        t.append("\n")

        # Connection
        if self.connected:
            t.append(" \u25cf ", style="bold green")
            t.append(self.conn_status, style="green")
        else:
            t.append(" \u25cb ", style="bold red")
            t.append(self.conn_status, style="red")
        t.append("\n\n")

        # -- Mode selector --
        t.append(" MODE", style="bold cyan underline")
        t.append("\n\n")

        for m in ["idle", "charge", "discharge", "pulse_charge"]:
            sel = (m == self.mode)
            radio = "\u25cf" if sel else "\u25cb"
            style = MODE_STYLES.get(m, "white")
            key = {"idle": "i", "charge": "c", "discharge": "x", "pulse_charge": "p"}[m]
            if sel:
                t.append(f" {radio} ", style=style)
                t.append(f"{m.upper()}", style=f"{style}")
            else:
                t.append(f" {radio} ", style="dim")
                t.append(f"{m.upper()}", style="dim")
            t.append(f"  ", style="dim")
            t.append(f" {key} ", style="bold white on dark_blue")
            t.append("\n")
        t.append("\n")

        # -- Sequences --
        t.append(" SEQUENCES", style="bold cyan underline")
        t.append("\n\n")

        for i in range(8):
            sel = (i == self.sequence)
            steps = str(SEQUENCES[i])
            if sel:
                t.append(f" \u25b8 ", style="bold green")
                t.append(f"{i+1}", style="bold green")
                t.append(f" {steps}", style="bold white")
            else:
                t.append(f"   ", style="dim")
                t.append(f"{i+1}", style="dim")
                t.append(f" {steps}", style="dim")
            t.append("\n")
        t.append("\n")
        t.append("  ", style="dim")
        t.append(" 1-8 ", style="bold white on dark_blue")
        t.append(" Select sequence\n", style="dim")

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
