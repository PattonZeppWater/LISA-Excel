"""
idp_notes.py — understand the ENGINEER'S NOTES and context left on a conduit.

The conduit-schedule NOTES/COMMENTS column and drawing annotations carry real intent that
the coarse columns don't: the conduit MATERIAL ("route in PVC-GRS"), a GROUND callout
("provide #8 GND"), a SPARE / PULL-ROPE / EMPTY run, EXISTING vs NEW work, and one-off
field instructions. Those land on each record's `deviations` field but were previously just
stored, not acted on. This module reads them and extracts the actionable meaning.

DESIGN — strictly ADDITIVE, so it can never corrupt a conduit that was already read
correctly (and never changes the conduit COUNT):
  * conduit TYPE is filled from a material named in the note ONLY when the type is blank/XXX,
  * a SPARE/PULL-ROPE run seeds a pull-rope fill ONLY when the conduit has no fill yet,
  * GROUND callouts are FLAGGED for verification (never auto-added — over-grounding is worse
    than a flag),
  * EXISTING/NEW/DEMO context and any other actionable note are FLAGGED so the human sees
    what the engineer wrote.
What it understood is appended to the note as "[EXE READ: …]" and surfaced via amber flags.
Fully offline, deterministic.
"""
import re

# material keywords → canonical conduit type. ORDER = most-specific first (PVC-GRS before PVC,
# RIGID/GRS before bare PVC) so a combined callout maps to the right thing.
_MAT_RULES = [
    (r"PVC[\s-]*(GRS|COAT)", "PVC-GRS"),
    (r"\bRGS\b|\bGRS\b|\bGRC\b|\bRIGID\b", "RGS"),
    (r"\bRMC\b", "RMC"),
    (r"\bIMC\b", "IMC"),
    (r"\bEMT\b", "EMT"),
    (r"\bLFMC\b|SEAL[\s-]*TITE|LIQUID[\s-]*TIGHT", "LFMC"),
    (r"\bFMC\b|\bFLEX\b", "FMC"),
    (r"\bPVC\b", "PVC"),
]

_GND_RE = re.compile(
    r"#\s*\d+/?\d*\s*(AWG\s*)?(CU\s*)?(GND|GROUND|EGC)"       # "#8 GND", "#6 AWG GROUND"
    r"|\b(PROVIDE|W/|WITH|INSTALL|ADD)\b[^.]{0,30}\b(GROUND|GND|EGC|BOND)\b"
    r"|GROUND(ING)?\s+(ROD|ELECTRODE|CONDUCTOR|BUS|JUMPER)"
    r"|\bUFER\b", re.I)
_SPARE_RE = re.compile(
    r"\bSPARE\b|\bEMPTY\b|\bFUTURE\b|PULL\s*(ROPE|STRING|LINE|CORD|WIRE)|MULE\s*TAPE|MULETAPE", re.I)
_EXNEW_RE = re.compile(
    r"\(E\)|\bEXIST(ING|\.)?\b|\(N\)|\bNEW\b|\bDEMO(LISH|LITION)?\b|\bREMOVE\b|\bRELOCATE\b|\bABANDON", re.I)
# a note that asks for an action but wasn't otherwise decoded → surface it for a human read
_ACTION_RE = re.compile(
    r"\b(PROVIDE|INSTALL|ROUTE|COORDINATE|REFER|SEE\s+DETAIL|VERIFY|FIELD\s+VERIFY|PER\s+PLAN|"
    r"PER\s+DETAIL|NIC|BY\s+OTHERS|RE-?USE|MATCH\s+EXISTING)\b", re.I)


def _flag(flags, f):
    if f not in flags:
        flags.append(f)


def interpret_notes(records, log=None):
    """Read each conduit's engineer notes (record['deviations']) and apply the additive rules
    above. Returns the count of conduits whose notes were understood/acted on."""
    n = 0
    for rec in records or []:
        note = str(rec.get("deviations") or "").strip()
        if not note or note.upper() == "XXX":
            continue
        # don't re-read our own annotation on a second pass
        base = re.sub(r"\s*\[EXE READ:[^\]]*\]", "", note).strip()
        if not base:
            continue
        up = base.upper()
        flags = rec.setdefault("flags", [])
        anno, touched = [], False

        # 1) conduit TYPE from a material named in the note — ONLY if type is missing
        ct = str(rec.get("ctype") or "").strip().upper()
        if ct in ("", "XXX"):
            for pat, mat in _MAT_RULES:
                if re.search(pat, up):
                    rec["ctype"] = mat
                    _flag(flags, "type_from_note")
                    anno.append("type %s" % mat)
                    touched = True
                    break

        # 2) SPARE / EMPTY / PULL-ROPE — seed a pull-rope fill only when there's no fill yet
        if _SPARE_RE.search(up):
            _flag(flags, "note_spare_or_pullrope")
            if not (rec.get("fill") or []):
                rec["fill"] = [{"type": "PULL_ROPE", "count": 1, "wire_ct": 1,
                                "gauge": "", "colors": ["N/A"]}]
                anno.append("spare/pull-rope")
            touched = True

        # 3) GROUND called out — FLAG for verification (never auto-add: over-grounding risk)
        if _GND_RE.search(base):
            _flag(flags, "note_ground_called_out")
            anno.append("ground called out — verify EGC")
            touched = True

        # 4) EXISTING / NEW / DEMO context → flag (informational)
        if _EXNEW_RE.search(up):
            _flag(flags, "note_existing_new")
            touched = True

        # 5) any other actionable note we didn't decode → surface it for a human read
        if not anno and _ACTION_RE.search(up) and len(base) > 12:
            _flag(flags, "note_review")

        if anno:
            rec["deviations"] = (base + "  [EXE READ: " + "; ".join(anno) + "]").strip()
        if touched:
            n += 1

    if log and n:
        log("Notes: understood the engineer's notes on %d conduit(s) — filled missing types "
            "and flagged spares, grounds, and existing/new work from the comments." % n)
    return n
