"""
idp_anatomy.py — apply finished-IDP drawing conventions to extractor output.

Logic reverse-engineered from finished IDP drawings (Handoff/idp_anatomy.json +
finished-idp-anatomy.md). Two jobs:
  apply_conventions()  — fill in wire COLORS and GAUGE by convention when the
                         source is silent (power phases BRN/ORG/YEL, control RED,
                         TSP pair RED/BLK, ...), using only values legal in the
                         workbook's Color dropdown; each applied value is noted so
                         idp_write flags the cell amber (never silent).
  check_archetypes()   — flag fills that won't render to a valid IDP archetype
                         (e.g. a 3-phase POWER group with non-phase colors).

Wired into idp_write.write_workbook(..., anatomy=True) so it runs on every write.
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_ANAT = os.path.join(_HERE, "Handoff", "idp_anatomy.json")
_SYM = os.path.join(_HERE, "Handoff", "lisa_symbols.json")

try:
    ANATOMY = json.load(open(_ANAT, encoding="utf-8"))
except Exception:
    ANATOMY = {}

try:
    # the FillIndex Color columns use abbreviated codes (BRN/ORG/YEL/RED/BLK...)
    _COLORS_OK = set(json.load(open(_SYM, encoding="utf-8"))["colors"]["Color_1"])
except Exception:
    _COLORS_OK = set()

# abbreviated Color-dropdown codes (NOT the full WireColor names)
GND_COLOR = "GRN"
TSP_COLOR = "RED/BLK"
POWER_PHASES = ["BRN", "ORG", "YEL", "WHT"]   # ØA, ØB, ØC, (neutral)
# CONTROL color is NOT universal (BLU in some projects, RED in others) — learned
# per project at runtime; see _project_control_color().


def _valid(color) -> bool:
    return (not _COLORS_OK) or (color in _COLORS_OK)


def _key(typ) -> str:
    return str(typ or "").replace("-", "").upper()


_CIRCUIT_TYPES = {"POWER", "CONTROL", "TSP", "MFG_CABLE"}   # carry a ground
_NO_GROUND_TYPES = {"PULL_ROPE", "FIBER", "CAT6"}           # never grounded
_ANALOG_RE = re.compile(
    r"\b(FLOW|PRESSURE|LEVEL|TEMPERATURE|ANALY[ZS]ER|TRANSMITTER|"
    r"LIT|FIT|PIT|TIT|AIT|CIT|LT|FT|PT|TT|AT|LE|FE|PE|TE|AE)\b", re.I)
# A field SWITCH (ISA 2nd-letter 'S': PSH/PSL/LSH/FSH/TSH/ZS…, or the word
# SWITCH) is a DISCRETE dry contact — CONTROL wiring, NOT an analog TSP — even
# though its name contains PRESSURE/LEVEL/FLOW. Learned from Lennar C061
# (DISCHARGE PRESSURE SWITCH PSH-061 = 2× CONTROL, not a TSP pair).
_SWITCH_RE = re.compile(r"\b(SWITCH|[PLFTAZ]S[HL]?\d*)\b", re.I)


# ── electrical-direction orientation ─────────────────────────────────────────
# Source = the more UPSTREAM end (where power/signal originates); Destination =
# the more DOWNSTREAM end (the load / field device). Ranked by the standard
# power-distribution hierarchy so an IDP reads with the direction of energy flow.
def _is_main_feeder(fills):
    """A main power feeder uses large service conductors (MCM / #n/0)."""
    for g in fills or []:
        if _key(g.get("type")) != "POWER":
            continue
        u = str(g.get("gauge") or "").upper()
        if "MCM" in u or "/0" in u or "/O" in u:
            return True
    return False


def _elec_rank(name, main_feeder):
    """Lower rank = more upstream (closer to the source of energy)."""
    u = str(name or "").upper()
    def has(*ks): return any(k in u for k in ks)
    if has("T.I.D", "UTILITY", "POCO", "SERVICE POLE", "POWER CO"): return 5
    # a generator connected to an ATS is drawn by AIC as the DESTINATION even on
    # its main feeder — the finished IDP keeps the schedule's FROM/TO (ATS→EG1),
    # NOT the electrical direction (73.1188 H2203: SOURCE=ATS1, DEST=EG1). So a
    # generator ranks as a load on every conduit; the schedule order is preserved.
    if has("ENGINE GEN", "GENERATOR") or re.search(r"\bEG\d*\b", u):
        return 72
    if has("METER", "PEDESTAL", "SERVICE ENTRANCE", "PDB-", "METER/MAIN"): return 12
    if has("UGPS", "PULL SECTION"): return 15
    if has("MSB", "SWITCHBOARD"): return 20
    if has("ATS", "TRANSFER SWITCH"): return 25
    if has("MCC", "SWITCHGEAR", "MOTOR CONTROL CENTER"): return 30
    if has("PLC", "RTU", "SCADA", "CONTROLLER"): return 33   # control source
    if has("XFM", "TRANSFORMER", "TX-"): return 35           # between MCC & panel
    if has("PANEL", "LP-", "PANELBOARD"): return 40
    if has("VFD", "RVSS", "STARTER"): return 45
    if has("DISC"): return 50
    if has("VAULT", "PULL BOX", "PBX", "J BOX", "JBOX", "J-BOX"): return 55
    if has("MOTOR", "PUMP", "PMP", "HEATER", "LIGHT", "RECEP", "MIXER",
           "COMPRESSOR", "TANKLESS", "FAN"): return 70
    if has("PSHL", "PSH", "PSL", "TRANSMITTER", "SWITCH", "SENSOR", "FE-", "FE ",
           "LE-", "ZS-", "FS-", "LIT", "FIT", "METER", "ANTENNA", "EYE WASH",
           "ENCLOSURE"): return 75
    return 60


def orient_by_electrical_direction(records):
    """Ensure each conduit's Source is upstream and Destination downstream by the
    power/signal-flow hierarchy. Flips the pair when the schedule has them
    backwards (e.g. a generator main feeder listed ATS->EG becomes EG->ATS) and
    flags the change. Runs BEFORE symbol attachment so S/D symbols land on the
    correct side. Returns the list of conduit names that were reoriented."""
    flipped = []
    for rec in records or []:
        src = " ".join(str(x) for x in (rec.get("source") or []))
        dst = " ".join(str(x) for x in (rec.get("dest") or []))
        if not src or not dst:
            continue
        mf = _is_main_feeder(rec.get("fill"))
        if _elec_rank(src, mf) > _elec_rank(dst, mf):
            rec["source"], rec["dest"] = rec.get("dest"), rec.get("source")
            for g in rec.get("fill", []):   # keep symbols on their real side
                g["s_symbol"], g["d_symbol"] = g.get("d_symbol"), g.get("s_symbol")
            rec.setdefault("flags", []).append("reoriented_electrical_direction")
            flipped.append(rec.get("name"))
    return flipped


def refine_source_symbols(records):
    """Give POWER-conduit SOURCE symbols more device-specificity using BOTH ends
    (symbol_infer sees only one name at a time, so it can't). Grounded in what the
    EDC three-lines actually show: an MCC/VFD bucket feeding a motor is a motor
    STARTER; a switchboard main is breakers; a pull section is a pull box. Only
    upgrades a GENERIC terminal-block/breaker default — never overrides a real
    recognized device (XFMR/MTR/DISC/…). Returns count refined."""
    try:
        import symbol_infer
        casc = symbol_infer.load_cascade()
    except Exception:
        return 0
    n = 0
    for rec in records or []:
        src = " ".join(rec.get("source") or []).upper()
        dst = " ".join(rec.get("dest") or []).upper()
        for g in rec.get("fill") or []:
            if _key(g.get("type")) != "POWER":
                continue
            ct = g.get("wire_ct") or g.get("count") or 1
            cur = str(g.get("s_symbol") or "").upper()
            if not re.search(r"TB_SQUARE|TB_ROUND|^CB-|-CB-|_CB", cur) and cur:
                continue                       # already a real device — leave it
            tok = None
            if re.search(r"\bXFM|TRANSFORMER|\bTX-[A-Z0-9]", src):
                tok = "XFMR_3PH"              # fed from a transformer secondary
            elif re.search(r"\bVFD|\d+VFD|\bRVSS|VARIABLE FREQUENCY|SOFT ?START", src) \
                    and re.search(r"MOTOR|PUMP|\bPMP\b|\bP-\d", dst):
                tok = "VFD"                   # VFD/RVSS drive feeding a motor
            elif re.search(r"\bMCC|BUCKET|\bSEC\.?\b|STARTER|\bFVNR", src) \
                    and re.search(r"MOTOR|PUMP|\bPMP\b|\bP-\d", dst):
                tok = "MTRStrt"                # across-line bucket feeding a motor
            elif re.search(r"MSB\b|SWITCHBOARD|SWITCHGEAR", src):
                # trailing boundary only — OCR sometimes drops the space (1000AMSB)
                tok = "CB-CB-CB" if ct >= 3 else "CB"
            elif re.search(r"\bUGPS\b|PULL SECTION|PULL ?BOX|PULLBOX", src):
                tok = "PullBox"
            if not tok:
                continue
            # ONLY blocks legal for this conduit's EXACT Wire Ct — a block from a
            # different slot would fail the LISA contract.
            slot = casc.get(f"POWER_{ct if ct in (1, 2, 3, 4, 6, 8) else 1}_L") or []
            block = next((c for c in slot
                          if re.sub(r"_(L|R)$", "", c).upper() == tok.upper()), None) \
                or next((c for c in slot
                         if re.sub(r"_(L|R)$", "", c).upper().startswith(tok.upper())), None)
            if block and block.upper() != cur:
                g["s_symbol"] = block
                g["s_symbol_conf"] = 0.75
                n += 1
    return n


def _has_ground(fills):
    for g in fills:
        if "GRN" in [str(c).upper() for c in (g.get("colors") or [])]:
            return True
        for k in ("s_symbol", "d_symbol"):
            nm = str(g.get(k) or "").upper()
            # A "no ground" motor block (MTR_3PH_NoGND_R) contains the substring
            # "GND" inside "NoGND" — strip that FIRST so it isn't read as a ground
            # already present (which wrongly suppressed the ground on motor feeders).
            nm = re.sub(r"NO[_ ]?GND", "", nm)
            if re.search(r"(^|[_-])GND(_|-|$)", nm) or "GROUND" in nm:
                return True
    return False


_UTILITY_RE = re.compile(r"\bT\.?I\.?D\.?\b|\bSMUD\b|\bPG&?E\b|\bPGE\b|TURLOCK|\bUTILITY\b", re.I)
_SERVICE_DEST_RE = re.compile(r"\bUGPS\b|MAIN SWITCH ?BOARD|\bMSB\b|SWITCH ?BOARD|SERVICE|\bMETER\b|PULL SECTION", re.I)


def _is_utility_service(rec):
    """A utility service lateral — a utility transformer (T.I.D / SMUD / PG&E …)
    feeding the service point (UGPS / MSB / switchboard / meter) — carries NO
    separate equipment ground in the raceway (grounding is at the service via the
    grounding-electrode conductor). The finished IDPs draw these as phase conductors
    only (e.g. H2201: T.I.D XFMER → MSB1, 3×500 MCM, no ground). Do not synthesize one."""
    src = " ".join(str(x) for x in (rec.get("source") or []))
    dst = " ".join(str(x) for x in (rec.get("dest") or []))
    src_is_xfmr = bool(re.search(r"\bXFME?R\b|TRANSFORMER", src, re.I))
    return bool(_UTILITY_RE.search(src)) and (src_is_xfmr or bool(_SERVICE_DEST_RE.search(dst)))


def ensure_ground(records):
    """Add a GRN ground conductor to any real-circuit conduit that lacks one —
    learned from the Stratford1 diff (30/42 finished conduits carry a separate
    ground our output omitted). Represented the only LISA-legal way to carry the
    GND symbol: a POWER / Wire-Ct-1 / GND_L·GND_R / GRN fill (the `GROUND` type
    the older drawings used is not in the current template's dropdown). Skips
    conduits already grounded and pull-rope/fiber/cat-6/empty conduits. Flags
    each added ground. Returns count added."""
    added = 0
    for rec in records or []:
        fills = rec.get("fill") or []
        if not fills:
            continue
        # A record parsed from a FINISHED IDP is authoritative about its own fill
        # — it already shows grounds exactly where AIC drew them (and omits them
        # where none exist, e.g. a TSP-only signal conduit or a utility service).
        # Never synthesize a ground on top of that.
        if rec.get("fill_authoritative"):
            continue
        # A conduit schedule that lists ground sizes for some conduits is
        # authoritative about grounds everywhere: absence of a ground on a given
        # conduit (e.g. a utility service) means none exists — do not synthesize.
        if rec.get("ground_authoritative"):
            continue
        types = {_key(g.get("type")) for g in fills}
        if not (types & _CIRCUIT_TYPES):        # only real circuits get a ground
            continue
        if types <= _NO_GROUND_TYPES:
            continue
        if _has_ground(fills):
            continue
        if _is_utility_service(rec):          # service lateral has no separate EGC
            rec.setdefault("flags", []).append("utility_service_no_ground")
            continue
        fills.append({"type": "POWER", "count": 1, "wire_ct": 1, "gauge": "#14",
                      "colors": ["GRN"], "s_symbol": "GND_L", "d_symbol": "GND_R",
                      "s_symbol_conf": 0.9, "d_symbol_conf": 0.9, "slots": 1,
                      "auto_ground": True,
                      "ground_note": "ground conductor added by convention (every finished "
                                     "IDP shows one); POWER/GND is the current-template way "
                                     "to carry the GND symbol — verify vs the drawing."})
        rec["fill"] = fills
        added += 1
    return added


def merge_analog_pairs(records):
    """An analog transmitter signal drawn as two discrete CONTROL wires → one
    TSP pair (RED/BLK) — learned from the Stratford1 diff (C004/C005/C008/C009/
    C017). Conservative trigger: source/dest names an analog instrument AND the
    conduit has exactly two CONTROL fills. Flags each. Returns count merged."""
    merged = 0
    for rec in records or []:
        if rec.get("fill_authoritative"):   # finished-IDP fill is ground truth
            continue
        names = " ".join(list(rec.get("source", [])) + list(rec.get("dest", [])))
        if not _ANALOG_RE.search(names):
            continue
        if _SWITCH_RE.search(names):        # a discrete switch is CONTROL, not TSP
            continue
        fills = rec.get("fill") or []
        ctrl = [g for g in fills if _key(g.get("type")) == "CONTROL"]
        if len(ctrl) != 2:
            continue
        keep = [g for g in fills if g not in ctrl]
        tsp = {"type": "TSP", "count": 1, "wire_ct": 1, "gauge": "#18",
               "colors": ["RED/BLK"], "s_symbol": "TB_Square_L", "d_symbol": "TB_Square_R",
               "s_symbol_conf": 0.5, "d_symbol_conf": 0.5, "slots": 1, "auto_tsp": True,
               "type_note": "two CONTROL wires merged to one TSP pair — analog instrument "
                            "signal (RED/BLK); verify vs the drawing."}
        rec["fill"] = [tsp] + keep
        merged += 1
    return merged


def _project_control_color(records):
    """Learn this project's CONTROL color from its own data (control varies by
    project: BLU in some, RED in others). Records it to the KB and returns the
    dominant control color, or None if the project's data never states one."""
    from collections import Counter
    c = Counter()
    for rec in records or []:
        for g in rec.get("fill", []) or []:
            if _key(g.get("type")) == "CONTROL":
                for col in (g.get("colors") or []):
                    if col:
                        c[col] += 1
    color = c.most_common(1)[0][0] if c else None
    if color:
        try:
            from mapping_table import KnowledgeBase
            KnowledgeBase().learn_value("control_color", "project", color)
        except Exception:
            pass
    return color


def _colors_for(typ, count, control_color=None):
    """Convention colors for a fill group, as Color-dropdown codes."""
    k = _key(typ)
    if k == "POWER":
        return POWER_PHASES[:count] if 1 <= count <= 4 else POWER_PHASES[:3]
    if k == "CONTROL":
        # only apply a control color if THIS project established one; never a
        # hard universal default.
        return [control_color] * max(int(count or 1), 1) if control_color else []
    if k == "TSP":
        return [TSP_COLOR]
    # No-conductor / single-cable types carry no wire color. Emit the legal
    # 'N/A' code explicitly — learned from the Stratford1 training diff, where
    # leaving it blank made LISA stamp 'XXX' (P001, C020A/B) or a stray mislabel
    # like 'ETHERNET' landed in the color cell (C010-C013).
    if k in ("PULL_ROPE", "FIBER", "CAT6"):
        return ["N/A"]
    return []   # MFG_CABLE: take colors from the source only


POWER_1PH = ["BLK", "WHT", "GRN"]   # L / N / G — single-phase branch colors


def _is_single_phase_branch(rec, grp):
    """Convention #6 (panelboard voltage → 1Ø vs 3Ø), approximated from the ground
    pattern the extractor already has: a POWER conductor group that carries its ground
    INTEGRALLY — a 2–3-wire branch with NO separate green EGC group in the conduit, at
    a branch (non-feeder) gauge — is single-phase (L/N or L/N/G), coloured BLK/WHT/GRN.
    A 3-phase feeder instead carries a SEPARATE 'W/GND' EGC group and stays ØA/ØB/ØC.
    The panelboard schedule / vision confirms the exact voltage + circuit number."""
    if _key(grp.get("type")) != "POWER":
        return False
    if grp.get("is_ground") or grp.get("auto_ground"):
        return False
    ct = int(grp.get("wire_ct") or grp.get("count") or 1)
    if ct not in (2, 3):
        return False
    # a SEPARATE ground group in the same conduit ⇒ this POWER group is polyphase
    for g in rec.get("fill", []):
        if g is grp:
            continue
        if g.get("is_ground") or g.get("auto_ground") or \
                str(g.get("s_symbol") or "").upper().startswith("GND"):
            return False
    gg = str(grp.get("gauge") or "").upper()          # feeder gauges are polyphase
    if "MCM" in gg or re.search(r"\b[1-4]/0\b", gg):
        return False
    return True


def _gauge_for(typ):
    gd = ANATOMY.get("gauge_defaults", {})
    return {"CONTROL": gd.get("control"), "TSP": gd.get("signal_tsp")}.get(_key(typ))


def apply_conventions(records, fill_colors=True, fill_gauge=True) -> list:
    """Fill missing colors/gauge by finished-IDP convention (in place).
    Only fills when the source left them blank; flags each via *_note keys."""
    applied = []
    ctrl_color = _project_control_color(records)   # learn per project, once
    fidx = 0
    for rec in records or []:
        for grp in rec.get("fill", []) or []:
            fidx += 1
            typ = grp.get("type")
            count = grp.get("wire_ct") or grp.get("count") or 1
            # kcmil sizes (>=250, e.g. 350/500/750) are a DIFFERENT system than AWG —
            # render as "NNN MCM" so LISA never tacks 'AWG' onto them (#350 -> "350 MCM").
            _g = str(grp.get("gauge") or "").strip().lstrip("#")
            if re.fullmatch(r"[0-9]+", _g) and int(_g) >= 250:
                grp["gauge"] = _g + " MCM"
            existing = [c for c in (grp.get("colors") or []) if c]
            # No-conductor cable types have no wire color: force the legal 'N/A'
            # code even if a bogus non-color (e.g. 'ETHERNET', or a source
            # descriptor) leaked into the color cell. Learned from Stratford1.
            if fill_colors and _key(typ) in ("PULL_ROPE", "FIBER", "CAT6"):
                if existing != ["N/A"]:
                    grp["colors"] = ["N/A"]
                    grp["color_note"] = (f"{typ} carries no wire color → N/A "
                                         f"(was {existing or 'blank'}); verify.")
                    applied.append((fidx, rec.get("name"), typ, "colors=N/A"))
                # gauge still handled below; skip the general color logic
                existing = ["N/A"]
            # A POWER group whose ONLY color is a folded-in ground (GRN, added by
            # lisa_contract.normalize_types) still needs its phase colors — the
            # bare "already colored" test would skip it and leave a 3-phase run
            # colored only GRN (and then trip check_archetypes). Re-color it and
            # keep the ground.
            ground_only_power = (_key(typ) == "POWER" and grp.get("ground_folded")
                                 and existing and set(existing) <= {"GRN"})
            # single-phase branch (integral ground) → L/N/G colours, not 3-phase
            single_phase = _is_single_phase_branch(rec, grp)
            if single_phase:
                grp["single_phase"] = True
            if fill_colors and (not existing or ground_only_power or single_phase):
                if single_phase:
                    cand = POWER_1PH[:count]
                else:
                    cand = _colors_for(typ, count, ctrl_color)
                    if ground_only_power and "GRN" not in cand:
                        cand = cand + ["GRN"]
                cand = [c for c in cand if _valid(c)]
                if cand:
                    grp["colors"] = cand
                    grp["color_note"] = (
                        ("Single-phase branch (no separate EGC) → L/N/G " if single_phase
                         else "Colors assigned by finished-IDP convention ")
                        + f"({typ} -> {', '.join(cand)}); verify vs the drawing.")
                    applied.append((fidx, rec.get("name"), typ, "colors=" + ",".join(cand)))
            if fill_gauge and not str(grp.get("gauge") or "").strip():
                g = _gauge_for(typ)
                if g:
                    grp["gauge"] = g
                    grp["gauge_note"] = (
                        f"Wire Gauge defaulted to {g} by convention for {typ}; verify.")
                    applied.append((fidx, rec.get("name"), typ, "gauge=" + g))
    return applied


_L_END = re.compile(r"_L(\s*\(.*\))?$", re.I)
_R_END = re.compile(r"_R(\s*\(.*\))?$", re.I)
_POWER_LIKE = {"POWER", "CONTROL", "TSP", "MFG_CABLE"}   # types a finished sheet always grounds


def check_archetypes(records) -> list:
    """Flag fills that won't render to a valid, finished-looking IDP sheet.

    Beyond the LISA dropdown contract (lisa_contract handles that), this checks
    the drawing-fidelity conventions seen on real finished sheets:
      - 3-phase POWER should use the phase colors (BRN/ORG/YEL)
      - TSP Wire Ct must be a legal domain value
      - S Symbol / D Symbol must actually be an L-side / R-side block (a flipped
        side silently produces a wrong-looking or ungenerated wire)
      - a conduit that carries real (non-spare) wire should show a separate
        ground the way every finished sheet does — missing ground is a common
        "looks incomplete vs the real drawing" gap
      - every conduit should have at least one FillIndex row, or its sheet will
        render essentially blank
    """
    issues = []
    fidx = 0
    for rec in records or []:
        fill = rec.get("fill", []) or []
        if not fill:
            issues.append({"row": fidx, "conduit": rec.get("name"),
                           "note": "conduit has no FillIndex rows — its IDP sheet "
                                   "would render with no wires"})
        has_ground = False
        has_real_wire = False
        for grp in fill:
            fidx += 1
            k = _key(grp.get("type"))
            count = grp.get("wire_ct") or grp.get("count") or 1
            colors = [c for c in (grp.get("colors") or []) if c]
            if "GRN" in colors:
                has_ground = True
            if k in _POWER_LIKE:
                has_real_wire = True
            if k == "POWER" and count == 3 and colors and not any(
                    c in POWER_PHASES for c in colors):
                issues.append({"row": fidx, "conduit": rec.get("name"),
                               "note": f"3-phase POWER with non-phase colors {colors} "
                                       f"(expected BRN/ORG/YEL)"})
            # TSP may be multi-pair (Ct 2, 3 seen in known-good sets) — only flag
            # counts outside the legal Wire-Ct domain.
            if k == "TSP" and count not in (1, 2, 3, 4, 6, 8):
                issues.append({"row": fidx, "conduit": rec.get("name"),
                               "note": f"TSP Wire Ct {count} not a legal Wire Ct"})
            ssym, dsym = grp.get("s_symbol"), grp.get("d_symbol")
            if ssym and not _L_END.search(ssym):
                issues.append({"row": fidx, "conduit": rec.get("name"),
                               "note": f"S Symbol {ssym!r} is not an _L (source-side) "
                                       f"block — check for a flipped side"})
            if dsym and not _R_END.search(dsym):
                issues.append({"row": fidx, "conduit": rec.get("name"),
                               "note": f"D Symbol {dsym!r} is not an _R (dest-side) "
                                       f"block — check for a flipped side"})
        if has_real_wire and not has_ground:
            issues.append({"row": fidx, "conduit": rec.get("name"),
                           "note": "no separate GRN ground wire found — every finished "
                                   "IDP sheet shows one; verify against the drawing"})
    return issues


if __name__ == "__main__":
    print("valid color codes:", sorted(_COLORS_OK) or "(none loaded)")
    demo = [{"name": "P100", "fill": [
        {"type": "POWER", "wire_ct": 3, "colors": [], "gauge": "#1"},
        {"type": "CONTROL", "wire_ct": 2, "colors": [], "gauge": ""},
        {"type": "TSP", "wire_ct": 1, "colors": [], "gauge": ""}]}]
    from pprint import pprint
    pprint(apply_conventions(demo))
    pprint([g for r in demo for g in r["fill"]])
