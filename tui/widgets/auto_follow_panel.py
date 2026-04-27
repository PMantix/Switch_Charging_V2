"""
Switching Circuit V2 - Auto-Follow Settings Modal.

Pop-up panel that lets the user toggle threshold-driven mode switching
on/off, pick the target switching mode (charge / pulse_charge), and
tune the enter/exit current thresholds. Values are sent live to the
server on each keystroke.
"""

from __future__ import annotations

from typing import Callable, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label


class _AutoFollowBody(Widget):
    """Body of the auto-follow modal. Re-renders automatically when
    `state` changes (Static.update() proved unreliable on older
    Textual; a reactive-driven custom widget always refreshes)."""

    DEFAULT_CSS = """
    _AutoFollowBody { width: 100%; height: auto; }
    """

    state: reactive[dict] = reactive({}, layout=True)

    def render(self) -> Text:
        s = self.state or {}
        enabled = bool(s.get("enabled", False))
        active = bool(s.get("active", False))
        target = s.get("target_mode", "charge")
        i_enter_ma = s.get("i_enter_a", 0.0) * 1000.0
        i_exit_ma = s.get("i_exit_a", 0.0) * 1000.0
        avg_i_ma = s.get("avg_current_a", 0.0) * 1000.0
        avg_v = s.get("avg_voltage_v", 0.0)

        t = Text()
        t.append("\n  Enabled       ")
        t.append("ON" if enabled else "OFF",
                 style="bold green" if enabled else "dim")
        t.append("\n  Target mode   ")
        t.append(target, style="bold")
        t.append("\n\n  I_enter       ")
        t.append(f"{i_enter_ma:6.2f}", style="bold")
        t.append(" mA\n  I_exit        ")
        t.append(f"{i_exit_ma:6.2f}", style="bold")
        t.append(" mA\n\n  Live current  ")
        t.append(f"{avg_i_ma:+7.2f} mA")
        t.append(f"\n  Live voltage  {avg_v:7.4f} V\n\n  State         ")
        if not enabled:
            t.append("disabled", style="dim")
        elif active:
            t.append(" SWITCHING ", style="bold green reverse")
        else:
            t.append(" transparent ", style="bold yellow")
        t.append("\n")
        return t

    def watch_state(self, _: dict) -> None:
        self.refresh()


class AutoFollowPanel(ModalScreen[None]):
    """Modal for configuring the auto-follow controller."""

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
        self._state = get_status() or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="af-box"):
            yield Label("[bold]Auto-Follow Settings[/]", id="af-title")
            yield _AutoFollowBody(id="af-body")
            yield Label(
                "[dim]enter=toggle  t=target  \\[ \\] adjust I_enter  "
                "{ } adjust I_exit  Esc=close[/]",
                id="af-hint",
            )

    def on_mount(self) -> None:
        # Make sure the modal screen, not the underlying app, gets keys.
        self.focus()
        # Seed the body
        self._refresh()
        # Live refresh so current/voltage update while the panel is open.
        self.set_interval(0.25, self._poll_status)

    def _refresh(self) -> None:
        body = self.query_one("#af-body", _AutoFollowBody)
        # Assign a NEW dict so reactive notices the change (mutating
        # the same dict in place won't trigger watch_state).
        body.state = dict(self._state)

    # -- key dispatch --------------------------------------------------------
    # Direct dispatch via on_key. BINDINGS resolution is unreliable for
    # this modal: it has no focusable children to anchor focus on, and
    # several keys collide with non-priority app bindings.
    def on_key(self, event) -> None:
        handler = {
            "escape": self.action_close,
            "enter": self.action_toggle_enabled,
            "t": self.action_cycle_target,
            "]": self.action_enter_up,
            "[": self.action_enter_down,
            "}": self.action_exit_up,
            "{": self.action_exit_down,
        }.get(event.key)
        if handler is not None:
            handler()
            event.stop()

    # -- actions -------------------------------------------------------------

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle_enabled(self) -> None:
        new = not bool(self._state.get("enabled", False))
        resp = self._send_cmd({"cmd": "auto_follow_set_enabled", "enabled": new})
        self._handle_resp(resp, label=f"Auto-follow {'ON' if new else 'OFF'}")

    def action_cycle_target(self) -> None:
        cur = self._state.get("target_mode", "charge")
        nxt = "pulse_charge" if cur == "charge" else "charge"
        resp = self._send_cmd({"cmd": "auto_follow_set_target", "target_mode": nxt})
        self._handle_resp(resp, label=f"Target = {nxt}")

    def action_enter_up(self) -> None: self._adjust(d_enter=0.001)
    def action_enter_down(self) -> None: self._adjust(d_enter=-0.001)
    def action_exit_up(self) -> None: self._adjust(d_exit=0.0005)
    def action_exit_down(self) -> None: self._adjust(d_exit=-0.0005)

    def _adjust(self, d_enter: float = 0.0, d_exit: float = 0.0) -> None:
        i_enter = max(0.0001, self._state.get("i_enter_a", 0.005) + d_enter)
        i_exit = max(0.0, self._state.get("i_exit_a", 0.002) + d_exit)
        if i_enter <= i_exit:
            i_enter = i_exit + 0.0001
        resp = self._send_cmd({
            "cmd": "auto_follow_set_thresholds",
            "i_enter_a": round(i_enter, 6),
            "i_exit_a": round(i_exit, 6),
        })
        self._handle_resp(resp, label=None)  # silent; values redraw in body

    def _handle_resp(self, resp, label: Optional[str]) -> None:
        if resp and resp.get("ok"):
            self._state = resp.get("auto_follow", self._state)
            self._refresh()
            if label:
                self.app.notify(label, title="Auto-Follow", timeout=1.0)
        else:
            self.app.notify(
                f"Command failed: {resp}",
                title="Auto-Follow", severity="warning", timeout=3.0,
            )

    # -- live status polling -------------------------------------------------

    def _poll_status(self) -> None:
        snap = self._get_status()
        if snap and snap != self._state:
            self._state = snap
            self._refresh()
