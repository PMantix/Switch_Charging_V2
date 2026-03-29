"""
Switching Circuit V2 - Main TUI Application.

Two-column layout with the circuit diagram on the left and control
panels on the right. Connects to the Raspberry Pi server over TCP
and updates in real-time via a state subscription stream.
"""

import logging
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static, Input, Label
from textual.worker import Worker

from tui.client import PiClient, ConnectionState
from tui.widgets.circuit_diagram import CircuitDiagram, STATE_DEFS
from tui.widgets.left_panel import LeftPanel
from tui.widgets.right_panel import RightPanel
from tui.widgets.connection_bar import ConnectionBar
from tui.widgets.mascot import Mascot

log = logging.getLogger(__name__)

MIN_FREQ = 0.1
MAX_FREQ = 300.0

CIRCUIT_MODES = ["idle", "charge", "discharge", "pulse_charge"]


# ---------------------------------------------------------------------------
# Connection dialog
# ---------------------------------------------------------------------------
class ConnectDialog(ModalScreen[str]):
    """Modal dialog for entering the Pi's IP address."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConnectDialog {
        align: center middle;
    }
    #connect-box {
        width: 50;
        height: 10;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #connect-title {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
    }
    #connect-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="connect-box"):
            yield Label("Connect to Raspberry Pi", id="connect-title")
            yield Input(placeholder="IP address (e.g. 192.168.1.100)", id="ip-input")
            yield Label("Press Enter to connect, Escape to cancel", id="connect-hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss("")


# ---------------------------------------------------------------------------
# Help overlay
# ---------------------------------------------------------------------------
class HelpScreen(ModalScreen[None]):
    """Simple help overlay showing all key bindings."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-box {
        width: 56;
        height: 24;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(
                "[bold cyan]Switching Circuit V2 - Key Bindings[/]\n\n"
                "[bold]Space[/]     Toggle start/stop (idle <-> charge)\n"
                "[bold]1-8[/]       Select sequence\n"
                "[bold]w / s[/]     Frequency +/- 0.1 Hz (fine)\n"
                "[bold]e / d[/]     Frequency +/- 1.0 Hz (medium)\n"
                "[bold]W / S[/]     Frequency +/- 10 Hz (coarse)\n"
                "[bold]m[/]         Cycle mode (idle->charge->discharge)\n"
                "[bold]c[/]         Set mode: Charge\n"
                "[bold]i[/]         Set mode: Idle\n"
                "[bold]x[/]         Set mode: Discharge\n"
                "[bold]r[/]         Reconnect to Pi\n"
                "[bold]?[/]         Toggle this help\n"
                "[bold]q[/]         Quit\n\n"
                "[dim]Press Escape or ? to close[/]"
            )

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
class SwitchingCircuitApp(App):
    """TUI client for the Switching Circuit V2 Pi server."""

    TITLE = "Switching Circuit V2"
    SUB_TITLE = "H-Bridge Controller"

    CSS = """
    Screen {
        background: $background;
    }
    #main-layout {
        height: 1fr;
    }
    #left-col {
        width: 32;
        border-right: solid $accent;
        overflow-y: auto;
        align-horizontal: left;
    }
    #center-col {
        width: 1fr;
        min-width: 68;
        overflow-y: auto;
        align-horizontal: center;
    }
    #right-col {
        width: 36;
        border-left: solid $accent;
        overflow-y: auto;
        align-horizontal: left;
    }
    ConnectionBar {
        dock: top;
        height: 1;
    }
    CircuitDiagram {
        width: auto;
    }
    Mascot {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("question_mark", "help", "Help"),
        Binding("space", "toggle_run", "Start/Stop", priority=True),
        Binding("1", "seq_1", "Seq 1", show=False),
        Binding("2", "seq_2", "Seq 2", show=False),
        Binding("3", "seq_3", "Seq 3", show=False),
        Binding("4", "seq_4", "Seq 4", show=False),
        Binding("5", "seq_5", "Seq 5", show=False),
        Binding("6", "seq_6", "Seq 6", show=False),
        Binding("7", "seq_7", "Seq 7", show=False),
        Binding("8", "seq_8", "Seq 8", show=False),
        Binding("w", "freq_up_fine", "+0.1Hz", show=False),
        Binding("s", "freq_down_fine", "-0.1Hz", show=False),
        Binding("e", "freq_up_med", "+1Hz", show=False),
        Binding("d", "freq_down_med", "-1Hz", show=False),
        Binding("W", "freq_up_coarse", "+10Hz", show=False),
        Binding("S", "freq_down_coarse", "-10Hz", show=False),
        Binding("m", "cycle_mode", "Mode", show=False),
        Binding("c", "mode_charge", "Charge", show=False),
        Binding("i", "mode_idle", "Idle", show=False),
        Binding("x", "mode_discharge", "Discharge", show=False),
        Binding("p", "mode_pulse", "Pulse", show=False),
        Binding("r", "reconnect", "Reconnect", show=False),
    ]

    def __init__(self, host: str = "", port: int = 5555):
        super().__init__()
        self._initial_host = host
        self._initial_port = port
        self._client: Optional[PiClient] = None
        self._circuit_mode = "idle"
        self._current_freq = 1.0
        self._current_seq = 0

    # -- Compose -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield ConnectionBar(id="conn-bar")
        with Horizontal(id="main-layout"):
            with Vertical(id="left-col"):
                yield LeftPanel(id="left-panel")
                yield Mascot(id="mascot")
            with Vertical(id="center-col"):
                yield CircuitDiagram(id="circuit")
            with Vertical(id="right-col"):
                yield RightPanel(id="right-panel")
        yield Footer()

    # -- Lifecycle -----------------------------------------------------------

    def on_mount(self) -> None:
        """Initialize client and connect."""
        self._client = PiClient(
            on_state=self._on_state_update,
            on_connection_change=self._on_connection_change,
        )

        if self._initial_host:
            self._do_connect(self._initial_host, self._initial_port)
        else:
            # Show the connection dialog
            self.push_screen(ConnectDialog(), self._on_connect_dialog_result)

    def _on_connect_dialog_result(self, result: str) -> None:
        if result:
            self._do_connect(result, self._initial_port)
        else:
            # User cancelled; run disconnected
            self._update_connection_ui(ConnectionState.DISCONNECTED)

    def _do_connect(self, host: str, port: int) -> None:
        conn_bar = self.query_one("#conn-bar", ConnectionBar)
        conn_bar.host = f"{host}:{port}"
        conn_bar.conn_label = "Connecting..."
        self._update_status_connection(False, "Connecting...")

        # Connect in a worker to avoid blocking the UI
        self.run_worker(
            self._connect_worker(host, port),
            name="connect",
            exclusive=True,
        )

    async def _connect_worker(self, host: str, port: int) -> None:
        """Worker coroutine that performs the blocking connect."""
        import asyncio

        loop = asyncio.get_event_loop()
        assert self._client is not None
        connected = await loop.run_in_executor(
            None, self._client.connect, host, port
        )
        if connected:
            # Subscribe to the state stream
            await loop.run_in_executor(None, self._client.subscribe)

    # -- State stream callback (called from background thread) ---------------

    def _on_state_update(self, data: dict) -> None:
        """Called from the PiClient recv thread with each state event."""
        self.call_from_thread(self._apply_state, data)

    def _apply_state(self, data: dict) -> None:
        """Apply a state update to all widgets (runs on the UI thread)."""
        mode = data.get("mode", "idle")
        seq = data.get("sequence", 0)
        step = data.get("step", 0)
        freq = data.get("frequency", 1.0)
        fets = data.get("fet_states", [False, False, False, False])

        self._circuit_mode = mode
        self._current_freq = freq
        self._current_seq = seq

        # Determine state index from FET states
        state_idx = self._fets_to_state_index(fets)

        # Update widgets
        circuit = self.query_one("#circuit", CircuitDiagram)
        circuit.update_from_server(fets, state_idx)

        rpanel = self.query_one("#right-panel", RightPanel)
        rpanel.mode = mode
        rpanel.sequence = seq
        rpanel.frequency = freq
        rpanel.step = step

        mascot = self.query_one("#mascot", Mascot)
        mascot.circuit_mode = mode

    @staticmethod
    def _fets_to_state_index(fets: list[bool]) -> int:
        """Map a FET state list to a state definition index."""
        tup = tuple(fets)
        for i, sd in enumerate(STATE_DEFS):
            if sd == tup:
                return i
        return 5  # default to all-off

    # -- Connection change callback ------------------------------------------

    def _on_connection_change(self, state: ConnectionState) -> None:
        """Called from PiClient thread on connection state changes."""
        self.call_from_thread(self._update_connection_ui, state)

    def _update_connection_ui(self, state: ConnectionState) -> None:
        conn_bar = self.query_one("#conn-bar", ConnectionBar)
        rpanel = self.query_one("#right-panel", RightPanel)

        if state == ConnectionState.CONNECTED:
            conn_bar.connected = True
            conn_bar.conn_label = "Connected"
            rpanel.connected = True
            rpanel.conn_status = "Connected"
        elif state == ConnectionState.CONNECTING:
            conn_bar.connected = False
            conn_bar.conn_label = "Connecting..."
            rpanel.connected = False
            rpanel.conn_status = "Connecting..."
        else:
            conn_bar.connected = False
            conn_bar.conn_label = "Disconnected"
            rpanel.connected = False
            rpanel.conn_status = "Disconnected"

    def _update_status_connection(self, connected: bool, label: str) -> None:
        rpanel = self.query_one("#right-panel", RightPanel)
        rpanel.connected = connected
        rpanel.conn_status = label

    # -- Actions: Mode -------------------------------------------------------

    def _send_mode(self, mode: str) -> None:
        if self._client and self._client.connection_state == ConnectionState.CONNECTED:
            self._client.set_mode(mode)

    def action_toggle_run(self) -> None:
        new_mode = "charge" if self._circuit_mode == "idle" else "idle"
        self._send_mode(new_mode)

    def action_cycle_mode(self) -> None:
        try:
            idx = CIRCUIT_MODES.index(self._circuit_mode)
        except ValueError:
            idx = 0
        new_mode = CIRCUIT_MODES[(idx + 1) % len(CIRCUIT_MODES)]
        self._send_mode(new_mode)

    def action_mode_charge(self) -> None:
        self._send_mode("charge")

    def action_mode_idle(self) -> None:
        self._send_mode("idle")

    def action_mode_discharge(self) -> None:
        self._send_mode("discharge")

    def action_mode_pulse(self) -> None:
        self._send_mode("pulse_charge")

    # -- Actions: Sequence ---------------------------------------------------

    def _send_sequence(self, seq: int) -> None:
        if self._client and self._client.connection_state == ConnectionState.CONNECTED:
            self._client.set_sequence(seq)

    def action_seq_1(self) -> None:
        self._send_sequence(0)

    def action_seq_2(self) -> None:
        self._send_sequence(1)

    def action_seq_3(self) -> None:
        self._send_sequence(2)

    def action_seq_4(self) -> None:
        self._send_sequence(3)

    def action_seq_5(self) -> None:
        self._send_sequence(4)

    def action_seq_6(self) -> None:
        self._send_sequence(5)

    def action_seq_7(self) -> None:
        self._send_sequence(6)

    def action_seq_8(self) -> None:
        self._send_sequence(7)

    # -- Actions: Frequency --------------------------------------------------

    def _adjust_freq(self, delta: float) -> None:
        new_freq = round(max(MIN_FREQ, min(MAX_FREQ, self._current_freq + delta)), 1)
        if self._client and self._client.connection_state == ConnectionState.CONNECTED:
            self._client.set_frequency(new_freq)
            self._current_freq = new_freq

    def action_freq_up_fine(self) -> None:
        self._adjust_freq(0.1)

    def action_freq_down_fine(self) -> None:
        self._adjust_freq(-0.1)

    def action_freq_up_med(self) -> None:
        self._adjust_freq(1.0)

    def action_freq_down_med(self) -> None:
        self._adjust_freq(-1.0)

    def action_freq_up_coarse(self) -> None:
        self._adjust_freq(10.0)

    def action_freq_down_coarse(self) -> None:
        self._adjust_freq(-10.0)

    # -- Actions: Help & Reconnect -------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_reconnect(self) -> None:
        if self._client:
            self._client.disconnect()
        self.push_screen(ConnectDialog(), self._on_connect_dialog_result)
