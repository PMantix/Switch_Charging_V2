"""
Switching Circuit V2 - Animated Mascot Widget.

A little lightning bolt character that reacts to the circuit state.
"""

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


# Compact animation frames (3 lines each)
IDLE_FRAMES = [
    [r"  (o.o) zzZ", r"  /| |\ ", r"  _| |_ "],
    [r"  (-.-) zz ", r"  /| |\ ", r"  _| |_ "],
]

CHARGE_FRAMES = [
    [r" \(^_^)/ *", r"  /| |\   ", r"  _/ \_  "],
    [r" *(^o^)* /", r"  \| |/   ", r"  _/ \_  "],
    [r" /(^_^)\ *", r"  /| |\   ", r"  _/ \_  "],
]

DISCHARGE_FRAMES = [
    [r"**(O_O)** ", r" /|###|\  ", r" _/   \_ "],
    [r" *(O_O)*  ", r" /|###|\  ", r" _/   \_ "],
]

PULSE_FRAMES = [
    [r" <(^_^)>  ", r"  /| |\   ", r"  _/ \_  "],
    [r"  (^_^)   ", r" <|   |>  ", r"  _/ \_  "],
    [r" >(^_^)<  ", r"  /| |\   ", r"  _/ \_  "],
]

MODE_FRAMES = {
    "idle": IDLE_FRAMES,
    "charge": CHARGE_FRAMES,
    "discharge": DISCHARGE_FRAMES,
    "pulse_charge": PULSE_FRAMES,
}

MODE_STYLES = {
    "idle": "dim",
    "charge": "bold yellow",
    "discharge": "bold red",
    "pulse_charge": "bold magenta",
}


class Mascot(Widget):
    """Animated mascot that reacts to circuit mode."""

    DEFAULT_CSS = """
    Mascot {
        dock: bottom;
        width: 100%;
        height: auto;
        padding: 0 1;
    }
    """

    circuit_mode: reactive[str] = reactive("idle")
    _frame: int = 0

    def on_mount(self) -> None:
        self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        frames = MODE_FRAMES.get(self.circuit_mode, IDLE_FRAMES)
        self._frame = (self._frame + 1) % len(frames)
        self.refresh()

    def render(self) -> Text:
        mode = self.circuit_mode
        frames = MODE_FRAMES.get(mode, IDLE_FRAMES)
        frame = frames[self._frame % len(frames)]
        style = MODE_STYLES.get(mode, "dim")

        t = Text()
        for line in frame:
            t.append(f"  {line}\n", style=style)
        return t

    def watch_circuit_mode(self, _: str) -> None:
        self._frame = 0
        self.refresh()
