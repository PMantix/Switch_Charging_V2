"""
Switching Circuit V2 - Connection Bar Widget.

Shows connection status, Pi hostname/IP, and latency.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


AUTO_STATE_STYLES = {
    "cc_charge": ("CC CHG", "bold green"),
    "cv_charge": ("CV CHG", "bold yellow"),
    "rest": ("REST", "bold dim"),
    "discharge": ("DISCH", "bold red"),
    "unknown": ("???", "bold magenta"),
}


class ConnectionBar(Widget):
    """Top bar showing connection info and auto mode status."""

    DEFAULT_CSS = """
    ConnectionBar {
        width: 100%;
        height: 1;
        background: $surface;
    }
    """

    connected: reactive[bool] = reactive(False)
    host: reactive[str] = reactive("")
    latency_ms: reactive[float] = reactive(0.0)
    conn_label: reactive[str] = reactive("Disconnected")
    probe_text: reactive[str] = reactive("")
    _auto_data: dict = {}

    def render(self) -> Text:
        t = Text()

        if self.connected:
            t.append(" \u25cf ", style="bold green")
            t.append("Connected", style="green")
            t.append(f"  {self.host}", style="bold white")
            if self.latency_ms > 0:
                t.append(f"  ({self.latency_ms:.0f}ms)", style="dim")
        else:
            t.append(" \u25cb ", style="bold red")
            t.append(self.conn_label, style="red")
            if self.host:
                t.append(f"  {self.host}", style="dim")

        # Auto mode status (right side of bar)
        ad = self._auto_data
        if ad and ad.get("running"):
            step_name = ad.get("step_name", "?")
            cycle = ad.get("cycle", 0) + 1
            total_cycles = ad.get("total_cycles", 1)
            detected = ad.get("detected_state", "unknown")
            match = ad.get("match", False)
            paused = ad.get("paused", False)
            det_label, det_style = AUTO_STATE_STYLES.get(detected, ("?", "white"))

            t.append("  \u2502 ", style="dim")
            t.append("AUTO ", style="bold blue")
            if paused:
                t.append("PAUSED ", style="bold yellow")
            t.append(f"{step_name}", style="bold white")
            t.append(f" [{cycle}/{total_cycles}]", style="dim")
            t.append("  ", style="dim")
            t.append(f"{det_label}", style=det_style)
            if match:
                t.append(" \u2714", style="bold green")
            else:
                t.append(" \u2718", style="bold red")
            if ad.get("in_timeout"):
                t.append("  TIMEOUT", style="bold red reverse")

        if self.probe_text:
            t.append("  │ ", style="dim")
            t.append(self.probe_text, style="bold cyan")

        return t

    def update_auto_status(self, auto_data: dict) -> None:
        """Store auto status data. Only refreshes if data actually changed."""
        if auto_data == self._auto_data:
            return
        self._auto_data = auto_data
        self.refresh()

    def watch_connected(self, _: bool) -> None:
        self.refresh()

    def watch_host(self, _: str) -> None:
        self.refresh()

    def watch_latency_ms(self, _: float) -> None:
        self.refresh()

    def watch_conn_label(self, _: str) -> None:
        self.refresh()

    def watch_probe_text(self, _: str) -> None:
        self.refresh()
