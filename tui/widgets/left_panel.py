"""
Switching Circuit V2 - Left Panel: State Reference & Key Bindings.
"""

from textual.widget import Widget
from rich.text import Text


class LeftPanel(Widget):
    """H-bridge state reference and keyboard controls."""

    DEFAULT_CSS = """
    LeftPanel {
        width: 100%;
        height: auto;
        padding: 0 1;
    }
    """

    def render(self) -> Text:
        t = Text()

        # -- H-Bridge States --
        t.append(" H-BRIDGE STATES\n", style="bold cyan underline")
        t.append("\n")

        states = [
            ("0", "P1+N1", "Forward",    "bold green"),
            ("1", "P1+N2", "Cross A-B",  "bold green"),
            ("2", "P2+N1", "Cross B-A",  "bold green"),
            ("3", "P2+N2", "Reverse",    "bold green"),
            ("4", "ALL",   "Discharge",  "bold yellow"),
            ("5", "---",   "Idle",       "bold red"),
        ]
        for num, fets, desc, style in states:
            t.append(f" {num} ", style="bold white")
            t.append(f"{fets:<6}", style=style)
            t.append(f" {desc}\n", style="dim")
        t.append("\n")

        # -- Key Bindings --
        t.append(" KEY BINDINGS\n", style="bold cyan underline")
        t.append("\n")

        sections = [
            ("CONTROL", [
                ("Space", "Start / Stop"),
                ("  m  ", "Cycle mode"),
            ]),
            ("MODE", [
                ("  c  ", "Charge"),
                ("  i  ", "Idle"),
                ("  x  ", "Discharge"),
                ("  p  ", "Pulse Charge"),
                ("  g  ", "Debug"),
            ]),
            ("FREQUENCY", [
                (" =/- ", "+/- 0.1 Hz"),
                (" w/s ", "+/- 0.1 Hz"),
                (" e/d ", "+/- 1.0 Hz"),
                (" W/S ", "+/- 10 Hz"),
            ]),
            ("SEQUENCE", [
                (" 1-8 ", "Select"),
            ]),
            ("DEBUG (g)", [
                (" 1-4 ", "Toggle P1/P2/N1/N2"),
            ]),
            ("SENSORS", [
                (" / * ", "Rate -/+"),
                ("  v  ", "Cycle plot mode"),
            ]),
            ("RECORD", [
                ("  l  ", "Start/stop"),
                (" [ ] ", "Duration -/+"),
            ]),
            ("OTHER", [
                ("  r  ", "Reconnect"),
                ("  q  ", "Quit"),
            ]),
        ]

        for section_name, keys in sections:
            t.append(f" {section_name}\n", style="bold white")
            for key, desc in keys:
                t.append("  ")
                t.append(f" {key} ", style="bold white on dark_blue")
                t.append(f" {desc}\n", style="dim")
            t.append("\n")

        return t
