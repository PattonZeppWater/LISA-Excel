"""
symbol_infer.py — infer the IDP symbol block (S Symbol / D Symbol) from a device
description, using the shape of the current symbol library.

The library block names encode the device, e.g.:
    XFMR_3PH_L, CB-TB_Square_R, PressureSwitch_NO_L, GND_R, MTP_R, DISC_Fuse-TB_Square_L
A block name = one or more device tokens (chained with '-'), then '_<L|R>'
(source/destination side). Valid symbols for a given wire Type + Wire Ct + side
are listed in idp_rules.json -> symbol_cascade, key "<TYPE-no-dashes>_<Ct>_<L|R>".

infer_symbol() recognizes device keywords in a From/To name or remark, then picks
the candidate block from the cascade whose name best matches the device.

Dependency-free (stdlib only). Loads the cascade from idp_rules.json.
"""
from __future__ import annotations

import json
import os
import re

# device keyword -> library device token (order: most specific first)
# each entry: (list of regex keywords, token, note)
_RULES = [
    (r"\bXFMR|TRANSFORMER\b|\bTX-[A-Z0-9]|\bTX\d", "XFMR", "transformer (1PH/3PH resolved by phase); TX-x tag"),
    (r"\bVFD|\d+VFD|VARIABLE FREQUENCY|\bRVSS\b|SOFT ?START", "VFD", "VFD / soft-start / RVSS drive (VFD_L/_R block, now in library)"),
    (r"\bMOTOR STARTER|MTRSTRT|\bFVNR\b|STARTER\b", "MTRStrt", "across-line / FVNR motor starter"),
    (r"\bUGPS\b|PULL SECTION|PULL ?BOX|PULLBOX|\bPB[X]?-", "PullBox", "pull section / pull box"),
    (r"CONTROL PANEL|CONTROL CABINET|\bPCP\b|\bLCP\b|\bMCP\b|\bRCP\b", "TB_Square", "a named control panel/cabinet is a terminal landing (TB), NOT a motor — even when 'PUMP' is in the name (e.g. PUMP CONTROL PANEL)"),
    (r"\bMOTOR|\bMTR\b|\bMTR-|\bPUMP\b|\bPMP\b|\bPMP-", "MTR", "motor / pump motor (1PH/3PH by phase)"),
    (r"\bFLOW ?METER|FLOWMETER|MAG(NETIC)? ?FLOW|\bFE-|\bFE \d|\bFM-", "Inst", "flow meter / element"),
    (r"\bLEVEL (TRANSMITTER|ELEMENT|SENSOR)|\bLE-|\bLE \d", "Inst_Sensor", "level element/sensor"),
    (r"FUSED DISCONNECT|DISC.?FUSE", "DISC_Fuse", "fused disconnect"),
    (r"\bDISCONNECT\b|\bDISC\b|\bDISC-", "DISC", "disconnect"),
    (r"CIRCUIT BREAKER|\bBREAKER\b|\bCB-|\bCB\b|\bMCB\b", "CB", "circuit breaker"),
    (r"\bFUSE\b|\bFU-", "Fuse", "fuse"),
    (r"\bGROUND\b|\bGND\b|\bEGC\b", "GND", "ground"),
    (r"PRESSURE SWITCH|\bPS[HL]L?\b|\bPS-", "PressureSwitch_NO", "pressure switch (PSH/PSL/PSHL)"),
    (r"FLOW SWITCH|\bFSL\b|\bFSH\b|\bFS-", "FlowSwitch_NO", "flow switch"),
    (r"LEVEL SWITCH|\bLSL\b|\bLSH\b|\bLS-", "LevelSwitch_NO", "level switch"),
    (r"TEMP(ERATURE)? SWITCH|\bTSL\b|\bTSH\b|\bTS-", "TemperatureSwitch_NO", "temperature switch"),
    (r"LIMIT SWITCH|\bZS-|\bZSC\b|\bZSO\b", "LimitSwitch_NO", "limit switch"),
    (r"\bHOA\b|HAND.?OFF.?AUTO|SELECTOR SWITCH", "HOASwitch_NO", "HOA/selector switch"),
    (r"PUSH ?BUTTON|\bPB-|\bPBL\b", "PushButton_NO", "pushbutton"),
    (r"\bVALVE\b|\bMOV-|\bSOV-|\bFCV-|\bSV-", "Valve", "valve/solenoid"),
    (r"RECEPTACLE|RECEP|\bGFI\b|\bGFCI\b", "RECP_Duplex", "receptacle"),
    (r"LIGHT|\bHORN\b|BEACON|STROBE|ALARM HORN", "LightHorn", "light/horn"),
    (r"\bHEATER\b|\bHTR\b", "HTR", "heater"),
    (r"\bMTP\b|MULTI.?FIBER|MTP-", "MTP", "MTP fiber"),
    (r"\bLC\b(?!P)", "LC", "LC fiber"),
    (r"\bSC\b", "SC", "SC fiber"),
    (r"\bST\b", "ST", "ST fiber"),
    (r"ETHERNET|\bRJ45\b|CAT ?[56]|NETWORK SWITCH", "RJ45", "ethernet/RJ45"),
    (r"\bCOAX\b|COAXIAL", "COAX", "coax"),
    (r"ANTENNA|\bANT\b|\bANT-", "ANT", "antenna"),
    (r"\bCONTACT\b|\bRELAY\b|\bCR-|\bICR\b", "Contact_NO", "relay contact"),
    (r"PULL ?BOX|JUNCTION BOX|\bJB\b|\bJB-", "PullBox", "pull/junction box"),
    (r"PULL ?ROPE|PULLROPE", "Pullrope", "pull rope"),
    (r"\bSPARE\b", "Spare", "spare"),
    (r"ANALYZER|TRANSMITTER|SENSOR|\bAIT\b|\bAE\b|\bAE-|\bAIT-", "Inst_Sensor", "analyzer/sensor (2/4-wire)"),
    (r"\bFIT\b|\bPIT\b|\bLIT\b|\bTIT\b|\bFT-|\bPT-|\bLT-|\bTT-|INSTRUMENT", "Inst", "instrument (2/4-wire)"),
    # panels / terminal blocks — the common landing point, kept last as default
    (r"TERMINAL BLOCK|\bTB-|\bTB\b|PANEL ?BOARD|\bLCP\b|\bMCC\b|\bPLC\b|\bPDP\b|\bPBD\b|\bCP-|\bRIO\b|\bLP-|CABINET", "TB_Square", "terminal block / panel"),
]

