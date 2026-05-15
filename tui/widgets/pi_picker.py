"""
Switching Circuit V2 - Pi Picker modal.

Scans for pi_SW# WiFi access points, lists them with signal strength,
and switches the Mac's WiFi to the selected AP. Each Pi runs its own
AP — no shared network, direct 1:1 connection.
"""

from __future__ import annotations

import threading
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from tui.wifi_scan import PiAP, ScanResult, scan_pi_aps, join_ap, current_ssid
from tui.widgets.pi_list import PiEntry, PiList


class PiPicker(ModalScreen[str]):
    """Modal screen that scans for pi_SW# APs and lets the user pick one.

    Dismisses with the chosen SSID (which maps to a Pi), or "" on cancel.
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

    def __init__(self, current_host: str = "", cached_aps: Optional[list[PiAP]] = None):
        super().__init__()
        self._current_ssid = current_ssid() or ""
        self._current_host = current_host
        self._cached_aps = cached_aps
        self._scan_result: Optional[ScanResult] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label("Switch Pi", id="picker-title")
            yield Label(
                f"Currently: {self._current_ssid or '(not connected)'}",
                id="picker-current",
            )
            yield PiList(id="picker-list")
            yield Label("[dim]Scanning WiFi...[/]", id="picker-status")
            yield Label(
                "↑↓ select  1-8 jump  r rescan  ⏎ switch  Esc cancel",
                id="picker-hint",
            )

    def on_mount(self) -> None:
        if self._cached_aps:
            self._apply_aps(self._cached_aps)
        self._start_scan()

    def _start_scan(self) -> None:
        try:
            self.query_one("#picker-status", Label).update("[dim]Scanning WiFi...[/]")
        except Exception:
            pass
        t = threading.Thread(target=self._scan_thread, daemon=True)
        t.start()

    def _scan_thread(self) -> None:
        result = scan_pi_aps()
        try:
            self.app.call_from_thread(self._on_scan_done, result)
        except Exception:
            pass

    def _on_scan_done(self, result: ScanResult) -> None:
        self._scan_result = result
        self._apply_aps(result.aps)
        if hasattr(self.app, "_fleet_cache"):
            self.app._fleet_cache = result.aps
        if result.warning:
            self._set_status(f"[bold yellow]{result.warning}[/]")

    def _apply_aps(self, aps: list[PiAP]) -> None:
        entries = []
        for ap in aps:
            signal_str = f"{ap.signal_dbm}dBm" if ap.signal_dbm is not None else ""
            entries.append(PiEntry(
                hostname=ap.ssid,
                latency_ms=float(ap.signal_dbm) if ap.signal_dbm is not None else None,
                is_current=ap.is_current,
                online=True,
            ))
        try:
            self.query_one("#picker-list", PiList).set_entries(entries)
            n = len(aps)
            current = " · " + self._current_ssid if self._current_ssid else ""
            self.query_one("#picker-status", Label).update(
                f"[dim]{n} AP{'s' if n != 1 else ''} found{current}[/]"
            )
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss("")

    def action_rescan(self) -> None:
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
        ssid = entry.hostname
        if ssid == self._current_ssid:
            self._set_status("[dim]Already on that AP[/]")
            return
        self._set_status(f"[bold]Switching to {ssid}...[/]")
        t = threading.Thread(target=self._join_thread, args=(ssid,), daemon=True)
        t.start()

    def _join_thread(self, ssid: str) -> None:
        def status_cb(msg: str) -> None:
            try:
                self.app.call_from_thread(self._set_status, f"[dim]{msg}[/]")
            except Exception:
                pass

        result = join_ap(ssid, status_cb=status_cb)
        try:
            self.app.call_from_thread(self._on_join_done, result)
        except Exception:
            pass

    def _on_join_done(self, result) -> None:
        if result.ok:
            self.dismiss(result.ssid)
        else:
            self._set_status(f"[bold red]{result.error}[/]")

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
