"""
wire_legend.py — AIC wire color / AWG / phase-code convention.

Transcribed from the project Wire Legend. Given a circuit function and a
phase/code letter, returns the wire color; given a function, returns the AWG
gauge. Also maps phase code letters to AutoCAD label terms (A/B/C -> %%CA/%%CB/%%CC).

Used to fill the FillIndex Color and Wire Gauge, and to normalize phase terms.
"""

# function -> { code_letter : color }   (POWER circuits are keyed by phase letter)
POWER = {
    "480VAC_3PH": {"A": "BROWN", "B": "ORANGE", "C": "YELLOW", "N": "GRAY", "G": "GREEN"},
    "240/208VAC_3PH": {"A": "BLACK", "B": "RED (ORG. IF HI LEG)", "C": "BLUE", "N": "WHITE", "G": "GREEN"},
    "240/120VAC_1PH": {"L1": "BLACK", "L2": "RED", "N": "WHITE", "G": "GREEN"},
}

# function -> single color (code letter N/A), plus AWG
SIGNAL = {
    "AC_CONTROL":            {"color": "RED (YELLOW FOR FOREIGN CIRCUITS)", "awg": "14"},
    "AC_DIGITAL_INPUT":      {"color": "RED", "awg": "16"},
    "AC_DIGITAL_OUTPUT":     {"color": "RED (YELLOW FOR FOREIGN CIRCUITS)", "awg": "16"},
    "DC_CONTROL":            {"color": "BLUE", "awg": "16"},
    "DC_DIGITAL_INPUT":      {"color": "BLUE", "awg": "16"},
    "DC_DIGITAL_OUTPUT":     {"color": "BLUE (YELLOW FOR FOREIGN CIRCUITS)", "awg": "16"},
    "24VDC_POS":             {"color": "BLUE", "awg": "16"},
    "24VDC_NEG":             {"color": "BLUE/WHITE", "awg": "16"},
    "12VDC_POS":             {"color": "PINK/WHITE", "awg": "16"},
    "12VDC_NEG":             {"color": "BLACK/WHITE", "awg": "16"},
}

# multi-conductor / special (keyed by code)
SPECIAL = {
    "TWISTED_SHIELDED_PAIR": {"codes": {"+": "RED", "-": "BLACK"}, "awg": "18"},
    "3WIRE_POTENTIOMETER":   {"codes": {"+10V": "RED", "WIPER": "BLUE", "GND": "BLACK"}, "awg": "18"},
    "PANEL_HEATER":          {"codes": {"L": "NATURAL - TRANSPARENT MICA SILICATE BRAID",
                                        "N": "NATURAL - TRANSPARENT MICA SILICATE BRAID"}, "awg": "14"},
}

# power AWG / insulation are "PER U.L."; signal/special insulation is 600V.
POWER_AWG = "PER U.L."

# phase code letter -> AutoCAD wire-label term (Ø = %%C)
PHASE_TERM = {"A": "%%CA", "B": "%%CB", "C": "%%CC"}


def phase_term(code):
    """A/B/C -> %%CA/%%CB/%%CC ; everything else passes through (N, G, L1, L2, +, -)."""
    return PHASE_TERM.get(str(code).strip().upper(), str(code).strip())


def color_for(function, code=None):
    """Return the wire color for a function (+ phase/code letter for POWER/SPECIAL)."""
    f = function
    if f in POWER:
        return POWER[f].get(str(code).strip().upper(), "")
    if f in SIGNAL:
        return SIGNAL[f]["color"]
    if f in SPECIAL:
        return SPECIAL[f]["codes"].get(str(code).strip(), "")
    return ""


def awg_for(function):
    if function in POWER:
        return POWER_AWG
    if function in SIGNAL:
        return SIGNAL[function]["awg"]
    if function in SPECIAL:
        return SPECIAL[function]["awg"]
    return ""


if __name__ == "__main__":
    print("480V 3Ø phase B ->", color_for("480VAC_3PH", "B"), "| AWG", awg_for("480VAC_3PH"))
    print("AC CONTROL ->", color_for("AC_CONTROL"), "| AWG", awg_for("AC_CONTROL"))
    print("TSP + ->", color_for("TWISTED_SHIELDED_PAIR", "+"), "| AWG", awg_for("TWISTED_SHIELDED_PAIR"))
    print("phase C term ->", phase_term("C"))
