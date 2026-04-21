#!/usr/bin/env python3
"""
Generate the flat KiCad 10 schematic for Switching Circuit V2 PCB.

Reads symbol definitions from stock KiCad libraries + the custom library, lays
out ~70 parts on a grid across an A3 sheet, connects nets via global labels.

Not pretty — placement is mechanical. The user can drag components around after
opening in the Schematic Editor. The important invariants are:
  - every part instance has a valid lib_id matching the lib_symbols block
  - every pin either has a global_label at its connection point or a no_connect
  - ERC clean

Run:
    /usr/bin/python3 pcb/gen_schematic.py
    /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli sch erc \\
        --output /tmp/erc.rpt \\
        pcb/switching_circuit_v2/switching_circuit_v2.kicad_sch
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #

PCB_DIR = Path(__file__).resolve().parent
REPO_ROOT = PCB_DIR.parent
STOCK_SYMBOLS = Path(
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
)
CUSTOM_LIB = PCB_DIR / "lib" / "switching_circuit_v2.kicad_sym"
PROJECT_DIR = PCB_DIR / "switching_circuit_v2"
OUTPUT_SCH = PROJECT_DIR / "switching_circuit_v2.kicad_sch"


# --------------------------------------------------------------------------- #
# S-expression utilities                                                      #
# --------------------------------------------------------------------------- #


def paren_match(text: str, start: int) -> int:
    """Given `text[start]` is an opening paren, return index of matching close."""
    depth = 0
    in_str = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ValueError("unmatched paren")


def extract_symbol(lib_path: Path, symbol_name: str) -> str:
    """Pull out the (symbol "NAME" ...) block from a .kicad_sym file."""
    text = lib_path.read_text()
    needle = f'(symbol "{symbol_name}"'
    idx = text.find(needle)
    if idx < 0:
        raise ValueError(f"symbol {symbol_name!r} not found in {lib_path.name}")
    close = paren_match(text, idx)
    return text[idx : close + 1]


def extract_pin_positions(symbol_text: str) -> dict[str, tuple[float, float, int]]:
    """Return {pin_number_str: (x, y, orientation_deg)} for every pin."""
    pins: dict[str, tuple[float, float, int]] = {}
    i = 0
    while True:
        j = symbol_text.find("(pin ", i)
        if j < 0:
            break
        end = paren_match(symbol_text, j)
        block = symbol_text[j : end + 1]
        at_m = re.search(r"\(at\s+([\-0-9.]+)\s+([\-0-9.]+)\s+(\d+)\)", block)
        num_m = re.search(r'\(number\s+"([^"]+)"', block)
        if at_m and num_m:
            pins[num_m.group(1)] = (
                float(at_m.group(1)),
                float(at_m.group(2)),
                int(at_m.group(3)),
            )
        i = end + 1
    return pins


# --------------------------------------------------------------------------- #
# Schema: every symbol we use                                                 #
# --------------------------------------------------------------------------- #
# Map lib_id -> (library file path, symbol_name)

LIBS = {
    "Device:R": (STOCK_SYMBOLS / "Device.kicad_sym", "R"),
    "Device:C": (STOCK_SYMBOLS / "Device.kicad_sym", "C"),
    "Device:LED": (STOCK_SYMBOLS / "Device.kicad_sym", "LED"),
    "Device:Polyfuse_Small": (STOCK_SYMBOLS / "Device.kicad_sym", "Polyfuse_Small"),
    "Transistor_FET:Q_NMOS_GSD": (STOCK_SYMBOLS / "Transistor_FET.kicad_sym", "Q_NMOS_GSD"),
    "Transistor_FET:Q_PMOS_GSD": (STOCK_SYMBOLS / "Transistor_FET.kicad_sym", "Q_PMOS_GSD"),
    "Device:D_TVS": (STOCK_SYMBOLS / "Device.kicad_sym", "D_TVS"),
    "Connector:Screw_Terminal_01x02": (
        STOCK_SYMBOLS / "Connector.kicad_sym",
        "Screw_Terminal_01x02",
    ),
    "Connector:Screw_Terminal_01x04": (
        STOCK_SYMBOLS / "Connector.kicad_sym",
        "Screw_Terminal_01x04",
    ),
    "Connector_Generic:Conn_01x04": (
        STOCK_SYMBOLS / "Connector_Generic.kicad_sym",
        "Conn_01x04",
    ),
    "Connector_Generic:Conn_01x05": (
        STOCK_SYMBOLS / "Connector_Generic.kicad_sym",
        "Conn_01x05",
    ),
    "Connector:TestPoint": (STOCK_SYMBOLS / "Connector.kicad_sym", "TestPoint"),
    "Sensor_Energy:INA226": (
        STOCK_SYMBOLS / "Sensor_Energy.kicad_sym",
        "INA226",
    ),
    "switching_circuit_v2:UCC5304": (CUSTOM_LIB, "UCC5304"),
    "switching_circuit_v2:B0512S_1WR3": (CUSTOM_LIB, "B0512S_1WR3"),
    "switching_circuit_v2:RP2040_Zero": (CUSTOM_LIB, "RP2040_Zero"),
    "power:PWR_FLAG": (STOCK_SYMBOLS / "power.kicad_sym", "PWR_FLAG"),
}


# Load symbol definitions + pin positions at import time
SYMBOL_TEXT: dict[str, str] = {}
PIN_POS: dict[str, dict[str, tuple[float, float, int]]] = {}
for lib_id, (path, name) in LIBS.items():
    txt = extract_symbol(path, name)
    SYMBOL_TEXT[lib_id] = txt
    PIN_POS[lib_id] = extract_pin_positions(txt)


# --------------------------------------------------------------------------- #
# Schematic DSL                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class Part:
    ref: str
    lib_id: str
    pos: tuple[float, float]
    value: str = ""
    footprint: str = ""
    pin_nets: dict[str, str] = field(default_factory=dict)
    nc_pins: list[str] = field(default_factory=list)
    rotation: int = 0  # 0, 90, 180, 270
    mirror: str = ""  # "", "x", "y"
    datasheet: str = ""
    fields: dict[str, str] = field(default_factory=dict)  # extra fields like LCSC, MPN


# --------------------------------------------------------------------------- #
# Parts definitions — all ~70 of them                                         #
# --------------------------------------------------------------------------- #

# Grid spacing: parts on 50mm x 30mm pitch for the top rows, tighter below.
# A3 sheet is 420mm x 297mm; we use margins of ~20mm.

PARTS: list[Part] = []


def add(p: Part) -> Part:
    PARTS.append(p)
    return p


# ---------- Power-in block (top-left) ----------
add(
    Part(
        "J1",
        "Connector:Screw_Terminal_01x04",
        (40, 30),
        value="Cycler In",
        footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4-5.08_1x04_P5.08mm_Horizontal",
        pin_nets={
            "1": "CYCLER_IN+",
            "2": "CYCLER_IN+",
            "3": "CYCLER_IN-",
            "4": "CYCLER_IN-",
        },
    )
)
add(
    Part(
        "F1",
        "Device:Polyfuse_Small",
        (60, 30),
        value="3A",
        footprint="Fuse:Fuse_1812_4532Metric",
        pin_nets={"1": "CYCLER_IN+", "2": "+HV_PREFUSE"},
    )
)
add(
    Part(
        "Q_RP",
        "Transistor_FET:Q_PMOS_GSD",
        (80, 35),
        value="AO3401A",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pin_nets={"1": "GND", "2": "+HV_PREFUSE", "3": "+HV"},
        fields={"MPN": "AO3401A", "LCSC": "C15127"},
    )
)
add(
    Part(
        "R_RP",
        "Device:R",
        (90, 30),
        value="10k",
        footprint="Resistor_SMD:R_0603_1608Metric",
        pin_nets={"1": "+HV_PREFUSE", "2": "GND"},
    )
)
add(
    Part(
        "D_TVS",
        "Device:D_TVS",
        (105, 40),
        value="SMBJ12CA",
        footprint="Diode_SMD:D_SMB",
        pin_nets={"1": "GND", "2": "+HV"},
        fields={"LCSC": "C87447"},
    )
)
add(
    Part(
        "C_BULK1",
        "Device:C",
        (120, 40),
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
        pin_nets={"1": "+HV", "2": "GND"},
    )
)
add(
    Part(
        "C_BULK2",
        "Device:C",
        (130, 40),
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
        pin_nets={"1": "+HV", "2": "GND"},
    )
)
add(
    Part(
        "C_5V1",
        "Device:C",
        (150, 40),
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
        pin_nets={"1": "+5V", "2": "GND"},
    )
)
add(
    Part(
        "C_5V2",
        "Device:C",
        (160, 40),
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
        pin_nets={"1": "+5V", "2": "GND"},
    )
)

# ---------- Power stage: 4 MOSFETs + 4 shunts + cell terminal ----------
# MOSFET Device:Q_NMOS_GSD pins: 1=Gate, 2=Source, 3=Drain
add(
    Part(
        "Q1",
        "Transistor_FET:Q_NMOS_GSD",
        (60, 80),
        value="AO3400A",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pin_nets={"1": "GATE_P1_OUT", "2": "CELL_A_POS", "3": "+HV_P1"},
        fields={"MPN": "AO3400A", "LCSC": "C20917"},
    )
)
add(
    Part(
        "Q2",
        "Transistor_FET:Q_NMOS_GSD",
        (120, 80),
        value="AO3400A",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pin_nets={"1": "GATE_P2_OUT", "2": "CELL_B_POS", "3": "+HV_P2"},
        fields={"MPN": "AO3400A", "LCSC": "C20917"},
    )
)
add(
    Part(
        "Q3",
        "Transistor_FET:Q_NMOS_GSD",
        (60, 130),
        value="AO3400A",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pin_nets={"1": "GATE_N1_OUT", "2": "GND_N1", "3": "CELL_A_NEG"},
        fields={"MPN": "AO3400A", "LCSC": "C20917"},
    )
)
add(
    Part(
        "Q4",
        "Transistor_FET:Q_NMOS_GSD",
        (120, 130),
        value="AO3400A",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pin_nets={"1": "GATE_N2_OUT", "2": "GND_N2", "3": "CELL_B_NEG"},
        fields={"MPN": "AO3400A", "LCSC": "C20917"},
    )
)

# Shunts (R, 2512)
for ref, pos, nets in [
    ("R_SH_P1", (60, 65), {"1": "+HV", "2": "+HV_P1"}),
    ("R_SH_P2", (120, 65), {"1": "+HV", "2": "+HV_P2"}),
    ("R_SH_N1", (60, 145), {"1": "GND_N1", "2": "GND"}),
    ("R_SH_N2", (120, 145), {"1": "GND_N2", "2": "GND"}),
]:
    add(
        Part(
            ref,
            "Device:R",
            pos,
            value="0R05",
            footprint="Resistor_SMD:R_2512_6332Metric",
            pin_nets=nets,
        )
    )

# Gate pulldowns (10k from gate to source)
for ref, pos, nets in [
    ("Rgpd_1", (70, 80), {"1": "GATE_P1_OUT", "2": "CELL_A_POS"}),
    ("Rgpd_2", (130, 80), {"1": "GATE_P2_OUT", "2": "CELL_B_POS"}),
    ("Rgpd_3", (70, 130), {"1": "GATE_N1_OUT", "2": "GND_N1"}),
    ("Rgpd_4", (130, 130), {"1": "GATE_N2_OUT", "2": "GND_N2"}),
]:
    add(
        Part(
            ref,
            "Device:R",
            pos,
            value="10k",
            footprint="Resistor_SMD:R_0603_1608Metric",
            pin_nets=nets,
        )
    )

# Cell terminal J2 — 4-pin screw terminal
# Connector:Screw_Terminal_01x04 pin 1 = top, so: 1=CELL_A_POS, 2=CELL_A_NEG, 3=CELL_B_POS, 4=CELL_B_NEG
add(
    Part(
        "J2",
        "Connector:Screw_Terminal_01x04",
        (90, 110),
        value="Cell",
        footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4-5.08_1x04_P5.08mm_Horizontal",
        pin_nets={
            "1": "CELL_B_NEG",
            "2": "CELL_A_NEG",
            "3": "CELL_B_POS",
            "4": "CELL_A_POS",
        },
    )
)

# ---------- INA226 sensors ----------
# Sensor_Energy:INA226 pins — verified from stock lib:
# 1=A1, 2=A0, 3=~ALERT, 4=SDA, 5=SCL, 6=VS, 7=GND, 8=Vbus, 9=Vin-, 10=Vin+
add(
    Part(
        "U_INA_P1",
        "Sensor_Energy:INA226",
        (180, 50),
        value="INA226",
        footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm",
        pin_nets={
            "1": "GND",  # A1=GND
            "2": "GND",  # A0=GND
            "3": "ALERT",
            "4": "SDA",
            "5": "SCL",
            "6": "+3V3",
            "7": "GND",
            "8": "+HV_P1",  # Vbus
            "9": "+HV_P1",  # Vin-
            "10": "+HV",  # Vin+
        },
        fields={"LCSC": "C113337"},
    )
)
add(
    Part(
        "U_INA_P2",
        "Sensor_Energy:INA226",
        (220, 50),
        value="INA226",
        footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm",
        pin_nets={
            "1": "GND",  # A1=GND
            "2": "+3V3",  # A0=VS
            "3": "ALERT",
            "4": "SDA",
            "5": "SCL",
            "6": "+3V3",
            "7": "GND",
            "8": "+HV_P2",
            "9": "+HV_P2",
            "10": "+HV",
        },
        fields={"LCSC": "C113337"},
    )
)
add(
    Part(
        "U_INA_N1",
        "Sensor_Energy:INA226",
        (180, 105),
        value="INA226",
        footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm",
        pin_nets={
            "1": "SDA",  # A1=SDA → addr 0x43
            "2": "+3V3",  # A0=VS
            "3": "ALERT",
            "4": "SDA",
            "5": "SCL",
            "6": "+3V3",
            "7": "GND",
            "8": "GND_N1",
            "9": "GND",
            "10": "GND_N1",
        },
        fields={"LCSC": "C113337"},
    )
)
add(
    Part(
        "U_INA_N2",
        "Sensor_Energy:INA226",
        (220, 105),
        value="INA226",
        footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm",
        pin_nets={
            "1": "+3V3",  # A1=VS
            "2": "+3V3",  # A0=VS
            "3": "ALERT",
            "4": "SDA",
            "5": "SCL",
            "6": "+3V3",
            "7": "GND",
            "8": "GND_N2",
            "9": "GND",
            "10": "GND_N2",
        },
        fields={"LCSC": "C113337"},
    )
)

# INA226 decoupling caps (one 100nF per INA, between VS and GND)
for ref, pos, nets in [
    ("C_INA_P1", (195, 30), {"1": "+3V3", "2": "GND"}),
    ("C_INA_P2", (235, 30), {"1": "+3V3", "2": "GND"}),
    ("C_INA_N1", (195, 85), {"1": "+3V3", "2": "GND"}),
    ("C_INA_N2", (235, 85), {"1": "+3V3", "2": "GND"}),
]:
    add(
        Part(
            ref,
            "Device:C",
            pos,
            value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric",
            pin_nets=nets,
        )
    )

# I²C + ALERT pullups to +3V3
for ref, pos, nets in [
    ("R_SDA", (260, 40), {"1": "+3V3", "2": "SDA"}),
    ("R_SCL", (260, 55), {"1": "+3V3", "2": "SCL"}),
    ("R_ALERT", (260, 70), {"1": "+3V3", "2": "ALERT"}),
]:
    add(
        Part(
            ref,
            "Device:R",
            pos,
            value="4.7k",
            footprint="Resistor_SMD:R_0603_1608Metric",
            pin_nets=nets,
        )
    )

# ---------- Gate drivers: 4× UCC5304 ----------
# switching_circuit_v2:UCC5304 pins: 1=IN, 2=VCCI, 3=VCCI, 4=GND, 5=VSS, 6=VSS, 7=OUT, 8=VDD
add(
    Part(
        "U1",
        "switching_circuit_v2:UCC5304",
        (60, 185),
        value="UCC5304DWVR",
        footprint="switching_circuit_v2:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
        pin_nets={
            "1": "GATE_P1_IN",
            "2": "+3V3",
            "3": "+3V3",
            "4": "GND",
            "5": "CELL_A_POS",
            "6": "CELL_A_POS",
            "7": "GATE_P1_OUT_PRE",
            "8": "VCC2_P1",
        },
        fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"},
    )
)
add(
    Part(
        "U2",
        "switching_circuit_v2:UCC5304",
        (120, 185),
        value="UCC5304DWVR",
        footprint="switching_circuit_v2:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
        pin_nets={
            "1": "GATE_P2_IN",
            "2": "+3V3",
            "3": "+3V3",
            "4": "GND",
            "5": "CELL_B_POS",
            "6": "CELL_B_POS",
            "7": "GATE_P2_OUT_PRE",
            "8": "VCC2_P2",
        },
        fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"},
    )
)
add(
    Part(
        "U3",
        "switching_circuit_v2:UCC5304",
        (60, 225),
        value="UCC5304DWVR",
        footprint="switching_circuit_v2:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
        pin_nets={
            "1": "GATE_N1_IN",
            "2": "+3V3",
            "3": "+3V3",
            "4": "GND",
            "5": "GND",
            "6": "GND",
            "7": "GATE_N1_OUT_PRE",
            "8": "+5V",
        },
        fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"},
    )
)
add(
    Part(
        "U4",
        "switching_circuit_v2:UCC5304",
        (120, 225),
        value="UCC5304DWVR",
        footprint="switching_circuit_v2:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
        pin_nets={
            "1": "GATE_N2_IN",
            "2": "+3V3",
            "3": "+3V3",
            "4": "GND",
            "5": "GND",
            "6": "GND",
            "7": "GATE_N2_OUT_PRE",
            "8": "+5V",
        },
        fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"},
    )
)

# Gate series resistors (10Ω each)
for ref, pos, nets in [
    ("R_G_U1", (80, 185), {"1": "GATE_P1_OUT_PRE", "2": "GATE_P1_OUT"}),
    ("R_G_U2", (140, 185), {"1": "GATE_P2_OUT_PRE", "2": "GATE_P2_OUT"}),
    ("R_G_U3", (80, 225), {"1": "GATE_N1_OUT_PRE", "2": "GATE_N1_OUT"}),
    ("R_G_U4", (140, 225), {"1": "GATE_N2_OUT_PRE", "2": "GATE_N2_OUT"}),
]:
    add(
        Part(
            ref,
            "Device:R",
            pos,
            value="10R",
            footprint="Resistor_SMD:R_0603_1608Metric",
            pin_nets=nets,
        )
    )

# VCCI decoupling (100nF each)
for ref, pos, nets in [
    ("C_VCCI_U1", (50, 170), {"1": "+3V3", "2": "GND"}),
    ("C_VCCI_U2", (110, 170), {"1": "+3V3", "2": "GND"}),
    ("C_VCCI_U3", (50, 210), {"1": "+3V3", "2": "GND"}),
    ("C_VCCI_U4", (110, 210), {"1": "+3V3", "2": "GND"}),
]:
    add(
        Part(
            ref,
            "Device:C",
            pos,
            value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric",
            pin_nets=nets,
        )
    )

# VDD-VSS decoupling — 10µF + 100nF per driver
for ref, pos, nets in [
    ("C_VDD_U1_1", (70, 200), {"1": "VCC2_P1", "2": "CELL_A_POS"}),
    ("C_VDD_U1_2", (80, 200), {"1": "VCC2_P1", "2": "CELL_A_POS"}),
    ("C_VDD_U2_1", (130, 200), {"1": "VCC2_P2", "2": "CELL_B_POS"}),
    ("C_VDD_U2_2", (140, 200), {"1": "VCC2_P2", "2": "CELL_B_POS"}),
    ("C_VDD_U3_1", (70, 240), {"1": "+5V", "2": "GND"}),
    ("C_VDD_U3_2", (80, 240), {"1": "+5V", "2": "GND"}),
    ("C_VDD_U4_1", (130, 240), {"1": "+5V", "2": "GND"}),
    ("C_VDD_U4_2", (140, 240), {"1": "+5V", "2": "GND"}),
]:
    value = "10uF" if ref.endswith("_1") else "100nF"
    fp = (
        "Capacitor_SMD:C_0805_2012Metric"
        if value == "10uF"
        else "Capacitor_SMD:C_0603_1608Metric"
    )
    add(Part(ref, "Device:C", pos, value=value, footprint=fp, pin_nets=nets))

# ---------- B0512S isolated supplies ----------
# switching_circuit_v2:B0512S_1WR3 pins: 1=Vin+, 2=Vin-, 3=Vout+, 4=Vout-
add(
    Part(
        "PS1",
        "switching_circuit_v2:B0512S_1WR3",
        (180, 185),
        value="B0512S-1WR3",
        footprint="switching_circuit_v2:B0512S_1WR3_SIP4",
        pin_nets={
            "1": "+5V",
            "2": "GND",
            "3": "VCC2_P1",
            "4": "CELL_A_POS",
        },
        fields={"MPN": "B0512S-1WR3"},
    )
)
add(
    Part(
        "PS2",
        "switching_circuit_v2:B0512S_1WR3",
        (220, 185),
        value="B0512S-1WR3",
        footprint="switching_circuit_v2:B0512S_1WR3_SIP4",
        pin_nets={
            "1": "+5V",
            "2": "GND",
            "3": "VCC2_P2",
            "4": "CELL_B_POS",
        },
        fields={"MPN": "B0512S-1WR3"},
    )
)

# B0512S input caps (4.7µF) and output caps (2.2µF)
for ref, pos, nets in [
    ("C_PS1_IN", (170, 170), {"1": "+5V", "2": "GND"}),
    ("C_PS1_OUT", (195, 170), {"1": "VCC2_P1", "2": "CELL_A_POS"}),
    ("C_PS2_IN", (210, 170), {"1": "+5V", "2": "GND"}),
    ("C_PS2_OUT", (235, 170), {"1": "VCC2_P2", "2": "CELL_B_POS"}),
]:
    value = "4.7uF" if ref.endswith("_IN") else "2.2uF"
    add(
        Part(
            ref,
            "Device:C",
            pos,
            value=value,
            footprint="Capacitor_SMD:C_0805_2012Metric",
            pin_nets=nets,
        )
    )

# ---------- MCU + peripherals ----------
# RP2040_Zero pads: 5V, GND, 3V3, GP0..GP15, GP26..GP29
add(
    Part(
        "A1",
        "switching_circuit_v2:RP2040_Zero",
        (300, 130),
        value="RP2040-Zero",
        footprint="switching_circuit_v2:RP2040_Zero_SocketedTH",
        pin_nets={
            "5V": "+5V",
            "GND": "GND",
            "3V3": "+3V3",
            "GP2": "GATE_P1_IN",
            "GP3": "GATE_P2_IN",
            "GP4": "GATE_N1_IN",
            "GP5": "GATE_N2_IN",
            "GP6": "SDA",
            "GP7": "SCL",
            "GP8": "ENC_CLK",
            "GP9": "ENC_DT",
            "GP10": "ENC_SW",
            "GP14": "DISPLAY_CLK",
            "GP15": "DISPLAY_DIO",
            "GP29": "ALERT",
            # Unused — each to its own test pad net
            "GP0": "TP_GP0",
            "GP1": "TP_GP1",
            "GP11": "TP_GP11",
            "GP12": "TP_GP12",
            "GP13": "TP_GP13",
            "GP26": "TP_GP26",
            "GP27": "TP_GP27",
            "GP28": "TP_GP28",
        },
    )
)

# Test-pad "components" for each unused GPIO, so there's something placing the net
for ref, pos, net in [
    ("TP1", (340, 100), "TP_GP0"),
    ("TP2", (340, 105), "TP_GP1"),
    ("TP3", (340, 110), "TP_GP11"),
    ("TP4", (340, 115), "TP_GP12"),
    ("TP5", (340, 120), "TP_GP13"),
    ("TP6", (340, 125), "TP_GP26"),
    ("TP7", (340, 130), "TP_GP27"),
    ("TP8", (340, 135), "TP_GP28"),
]:
    add(
        Part(
            ref,
            "Connector:TestPoint",
            pos,
            value=f"TP {net}",
            footprint="TestPoint:TestPoint_Pad_D1.5mm",
            pin_nets={"1": net},
        )
    )

# Rotary encoder header J3 (5-pin JST-XH)
add(
    Part(
        "J3",
        "Connector_Generic:Conn_01x05",
        (340, 170),
        value="Encoder",
        footprint="Connector_JST:JST_XH_B5B-XH-A_1x05_P2.50mm_Vertical",
        pin_nets={
            "1": "+3V3",
            "2": "GND",
            "3": "ENC_CLK",
            "4": "ENC_DT",
            "5": "ENC_SW",
        },
    )
)

# TM1637 display header J4 (4-pin JST-XH)
add(
    Part(
        "J4",
        "Connector_Generic:Conn_01x04",
        (340, 210),
        value="Display",
        footprint="Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical",
        pin_nets={
            "1": "+3V3",
            "2": "GND",
            "3": "DISPLAY_CLK",
            "4": "DISPLAY_DIO",
        },
    )
)

# Status LEDs (1kΩ from LED cathode to GATE_*_IN, anode to +3V3)
for i, (ref_led, ref_r, pos_led, pos_r, net) in enumerate(
    [
        ("D_LED_P1", "R_LED_P1", (300, 200), (300, 205), "GATE_P1_IN"),
        ("D_LED_P2", "R_LED_P2", (310, 200), (310, 205), "GATE_P2_IN"),
        ("D_LED_N1", "R_LED_N1", (300, 220), (300, 225), "GATE_N1_IN"),
        ("D_LED_N2", "R_LED_N2", (310, 220), (310, 225), "GATE_N2_IN"),
    ]
):
    # Device:LED pin 1 = K (cathode), pin 2 = A (anode).
    # Light on when GATE_xx_IN is HIGH (MCU commanded ON): anode → GATE_xx_IN,
    # cathode → R → GND, current = (3.3-2)/1k ≈ 1.3 mA.
    led_internal = f"{ref_led}_K"
    add(
        Part(
            ref_led,
            "Device:LED",
            pos_led,
            value="GREEN",
            footprint="LED_SMD:LED_0603_1608Metric",
            pin_nets={"1": led_internal, "2": net},
        )
    )
    add(
        Part(
            ref_r,
            "Device:R",
            pos_r,
            value="1k",
            footprint="Resistor_SMD:R_0603_1608Metric",
            pin_nets={"1": led_internal, "2": "GND"},
        )
    )

add(
    Part(
        "D_LED_PWR",
        "Device:LED",
        (320, 200),
        value="GREEN",
        footprint="LED_SMD:LED_0603_1608Metric",
        pin_nets={"1": "PWR_LED_K", "2": "+3V3"},
    )
)
add(
    Part(
        "R_LED_PWR",
        "Device:R",
        (320, 205),
        value="1k",
        footprint="Resistor_SMD:R_0603_1608Metric",
        pin_nets={"1": "PWR_LED_K", "2": "GND"},
    )
)

# ---------- Cycler-neg tie and PWR_FLAGs ----------
# 0-ohm resistor between CYCLER_IN- and GND. DNP to lift the tie for floating cyclers.
add(
    Part(
        "R_CYC_GND",
        "Device:R",
        (50, 45),
        value="0R",
        footprint="Resistor_SMD:R_0805_2012Metric",
        pin_nets={"1": "CYCLER_IN-", "2": "GND"},
    )
)

# PWR_FLAG symbols tell ERC "this net is driven by an external source"
# Place one per externally-driven net: +HV, GND, CELL_*_*, etc.
# Dedicated column on the far right, clear of all other pin positions
for ref, pos, net in [
    ("PWR_FLAG_HV", (400, 40), "+HV"),
    ("PWR_FLAG_GND", (400, 55), "GND"),
    ("PWR_FLAG_5V", (400, 70), "+5V"),
    ("PWR_FLAG_CAN", (400, 85), "CELL_A_NEG"),
    ("PWR_FLAG_CBN", (400, 100), "CELL_B_NEG"),
]:
    add(
        Part(
            ref,
            "power:PWR_FLAG",
            pos,
            value="PWR_FLAG",
            pin_nets={"1": net},
        )
    )


# --------------------------------------------------------------------------- #
# Emit                                                                        #
# --------------------------------------------------------------------------- #


def u() -> str:
    return str(uuid.uuid4())


def abs_pin(part: Part, pin_num: str) -> tuple[float, float, int]:
    """Return (abs_x, abs_y, pin_orientation) for a part's pin given rotation and mirror."""
    px, py, pa = PIN_POS[part.lib_id][pin_num]
    # Apply rotation
    r = part.rotation
    if r == 0:
        x, y, a = px, py, pa
    elif r == 90:
        x, y, a = -py, px, (pa + 90) % 360
    elif r == 180:
        x, y, a = -px, -py, (pa + 180) % 360
    elif r == 270:
        x, y, a = py, -px, (pa + 270) % 360
    else:
        raise ValueError(f"unsupported rotation {r}")
    # Mirror
    if part.mirror == "y":  # mirror across vertical axis — flip X
        x = -x
        a = (180 - a) % 360
    elif part.mirror == "x":
        y = -y
        a = -a % 360
    # Translate by part position
    # Note: KiCad schematic Y axis increases downward — we negate pin Y so that
    # a symbol pin defined at +Y in the symbol appears ABOVE the position point
    # in the schematic (visually up = -Y in screen coordinates).
    return part.pos[0] + x, part.pos[1] - y, a


