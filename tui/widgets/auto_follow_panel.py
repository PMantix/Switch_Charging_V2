"""
Switching Circuit V2 - Auto-Follow Settings Modal.

Pop-up panel that lets the user toggle threshold-driven mode switching
on/off, pick the target switching mode (charge / pulse_charge), and
tune the enter/exit current thresholds. Values are sent live to the
server on each keystroke.
"""

from __future__ import annotations

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class AutoFollowPanel(ModalScreen[None]):
    """Modal for configuring the auto-follow controller."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("space", "toggle_enabled", "Toggle"),
        Binding("t", "cycle_target", "Target"),
        Binding("]", "enter_up", "I_enter +1mA"),
        Binding("[", "enter_down", "I_enter -1mA"),
        Binding("}", "exit_up", "I_exit +0.5mA"),
        Binding("{", "exit_down", "I_exit -0.5mA"),
    ]

    DEFAULT_CSS = """
    AutoFollowPanel {
        align: center middle;
    }
    #af-box {
        width: 56;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #af-title {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
    }
    #af-body {
        width: 100%;
        height: auto;
    }
    #af-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        get_status: Callable[[], dict],
        send_cmd: Callable[[dict], Optional[dict]],
    ):
        super().__init__()
        self._get_status = get_status
        self._send_cmd = send_cmd
        # Local mirror of state — refreshed on every action so the panel
        # reflects what the server actually accepted.
        self._state = get_status() or {}

    # -- compose -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="af-box"):
            yield Label("[bold]Auto-Follow Settings[/]", id="af-title")
            yield Static(self._render_body(), id="af-body")
            yield Label(
                "[dim]space=toggle  t=target  [/] / [/] -/+ I_enter  "
                "{/} -/+ I_exit  Esc=close[/]",
                id="af-hint",
            )

    # -- rendering -----------------------------------------------------------

    def _render_body(self) -> str:
        s = self._state
        enabled = s.get("enabled", False)
        active = s.get("active", False)
        target = s.get("target_mode", "charge")
        i_enter_ma = s.get("i_enter_a", 0.0) * 1000.0
        i_exit_ma = s.get("i_exit_a", 0.0) * 1000.0
        avg_i_ma = s.get("avg_current_a", 0.0) * 1000.0
        avg_v = s.get("avg_voltage_v", 0.0)

        en_color = "bold green" if enabled else "dim"
        en_text = "ON" if enabled else "OFF"

        if not enabled:
            state_text = "[dim]disabled[/]"
        elif active:
            state_text = "[bold green reverse] SWITCHING [/]"
        else:
            state_text = "[bold yellow] transparent [/]"

        return (
            f"\n"
            f"  Enabled       [{en_color}]{en_text}[/]\n"
            f"  Target mode   [bold]{target}[/]\n"
            f"\n"
            f"  I_enter       [bold]{i_enter_ma:6.2f}[/] mA\n"
            f"  I_exit        [bold]{i_exit_ma:6.2f}[/] mA\n"
            f"\n"
            f"  Live current  {avg_i_ma:+7.2f} mA\n"
            f"  Live voltage  {avg_v:7.4f} V\n"
            f"\n"
            f"  State         {state_text}\n"
        )

    def _refresh(self) -> None:
        body = self.query_one("#af-body", Static)
        body.update(self._render_body())

    # -- actions -------------------------------------------------------------

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle_enabled(self) -> None:
        new = not bool(self._state.get("enabled", False))
        resp = self._send_cmd({"cmd": "auto_follow_set_enabled", "enabled": new})
        if resp and resp.get("ok"):
            self._state = resp.get("auto_follow", self._state)
            self._refresh()

    def action_cycle_target(self) -> None:
        cur = self._state.get("target_mode", "charge")
        nxt = "pulse_charge" if cur == "charge" else "charge"
        resp = self._send_cmd({"cmd": "auto_follow_set_target", "target_mode": nxt})
        if resp and resp.get("ok"):
            self._state = resp.get("auto_follow", self._state)
            self._refresh()

    def action_enter_up(self) -> None:
        self._adjust(d_enter=0.001)

    def action_enter_down(self) -> None:
        self._adjust(d_enter=-0.001)

    def action_exit_up(self) -> None:
        self._adjust(d_exit=0.0005)

    def action_exit_down(self) -> None:
        self._adjust(d_exit=-0.0005)

    def _adjust(self, d_enter: float = 0.0, d_exit: float = 0.0) -> None:
        i_enter = max(0.0001, self._state.get("i_enter_a", 0.005) + d_enter)
        i_exit = max(0.0, self._state.get("i_exit_a", 0.002) + d_exit)
        # Maintain enter > exit by at least 0.1 mA
        if i_enter <= i_exit:
            i_enter = i_exit + 0.0001
        resp = self._send_cmd({
            "cmd": "auto_follow_set_thresholds",
            "i_enter_a": round(i_enter, 6),
            "i_exit_a": round(i_exit, 6),
        })
        if resp and resp.get("ok"):
            self._state = resp.get("auto_follow", self._state)
            self._refresh()

    # -- live update from broadcast -----------------------------------------

    def on_mount(self) -> None:
        # Periodic refresh so the live current/voltage updates while the
        # panel is open. set_interval runs on the UI thread.
        self.set_interval(0.25, self._poll_status)

    def _poll_status(self) -> None:
        snap = self._get_status()
        if snap and snap != self._state:
            self._state = snap
            self._refresh()
