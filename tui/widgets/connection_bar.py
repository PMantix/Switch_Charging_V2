"""
Switching Circuit V2 - Connection Bar Widget.

Shows connection status, Pi hostname/IP, and latency.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


class ConnectionBar(Widget):
    """Top bar showing connection info."""

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

        return t

    def watch_connected(self, _: bool) -> None:
        self.refresh()

    def watch_host(self, _: str) -> None:
        self.refresh()

    def watch_latency_ms(self, _: float) -> None:
        self.refresh()

    def watch_conn_label(self, _: str) -> None:
        self.refresh()