_DEFAULT_TOKEN = "TB_Square"   # most connections land on a terminal block

# user-taught keyword rules (from the Remembered Logic store), checked FIRST
_USER_RULES = []   # list of (keyword_regex_or_text, token)

# Landing-SIGNAL labels that were harvested into the learned store but are NOT device
# identifiers — they name a wire's function (a signal that lands on a terminal block), so
# using them for DEVICE→symbol inference on a conduit's From/To equipment name HIJACKS the
# real device (e.g. 'OPEN' would turn 'MAIN DISCONNECT (OPEN)' into a terminal block). They
# are skipped for device inference; the terminal-block symbol they map to is still applied at
# the LANDING level by idp_edc_symbols, which is where they belong.
_LANDING_SIGNAL = {
    "OPEN", "CLOSE", "OPEN POSITION", "CLOSED POSITION", "OPEN SIGNAL", "CLOSED SIGNAL",
    "CLSOED SIGNAL", "OPEN STATUS", "CLOSED STATUS", "CALL TO OPEN", "CALL TO CLOSE",
    "CALL TO STOP", "COMMON", "NEUTRAL", "REMOTE READY", "L/R IN REMOTE", "LANDING LUG",
    "VALVE OPEN", "VALVE CLOSED", "PLC (DI)", "PLC (DO)",
}


def register_keyword(keyword, token):
    """Add a user-taught device keyword → library token rule (checked before the
    built-in rules). Called by logic_store.apply()."""
    kw = str(keyword).strip()
    tok = str(token).strip()
    if kw and tok and (kw, tok) not in _USER_RULES:
        _USER_RULES.insert(0, (kw, tok))


def _cascade_path():
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    mp = getattr(sys, "_MEIPASS", None)   # PyInstaller bundle root (the exe)
    cands = [
        os.path.join(here, "..", "IDP_Builder", "resources", "idp_rules.json"),
        os.path.join(here, "idp_rules.json"),
    ]
    if mp:
        cands += [os.path.join(mp, "idp_rules.json"),
                  os.path.join(mp, "IDP_Builder", "resources", "idp_rules.json")]
    for p in cands:
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def load_cascade():
    p = _cascade_path()
    if not p:
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f).get("symbol_cascade", {})


