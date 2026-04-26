"""
Switching Circuit V2 - Pi Picker modal.

Lets the user swap the active Pi without restarting the TUI. Probes every
fleet hostname in parallel, lists the live ones with latency, and dismisses
with the chosen hostname (or the empty string on cancel).
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from tui.discovery import (
    FLEET_HOSTNAMES,
    FleetHit,
    discover_fleet_async,
)
from tui.widgets.pi_list import PiEntry, PiList


class PiPicker(ModalScreen[str]):
    """Modal screen that probes the fleet and lets the user pick a Pi.

    Dismisses with the chosen hostname, or "" on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("r", "rescan", "Rescan", show=False),
        Binding("enter", "confirm", "Switch", show=False),
        *[Binding(str(n), f"select_{n}", show=False) for n in range(1, 9)],
    ]

    DEFAULT_CSS = """
    PiPicker {
        align: center middle;
    }
    #picker-box {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #picker-title {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
    }
    #picker-current {
        width: 100%;
        margin-bottom: 1;
        color: $text-muted;
    }
    #picker-status {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    #picker-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, current_host: str = ""):
        super().__init__()
        self._current_host = current_host

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label("Switch Pi", id="picker-title")
            yield Label(
                f"Currently: {self._current_host or '(not connected)'}",
                id="picker-current",
            )
            yield PiList(id="picker-list")
            yield Label("[dim]Probing fleet...[/]", id="picker-status")
            yield Label(
                "↑↓ select  1-8 jump  r rescan  ⏎ switch  Esc cancel",
                id="picker-hint",
            )

    def on_mount(self) -> None:
        self._populate_offline_placeholders()
        self._start_scan()

    # -- scanning ------------------------------------------------------------

    def _populate_offline_placeholders(self) -> None:
        """Show all 8 hostnames immediately (greyed out) so the picker feels
        responsive while the scan runs. Live hits replace them as they
        return."""
        entries = [
            PiEntry(
                hostname=h,
                latency_ms=None,
                is_current=(h == self._current_host),
                online=False,
            )
            for h in FLEET_HOSTNAMES
        ]
        try:
            self.query_one("#picker-list", PiList).set_entries(entries)
        except Exception:
            pass

    def _start_scan(self) -> None:
        try:
            self.query_one("#picker-status", Label).update("[dim]Probing fleet...[/]")
        except Exception:
            pass
        discover_fleet_async(callback=self._on_scan_result)

    def _on_scan_result(self, hits: list[FleetHit]) -> None:
        try:
            self.app.call_from_thread(self._apply_scan_result, hits)
        except Exception:
            pass

    def _apply_scan_result(self, hits: list[FleetHit]) -> None:
        live_hostnames = {h.hostname for h in hits}

        # Live hits first (already sorted by hostname), offline after.
        entries: list[PiEntry] = []
        for hit in hits:
            entries.append(PiEntry(
                hostname=hit.hostname,
                latency_ms=hit.latency_ms,
                is_current=(hit.hostname == self._current_host),
                online=True,
            ))
        for h in FLEET_HOSTNAMES:
            if h not in live_hostnames:
                entries.append(PiEntry(
                    hostname=h,
                    latency_ms=None,
                    is_current=(h == self._current_host),
                    online=False,
                ))

        try:
            self.query_one("#picker-list", PiList).set_entries(entries)
            self.query_one("#picker-status", Label).update(
                f"[dim]{len(hits)} live  /  {len(FLEET_HOSTNAMES)} hostnames[/]"
            )
        except Exception:
            pass

    # -- actions -------------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss("")

    def action_rescan(self) -> None:
        self._populate_offline_placeholders()
        self._start_scan()

    def action_confirm(self) -> None:
        picker = self.query_one("#picker-list", PiList)
        entry = picker.selected_entry()
        if not entry:
            self._set_status("[bold red]No Pi selected[/]")
            return
        if not entry.online:
            self._set_status(f"[bold yellow]{entry.hostname} is offline[/]")
            return
        if entry.hostname == self._current_host:
            self._set_status("[dim]Already on that Pi[/]")
            return
        self.dismiss(entry.hostname)

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#picker-status", Label).update(msg)
        except Exception:
            pass

    def _select(self, idx: int) -> None:
        self.query_one("#picker-list", PiList).set_selected(idx)

    def action_select_1(self) -> None: self._select(0)
    def action_select_2(self) -> None: self._select(1)
    def action_select_3(self) -> None: self._select(2)
    def action_select_4(self) -> None: self._select(3)
    def action_select_5(self) -> None: self._select(4)
    def action_select_6(self) -> None: self._select(5)
    def action_select_7(self) -> None: self._select(6)
    def action_select_8(self) -> None: self._select(7)
