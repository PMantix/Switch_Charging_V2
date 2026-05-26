#!/usr/bin/env python3
"""
Generate the flat KiCad 10 schematic for Switching Circuit V3 PCB.

Reads symbol definitions from stock KiCad libraries + the custom library, lays
out ~120 parts on a grid across an A3 sheet, connects nets via global labels.

Not pretty — placement is mechanical. The user can drag components around after
opening in the Schematic Editor. The important invariants are:
  - every part instance has a valid lib_id matching the lib_symbols block
  - every pin either has a global_label at its connection point or a no_connect
  - ERC clean

Run:
    python pcb/v3/gen_schematic_v3.py
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
REPO_ROOT = PCB_DIR.parent.parent

# Stock KiCad symbol library — Windows first, macOS fallback
STOCK_SYMBOLS = Path("C:/Program Files/KiCad/10.0/share/kicad/symbols")
if not STOCK_SYMBOLS.exists():
    STOCK_SYMBOLS = Path(
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
    )

CUSTOM_LIB = PCB_DIR / "lib" / "switching_circuit_v3.kicad_sym"
PROJECT_DIR = PCB_DIR / "switching_circuit_v3"
OUTPUT_SCH = PROJECT_DIR / "switching_circuit_v3.kicad_sch"


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
    # Stock symbols
    "Device:R": (STOCK_SYMBOLS / "Device.kicad_sym", "R"),
    "Device:C": (STOCK_SYMBOLS / "Device.kicad_sym", "C"),
    "Device:LED": (STOCK_SYMBOLS / "Device.kicad_sym", "LED"),
    "Device:Polyfuse_Small": (STOCK_SYMBOLS / "Device.kicad_sym", "Polyfuse_Small"),
    "Device:D_TVS": (STOCK_SYMBOLS / "Device.kicad_sym", "D_TVS"),
    "Device:D": (STOCK_SYMBOLS / "Device.kicad_sym", "D"),
    "Transistor_FET:Q_NMOS_GSD": (
        STOCK_SYMBOLS / "Transistor_FET.kicad_sym",
        "Q_NMOS_GSD",
    ),
    "Transistor_FET:Q_PMOS_GSD": (
        STOCK_SYMBOLS / "Transistor_FET.kicad_sym",
        "Q_PMOS_GSD",
    ),
    "Connector:Screw_Terminal_01x02": (
        STOCK_SYMBOLS / "Connector.kicad_sym",
        "Screw_Terminal_01x02",
    ),
    "Connector:TestPoint": (STOCK_SYMBOLS / "Connector.kicad_sym", "TestPoint"),
    "Connector_Generic:Conn_01x04": (
        STOCK_SYMBOLS / "Connector_Generic.kicad_sym",
        "Conn_01x04",
    ),
    "Connector_Generic:Conn_01x06": (
        STOCK_SYMBOLS / "Connector_Generic.kicad_sym",
        "Conn_01x06",
    ),
    "power:PWR_FLAG": (STOCK_SYMBOLS / "power.kicad_sym", "PWR_FLAG"),
    # Custom symbols
    "switching_circuit_v3:UCC5304": (CUSTOM_LIB, "UCC5304"),
    "switching_circuit_v3:B0512S_1WR3": (CUSTOM_LIB, "B0512S_1WR3"),
    "switching_circuit_v3:Pico2": (CUSTOM_LIB, "Pico2"),
    "switching_circuit_v3:ADS131M04": (CUSTOM_LIB, "ADS131M04"),
    "switching_circuit_v3:INA180A1": (CUSTOM_LIB, "INA180A1"),
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
# Parts definitions — all ~120 of them                                        #
# --------------------------------------------------------------------------- #

# Layout groups across A3 sheet (420x297mm), margins ~20mm:
#   Power input:       x=25-160,  y=25-50
#   Power stage:       x=25-145,  y=60-165
#   Current sense:     x=170-260, y=25-55
#   ADCs + support:    x=170-295, y=60-125
#   Bus dividers:      x=170-290, y=130-165
#   Gate drivers:      x=25-155,  y=175-265
#   Isolated supply:   x=170-295, y=175-215
#   MCU:               x=310-365, y=60-200
#   Peripherals:       x=375-415, y=25-165
#   LEDs + NeoPixel:   x=310-390, y=220-270
#   PWR_FLAGs:         x=400-415, y=220-275

PARTS: list[Part] = []


def add(p: Part) -> Part:
    PARTS.append(p)
    return p


# ========================================================================== #
# SECTION 1: Power-in protection (top-left area, y=30-50)                    #
# ========================================================================== #

# J1 is Kelvin clip pads — copper pads only, no schematic component.
# Use test points to introduce CYCLER_IN+ and CYCLER_IN- nets.
add(Part("TP_CYC_P", "Connector:TestPoint", (40, 30), value="CYCLER_IN+",
         footprint="TestPoint:TestPoint_Pad_D1.5mm",
         pin_nets={"1": "CYCLER_IN+"}))
add(Part("TP_CYC_N", "Connector:TestPoint", (50, 30), value="CYCLER_IN-",
         footprint="TestPoint:TestPoint_Pad_D1.5mm",
         pin_nets={"1": "CYCLER_IN-"}))

add(Part("F1", "Device:Polyfuse_Small", (60, 30), value="3A",
         footprint="Fuse:Fuse_1812_4532Metric",
         pin_nets={"1": "CYCLER_IN+", "2": "+HV_PREFUSE"}))

# Q_PMOS_GSD: 1=Gate, 2=Source, 3=Drain
add(Part("Q_RP", "Transistor_FET:Q_PMOS_GSD", (80, 35), value="AO3401A",
         footprint="Package_TO_SOT_SMD:SOT-23",
         pin_nets={"1": "CYCLER_IN-", "2": "+HV_PREFUSE", "3": "+HV"},
         fields={"MPN": "AO3401A", "LCSC": "C15127"}))

add(Part("R_RP", "Device:R", (90, 30), value="10k",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "+HV_PREFUSE", "2": "CYCLER_IN-"}))

add(Part("D_TVS", "Device:D_TVS", (105, 40), value="SMBJ12CA",
         footprint="Diode_SMD:D_SMB",
         pin_nets={"1": "GND", "2": "+HV"},
         fields={"LCSC": "C87447"}))

add(Part("C_BULK1", "Device:C", (120, 40), value="10uF",
         footprint="Capacitor_SMD:C_1206_3216Metric",
         pin_nets={"1": "+HV", "2": "GND"}))

add(Part("C_BULK2", "Device:C", (130, 40), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+HV", "2": "GND"}))

add(Part("C_5V1", "Device:C", (150, 40), value="10uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "+5V", "2": "GND"}))

add(Part("C_5V2", "Device:C", (160, 40), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+5V", "2": "GND"}))

# Cycler-neg tie to GND (DNP to lift for floating cyclers)
add(Part("R_CYC_GND", "Device:R", (50, 45), value="0R",
         footprint="Resistor_SMD:R_0805_2012Metric",
         pin_nets={"1": "CYCLER_IN-", "2": "GND"}))

# ========================================================================== #
# SECTION 2: Power stage — 4 MOSFETs + 4 shunts + pulldowns + cell terms    #
# ========================================================================== #

# Q_NMOS_GSD: 1=Gate, 2=Source, 3=Drain
add(Part("Q1", "Transistor_FET:Q_NMOS_GSD", (60, 80), value="AO3400A",
         footprint="Package_TO_SOT_SMD:SOT-23",
         pin_nets={"1": "GATE_P1_OUT", "2": "CELL_A_POS", "3": "+HV_P1"},
         fields={"MPN": "AO3400A", "LCSC": "C20917"}))

add(Part("Q2", "Transistor_FET:Q_NMOS_GSD", (120, 80), value="AO3400A",
         footprint="Package_TO_SOT_SMD:SOT-23",
         pin_nets={"1": "GATE_P2_OUT", "2": "CELL_B_POS", "3": "+HV_P2"},
         fields={"MPN": "AO3400A", "LCSC": "C20917"}))

add(Part("Q3", "Transistor_FET:Q_NMOS_GSD", (60, 130), value="AO3400A",
         footprint="Package_TO_SOT_SMD:SOT-23",
         pin_nets={"1": "GATE_N1_OUT", "2": "GND_N1", "3": "CELL_A_NEG"},
         fields={"MPN": "AO3400A", "LCSC": "C20917"}))

add(Part("Q4", "Transistor_FET:Q_NMOS_GSD", (120, 130), value="AO3400A",
         footprint="Package_TO_SOT_SMD:SOT-23",
         pin_nets={"1": "GATE_N2_OUT", "2": "GND_N2", "3": "CELL_B_NEG"},
         fields={"MPN": "AO3400A", "LCSC": "C20917"}))

# Shunts — 0.01 ohm 2512
for ref, pos, nets in [
    ("R_SH_P1", (60, 65), {"1": "+HV", "2": "+HV_P1"}),
    ("R_SH_P2", (120, 65), {"1": "+HV", "2": "+HV_P2"}),
    ("R_SH_N1", (60, 145), {"1": "GND_N1", "2": "GND"}),
    ("R_SH_N2", (120, 145), {"1": "GND_N2", "2": "GND"}),
]:
    add(Part(ref, "Device:R", pos, value="0R01",
             footprint="Resistor_SMD:R_2512_6332Metric",
             pin_nets=nets))

# Gate pulldowns (10k from gate to source)
for ref, pos, nets in [
    ("Rgpd_1", (70, 80), {"1": "GATE_P1_OUT", "2": "CELL_A_POS"}),
    ("Rgpd_2", (130, 80), {"1": "GATE_P2_OUT", "2": "CELL_B_POS"}),
    ("Rgpd_3", (70, 130), {"1": "GATE_N1_OUT", "2": "GND_N1"}),
    ("Rgpd_4", (130, 130), {"1": "GATE_N2_OUT", "2": "GND_N2"}),
]:
    add(Part(ref, "Device:R", pos, value="10k",
             footprint="Resistor_SMD:R_0603_1608Metric",
             pin_nets=nets))

# Cell terminals — TWO 2-pos screw terminals
add(Part("J2a", "Connector:Screw_Terminal_01x02", (90, 100), value="Cell+",
         footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
         pin_nets={"1": "CELL_A_POS", "2": "CELL_B_POS"}))
add(Part("J2b", "Connector:Screw_Terminal_01x02", (90, 120), value="Cell-",
         footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
         pin_nets={"1": "CELL_A_NEG", "2": "CELL_B_NEG"}))

# ========================================================================== #
# SECTION 3: High-side current-sense amps — 2x INA180A1                     #
# ========================================================================== #

# INA180A1 Pinout A (TI SBOS741H): 1=OUT, 2=GND, 3=IN+, 4=IN-, 5=VS
add(Part("U_CSA_P1", "switching_circuit_v3:INA180A1", (180, 60), value="INA180A1",
         footprint="Package_TO_SOT_SMD:SOT-23-5",
         pin_nets={"1": "CSA_P1_OUT", "2": "GND", "3": "+HV",
                   "4": "+HV_P1", "5": "+3V3"},
         fields={"LCSC": "C122228"}))
add(Part("U_CSA_P2", "switching_circuit_v3:INA180A1", (220, 60), value="INA180A1",
         footprint="Package_TO_SOT_SMD:SOT-23-5",
         pin_nets={"1": "CSA_P2_OUT", "2": "GND", "3": "+HV",
                   "4": "+HV_P2", "5": "+3V3"},
         fields={"LCSC": "C122228"}))

# CSA decoupling (VS to GND, 100nF)
add(Part("C_CSA_P1", "Device:C", (195, 50), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "GND"}))
add(Part("C_CSA_P2", "Device:C", (235, 50), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "GND"}))

# ========================================================================== #
# SECTION 4: ADS131M04 ADCs                                                 #
# ========================================================================== #

# ADS131M04 pins: 1=AIN0P, 2=AIN0N, 3=AIN1N, 4=AIN1P, 5=AIN2P, 6=AIN2N,
#   7=AIN3N, 8=AIN3P, 9=SYNC/RESET, 10=CS, 11=DRDY, 12=SCLK, 13=DOUT,
#   14=DIN, 15=CLKIN, 16=CAP, 17=DGND, 18=DVDD, 19=AVDD, 20=AGND, 21=EPAD
add(Part("U_ADC1", "switching_circuit_v3:ADS131M04", (180, 90), value="ADS131M04",
         footprint="Package_DFN_QFN:Texas_RUK0020B_WQFN-20-1EP_3x3mm_P0.4mm_EP1.7x1.7mm",
         pin_nets={
             "1": "CSA_P1_OUT",    # AIN0P — P1 shunt via INA180
             "2": "AGND",          # AIN0N
             "3": "AGND",          # AIN1N
             "4": "CSA_P2_OUT",    # AIN1P — P2 shunt via INA180
             "5": "GND_N1",        # AIN2P — N1 shunt direct
             "6": "GND",           # AIN2N
             "7": "GND",           # AIN3N
             "8": "GND_N2",        # AIN3P — N2 shunt direct
             "9": "ADS_RESET",     # SYNC/RESET
             "10": "ADS1_CS",      # CS
             "11": "ADS1_DRDY",    # DRDY
             "12": "ADS_SCK",      # SCLK
             "13": "ADS_MISO",     # DOUT
             "14": "ADS_MOSI",     # DIN
             "15": "ADS_CLKIN",    # CLKIN
             "16": "CAP1",         # CAP
             "17": "GND",          # DGND
             "18": "+3V3",         # DVDD
             "19": "+3V3_A",       # AVDD
             "20": "AGND",         # AGND
             "21": "AGND",         # thermal pad
         },
         fields={"LCSC": "C5121509"}))

add(Part("U_ADC2", "switching_circuit_v3:ADS131M04", (260, 90), value="ADS131M04",
         footprint="Package_DFN_QFN:Texas_RUK0020B_WQFN-20-1EP_3x3mm_P0.4mm_EP1.7x1.7mm",
         pin_nets={
             "1": "VDIV_HV",       # AIN0P — +HV bus voltage divider
             "2": "AGND",          # AIN0N
             "3": "AGND",          # AIN1N
             "4": "VDIV_HV_P1",   # AIN1P — +HV_P1 divider
             "5": "VDIV_HV_P2",   # AIN2P — +HV_P2 divider
             "6": "AGND",          # AIN2N
             "7": "AGND",          # AIN3N
             "8": "VDIV_GND_N1",  # AIN3P — GND_N1 divider
             "9": "ADS_RESET",     # SYNC/RESET (shared)
             "10": "ADS2_CS",      # CS
             "11": "ADS2_DRDY",    # DRDY
             "12": "ADS_SCK",      # SCLK (shared)
             "13": "ADS_MISO",     # DOUT (shared)
             "14": "ADS_MOSI",     # DIN (shared)
             "15": "ADS_CLKIN",    # CLKIN (shared)
             "16": "CAP2",         # CAP
             "17": "GND",          # DGND
             "18": "+3V3",         # DVDD
             "19": "+3V3_A",       # AVDD
             "20": "AGND",         # AGND
             "21": "AGND",         # thermal pad
         },
         fields={"LCSC": "C5121509"}))

# ADS support components — shared AVDD bulk cap
add(Part("C_AVDD1", "Device:C", (200, 120), value="10uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "+3V3_A", "2": "AGND"}))

# Per-device AVDD decoupling (100nF)
add(Part("C_AVDD2_U1", "Device:C", (190, 115), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3_A", "2": "AGND"}))
add(Part("C_AVDD2_U2", "Device:C", (270, 115), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3_A", "2": "AGND"}))

# Per-device DVDD decoupling (1uF)
add(Part("C_DVDD_U1", "Device:C", (190, 110), value="1uF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "GND"}))
add(Part("C_DVDD_U2", "Device:C", (270, 110), value="1uF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "GND"}))

# Per-device CAP pin capacitor (220nF)
add(Part("C_CAP_U1", "Device:C", (195, 105), value="220nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "CAP1", "2": "GND"}))
add(Part("C_CAP_U2", "Device:C", (275, 105), value="220nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "CAP2", "2": "GND"}))

# Ferrite bead (analog supply filter) — modeled as Device:R (2 pins)
add(Part("L_AVDD", "Device:R", (210, 120), value="600R@100MHz",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "+3V3_A"}))

# ========================================================================== #
# SECTION 5: Bus voltage dividers + clamps                                   #
# ========================================================================== #

# 4x voltage divider: 91k high / 9.1k low / 100nF anti-alias / BAV99 clamp
_DIVIDERS = [
    ("1", "+HV", "VDIV_HV"),
    ("2", "+HV_P1", "VDIV_HV_P1"),
    ("3", "+HV_P2", "VDIV_HV_P2"),
    ("4", "GND_N1", "VDIV_GND_N1"),
]
for i, (suffix, high_net, vdiv_net) in enumerate(_DIVIDERS):
    x = 180 + i * 30
    add(Part(f"R_DIV_H{suffix}", "Device:R", (x, 130), value="91k",
             footprint="Resistor_SMD:R_0603_1608Metric",
             pin_nets={"1": high_net, "2": vdiv_net}))
    add(Part(f"R_DIV_L{suffix}", "Device:R", (x, 140), value="9.1k",
             footprint="Resistor_SMD:R_0603_1608Metric",
             pin_nets={"1": vdiv_net, "2": "AGND"}))
    add(Part(f"C_AA{suffix}", "Device:C", (x + 5, 140), value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric",
             pin_nets={"1": vdiv_net, "2": "AGND"}))
    # BAV99 input clamp — use Q_NMOS_GSD as 3-pin SOT-23 stand-in
    # BAV99 pinout: 1=A1(anode D1), 2=K2(cathode D2), 3=K1/A2(signal)
    # Mapped to Q_NMOS_GSD: 1=Gate→AGND, 2=Source→+3V3_A, 3=Drain→VDIV_*
    add(Part(f"D_CLAMP{suffix}", "Transistor_FET:Q_NMOS_GSD", (x + 10, 135),
             value="BAV99",
             footprint="Package_TO_SOT_SMD:SOT-23",
             pin_nets={"1": "AGND", "2": "+3V3_A", "3": vdiv_net}))

# ========================================================================== #
# SECTION 6: Gate drivers — 4x UCC5304                                      #
# ========================================================================== #

# UCC5304 pins: 1=IN, 2=VCCI, 3=VCCI, 4=GND, 5=VSS, 6=VSS, 7=OUT, 8=VDD
add(Part("U1", "switching_circuit_v3:UCC5304", (60, 185), value="UCC5304DWVR",
         footprint="switching_circuit_v3:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
         pin_nets={"1": "GATE_P1_IN", "2": "+3V3", "3": "+3V3", "4": "GND",
                   "5": "CELL_A_POS", "6": "CELL_A_POS",
                   "7": "GATE_P1_OUT_PRE", "8": "VCC2_P1"},
         fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"}))

add(Part("U2", "switching_circuit_v3:UCC5304", (120, 185), value="UCC5304DWVR",
         footprint="switching_circuit_v3:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
         pin_nets={"1": "GATE_P2_IN", "2": "+3V3", "3": "+3V3", "4": "GND",
                   "5": "CELL_B_POS", "6": "CELL_B_POS",
                   "7": "GATE_P2_OUT_PRE", "8": "VCC2_P2"},
         fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"}))

add(Part("U3", "switching_circuit_v3:UCC5304", (60, 225), value="UCC5304DWVR",
         footprint="switching_circuit_v3:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
         pin_nets={"1": "GATE_N1_IN", "2": "+3V3", "3": "+3V3", "4": "GND",
                   "5": "GND", "6": "GND",
                   "7": "GATE_N1_OUT_PRE", "8": "VCC2_LS"},
         fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"}))

add(Part("U4", "switching_circuit_v3:UCC5304", (120, 225), value="UCC5304DWVR",
         footprint="switching_circuit_v3:SOIC-8_DWV_7.5x11.5mm_P1.27mm",
         pin_nets={"1": "GATE_N2_IN", "2": "+3V3", "3": "+3V3", "4": "GND",
                   "5": "GND", "6": "GND",
                   "7": "GATE_N2_OUT_PRE", "8": "VCC2_LS"},
         fields={"MPN": "UCC5304DWVR", "DNP_JLC": "TRUE"}))

# Gate series resistors (10 ohm each)
for ref, pos, nets in [
    ("R_G_U1", (80, 185), {"1": "GATE_P1_OUT_PRE", "2": "GATE_P1_OUT"}),
    ("R_G_U2", (140, 185), {"1": "GATE_P2_OUT_PRE", "2": "GATE_P2_OUT"}),
    ("R_G_U3", (80, 225), {"1": "GATE_N1_OUT_PRE", "2": "GATE_N1_OUT"}),
    ("R_G_U4", (140, 225), {"1": "GATE_N2_OUT_PRE", "2": "GATE_N2_OUT"}),
]:
    add(Part(ref, "Device:R", pos, value="10R",
             footprint="Resistor_SMD:R_0603_1608Metric",
             pin_nets=nets))

# VCCI decoupling (100nF each)
for ref, pos, nets in [
    ("C_VCCI_U1", (50, 170), {"1": "+3V3", "2": "GND"}),
    ("C_VCCI_U2", (110, 170), {"1": "+3V3", "2": "GND"}),
    ("C_VCCI_U3", (50, 210), {"1": "+3V3", "2": "GND"}),
    ("C_VCCI_U4", (110, 210), {"1": "+3V3", "2": "GND"}),
]:
    add(Part(ref, "Device:C", pos, value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric",
             pin_nets=nets))

# VDD-VSS decoupling — 10uF + 100nF per driver
# U1/U2: VCC2_P1/P2 to CELL_A/B_POS
# U3/U4: VCC2_LS to GND
for ref, pos, nets in [
    ("C_VDD_U1_1", (70, 200), {"1": "VCC2_P1", "2": "CELL_A_POS"}),
    ("C_VDD_U1_2", (80, 200), {"1": "VCC2_P1", "2": "CELL_A_POS"}),
    ("C_VDD_U2_1", (130, 200), {"1": "VCC2_P2", "2": "CELL_B_POS"}),
    ("C_VDD_U2_2", (140, 200), {"1": "VCC2_P2", "2": "CELL_B_POS"}),
    ("C_VDD_U3_1", (70, 240), {"1": "VCC2_LS", "2": "GND"}),
    ("C_VDD_U3_2", (80, 240), {"1": "VCC2_LS", "2": "GND"}),
    ("C_VDD_U4_1", (130, 240), {"1": "VCC2_LS", "2": "GND"}),
    ("C_VDD_U4_2", (140, 240), {"1": "VCC2_LS", "2": "GND"}),
]:
    value = "10uF" if ref.endswith("_1") else "100nF"
    fp = ("Capacitor_SMD:C_1206_3216Metric" if value == "10uF"
          else "Capacitor_SMD:C_0603_1608Metric")
    add(Part(ref, "Device:C", pos, value=value, footprint=fp, pin_nets=nets))

# ========================================================================== #
# SECTION 7: Isolated supplies — 3x B0512S                                  #
# ========================================================================== #

# B0512S_1WR3 Mornsun pinout: 1=Vin-(GND), 2=Vin+, 3=Vout-(0V), 4=Vout+(+Vo)
add(Part("PS1", "switching_circuit_v3:B0512S_1WR3", (180, 185), value="B0512S-1WR3",
         footprint="switching_circuit_v3:B0512S_1WR3_SIP4",
         pin_nets={"1": "GND", "2": "+5V", "3": "CELL_A_POS", "4": "VCC2_P1"},
         fields={"MPN": "B0512S-1WR3"}))

add(Part("PS2", "switching_circuit_v3:B0512S_1WR3", (220, 185), value="B0512S-1WR3",
         footprint="switching_circuit_v3:B0512S_1WR3_SIP4",
         pin_nets={"1": "GND", "2": "+5V", "3": "CELL_B_POS", "4": "VCC2_P2"},
         fields={"MPN": "B0512S-1WR3"}))

add(Part("PS3", "switching_circuit_v3:B0512S_1WR3", (260, 185), value="B0512S-1WR3",
         footprint="switching_circuit_v3:B0512S_1WR3_SIP4",
         pin_nets={"1": "GND", "2": "+5V", "3": "GND", "4": "VCC2_LS"},
         fields={"MPN": "B0512S-1WR3"}))

# B0512S decoupling — per converter: 4.7uF + 100nF input, 2.2uF + 100nF output
# PS1
add(Part("C_PS1_IN1", "Device:C", (170, 170), value="4.7uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "+5V", "2": "GND"}))
add(Part("C_PS1_IN2", "Device:C", (175, 170), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+5V", "2": "GND"}))
add(Part("C_PS1_OUT1", "Device:C", (190, 170), value="2.2uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "VCC2_P1", "2": "CELL_A_POS"}))
add(Part("C_PS1_OUT2", "Device:C", (195, 170), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "VCC2_P1", "2": "CELL_A_POS"}))

# PS2
add(Part("C_PS2_IN1", "Device:C", (210, 170), value="4.7uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "+5V", "2": "GND"}))
add(Part("C_PS2_IN2", "Device:C", (215, 170), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+5V", "2": "GND"}))
add(Part("C_PS2_OUT1", "Device:C", (230, 170), value="2.2uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "VCC2_P2", "2": "CELL_B_POS"}))
add(Part("C_PS2_OUT2", "Device:C", (235, 170), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "VCC2_P2", "2": "CELL_B_POS"}))

# PS3
add(Part("C_PS3_IN1", "Device:C", (250, 170), value="4.7uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "+5V", "2": "GND"}))
add(Part("C_PS3_IN2", "Device:C", (255, 170), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+5V", "2": "GND"}))
add(Part("C_PS3_OUT1", "Device:C", (270, 170), value="2.2uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "VCC2_LS", "2": "GND"}))
add(Part("C_PS3_OUT2", "Device:C", (275, 170), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "VCC2_LS", "2": "GND"}))

# Bleeder resistors (1.2k, 0805)
add(Part("R_BLEED_PS1", "Device:R", (185, 195), value="1.2k",
         footprint="Resistor_SMD:R_0805_2012Metric",
         pin_nets={"1": "VCC2_P1", "2": "CELL_A_POS"}))
add(Part("R_BLEED_PS2", "Device:R", (225, 195), value="1.2k",
         footprint="Resistor_SMD:R_0805_2012Metric",
         pin_nets={"1": "VCC2_P2", "2": "CELL_B_POS"}))
add(Part("R_BLEED_PS3", "Device:R", (265, 195), value="1.2k",
         footprint="Resistor_SMD:R_0805_2012Metric",
         pin_nets={"1": "VCC2_LS", "2": "GND"}))

# ========================================================================== #
# SECTION 8: MCU — Pico 2                                                   #
# ========================================================================== #

add(Part("A1", "switching_circuit_v3:Pico2", (340, 125), value="Pico2",
         footprint="switching_circuit_v3:Pico2_SocketedTH",
         pin_nets={
             "VBUS": "+5V",
             "GND1": "GND",
             "GND2": "GND",
             "3V3_OUT": "+3V3",
             "GP0": "LED_AUTO",
             "GP1": "NEOPIXEL",
             "GP2": "GATE_P1_IN",
             "GP3": "GATE_P2_IN",
             "GP4": "GATE_N1_IN",
             "GP5": "GATE_N2_IN",
             "GP6": "SDA",
             "GP7": "SCL",
             "GP8": "BTN_A",
             "GP9": "BTN_B",
             "GP10": "ADS_SCK",
             "GP11": "ADS_MOSI",
             "GP12": "ADS_MISO",
             "GP13": "ADS1_CS",
             "GP14": "ADS1_DRDY",
             "GP15": "ADS_RESET",
             "GP16": "SD_MISO",
             "GP17": "SD_CS",
             "GP18": "SD_SCK",
             "GP19": "SD_MOSI",
             "GP20": "BTN_C",
             "GP21": "ADS2_DRDY",
             "GP22": "LED_REC",
             "GP26": "ADS2_CS",
             "GP27": "ADS_CLKIN",
             "GP28": "TP_GP28",
             "AGND": "AGND",
         },
         nc_pins=["RUN", "ADC_VREF", "3V3_EN", "VSYS"]))

# ========================================================================== #
# SECTION 9: OLED + I2C pullups                                             #
# ========================================================================== #

add(Part("J3", "Connector_Generic:Conn_01x04", (395, 35), value="OLED",
         footprint="Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical",
         pin_nets={"1": "+3V3", "2": "GND", "3": "SDA", "4": "SCL"}))

add(Part("R_SDA", "Device:R", (385, 30), value="4.7k",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "SDA"}))
add(Part("R_SCL", "Device:R", (385, 40), value="4.7k",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "SCL"}))

# ========================================================================== #
# SECTION 10: microSD                                                        #
# ========================================================================== #

# Model microSD socket as Conn_01x06 (MISO, CS, SCK, MOSI, VDD, GND)
add(Part("J4", "Connector_Generic:Conn_01x06", (395, 75), value="microSD",
         footprint="switching_circuit_v3:TF_PUSH",
         pin_nets={"1": "SD_MISO", "2": "SD_CS", "3": "SD_SCK",
                   "4": "SD_MOSI", "5": "+3V3", "6": "GND"}))

# SD power decoupling
add(Part("C_SD1", "Device:C", (385, 65), value="10uF",
         footprint="Capacitor_SMD:C_0805_2012Metric",
         pin_nets={"1": "+3V3", "2": "GND"}))
add(Part("C_SD2", "Device:C", (385, 75), value="100nF",
         footprint="Capacitor_SMD:C_0603_1608Metric",
         pin_nets={"1": "+3V3", "2": "GND"}))

# ========================================================================== #
# SECTION 11: Buttons — 3x tact switches                                    #
# ========================================================================== #

# Model each tact switch as Device:R with value "SW" (2-pin stand-in)
for ref_sw, ref_r, ref_c, pos_sw, pos_r, pos_c, net in [
    ("SW_A", "R_BTN_A", "C_BTN_A", (385, 110), (390, 110), (395, 110), "BTN_A"),
    ("SW_B", "R_BTN_B", "C_BTN_B", (385, 125), (390, 125), (395, 125), "BTN_B"),
    ("SW_C", "R_BTN_C", "C_BTN_C", (385, 140), (390, 140), (395, 140), "BTN_C"),
]:
    add(Part(ref_sw, "Device:R", pos_sw, value="SW",
             footprint="Button_Switch_SMD:SW_SPST_TL3342",
             pin_nets={"1": net, "2": "GND"}))
    add(Part(ref_r, "Device:R", pos_r, value="10k",
             footprint="Resistor_SMD:R_0603_1608Metric",
             pin_nets={"1": "+3V3", "2": net}))
    add(Part(ref_c, "Device:C", pos_c, value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric",
             pin_nets={"1": net, "2": "GND"}))

# ========================================================================== #
# SECTION 12: Status LEDs                                                    #
# ========================================================================== #

# Gate drive indicator LEDs (anode at +3V3, cathode through R to GATE_*_IN)
# Device:LED pin 1 = K (cathode), pin 2 = A (anode)
for ref_led, ref_r, pos_led, pos_r, net in [
    ("D_LED_P1", "R_LED_P1", (315, 225), (315, 232), "GATE_P1_IN"),
    ("D_LED_P2", "R_LED_P2", (330, 225), (330, 232), "GATE_P2_IN"),
    ("D_LED_N1", "R_LED_N1", (345, 225), (345, 232), "GATE_N1_IN"),
    ("D_LED_N2", "R_LED_N2", (360, 225), (360, 232), "GATE_N2_IN"),
]:
    k_net = f"{ref_led}_K"
    add(Part(ref_led, "Device:LED", pos_led, value="GREEN",
             footprint="LED_SMD:LED_0603_1608Metric",
             pin_nets={"1": k_net, "2": "+3V3"}))
    add(Part(ref_r, "Device:R", pos_r, value="1k",
             footprint="Resistor_SMD:R_0603_1608Metric",
             pin_nets={"1": k_net, "2": net}))

# Power LED
add(Part("D_LED_PWR", "Device:LED", (375, 225), value="GREEN",
         footprint="LED_SMD:LED_0603_1608Metric",
         pin_nets={"1": "PWR_LED_K", "2": "+3V3"}))
add(Part("R_LED_PWR", "Device:R", (375, 232), value="1k",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "PWR_LED_K", "2": "GND"}))

# GPIO-driven LEDs (anode through R to GPIO, cathode to GND)
add(Part("D_LED_REC", "Device:LED", (315, 245), value="GREEN",
         footprint="LED_SMD:LED_0603_1608Metric",
         pin_nets={"1": "GND", "2": "LED_REC_A"}))
add(Part("R_LED_REC", "Device:R", (315, 252), value="1k",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "LED_REC_A", "2": "LED_REC"}))

add(Part("D_LED_AUTO", "Device:LED", (330, 245), value="YELLOW",
         footprint="LED_SMD:LED_0603_1608Metric",
         pin_nets={"1": "GND", "2": "LED_AUTO_A"}))
add(Part("R_LED_AUTO", "Device:R", (330, 252), value="1k",
         footprint="Resistor_SMD:R_0603_1608Metric",
         pin_nets={"1": "LED_AUTO_A", "2": "LED_AUTO"}))

# NeoPixel VDD level shift: D_NEO (1N4148W)
# Device:D pin 1=K(cathode), pin 2=A(anode)
add(Part("D_NEO", "Device:D", (350, 250), value="1N4148W",
         footprint="Diode_SMD:D_SOD-123",
         pin_nets={"1": "NEO_VDD", "2": "+5V"}))

# NeoPixel connection test points
add(Part("TP_NEO_VDD", "Connector:TestPoint", (360, 245), value="NEO_VDD",
         footprint="TestPoint:TestPoint_Pad_D1.5mm",
         pin_nets={"1": "NEO_VDD"}))
add(Part("TP_NEO_DIN", "Connector:TestPoint", (365, 245), value="NEO_DIN",
         footprint="TestPoint:TestPoint_Pad_D1.5mm",
         pin_nets={"1": "NEOPIXEL"}))
add(Part("TP_NEO_GND", "Connector:TestPoint", (370, 245), value="NEO_GND",
         footprint="TestPoint:TestPoint_Pad_D1.5mm",
         pin_nets={"1": "GND"}))

# ========================================================================== #
# SECTION 13: PWR_FLAG symbols                                               #
# ========================================================================== #

for ref, pos, net in [
    ("PWR_FLAG_HV", (405, 225), "+HV"),
    ("PWR_FLAG_GND", (405, 235), "GND"),
    ("PWR_FLAG_5V", (405, 245), "+5V"),
    ("PWR_FLAG_CAN", (405, 255), "CELL_A_NEG"),
    ("PWR_FLAG_CBN", (405, 265), "CELL_B_NEG"),
    ("PWR_FLAG_AGND", (405, 275), "AGND"),
]:
    add(Part(ref, "power:PWR_FLAG", pos, value="PWR_FLAG",
             pin_nets={"1": net}))

# ========================================================================== #
# SECTION 14: Test pads                                                      #
# ========================================================================== #

add(Part("TP1", "Connector:TestPoint", (385, 160), value="GP28",
         footprint="TestPoint:TestPoint_Pad_D1.5mm",
         pin_nets={"1": "TP_GP28"}))


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
    s.append('\t\t\t(project "switching_circuit_v3"')
    s.append('\t\t\t\t(path "/11111111-1111-1111-1111-111111111111"')
    s.append(f'\t\t\t\t\t(reference "{ref}") (unit 1)')
    s.append('\t\t\t\t)')
    s.append('\t\t\t)')
    s.append('\t\t)')
    s.append("\t)")
    return "\n".join(s)


def emit_global_label(x: float, y: float, orientation: int, net: str) -> str:
    """Emit a global_label at (x, y). Orientation matches pin (text points outward)."""
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
    lines.append('\t(generator "gen_schematic_v3.py")')
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
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    sch = generate()
    OUTPUT_SCH.write_text(sch)
    print(f"Wrote {OUTPUT_SCH} ({len(sch)} chars, {len(PARTS)} parts)")


if __name__ == "__main__":
    main()
