"""
Switching Circuit V2 - Sequence Selector Widget.

Lists all 8 sequences with current selection highlighted.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


SEQUENCES = [
    [5, 5, 5, 5],   # 1: all-off (idle)
    [0, 1, 2, 3],   # 2
    [0, 1, 3, 2],   # 3
    [0, 2, 1, 3],   # 4
    [0, 2, 3, 1],   # 5
    [0, 3, 1, 2],   # 6
    [0, 3, 2, 1],   # 7
    [4, 4, 4, 4],   # 8: all-on
]

SEQ_LABELS = [
    "Idle (all off)",
    "Fwd > Cross > Rev > Cross",
    "Fwd > Cross > Rev(alt) > Rev",
    "Fwd > Rev > Cross > Rev(alt)",
    "Fwd > Rev > Rev(alt) > Cross",
    "Fwd > Rev(alt) > Cross > Rev",
    "Fwd > Rev(alt) > Rev > Cross",
    "Discharge (all on)",
]


class SequenceSelector(Widget):
    """Displays all 8 sequences with current selection."""

    DEFAULT_CSS = """
    SequenceSelector {
        width: 100%;
        min-height: 12;
        padding: 0 1;
    }
    """

    sequence: reactive[int] = reactive(0)

    def render(self) -> Text:
        t = Text()
        t.append("  SEQUENCES ", style="bold cyan underline")
        t.append("(1-8)\n", style="dim")
        t.append("\n")

        for i in range(8):
            selected = (i == self.sequence)
            marker = " > " if selected else "   "
            num = f"{i + 1}"
            steps = str(SEQUENCES[i])
            label = SEQ_LABELS[i]

            if selected:
                t.append(marker, style="bold green")
                t.append(num, style="bold green")
                t.append(f" {steps:<14}", style="bold white")
                t.append(f" {label}", style="green")
            else:
                t.append(marker, style="dim")
                t.append(num, style="dim")
                t.append(f" {steps:<14}", style="dim")
                t.append(f" {label}", style="dim")
            t.append("\n")

        return t

    def watch_sequence(self, _: int) -> None:
        self.refresh()
