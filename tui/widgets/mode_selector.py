"""
Switching Circuit V2 - Mode Selector Widget.

Shows three modes with radio-button style indicator.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


MODES = ["idle", "charge", "discharge"]

MODE_KEYS = {
    "idle": "i",
    "charge": "c",
    "discharge": "x",
}

MODE_STYLES = {
    "idle": "bold red",
    "charge": "bold green",
    "discharge": "bold yellow",
}


class ModeSelector(Widget):
    """Radio-button style mode selector."""

    DEFAULT_CSS = """
    ModeSelector {
        width: 100%;
        min-height: 8;
        padding: 0 1;
    }
    """

    mode: reactive[str] = reactive("idle")

    def render(self) -> Text:
        t = Text()
        t.append("  MODE ", style="bold cyan underline")
        t.append("(m to cycle)\n", style="dim")
        t.append("\n")

        for m in MODES:
            selected = (m == self.mode)
            radio = "(\u25cf)" if selected else "( )"  # filled vs empty circle
            style = MODE_STYLES.get(m, "white")
            key = MODE_KEYS.get(m, "?")

            if selected:
                t.append(f"  {radio} ", style=style)
                t.append(f"{m.upper():<12}", style=f"{style} reverse")
                t.append(f" [{key}]", style="dim")
            else:
                t.append(f"  {radio} ", style="dim")
                t.append(f"{m.upper():<12}", style="dim")
                t.append(f" [{key}]", style="dim")
            t.append("\n")

        return t

    def watch_mode(self, _: str) -> None:
        self.refresh()
