"""
Switching Circuit V2 - INA226 Calibration Wizard.

Two-point linear calibration per channel.  The user connects a cell
across a terminal pair (e.g. P1+N2) with an external ammeter in series.
Both sensors see the same current but get independent gain/offset
corrections because each has its own shunt resistor.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

PAIRS = [
    ("P1+N1", 0, 2),
    ("P1+N2", 0, 3),
    ("P2+N1", 1, 2),
    ("P2+N2", 1, 3),
]
SENSOR_NAMES = ["P1", "P2", "N1", "N2"]
SAMPLE_TARGET = 50


class Step(Enum):
    SELECT_PAIR = auto()
    LOW_ENTRY = auto()
    LOW_CAPTURE = auto()
    HIGH_ENTRY = auto()
    HIGH_CAPTURE = auto()
    CONFIRM = auto()


class CalibrationScreen(ModalScreen[Optional[dict]]):
    """Multi-step calibration wizard. Dismisses with calibration result or None."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "next_step", "Next", show=False),
    ]

    DEFAULT_CSS = """
    CalibrationScreen {
        align: center middle;
    }
    #cal-box {
        width: 64;
        height: auto;
        max-height: 28;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #cal-title {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
    }
    #cal-body {
        width: 100%;
        min-height: 6;
    }
    #cal-input {
        width: 100%;
        margin-top: 1;
    }
    #cal-status {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    #cal-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self):
        super().__init__()
        self._step = Step.SELECT_PAIR
        self._pair_idx = 0
        self._ch_a = 0
        self._ch_b = 2
        self._low_ref = 0.0
        self._high_ref = 0.0
        self._low_raw_a = 0.0
        self._low_raw_b = 0.0
        self._high_raw_a = 0.0
        self._high_raw_b = 0.0
        self._samples_a: list[float] = []
        self._samples_b: list[float] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="cal-box"):
            yield Label("INA226 Calibration", id="cal-title")
            yield Label("", id="cal-body")
            yield Input(placeholder="", id="cal-input")
            yield Label("", id="cal-status")
            yield Label("Enter to continue  Esc to cancel", id="cal-hint")

    def on_mount(self) -> None:
        self._show_step()

    def _show_step(self) -> None:
        body = self.query_one("#cal-body", Label)
        inp = self.query_one("#cal-input", Input)
        status = self.query_one("#cal-status", Label)

        if self._step == Step.SELECT_PAIR:
            lines = "[bold]Select terminal pair:[/]\n\n"
            for i, (name, _, _) in enumerate(PAIRS):
                marker = " > " if i == self._pair_idx else "   "
                style = "bold" if i == self._pair_idx else ""
                lines += f"{marker}[{style}]{i+1}  {name}[/{style}]\n"
            reset_marker = " > " if self._pair_idx == len(PAIRS) else "   "
            reset_style = "bold" if self._pair_idx == len(PAIRS) else ""
            lines += f"\n{reset_marker}[{reset_style}]5  Reset All Calibration[/{reset_style}]"
            body.update(lines)
            inp.display = False
            status.update("[dim]↑↓ select  1-5 jump  Enter confirm  Esc cancel[/]")

        elif self._step == Step.LOW_ENTRY:
            pair_name = PAIRS[self._pair_idx][0]
            body.update(
                f"[bold]Pair: {pair_name}[/]\n\n"
                "Set a [bold]low[/] known current.\n"
                "Enter the ammeter reading in mA:"
            )
            inp.display = True
            inp.value = ""
            inp.placeholder = "e.g. 10.5"
            inp.focus()
            status.update("")

        elif self._step == Step.LOW_CAPTURE:
            self._samples_a.clear()
            self._samples_b.clear()
            self._install_sink()
            body.update(
                f"Capturing low-point samples...\n"
                f"Hold current steady."
            )
            inp.display = False
            status.update(f"[dim]0/{SAMPLE_TARGET} samples[/]")

        elif self._step == Step.HIGH_ENTRY:
            body.update(
                f"Low point captured:\n"
                f"  Reference: {self._low_ref:.3f} mA\n"
                f"  {SENSOR_NAMES[self._ch_a]} raw: {self._low_raw_a*1000:.3f} mA\n"
                f"  {SENSOR_NAMES[self._ch_b]} raw: {self._low_raw_b*1000:.3f} mA\n\n"
                "Set a [bold]high[/] known current.\n"
                "Enter the ammeter reading in mA:"
            )
            inp.display = True
            inp.value = ""
            inp.placeholder = "e.g. 50.0"
            inp.focus()
            status.update("")

        elif self._step == Step.HIGH_CAPTURE:
            self._samples_a.clear()
            self._samples_b.clear()
            self._install_sink()
            body.update(
                f"Capturing high-point samples...\n"
                f"Hold current steady."
            )
            inp.display = False
            status.update(f"[dim]0/{SAMPLE_TARGET} samples[/]")

        elif self._step == Step.CONFIRM:
            self._compute_and_show()

    def _install_sink(self) -> None:
        self.app._cal_sample_sink = self._on_sample

    def _remove_sink(self) -> None:
        if hasattr(self.app, "_cal_sample_sink"):
            self.app._cal_sample_sink = None

    def _on_sample(self, sensors: dict) -> None:
        if len(self._samples_a) >= SAMPLE_TARGET:
            return
        sa = sensors.get(SENSOR_NAMES[self._ch_a], {})
        sb = sensors.get(SENSOR_NAMES[self._ch_b], {})
        ca = sa.get("current", 0.0)
        cb = sb.get("current", 0.0)
        self._samples_a.append(ca)
        self._samples_b.append(cb)
        n = len(self._samples_a)
        try:
            status = self.query_one("#cal-status", Label)
            mean_a = sum(self._samples_a) / n
            mean_b = sum(self._samples_b) / n
            status.update(
                f"[dim]{n}/{SAMPLE_TARGET}  "
                f"{SENSOR_NAMES[self._ch_a]}={mean_a*1000:.3f}mA  "
                f"{SENSOR_NAMES[self._ch_b]}={mean_b*1000:.3f}mA[/]"
            )
        except Exception:
            pass

        if n >= SAMPLE_TARGET:
            self._remove_sink()
            avg_a = sum(self._samples_a) / n
            avg_b = sum(self._samples_b) / n
            if self._step == Step.LOW_CAPTURE:
                self._low_raw_a = avg_a
                self._low_raw_b = avg_b
                self._step = Step.HIGH_ENTRY
                self.call_later(self._show_step)
            elif self._step == Step.HIGH_CAPTURE:
                self._high_raw_a = avg_a
                self._high_raw_b = avg_b
                self._step = Step.CONFIRM
                self.call_later(self._show_step)

    def _compute_and_show(self) -> None:
        low_ref_a = self._low_ref / 1000.0
        high_ref_a = self._high_ref / 1000.0

        results = []
        for ch, raw_low, raw_high in [
            (self._ch_a, self._low_raw_a, self._high_raw_a),
            (self._ch_b, self._low_raw_b, self._high_raw_b),
        ]:
            delta_raw = raw_high - raw_low
            if abs(delta_raw) < 1e-9:
                try:
                    self.query_one("#cal-body", Label).update(
                        "[bold red]Error:[/] Raw readings are identical.\n"
                        "Use more different currents."
                    )
                    self.query_one("#cal-status", Label).update("[dim]Press Esc to cancel[/]")
                except Exception:
                    pass
                return
            gain = (high_ref_a - low_ref_a) / delta_raw
            offset = low_ref_a - gain * raw_low
            results.append((ch, gain, offset))

        lines = "[bold]Calibration Results:[/]\n\n"
        for ch, gain, offset in results:
            lines += (
                f"  [bold]{SENSOR_NAMES[ch]}[/]: "
                f"gain={gain:.5f}  offset={offset*1000:.4f}mA\n"
            )
        lines += f"\n  Low ref: {self._low_ref:.3f}mA  High ref: {self._high_ref:.3f}mA\n"
        lines += "\n[bold]Enter[/] to save  [bold]Esc[/] to cancel"

        try:
            self.query_one("#cal-body", Label).update(lines)
            self.query_one("#cal-input", Input).display = False
            self.query_one("#cal-status", Label).update("")
        except Exception:
            pass

        self._results = results

    def action_cancel(self) -> None:
        self._remove_sink()
        self.dismiss(None)

    def action_next_step(self) -> None:
        if self._step == Step.SELECT_PAIR:
            if self._pair_idx < len(PAIRS):
                _, self._ch_a, self._ch_b = PAIRS[self._pair_idx]
                self._step = Step.LOW_ENTRY
                self._show_step()
            return

        if self._step in (Step.LOW_ENTRY, Step.HIGH_ENTRY):
            try:
                inp = self.query_one("#cal-input", Input)
                val = float(inp.value.strip())
            except (ValueError, AttributeError):
                try:
                    self.query_one("#cal-status", Label).update(
                        "[bold red]Enter a valid number in mA[/]"
                    )
                except Exception:
                    pass
                return

            if self._step == Step.LOW_ENTRY:
                self._low_ref = val
                self._step = Step.LOW_CAPTURE
            else:
                self._high_ref = val
                self._step = Step.HIGH_CAPTURE
            self._show_step()
            return

        if self._step == Step.CONFIRM:
            if hasattr(self, "_results"):
                self.dismiss({
                    "calibrations": [(ch, g, o) for ch, g, o in self._results]
                })
            return

    def on_key(self, event) -> None:
        if self._step != Step.SELECT_PAIR:
            return
        key = event.key
        if key == "up":
            self._pair_idx = max(0, self._pair_idx - 1)
            self._show_step()
            event.stop()
        elif key == "down":
            self._pair_idx = min(len(PAIRS), self._pair_idx + 1)
            self._show_step()
            event.stop()
        elif event.character in ("1", "2", "3", "4"):
            idx = int(event.character) - 1
            self._pair_idx = idx
            _, self._ch_a, self._ch_b = PAIRS[idx]
            self._step = Step.LOW_ENTRY
            self._show_step()
            event.stop()
        elif event.character == "5" or (key == "enter" and self._pair_idx == len(PAIRS)):
            self.dismiss({"reset": True})
            event.stop()

    def on_unmount(self) -> None:
        self._remove_sink()
