"""
Switching Circuit V2 - Help Panel & State Reference Widget.

Displays key bindings and H-bridge state descriptions on the right side.
"""

from textual.widget import Widget
from rich.text import Text


STATE_DESCRIPTIONS = [
    ("State 0", "+A/-A", "Forward",  "P1+N1 on: current flows left-to-right through load"),
    ("State 1", "+A/-B", "Cross",    "P1+N2 on: diagonal path, top-left to bottom-right"),
    ("State 2", "+B/-A", "Cross",    "P2+N1 on: diagonal path, top-right to bottom-left"),
    ("State 3", "+B/-B", "Reverse",  "P2+N2 on: current flows right-to-left through load"),
    ("State 4", "ALL",   "Discharge","All FETs on: direct conduction, all paths active"),
    ("State 5", "---",   "Idle",     "All FETs off: no current flowing"),
]


class HelpPanel(Widget):
    """Key bindings and state reference, always visible on the right."""

    DEFAULT_CSS = """
    HelpPanel {
        width: 100%;
        padding: 0 1;
    }
    """

    def render(self) -> Text:
        t = Text()

        # State reference
        t.append("  H-BRIDGE STATES\n", style="bold cyan underline")
        t.append("\n")
        for name, pins, label, desc in STATE_DESCRIPTIONS:
            t.append(f"  {name}", style="bold white")
            t.append(f"  {pins:<5}", style="bold yellow")
            t.append(f"  {label:<10}", style="green")
            t.append(f"{desc}\n", style="dim")
        t.append("\n")

        # Key bindings
        t.append("  KEY BINDINGS\n", style="bold cyan underline")
        t.append("\n")
        keys = [
            ("Space",   "Start / Stop (idle <-> charge)"),
            ("1-8",     "Select sequence directly"),
            ("w / s",   "Frequency +/- 0.1 Hz (fine)"),
            ("e / d",   "Frequency +/- 1.0 Hz (medium)"),
            ("W / S",   "Frequency +/- 10 Hz (coarse)"),
            ("m",       "Cycle mode: idle -> charge -> discharge"),
            ("c / i / x", "Set mode directly: charge / idle / discharge"),
            ("r",       "Reconnect to Pi"),
            ("q",       "Quit"),
        ]
        for key, desc in keys:
            t.append(f"  {key:<11}", style="bold white")
            t.append(f" {desc}\n", style="dim")

        return t
