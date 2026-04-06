"""
Switching Circuit V2 - Detailed H-Bridge Circuit Diagram Widget.

Shows N-channel MOSFETs with D/G/S in the vertical power path,
UCC5304 gate drivers branching off horizontally to the gate,
RP2040-Zero GPIO connections to each driver input,
and INA226 power monitors in each MOSFET current path.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


# State definitions: (P1, P2, N1, N2)
STATE_DEFS = [
    (True, False, True, False),   # 0: +A/-A (Forward)
    (True, False, False, True),   # 1: +A/-B (Cross)
    (False, True, True, False),   # 2: +B/-A (Cross)
    (False, True, False, True),   # 3: +B/-B (Reverse)
    (True, True, True, True),     # 4: All on (Discharge)
    (False, False, False, False), # 5: All off (Idle)
]

STATE_LABELS = [
    "Forward (+A/-A)  [Pulse: Battery A]",
    "Cross (+A/-B)",
    "Cross (+B/-A)",
    "Reverse (+B/-B)  [Pulse: Battery B]",
    "DISCHARGE - All Conducting",
    "IDLE - No Current",
]

STATE_PATHS = [
    "VCC > INA226(P1) > P1(D>S) > +A > LOAD > -A > N1(D>S) > INA226(N1) > GND",
    "VCC > INA226(P1) > P1(D>S) > +A > LOAD > -B > N2(D>S) > INA226(N2) > GND",
    "VCC > INA226(P2) > P2(D>S) > +B > LOAD > -A > N1(D>S) > INA226(N1) > GND",
    "VCC > INA226(P2) > P2(D>S) > +B > LOAD > -B > N2(D>S) > INA226(N2) > GND",
    "All MOSFETs conducting to GND",
    "No current flowing",
]


def _on(active: bool) -> str:
    return "bold green" if active else "dim red"

def _wire_h(active: bool) -> str:
    return "\u2501" if active else "\u2500"

def _wire_v(active: bool) -> str:
    return "\u2503" if active else "\u2502"


class CircuitDiagram(Widget):
    """Detailed H-bridge schematic with drivers, MOSFETs, and INA226 sensors."""

    DEFAULT_CSS = """
    CircuitDiagram {
        width: auto;
        height: auto;
        padding: 3 0 0 0;
    }
    """

    fet_states: reactive[tuple[bool, ...]] = reactive((False, False, False, False))
    state_index: reactive[int] = reactive(5)
    telemetry: reactive[dict] = reactive({})

    def render(self) -> Text:
        p1, p2, n1, n2 = self.fet_states
        idx = self.state_index
        tel = self.telemetry

        label = STATE_LABELS[idx] if 0 <= idx <= 5 else "Unknown"
        path = STATE_PATHS[idx] if 0 <= idx <= 5 else ""

        load_lr = (p1 and n1) or (p1 and n2)
        load_rl = (p2 and n1) or (p2 and n2)
        load_on = load_lr or load_rl

        if load_lr and not load_rl:
            arrow = "\u25b6\u25b6\u25b6"
        elif load_rl and not load_lr:
            arrow = "\u25c0\u25c0\u25c0"
        elif load_lr and load_rl:
            arrow = "\u25c0\u25b6\u25c0"
        else:
            arrow = "\u2500\u2500\u2500"

        sp1, sp2, sn1, sn2 = _on(p1), _on(p2), _on(n1), _on(n2)
        sl = _on(load_on)
        d = "dim"
        sg = "bold magenta"       # gate drive signal (RP2040 GPIO)
        sd = "bold cyan"          # driver IC (UCC5304)
        si = "bold yellow"        # INA226 sensor

        hl = _wire_h(load_on)
        vp1, vp2 = _wire_v(p1), _wire_v(p2)
        vn1, vn2 = _wire_v(n1), _wire_v(n2)

        # MOSFET symbol chars
        ch_on  = "\u2551\u2502"  # ║│
        ch_off = "\u2502\u2502"  # ││

        mp1 = ch_on if p1 else ch_off
        mp2 = ch_on if p2 else ch_off
        mn1 = ch_on if n1 else ch_off
        mn2 = ch_on if n2 else ch_off

        # Cell outline style
        sc = "bold white"
        sci = "bold white" if load_on else d

        t = Text()

        # ── Title ──
        t.append("              H-BRIDGE SWITCHING CIRCUIT V2\n", style="bold cyan")
        t.append("            RP2040-Zero + INA226 Sensing\n", style="dim cyan")
        t.append("\n")

        # ── VCC Rail ──
        t.append("              ", style=d)
        t.append(_wire_h(p1) * 7, style=sp1)
        t.append(_wire_h(p1 or p2) * 4, style=_on(p1 or p2))
        t.append(" VCC ", style="bold white")
        t.append(_wire_h(p1 or p2) * 4, style=_on(p1 or p2))
        t.append(_wire_h(p2) * 7, style=sp2)
        t.append("\n")

        # ── INA226 high-side ──
        t.append("              ", style=d)
        t.append(vp1, style=sp1)
        t.append("                            ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        t.append("           ", style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append("                      ", style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append("\n")

        t.append("            ", style=d)
        t.append("(0x40)", style="dim yellow")
        t.append("                        ", style=d)
        t.append("(0x41)", style="dim yellow")
        t.append("\n")

        t.append("              ", style=d)
        t.append(vp1, style=sp1)
        t.append("                            ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # ── High-side Drain ──
        t.append("              ", style=d)
        t.append(vp1, style=sp1)
        t.append("  D                       D  ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # ── High-side MOSFET top ──
        t.append("              ", style=d)
        t.append(mp1, style=sp1)
        t.append("                         ", style=d)
        t.append(mp2, style=sp2)
        t.append("\n")

        # ── High-side MOSFET gate line + driver ──
        # Left driver -> P1 gate
        t.append("    GP2", style=sg if p1 else d)
        t.append("\u2500", style=sg if p1 else d)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500G", style=sp1)
        t.append(mp1, style=sp1)
        t.append("  P1", style=sp1)
        # spacer
        t.append("               ", style=d)
        # Right driver -> P2 gate
        t.append("P2  ", style=sp2)
        t.append(mp2, style=sp2)
        t.append("G\u2500", style=sp2)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500", style=sg if p2 else d)
        t.append("GP3   ", style=sg if p2 else d)
        t.append("\n")

        # ── High-side MOSFET bottom ──
        t.append("              ", style=d)
        t.append(mp1, style=sp1)
        t.append("                         ", style=d)
        t.append(mp2, style=sp2)
        t.append("\n")

        # ── High-side Source ──
        t.append("              ", style=d)
        t.append(vp1, style=sp1)
        t.append("  S                       S  ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # ── +A / +B node labels ──
        t.append("             ", style=d)
        t.append("+A", style="bold" if p1 else d)
        t.append("                           ", style=d)
        t.append("+B", style="bold" if p2 else d)
        t.append("\n")

        # ── Pouch Cell ──
        # Top terminal tabs
        t.append("              ", style=d)
        t.append(vp1, style=sp1)
        t.append("                         ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # Cell top edge with terminal connections
        t.append("              ", style=d)
        t.append(vp1, style=sp1)
        t.append("  \u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510  ", style=sc)
        t.append(vp2, style=sp2)
        t.append("\n")

        t.append("              ", style=d)
        t.append(_wire_h(p1), style=sp1)
        t.append("\u2500\u2500\u2524", style=sp1)
        t.append("+A", style="bold" if p1 else d)
        t.append("             ", style=sci)
        t.append("+B", style="bold" if p2 else d)
        t.append("\u251c\u2500\u2500", style=sp2)
        t.append(_wire_h(p2), style=sp2)
        t.append("\n")

        # Cell body
        t.append("              ", style=d)
        t.append(" ", style=d)
        t.append("  \u2502", style=sc)
        t.append("                   ", style=sci)
        t.append("\u2502  ", style=sc)
        t.append("\n")

        # Arrow row
        t.append("              ", style=d)
        t.append(" ", style=d)
        t.append("  \u2502", style=sc)
        t.append("    ", style=sci)
        t.append(f"  {arrow}  ", style=sl)
        t.append("        ", style=sci)
        t.append("\u2502  ", style=sc)
        t.append("\n")

        # Cell label
        t.append("              ", style=d)
        t.append(" ", style=d)
        t.append("  \u2502", style=sc)
        t.append("    ", style=sci)
        t.append(" POUCH CELL ", style=sci)
        t.append("    ", style=sci)
        t.append("\u2502  ", style=sc)
        t.append("\n")

        # Arrow row 2
        t.append("              ", style=d)
        t.append(" ", style=d)
        t.append("  \u2502", style=sc)
        t.append("    ", style=sci)
        t.append(f"  {arrow}  ", style=sl)
        t.append("        ", style=sci)
        t.append("\u2502  ", style=sc)
        t.append("\n")

        t.append("              ", style=d)
        t.append(" ", style=d)
        t.append("  \u2502", style=sc)
        t.append("                   ", style=sci)
        t.append("\u2502  ", style=sc)
        t.append("\n")

        # Cell bottom terminals
        t.append("              ", style=d)
        t.append(_wire_h(n1), style=sn1)
        t.append("\u2500\u2500\u2524", style=sn1)
        t.append("-A", style="bold" if n1 else d)
        t.append("             ", style=sci)
        t.append("-B", style="bold" if n2 else d)
        t.append("\u251c\u2500\u2500", style=sn2)
        t.append(_wire_h(n2), style=sn2)
        t.append("\n")

        # Cell bottom edge
        t.append("              ", style=d)
        t.append(vn1, style=sn1)
        t.append("  \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518  ", style=sc)
        t.append(vn2, style=sn2)
        t.append("\n")

        # Bottom terminal tabs
        t.append("              ", style=d)
        t.append(vn1, style=sn1)
        t.append("                         ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── -A / -B node labels ──
        t.append("             ", style=d)
        t.append("-A", style="bold" if n1 else d)
        t.append("                           ", style=d)
        t.append("-B", style="bold" if n2 else d)
        t.append("\n")

        # ── Low-side Drain ──
        t.append("              ", style=d)
        t.append(vn1, style=sn1)
        t.append("  D                       D  ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── Low-side MOSFET top ──
        t.append("              ", style=d)
        t.append(mn1, style=sn1)
        t.append("                         ", style=d)
        t.append(mn2, style=sn2)
        t.append("\n")

        # ── Low-side MOSFET gate line + driver ──
        t.append("    GP4", style=sg if n1 else d)
        t.append("\u2500", style=sg if n1 else d)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500G", style=sn1)
        t.append(mn1, style=sn1)
        t.append("  N1", style=sn1)
        t.append("               ", style=d)
        t.append("N2  ", style=sn2)
        t.append(mn2, style=sn2)
        t.append("G\u2500", style=sn2)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500", style=sg if n2 else d)
        t.append("GP5   ", style=sg if n2 else d)
        t.append("\n")

        # ── Low-side MOSFET bottom ──
        t.append("              ", style=d)
        t.append(mn1, style=sn1)
        t.append("                         ", style=d)
        t.append(mn2, style=sn2)
        t.append("\n")

        # ── Low-side Source ──
        t.append("              ", style=d)
        t.append(vn1, style=sn1)
        t.append("  S                       S  ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── INA226 low-side ──
        t.append("              ", style=d)
        t.append(vn1, style=sn1)
        t.append("                            ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        t.append("           ", style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append("                      ", style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append("\n")

        t.append("            ", style=d)
        t.append("(0x44)", style="dim yellow")
        t.append("                        ", style=d)
        t.append("(0x45)", style="dim yellow")
        t.append("\n")

        t.append("              ", style=d)
        t.append(vn1, style=sn1)
        t.append("                            ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── GND Rail ──
        t.append("              ", style=d)
        t.append(_wire_h(n1) * 7, style=sn1)
        t.append(_wire_h(n1 or n2) * 4, style=_on(n1 or n2))
        t.append(" GND ", style="bold white")
        t.append(_wire_h(n1 or n2) * 4, style=_on(n1 or n2))
        t.append(_wire_h(n2) * 7, style=sn2)
        t.append("\n")

        t.append("\n")

        # ── State + Path ──
        t.append("  State: ", style=d)
        t.append(label, style="bold yellow")
        t.append("\n")
        t.append("  Path:  ", style=d)
        t.append(path, style="italic" if load_on else d)
        t.append("\n")

        # ── Telemetry readout ──
        t.append("\n")
        t.append("  \u250c\u2500 INA226 Telemetry ", style="dim cyan")
        t.append("\u2500" * 28, style="dim cyan")
        t.append("\u2510\n", style="dim cyan")

        def _fmt_v(key: str) -> str:
            val = tel.get(key)
            return f"{val:6.3f}V" if val is not None else "  ---V"

        def _fmt_i(key: str) -> str:
            val = tel.get(key)
            return f"{val:6.1f}mA" if val is not None else "  ---mA"

        t.append("  \u2502  ", style="dim cyan")
        t.append("P1 ", style=sp1)
        t.append(_fmt_v("p1_voltage"), style="white")
        t.append("  ", style=d)
        t.append(_fmt_i("p1_current"), style="white")
        t.append("    ", style=d)
        t.append("P2 ", style=sp2)
        t.append(_fmt_v("p2_voltage"), style="white")
        t.append("  ", style=d)
        t.append(_fmt_i("p2_current"), style="white")
        t.append("  \u2502\n", style="dim cyan")

        t.append("  \u2502  ", style="dim cyan")
        t.append("N1 ", style=sn1)
        t.append(_fmt_v("n1_voltage"), style="white")
        t.append("  ", style=d)
        t.append(_fmt_i("n1_current"), style="white")
        t.append("    ", style=d)
        t.append("N2 ", style=sn2)
        t.append(_fmt_v("n2_voltage"), style="white")
        t.append("  ", style=d)
        t.append(_fmt_i("n2_current"), style="white")
        t.append("  \u2502\n", style="dim cyan")

        t.append("  \u2514", style="dim cyan")
        t.append("\u2500" * 47, style="dim cyan")
        t.append("\u2518\n", style="dim cyan")

        return t

    def watch_fet_states(self) -> None:
        self.refresh()

    def watch_state_index(self) -> None:
        self.refresh()

    def watch_telemetry(self) -> None:
        self.refresh()

    def update_from_server(self, fet_states: list[bool], state_index: int,
                           telemetry: dict | None = None) -> None:
        """Convenience method to set values from a server state update."""
        new_fets = tuple(fet_states)
        self.state_index = state_index
        if telemetry is not None:
            self.telemetry = telemetry
        if new_fets == self.fet_states:
            self.refresh()
        else:
            self.fet_states = new_fets