def recognize_device(text, wire_ct=None):
    """Return the device token recognized from a free-text name/remark (or None).
    wire_ct disambiguates motor/transformer phase: a 3+-conductor POWER feeder is
    3-phase even when the name doesn't spell it out (e.g. 'PMP-P-01')."""
    if not text:
        return None
    u = str(text).upper()
    # user-taught rules win over built-ins — but with three guards:
    #  (a) SKIP landing-signal labels (harvested but not device names — they hijack devices);
    #  (b) match at a TOKEN boundary on BOTH ends so a short tag can't match inside a bigger
    #      one — leading `(?<![A-Z0-9])` (so 'OL-' can't match inside 'CONTROL-') and a
    #      class-aware trailing guard (so 'LT-1' can't match 'LT-10', 'CLOSE' can't match
    #      'CLOSED') while a prefix tag ending in '-'/')' still matches ('OL-' -> 'OL-1');
    #  (c) try the LONGEST keyword first, so a specific rule ('MOTOR STARTER') wins over a
    #      generic one ('MOTOR') regardless of store order.
    for kw, tok in sorted(_USER_RULES, key=lambda kt: -len(str(kt[0]))):
        ku = kw.upper().strip()
        if not ku or ku in _LANDING_SIGNAL:
            continue
        last = ku[-1]
        if last.isdigit():
            trail = r'(?!\d)'        # numeric tag: don't extend into a longer number
        elif last.isalpha():
            trail = r'(?![A-Z])'     # word tag: don't match inside a longer word
        else:
            trail = ''               # prefix tag (ends in '-', ')', …): match as a prefix
        if re.search(r'(?<![A-Z0-9])' + re.escape(ku) + trail, u):
            return tok
    for pat, token, _ in _RULES:
        if re.search(pat, u):
            # resolve phase for motors/transformers — by name, else by wire count
            if token in ("XFMR", "MTR"):
                three = bool(re.search(r"3\s*PH|3-PH|3PH|480|3\s*PHASE", u)) \
                    or ("3" in u) or (wire_ct is not None and wire_ct >= 3)
                return f"{token}_3PH" if three else f"{token}_1PH"
            if token in ("Inst", "Inst_Sensor"):
                return f"{token}_4W" if re.search(r"4\s*W|4-W|4 WIRE|4W", u) else f"{token}_2W"
            return token
    return None


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def infer_symbol(text, wire_type, wire_ct, side, cascade=None):
    """Infer the best library symbol block for a device.

    text      : the From/To equipment name or remark
    wire_type : FillIndex Type (POWER/CONTROL/TSP/MFG_CABLE/FIBER/CAT-6/PULL_ROPE)
    wire_ct   : Wire Ct (1..8)
    side      : 'L' (source) or 'R' (destination)
    returns   : dict(symbol, token, confidence, matched)
    """
    if cascade is None:
        cascade = load_cascade()
    tok = _norm(wire_type or "")
    ct = wire_ct if wire_ct in (1, 2, 3, 4, 6, 8) else 1
    key = f"{tok}_{ct}_{side}"
    candidates = cascade.get(key) or cascade.get(f"{tok}_1_{side}") \
        or cascade.get(f"POWER_1_{side}") or []
    if not candidates:
        return {"symbol": "", "token": None, "confidence": 0.0, "matched": ""}

    device = recognize_device(text, wire_ct=ct)
    if not device:
        device = _DEFAULT_TOKEN
        conf_base = 0.35
    else:
        conf_base = 0.85

    dnorm = _norm(device)
    # score each candidate by how well its (side-stripped) name matches the device
    # token; tie-break toward the SIMPLEST (shortest) matching block.
    best, best_key = None, (-1, 0)
    for cand in candidates:
        base = _norm(re.sub(r"_(L|R)$", "", cand))
        if dnorm and dnorm in base:
            score = len(dnorm) + (5 if base == dnorm else 0)
        else:
            score = 0
        key = (score, -len(base))   # higher score, then shorter name
        if key > best_key:
            best, best_key = cand, key
    best_score = best_key[0]

    if best_score <= 0:
        # no device match — fall back to a sensible generic for the slot, preferring
        # a terminal block, then a fiber/network patch; never antenna/coax.
        for pref in ("TB_SQUARE", "TB", "MTP", "LC", "RJ45", "SPARE", "GND"):
            m = next((c for c in candidates
                      if pref in _norm(re.sub(r"_(L|R)$", "", c))), None)
            if m:
                return {"symbol": m, "token": device, "confidence": 0.3, "matched": "fallback"}
        return {"symbol": candidates[0], "token": device, "confidence": 0.25, "matched": "fallback"}

    conf = min(0.95, conf_base + 0.02 * best_score) if device != _DEFAULT_TOKEN else 0.4
    return {"symbol": best, "token": device, "confidence": round(conf, 2), "matched": device}


if __name__ == "__main__":
    import sys
    casc = load_cascade()
    tests = [
        ("SMUD TRANSFORMER 480V 3PH", "POWER", 3, "R"),
        ("MAIN BREAKER CB-100", "POWER", 3, "R"),
        ("PRESSURE SWITCH PS-300", "CONTROL", 2, "L"),
        ("GROUND", "POWER", 1, "L"),
        ("FIBER MTP-100", "FIBER", 1, "R"),
        ("239-FCS-LCP-400 CHEMICAL BUILDING", "CONTROL", 2, "L"),
        ("RAW WATER pH ANALYZER AIT-003", "TSP", 2, "R"),
    ]
    if len(sys.argv) > 1:
        r = infer_symbol(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "CONTROL",
                         int(sys.argv[3]) if len(sys.argv) > 3 else 2,
                         sys.argv[4] if len(sys.argv) > 4 else "L", casc)
        print(r)
    else:
        for t in tests:
            print(t, "->", infer_symbol(*t, casc))
