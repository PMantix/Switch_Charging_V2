"""
Switching Circuit V2 - Auto Mode Status Panel.

Displays schedule progress, step list with current step highlighted,
detected vs expected cycler state, recent events log, and timeout warnings.
"""

import json

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


STATE_STYLES = {
    "cc_charge": ("CC CHARGE", "bold green"),
    "cv_charge": ("CV CHARGE", "bold yellow"),
    "rest": ("REST", "bold dim"),
    "discharge": ("DISCHARGE", "bold red"),
    "unknown": ("UNKNOWN", "bold magenta"),
}

STATE_ABBREV = {
    "cc_charge": "CC",
    "cv_charge": "CV",
    "rest": "RST",
    "discharge": "DIS",
}

ACTION_LABELS = {
    "charge": "SWITCHING",
    "discharge": "ALL ON",
    "idle": "ALL OFF",
    "pulse_charge": "PULSE",
}

PHASE_STYLES = {
    "entering": ("ENTERING", "bold cyan"),
    "active": ("ACTIVE", "bold green"),
    "sensing": ("SENSING", "bold yellow"),
    "transitioning": ("TRANSITION", "bold magenta"),
}


class AutoPanel(Widget):
    """Auto mode status display — shown in the right column during AUTO mode."""

    DEFAULT_CSS = """
    AutoPanel {
        width: 100%;
        height: auto;
        padding: 0 1;
    }
    """

    auto_data: reactive[dict] = reactive({}, layout=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._text_cache: Text | None = None
        self._text_cache_key: str | None = None

    def render(self) -> Text:
        # Most auto ticks don't change auto_data (or only change step_elapsed_s
        # while the rest of the dict is stable). Cache by a JSON snapshot so
        # the large Text build only runs when something real changed.
        try:
            key = json.dumps(self.auto_data, sort_keys=True, default=str)
        except (TypeError, ValueError):
            key = None
        if key is not None and key == self._text_cache_key and self._text_cache is not None:
            return self._text_cache
        t = self._render_impl()
        if key is not None:
            self._text_cache = t
            self._text_cache_key = key
        return t

    def _render_impl(self) -> Text:
        t = Text()
        t.append(" Tab", style="bold white on dark_blue")
        t.append(" \u2192 Status panel\n\n", style="dim")
        d = self.auto_data

        if not d or not d.get("running"):
            t.append(" AUTO MODE\n", style="bold cyan underline")
            t.append("\n Not running\n", style="dim")
            return t

        # -- Header --
        t.append(" AUTO MODE", style="bold blue underline")
        if d.get("paused"):
            t.append("  PAUSED", style="bold yellow reverse")
        t.append("\n\n")

        # -- Schedule info --
        name = d.get("schedule_name", "?")
        cycle = d.get("cycle", 0)
        total_cycles = d.get("total_cycles", 1)
        t.append(" Schedule ", style="dim")
        t.append(f"{name}\n", style="bold white")
        t.append(" Cycle    ", style="dim")
        t.append(f"{cycle + 1}", style="bold white")
        t.append(f" / {total_cycles}\n", style="dim")

        # -- Current step --
        step_idx = d.get("step_index", 0)
        total_steps = d.get("total_steps", 0)
        step_name = d.get("step_name", "?")
        elapsed = d.get("step_elapsed_s", 0)
        timeout = d.get("step_timeout_s", 0)

        # Phase
        phase = d.get("step_phase", "active")
        phase_label, phase_style = PHASE_STYLES.get(phase, (phase.upper(), "white"))
        t.append(" Phase    ", style="dim")
        t.append(f"{phase_label}", style=phase_style)

        # Circuit action
        action = d.get("circuit_action", "idle")
        action_label = ACTION_LABELS.get(action, action.upper())
        t.append(f"  ({action_label})\n", style="dim")

        # Timing + progress bar
        t.append(" Elapsed  ", style="dim")
        t.append(f"{self._fmt_time(elapsed)}", style="white")
        t.append(f" / {self._fmt_time(timeout)}", style="dim")
        pct = min(1.0, elapsed / timeout) if timeout > 0 else 0
        bar_w = 14
        filled = int(pct * bar_w)
        t.append(" [", style="dim")
        bar_color = "bold red" if d.get("in_timeout") else "bold green"
        t.append("\u2588" * filled, style=bar_color)
        t.append("\u2591" * (bar_w - filled), style="dim")
        t.append(f"] {pct*100:.0f}%\n", style="dim")

        # Timeout warning
        if d.get("in_timeout"):
            on_timeout = d.get("on_timeout", "wait")
            grace = d.get("timeout_grace_s", 0)
            t.append(" \u26a0 TIMEOUT", style="bold red reverse")
            t.append(f"  {on_timeout}", style="bold yellow")
            if grace > 0:
                t.append(f"  grace={self._fmt_time(grace)}", style="dim")
            t.append("\n")

        t.append("\n")

        # -- Step list --
        self._render_step_list(t, d)

        # -- Detection status --
        t.append(" DETECTION\n", style="bold cyan underline")
        t.append("\n")

        expected = d.get("expected_state", "unknown")
        detected = d.get("detected_state", "unknown")
        confidence = d.get("detected_confidence", 0.0)
        match = d.get("match", False)
        current_ma = d.get("detected_current_ma", 0.0)
        voltage_v = d.get("detected_voltage_v", 0.0)

        exp_label, exp_style = STATE_STYLES.get(expected, (expected.upper(), "white"))
        det_label, det_style = STATE_STYLES.get(detected, (detected.upper(), "white"))

        t.append(" Expected ", style="dim")
        t.append(f"{exp_label}\n", style=exp_style)
        t.append(" Detected ", style="dim")
        t.append(f"{det_label}", style=det_style)
        t.append(f"  {confidence*100:.0f}%\n", style="dim")

        t.append(" Match    ", style="dim")
        if match:
            t.append("\u2714 MATCH\n", style="bold green")
        else:
            t.append("\u2718 MISMATCH\n", style="bold red")

        t.append(f" Current  ", style="dim")
        t.append(f"{current_ma:+.2f} mA\n", style="white")
        t.append(f" Voltage  ", style="dim")
        t.append(f"{voltage_v:.4f} V\n", style="white")
        t.append("\n")

        # -- Recent events --
        self._render_events(t, d)

        # -- Controls hint --
        t.append(" CONTROLS\n", style="bold cyan underline")
        t.append("\n")
        t.append("  ", style="dim")
        t.append(" n ", style="bold white on dark_blue")
        t.append(" Skip step    ", style="dim")
        t.append(" space ", style="bold white on dark_blue")
        t.append(" Pause\n", style="dim")
        t.append("  ", style="dim")
        t.append(" i ", style="bold white on dark_blue")
        t.append(" Stop auto\n", style="dim")

        return t

    def _render_step_list(self, t: Text, d: dict) -> None:
        """Render compact step list with current step highlighted."""
        steps = d.get("steps", [])
        if not steps:
            return

        step_idx = d.get("step_index", 0)
        t.append(" STEPS\n", style="bold cyan underline")
        t.append("\n")

        for i, s in enumerate(steps):
            abbr = STATE_ABBREV.get(s.get("expected_state", ""), "?")
            name = s.get("name", f"Step {i}")
            if len(name) > 18:
                name = name[:17] + "\u2026"

            if i < step_idx:
                t.append("  \u2714 ", style="dim green")
                t.append(f"{name}", style="dim")
                t.append(f" [{abbr}]\n", style="dim")
            elif i == step_idx:
                t.append("  \u25b6 ", style="bold white")
                t.append(f"{name}", style="bold white")
                _, st_style = STATE_STYLES.get(s.get("expected_state", ""), ("?", "white"))
                t.append(f" [{abbr}]\n", style=st_style)
            else:
                t.append("    ", style="dim")
                t.append(f"{name}", style="dim white")
                t.append(f" [{abbr}]\n", style="dim")

        t.append("\n")

    def _render_events(self, t: Text, d: dict) -> None:
        """Render recent event log, color-coded by prefix."""
        events = d.get("recent_events", [])
        if not events:
            return

        t.append(" EVENTS\n", style="bold cyan underline")
        t.append("\n")

        for ev in events[-5:]:
            if not ev or len(ev) < 10:
                t.append(f" {ev}\n", style="dim")
                continue
            ts_part = ev[:8]
            msg_part = ev[9:]
            t.append(f" {ts_part} ", style="dim")
            if msg_part.startswith("!!") or msg_part.startswith("XX"):
                t.append(f"{msg_part}\n", style="bold red")
            elif msg_part.startswith(">>"):
                t.append(f"{msg_part}\n", style="bold green")
            elif msg_part.startswith("<>"):
                t.append(f"{msg_part}\n", style="bold cyan")
            elif msg_part.startswith("=="):
                t.append(f"{msg_part}\n", style="bold yellow")
            else:
                t.append(f"{msg_part}\n", style="dim")

        t.append("\n")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        s = int(seconds)
        if s >= 3600:
            return f"{s // 3600}h {(s % 3600) // 60}m {s % 60}s"
        if s >= 60:
            return f"{s // 60}m {s % 60}s"
        return f"{s}s"

    def watch_auto_data(self, _: dict) -> None:
        self.refresh()
