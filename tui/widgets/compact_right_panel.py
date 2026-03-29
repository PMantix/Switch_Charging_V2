"""
Switching Circuit V2 - Compact Right Panel.

Single widget combining status, frequency, mode, sequence, and help
into a dense layout suitable for 80x24 terminals.
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
}


class CompactRightPanel(Widget):
    """All controls and info in one compact widget."""

    DEFAULT_CSS = """
    CompactRightPanel {
        width: 100%;
        height: 1fr;
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

        # --- Status line ---
        ms = MODE_STYLES.get(self.mode, "white")
        t.append(f" {self.mode.upper():<10}", style=f"{ms} reverse")
        t.append(f" Seq:{self.sequence + 1}/8", style="bold white")
        t.append(f" Step:{self.step + 1}/4", style="white")
        t.append("\n")

        # --- Frequency ---
        t.append(f" {self.frequency:6.1f} Hz", style="bold white on dark_blue")
        period = 1.0 / self.frequency if self.frequency > 0 else 0
        if period < 1.0:
            t.append(f"  ({period*1000:.1f}ms)", style="dim")
        else:
            t.append(f"  ({period:.2f}s)", style="dim")
        t.append("\n")

        # --- Frequency keys ---
        t.append(" w/s", style="bold")
        t.append(":.1 ", style="dim")
        t.append("e/d", style="bold")
        t.append(":1 ", style="dim")
        t.append("W/S", style="bold")
        t.append(":10", style="dim")
        t.append("\n")

        # --- Separator ---
        t.append(" ─────────────────────────────\n", style="dim")

        # --- Sequences ---
        t.append(" SEQUENCES", style="bold cyan")
        t.append(" (1-8)\n", style="dim")
        for i in range(8):
            sel = (i == self.sequence)
            mk = ">" if sel else " "
            steps = str(SEQUENCES[i])
            if sel:
                t.append(f" {mk}{i+1}", style="bold green")
                t.append(f" {steps}", style="bold white")
            else:
                t.append(f" {mk}{i+1}", style="dim")
                t.append(f" {steps}", style="dim")
            t.append("\n")

        # --- Separator ---
        t.append(" ─────────────────────────────\n", style="dim")

        # --- Mode ---
        t.append(" MODE", style="bold cyan")
        t.append("  ", style="dim")
        for m in ["idle", "charge", "discharge"]:
            if m == self.mode:
                t.append(f" [{m[0].upper()}]", style=MODE_STYLES[m])
            else:
                t.append(f"  {m[0].upper()} ", style="dim")
        t.append("\n")

        # --- Keys ---
        t.append(" m", style="bold")
        t.append(":cycle ", style="dim")
        t.append("c", style="bold")
        t.append(":chg ", style="dim")
        t.append("i", style="bold")
        t.append(":idle ", style="dim")
        t.append("x", style="bold")
        t.append(":dis", style="dim")
        t.append("\n")

        # --- Connection ---
        if self.connected:
            t.append(" \u25cf ", style="bold green")
            t.append(self.conn_status, style="green")
        else:
            t.append(" \u25cb ", style="bold red")
            t.append(self.conn_status, style="red")
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