def emit_symbol_instance(part: Part) -> str:
    ref = part.ref
    lib_id = part.lib_id
    x, y = part.pos
    s = []
    s.append(f'\t(symbol (lib_id "{lib_id}")')
    s.append(f"\t\t(at {x} {y} {part.rotation})")
    if part.mirror:
        s.append(f'\t\t(mirror {part.mirror})')
    s.append(f"\t\t(unit 1)")
    s.append(f"\t\t(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)")
    s.append(f'\t\t(uuid "{u()}")')
    s.append(f'\t\t(property "Reference" "{ref}" (at {x + 2.54} {y - 2.54} 0))')
    val = part.value or ref
    s.append(f'\t\t(property "Value" "{val}" (at {x + 2.54} {y + 2.54} 0))')
    if part.footprint:
        s.append(
            f'\t\t(property "Footprint" "{part.footprint}" (at {x} {y} 0) (hide yes))'
        )
    if part.datasheet:
        s.append(
            f'\t\t(property "Datasheet" "{part.datasheet}" (at {x} {y} 0) (hide yes))'
        )
    for k, v in part.fields.items():
        s.append(f'\t\t(property "{k}" "{v}" (at {x} {y} 0) (hide yes))')
    # Pins — each needs a pin spec with UUID for net connection
    for pin_num in PIN_POS[part.lib_id]:
        s.append(f'\t\t(pin "{pin_num}" (uuid "{u()}"))')
    # Instances block — KiCad 10 schematic symbols require this for project wiring
    s.append('\t\t(instances')
    s.append('\t\t\t(project "switching_circuit_v2"')
    s.append('\t\t\t\t(path "/11111111-1111-1111-1111-111111111111"')
    s.append(f'\t\t\t\t\t(reference "{ref}") (unit 1)')
    s.append('\t\t\t\t)')
    s.append('\t\t\t)')
    s.append('\t\t)')
    s.append("\t)")
    return "\n".join(s)


