"""
Switching Circuit V2 - Detailed H-Bridge Circuit Diagram Widget.

Shows N-channel MOSFETs with D/G/S in the vertical power path,
UCC5304 gate drivers branching off horizontally to the gate,
RP2040-Zero GPIO connections to each driver input,
and INA226 power monitors in each MOSFET current path.

In pulse charge mode, shows two separate pouch cells.
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
    "VCC > P1 > +A > LOAD > -A > N1 > GND",
    "VCC > P1 > +A > LOAD > -B > N2 > GND",
    "VCC > P2 > +B > LOAD > -A > N1 > GND",
    "VCC > P2 > +B > LOAD > -B > N2 > GND",
    "All MOSFETs conducting to GND",
    "No current flowing",
]

# Column positions (determined by gate driver line width)
# Gate line: "    GP2─┤5304├─G║│  P1...P2  ║│G─┤5304├─GP3"
#             0   4  7 8    13 1516 17    ...    41 42
COL_L = 16   # left MOSFET / vertical wire column
COL_R = 41   # right MOSFET / vertical wire column


def _on(active: bool) -> str:
    return "bold green" if active else "dim red"

def _wh(active: bool) -> str:
    return "\u2501" if active else "\u2500"

def _wv(active: bool) -> str:
    return "\u2503" if active else "\u2502"


class CircuitDiagram(Widget):
    """Detailed H-bridge schematic with drivers, MOSFETs, and INA226 sensors."""

    DEFAULT_CSS = """
    CircuitDiagram {
        width: auto;
        height: auto;
        padding: 0;
    }
    """

    fet_states: reactive[tuple[bool, ...]] = reactive((False, False, False, False))
    state_index: reactive[int] = reactive(5)
    circuit_mode: reactive[str] = reactive("idle")

    def render(self) -> Text:
        p1, p2, n1, n2 = self.fet_states
        idx = self.state_index
        is_pulse = self.circuit_mode == "pulse_charge"

        label = STATE_LABELS[idx] if 0 <= idx <= 5 else "Unknown"

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
        sg = "bold magenta"       # gate drive signal
        sd = "bold cyan"          # driver IC
        si = "bold yellow"        # INA226

        vp1, vp2 = _wv(p1), _wv(p2)
        vn1, vn2 = _wv(n1), _wv(n2)

        # MOSFET symbols (2 chars wide)
        mp1 = "\u2551\u2502" if p1 else "\u2502\u2502"
        mp2 = "\u2551\u2502" if p2 else "\u2502\u2502"
        mn1 = "\u2551\u2502" if n1 else "\u2502\u2502"
        mn2 = "\u2551\u2502" if n2 else "\u2502\u2502"

        # Cell styles
        sc = "bold white"
        sci = "bold white" if load_on else d

        # Spacing helpers
        P = " " * COL_L                    # left padding (16 spaces)
        GV = " " * (COL_R - COL_L - 1)    # gap between 1-char verticals (24)
        GM = " " * (COL_R - COL_L - 2)    # gap between 2-char MOSFETs (23)

        # D/S label gap: "  D" + gap + "D  " between verticals
        ds_inner = COL_R - COL_L - 1 - 6  # 24 - 6 = 18
        ds_gap = " " * ds_inner

        t = Text()

        # ── Title ──
        t.append("                H-BRIDGE SWITCHING CIRCUIT V2\n", style="bold cyan")

        # ── VCC Rail ──
        rail_left = (COL_R - COL_L - 5) // 2      # wires left of " VCC "
        rail_right = COL_R - COL_L - 5 - rail_left # wires right
        t.append(P, style=d)
        t.append(_wh(p1) * rail_left, style=sp1)
        t.append(_wh(p1 or p2) * 1, style=_on(p1 or p2))
        t.append(" VCC ", style="bold white")
        t.append(_wh(p1 or p2) * 1, style=_on(p1 or p2))
        t.append(_wh(p2) * rail_right, style=sp2)
        t.append("\n")

        # ── INA226 high-side ──
        t.append(P, style=d)
        t.append(vp1, style=sp1)
        t.append(GV, style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        ina_gap = COL_R - COL_L - 1 - 16  # 24 - 16 = 8
        t.append(" " * (COL_L - 3), style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append(" " * ina_gap, style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append("\n")

        t.append(" " * (COL_L - 2), style=d)
        t.append("(0x40)", style="dim yellow")
        t.append(" " * (ina_gap + 4), style=d)
        t.append("(0x41)", style="dim yellow")
        t.append("\n")

        t.append(P, style=d)
        t.append(vp1, style=sp1)
        t.append(GV, style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # ── High-side Drain ──
        t.append(P, style=d)
        t.append(vp1, style=sp1)
        t.append(f"  D{ds_gap}D  ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # ── High-side MOSFET top ──
        t.append(P, style=d)
        t.append(mp1, style=sp1)
        t.append(GM, style=d)
        t.append(mp2, style=sp2)
        t.append("\n")

        # ── High-side MOSFET gate line + driver ──
        t.append("    GP2", style=sg if p1 else d)
        t.append("\u2500", style=sg if p1 else d)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500G", style=sp1)
        t.append(mp1, style=sp1)
        t.append("  P1", style=sp1)
        t.append(" " * (COL_R - COL_L - 2 - 8), style=d)  # 23 - 8 = 15
        t.append("P2  ", style=sp2)
        t.append(mp2, style=sp2)
        t.append("G\u2500", style=sp2)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500", style=sg if p2 else d)
        t.append("GP3", style=sg if p2 else d)
        t.append("\n")

        # ── High-side MOSFET bottom ──
        t.append(P, style=d)
        t.append(mp1, style=sp1)
        t.append(GM, style=d)
        t.append(mp2, style=sp2)
        t.append("\n")

        # ── High-side Source ──
        t.append(P, style=d)
        t.append(vp1, style=sp1)
        t.append(f"  S{ds_gap}S  ", style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        # ── +A / +B node labels ──
        node_gap = COL_R - COL_L - 1 - 4  # 24 - 4 = 20
        t.append(" " * (COL_L - 1), style=d)
        t.append("+A", style="bold" if p1 else d)
        t.append(" " * node_gap, style=d)
        t.append("+B", style="bold" if p2 else d)
        t.append("\n")

        # ── Cell section ──
        # Box coordinates
        BOX_L = COL_L + 3   # 19
        BOX_R = COL_R - 2   # 39
        BOX_W = BOX_R - BOX_L + 1  # 21
        BOX_INNER = BOX_W - 2       # 19

        t.append(P, style=d)
        t.append(vp1, style=sp1)
        t.append(GV, style=d)
        t.append(vp2, style=sp2)
        t.append("\n")

        if is_pulse:
            # ── Dual cell for pulse charge ──
            # Left cell: BOX_L to mid, Right cell: mid to BOX_R
            half = BOX_INNER // 2  # 9
            cell_w = half - 1      # 8 inner width per cell

            # Top edges
            t.append(P, style=d)
            t.append(vp1, style=sp1)
            t.append("  \u250c" + "\u2500" * cell_w + "\u2510", style=sc)
            t.append(" ", style=d)
            t.append("\u250c" + "\u2500" * cell_w + "\u2510  ", style=sc)
            t.append(vp2, style=sp2)
            t.append("\n")

            # Terminal line
            t.append(P, style=d)
            t.append(_wh(p1), style=sp1)
            t.append("\u2500\u2524", style=sp1)
            t.append("+A", style="bold" if p1 else d)
            lpad = cell_w - 4
            t.append(" " * lpad, style=sci)
            t.append("\u2502", style=sc)
            t.append(" ", style=d)
            t.append("\u2502", style=sc)
            rpad = cell_w - 4
            t.append(" " * rpad, style=sci)
            t.append("+B", style="bold" if p2 else d)
            t.append("\u251c\u2500", style=sp2)
            t.append(_wh(p2), style=sp2)
            t.append("\n")

            # Cell body
            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * cell_w, style=sci)
            t.append("\u2502", style=sc)
            t.append(" ", style=d)
            t.append("\u2502", style=sc)
            t.append(" " * cell_w, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            # Labels
            la = "CELL A"
            lb = "CELL B"
            la_pad = (cell_w - len(la)) // 2
            lb_pad = (cell_w - len(lb)) // 2
            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * la_pad + la + " " * (cell_w - la_pad - len(la)), style=sci)
            t.append("\u2502", style=sc)
            t.append(" ", style=d)
            t.append("\u2502", style=sc)
            t.append(" " * lb_pad + lb + " " * (cell_w - lb_pad - len(lb)), style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            # Arrow rows
            for _ in range(2):
                arr_l = "\u25b6\u25b6" if p1 and n1 else "\u2500\u2500"
                arr_r = "\u25b6\u25b6" if p2 and n2 else "\u2500\u2500"
                la_arr_pad = (cell_w - 2) // 2
                lb_arr_pad = (cell_w - 2) // 2
                t.append(P, style=d)
                t.append(" ", style=d)
                t.append("  \u2502", style=sc)
                t.append(" " * la_arr_pad, style=sci)
                t.append(arr_l, style=_on(p1 and n1))
                t.append(" " * (cell_w - la_arr_pad - 2), style=sci)
                t.append("\u2502", style=sc)
                t.append(" ", style=d)
                t.append("\u2502", style=sc)
                t.append(" " * lb_arr_pad, style=sci)
                t.append(arr_r, style=_on(p2 and n2))
                t.append(" " * (cell_w - lb_arr_pad - 2), style=sci)
                t.append("\u2502  ", style=sc)
                t.append("\n")

            # Cell body bottom
            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * cell_w, style=sci)
            t.append("\u2502", style=sc)
            t.append(" ", style=d)
            t.append("\u2502", style=sc)
            t.append(" " * cell_w, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            # Bottom terminals
            t.append(P, style=d)
            t.append(_wh(n1), style=sn1)
            t.append("\u2500\u2524", style=sn1)
            t.append("-A", style="bold" if n1 else d)
            t.append(" " * lpad, style=sci)
            t.append("\u2502", style=sc)
            t.append(" ", style=d)
            t.append("\u2502", style=sc)
            t.append(" " * rpad, style=sci)
            t.append("-B", style="bold" if n2 else d)
            t.append("\u251c\u2500", style=sn2)
            t.append(_wh(n2), style=sn2)
            t.append("\n")

            # Bottom edges
            t.append(P, style=d)
            t.append(vn1, style=sn1)
            t.append("  \u2514" + "\u2500" * cell_w + "\u2518", style=sc)
            t.append(" ", style=d)
            t.append("\u2514" + "\u2500" * cell_w + "\u2518  ", style=sc)
            t.append(vn2, style=sn2)
            t.append("\n")

        else:
            # ── Single cell ──
            # Top edge
            t.append(P, style=d)
            t.append(vp1, style=sp1)
            t.append("  \u250c" + "\u2500" * BOX_INNER + "\u2510  ", style=sc)
            t.append(vp2, style=sp2)
            t.append("\n")

            # Top terminal line
            t.append(P, style=d)
            t.append(_wh(p1), style=sp1)
            t.append("\u2500\u2524", style=sp1)
            t.append("+A", style="bold" if p1 else d)
            t.append(" " * (BOX_INNER - 4), style=sci)
            t.append("+B", style="bold" if p2 else d)
            t.append("\u251c\u2500", style=sp2)
            t.append(_wh(p2), style=sp2)
            t.append("\n")

            # Cell body
            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * BOX_INNER, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            # Arrow + label
            cell_label = "POUCH CELL"
            lbl_pad = (BOX_INNER - len(cell_label)) // 2
            lbl_rpad = BOX_INNER - lbl_pad - len(cell_label)

            arr_pad = (BOX_INNER - 3) // 2
            arr_rpad = BOX_INNER - arr_pad - 3

            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * arr_pad, style=sci)
            t.append(arrow, style=sl)
            t.append(" " * arr_rpad, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * lbl_pad, style=sci)
            t.append(cell_label, style=sci)
            t.append(" " * lbl_rpad, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * arr_pad, style=sci)
            t.append(arrow, style=sl)
            t.append(" " * arr_rpad, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            # Cell body bottom
            t.append(P, style=d)
            t.append(" ", style=d)
            t.append("  \u2502", style=sc)
            t.append(" " * BOX_INNER, style=sci)
            t.append("\u2502  ", style=sc)
            t.append("\n")

            # Bottom terminal line
            t.append(P, style=d)
            t.append(_wh(n1), style=sn1)
            t.append("\u2500\u2524", style=sn1)
            t.append("-A", style="bold" if n1 else d)
            t.append(" " * (BOX_INNER - 4), style=sci)
            t.append("-B", style="bold" if n2 else d)
            t.append("\u251c\u2500", style=sn2)
            t.append(_wh(n2), style=sn2)
            t.append("\n")

            # Bottom edge
            t.append(P, style=d)
            t.append(vn1, style=sn1)
            t.append("  \u2514" + "\u2500" * BOX_INNER + "\u2518  ", style=sc)
            t.append(vn2, style=sn2)
            t.append("\n")

        # ── Bottom terminal tabs ──
        t.append(P, style=d)
        t.append(vn1, style=sn1)
        t.append(GV, style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── -A / -B node labels ──
        t.append(" " * (COL_L - 1), style=d)
        t.append("-A", style="bold" if n1 else d)
        t.append(" " * node_gap, style=d)
        t.append("-B", style="bold" if n2 else d)
        t.append("\n")

        # ── Low-side Drain ──
        t.append(P, style=d)
        t.append(vn1, style=sn1)
        t.append(f"  D{ds_gap}D  ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── Low-side MOSFET top ──
        t.append(P, style=d)
        t.append(mn1, style=sn1)
        t.append(GM, style=d)
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
        t.append(" " * (COL_R - COL_L - 2 - 8), style=d)
        t.append("N2  ", style=sn2)
        t.append(mn2, style=sn2)
        t.append("G\u2500", style=sn2)
        t.append("\u2524", style=sd)
        t.append("5304", style=sd)
        t.append("\u251c", style=sd)
        t.append("\u2500", style=sg if n2 else d)
        t.append("GP5", style=sg if n2 else d)
        t.append("\n")

        # ── Low-side MOSFET bottom ──
        t.append(P, style=d)
        t.append(mn1, style=sn1)
        t.append(GM, style=d)
        t.append(mn2, style=sn2)
        t.append("\n")

        # ── Low-side Source ──
        t.append(P, style=d)
        t.append(vn1, style=sn1)
        t.append(f"  S{ds_gap}S  ", style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── INA226 low-side ──
        t.append(P, style=d)
        t.append(vn1, style=sn1)
        t.append(GV, style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        t.append(" " * (COL_L - 3), style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append(" " * ina_gap, style=d)
        t.append("\u2524", style=si)
        t.append("INA226", style=si)
        t.append("\u251c", style=si)
        t.append("\n")

        t.append(" " * (COL_L - 2), style=d)
        t.append("(0x43)", style="dim yellow")
        t.append(" " * (ina_gap + 4), style=d)
        t.append("(0x45)", style="dim yellow")
        t.append("\n")

        t.append(P, style=d)
        t.append(vn1, style=sn1)
        t.append(GV, style=d)
        t.append(vn2, style=sn2)
        t.append("\n")

        # ── GND Rail ──
        t.append(P, style=d)
        t.append(_wh(n1) * rail_left, style=sn1)
        t.append(_wh(n1 or n2) * 1, style=_on(n1 or n2))
        t.append(" GND ", style="bold white")
        t.append(_wh(n1 or n2) * 1, style=_on(n1 or n2))
        t.append(_wh(n2) * rail_right, style=sn2)
        t.append("\n")

        # ── State ──
        t.append("\n")
        t.append("  State: ", style=d)
        t.append(label, style="bold yellow")
        t.append("\n")

        return t

    def watch_fet_states(self) -> None:
        self.refresh()

    def watch_state_index(self) -> None:
        self.refresh()

    def watch_circuit_mode(self) -> None:
        self.refresh()

    def update_from_server(self, fet_states: list[bool], state_index: int,
                           mode: str = "idle") -> None:
        """Convenience method to set values from a server state update."""
        self.circuit_mode = mode
        new_fets = tuple(fet_states)
        self.state_index = state_index
        if new_fets == self.fet_states:
            self.refresh()
        else:
            self.fet_states = new_fets
