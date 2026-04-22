"""
Switching Circuit V2 - Fleet AP list widget.

Renders visible `pi_SW#` access points plus greyed-out entries for known
fleet hostnames that weren't seen in the scan. Used in the ConnectDialog
today; may be surfaced on a dedicated status screen later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


@dataclass(frozen=True)
class FleetEntry:
    ssid: str
    signal_dbm: Optional[int]
    is_current: bool
    online: bool  # False for hostname-only entries that didn't appear in the scan


class FleetList(Widget):
    """Scrolling list of fleet APs with single-selection.

    Selection index is a reactive so parent screens can observe it. Number
    keys 1..8 are handled at the parent dialog level — this widget exposes
    `set_selected(i)` and arrow-key handling via its own focus.
    """

    DEFAULT_CSS = """
    FleetList {
        height: auto;
        max-height: 10;
        padding: 0 1;
        background: $panel;
        border: round $accent;
    }
    """

    entries: reactive[tuple[FleetEntry, ...]] = reactive(())
    selected_index: reactive[int] = reactive(0)

    can_focus = True

    def set_entries(self, entries: Iterable[FleetEntry]) -> None:
        self.entries = tuple(entries)
        if self.selected_index >= len(self.entries):
            self.selected_index = max(0, len(self.entries) - 1)
        self.refresh()

    def set_selected(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            self.selected_index = index
            self.refresh()

    def selected_entry(self) -> Optional[FleetEntry]:
        if not self.entries:
            return None
        idx = max(0, min(self.selected_index, len(self.entries) - 1))
        return self.entries[idx]

    # -- rendering -----------------------------------------------------------

    def render(self) -> Text:
        if not self.entries:
            return Text("  (no pi_SW# APs visible — press r to rescan)", style="dim")

        out = Text()
        for i, entry in enumerate(self.entries):
            prefix = "▶ " if i == self.selected_index else "  "
            index_label = f"[{i + 1}]" if i < 9 else "[ ]"

            row = Text()
            row.append(prefix, style="bold cyan" if i == self.selected_index else "dim")
            row.append(f"{index_label} ", style="bold")
            row.append(entry.ssid.ljust(10))

            if entry.online:
                if entry.signal_dbm is not None:
                    row.append(f"  {entry.signal_dbm:>4} dBm")
                else:
                    row.append("   —   ")
                if entry.is_current:
                    row.append("  (current)", style="bold green")
            else:
                row.append("  offline", style="dim italic")

            out.append(row)
            if i < len(self.entries) - 1:
                out.append("\n")
        return out

    # -- keys ----------------------------------------------------------------

    def on_key(self, event) -> None:
        if event.key == "up":
            event.stop()
            if self.entries:
                self.selected_index = (self.selected_index - 1) % len(self.entries)
                self.refresh()
        elif event.key == "down":
            event.stop()
            if self.entries:
                self.selected_index = (self.selected_index + 1) % len(self.entries)
                self.refresh()