def emit_global_label(x: float, y: float, orientation: int, net: str) -> str:
    """Emit a global_label at (x, y). Orientation matches pin (text points outward)."""
    # Global label shape: "input", "output", "bidirectional", "tri_state", "passive"
    shape = "passive"
    return (
        f'\t(global_label "{net}" (shape {shape}) (at {x} {y} {orientation})\n'
        f'\t\t(effects (font (size 1.27 1.27)) (justify left))\n'
        f'\t\t(uuid "{u()}")\n'
        f"\t)"
    )


def emit_no_connect(x: float, y: float) -> str:
    return f'\t(no_connect (at {x} {y}) (uuid "{u()}"))'


def _snap(v: float, grid: float = 1.27) -> float:
    return round(v / grid) * grid


def generate() -> str:
    # Snap all part positions to the 1.27mm grid so pin endpoints land on the
    # schematic connection grid. Stock symbol pin offsets are already grid-aligned.
    for p in PARTS:
        p.pos = (_snap(p.pos[0]), _snap(p.pos[1]))
    lines = []
    lines.append("(kicad_sch")
    lines.append("\t(version 20250114)")
    lines.append('\t(generator "gen_schematic.py")')
    lines.append('\t(generator_version "10.0")')
    lines.append('\t(uuid "11111111-1111-1111-1111-111111111111")')
    lines.append('\t(paper "A3")')

    # lib_symbols: copy the symbol definition for every lib_id we use
    lines.append("\t(lib_symbols")
    for lib_id in LIBS:
        # Re-key the symbol name to lib_id:name format in the embedded copy
        txt = SYMBOL_TEXT[lib_id]
        _, name = lib_id.split(":", 1)
        # Replace (symbol "name" with (symbol "lib_id:name" — KiCad expects this
        keyed = re.sub(
            r'\(symbol "' + re.escape(name) + r'"',
            f'(symbol "{lib_id}"',
            txt,
            count=1,
        )
        # Indent the full block by one tab relative to current depth
        for ln in keyed.split("\n"):
            lines.append("\t\t" + ln if ln.strip() else ln)
    lines.append("\t)")

    # Symbol instances
    for p in PARTS:
        lines.append(emit_symbol_instance(p))

    # For every pin of every part, emit a global_label at the pin's absolute position
    for p in PARTS:
        for pin_num, net in p.pin_nets.items():
            if pin_num not in PIN_POS[p.lib_id]:
                raise ValueError(
                    f"part {p.ref} lib_id {p.lib_id}: pin {pin_num} not in symbol"
                )
            x, y, a = abs_pin(p, pin_num)
            lines.append(emit_global_label(x, y, a, net))
        for pin_num in p.nc_pins:
            x, y, _ = abs_pin(p, pin_num)
            lines.append(emit_no_connect(x, y))

    lines.append("\t(sheet_instances")
    lines.append('\t\t(path "/" (page "1"))')
    lines.append("\t)")
    lines.append("\t(embedded_fonts no)")
    lines.append(")")
    return "\n".join(lines) + "\n"


def main() -> None:
    sch = generate()
    OUTPUT_SCH.write_text(sch)
    print(f"Wrote {OUTPUT_SCH} ({len(sch)} chars, {len(PARTS)} parts)")


if __name__ == "__main__":
    main()
