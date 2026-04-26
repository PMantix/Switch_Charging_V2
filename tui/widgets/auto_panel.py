"""
Switching Circuit V2 - Schedule Monitor Panel.

Passive PLAN | OBSERVED display. Shows the loaded schedule's expected
state alongside what CyclerDetector currently sees, plus a divergence
indicator. Does not control any modes.

Class kept named AutoPanel for backwards-compatible widget IDs in the
TUI layout; consider renaming in a follow-up.
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


class AutoPanel(Widget):
    """Schedule monitor display — shown in the right column whenever a
    schedule is loaded."""

    DEFAULT_CSS = """
    AutoPanel {
        width: 100%;
        height: auto;
        padding: 0 1;
    }
    """

    monitor_data: reactive[dict] = reactive({}, layout=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._text_cache: Text | None = None
        self._text_cache_key: str | None = None

    def render(self) -> Text:
        # Most ticks change only the elapsed-second fields; cache the
        # heavy Text build by a JSON snapshot of the input.
        try:
            key = json.dumps(self.monitor_data, sort_keys=True, default=str)
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
        t.append(" → Status panel\n\n", style="dim")
        m = self.monitor_data

        if not m or not m.get("loaded"):
            t.append(" SCHEDULE MONITOR\n", style="bold cyan underline")
            t.append("\n No schedule loaded\n", style="dim")
            t.append("\n Press ", style="dim")
            t.append(" a ", style="bold white on dark_blue")
            t.append(" to load a schedule.\n", style="dim")
            return t

        self._render_monitor(t, m)
        return t

    def _render_monitor(self, t: Text, m: dict) -> None:
        plan = m.get("plan", {}) or {}
        obs = m.get("observed", {}) or {}
        divergence = m.get("divergence", "unknown")
        running = m.get("running", False)
        complete = plan.get("schedule_complete", False)

        t.append(" SCHEDULE MONITOR", style="bold blue underline")
        if not running:
            t.append("  STOPPED", style="bold yellow reverse")
        elif complete:
            t.append("  COMPLETE", style="bold green reverse")
        t.append("\n\n")

        name = m.get("schedule_name", "?")
        cycle = plan.get("cycle", 0)
        total_cycles = m.get("total_cycles", 1)
        total_steps = m.get("total_steps", 0)
        step_idx = plan.get("step_index", 0)

        t.append(" Schedule ", style="dim")
        t.append(f"{name}\n", style="bold white")
        t.append(" Cycle    ", style="dim")
        t.append(f"{cycle + 1}", style="bold white")
        t.append(f" / {total_cycles}\n", style="dim")
        t.append(" Step     ", style="dim")
        t.append(f"{step_idx + 1}", style="bold white")
        t.append(f" / {total_steps}\n\n", style="dim")

        # Two-column PLAN | OBSERVED display
        plan_state = plan.get("expected_state", "")
        plan_label, plan_style = STATE_STYLES.get(
            plan_state, (plan_state.upper() if plan_state else "—", "white"),
        )
        obs_state = obs.get("state", "unknown")
        obs_label, obs_style = STATE_STYLES.get(obs_state, (obs_state.upper(), "white"))

        t.append(" ", style="dim")
        t.append("PLAN", style="bold cyan")
        t.append("              ", style="dim")
        t.append("OBSERVED\n", style="bold cyan")
        t.append(" ─────────────    ────────────\n", style="dim")

        step_name = plan.get("step_name", "—")
        if len(step_name) > 12:
            step_name = step_name[:11] + "…"
        t.append(f" {step_name:<13}", style="white")
        t.append("    ", style="dim")
        t.append(f"{obs_label}\n", style=obs_style)

        t.append(f" {plan_label:<13}", style=plan_style)
        t.append("    ", style="dim")
        confidence = obs.get("confidence", 0.0)
        t.append(f"{confidence*100:.0f}% conf\n", style="dim")

        elapsed = plan.get("step_elapsed_s", 0.0)
        timeout = plan.get("step_timeout_s", 0.0)
        current_a = obs.get("current_a", 0.0)
        voltage_v = obs.get("voltage_v", 0.0)

        t.append(f" {self._fmt_time(elapsed)}/{self._fmt_time(timeout):<8}",
                 style="white")
        t.append("  ", style="dim")
        t.append(f"{current_a*1000:+6.1f} mA\n", style="white")

        pct = min(1.0, elapsed / timeout) if timeout > 0 else 0
        bar_w = 13
        filled = int(pct * bar_w)
        t.append(" [", style="dim")
        bar_color = "bold green" if running and not complete else "dim"
        t.append("█" * filled, style=bar_color)
        t.append("░" * (bar_w - filled), style="dim")
        t.append("]   ", style="dim")
        t.append(f"{voltage_v:6.4f} V\n", style="white")

        t.append("\n")

        t.append(" Divergence  ", style="dim")
        if divergence == "match":
            t.append("✓ FOLLOWING\n", style="bold green")
        elif divergence == "mismatch":
            t.append("✗ DIVERGED\n", style="bold red reverse")
        else:
            t.append("? unknown\n", style="dim yellow")

        t.append("\n")

        t.append(" CONTROLS\n", style="bold cyan underline")
        t.append("\n")
        t.append("  ", style="dim")
        t.append(" a ", style="bold white on dark_blue")
        t.append(" Load schedule    ", style="dim")
        t.append(" M ", style="bold white on dark_blue")
        t.append(" Restart clock\n", style="dim")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        s = int(seconds)
        if s >= 3600:
            return f"{s // 3600}h {(s % 3600) // 60}m {s % 60}s"
        if s >= 60:
            return f"{s // 60}m {s % 60}s"
        return f"{s}s"

    def watch_monitor_data(self, _: dict) -> None:
        self.refresh()
