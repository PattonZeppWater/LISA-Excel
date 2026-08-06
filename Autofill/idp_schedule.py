"""
idp_schedule.py — turn a manually-entered conduit-schedule grid into write-ready
records, INSIDE the exe (no Excel intermediary).

A vector conduit-schedule sheet has no text layer, so it cannot be read
automatically without OCR (which garbles CAD) or an API. The exe therefore
renders the page for the user to read and captures the rows in a grid; this
module converts those rows into the same record shape the writer already
consumes — identical to the Excel-bridge path, so the output is the same
0-violation workbook.

Grid row schema (one dict per conduit, matching the E-3 schedule columns):
    name, src, dst, size, ctype,
    cond_qty, cond_gauge, gnd, cable_qty, cable_type, notes
"""
import re
import idp_extract

_CABLE_ALIASES = {
    "CAT-6 SHLD": "CAT-6", "CAT6": "CAT-6", "CAT-6": "CAT-6",
    "PULLROPE": "PULL_ROPE", "PULL ROPE": "PULL_ROPE", "PULLROPES": "PULL_ROPE",
    "2C/16STP": "TSP", "STP": "TSP", "TSP": "TSP",
    "MFR CABLE": "MFG_CABLE", "MFG CABLE": "MFG_CABLE", "MFG_CABLE": "MFG_CABLE",
    "FIBER": "FIBER", "COAX": "COAX",
}


def _int(v):
    try:
        return int(float(str(v).strip())) if str(v or "").strip() else 0
    except ValueError:
        return 0


def _norm_cable(s):
    t = str(s or "").strip().upper()
    if not t:
        return "MFG_CABLE"
    return _CABLE_ALIASES.get(t, t.replace(" ", "_"))


def rows_to_records(rows):
    """Convert manually-entered schedule rows into write-ready records.

    Ground is represented the only LISA-legal way (POWER / wire-ct 1 /
    GND_L·GND_R / GRN) carrying the entered gauge — never a fabricated default.
    If any row lists a ground, the whole schedule is marked ground-authoritative
    so the writer will not synthesize grounds where the schedule shows none."""
    recs, has_ground = [], False
    for row in rows or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        frm = str(row.get("src") or "").strip()
        to = str(row.get("dst") or "").strip()
        fill = []

        # MC-E-8 schema (conduit schedule only — conductor specs live on the cable
        # schedule): seed a rough fill from the TYP column + the CABLES-IN-CONDUIT
        # count. Class from TYP (POWER/CONTROL/GROUND), one group, count = # cables.
        if row.get("typ") is not None or row.get("cables") is not None:
            typ = str(row.get("typ") or "").upper()
            cables = re.findall(r"C-?\d+[A-Z]?", str(row.get("cables") or ""), re.I)
            n = max(1, min(len(cables), 4))
            if "SPARE" in str(row.get("cables") or "").upper() and not cables:
                pass                                   # spare conduit → no fill
            else:
                kind = "POWER" if ("POWER" in typ or "PWR" in typ or "GROUND" in typ) else "CONTROL"
                fill.append({"type": kind, "count": n, "wire_ct": n, "gauge": "",
                             "colors": [], "slots": n})
            recs.append({
                "name": name, "source": [frm], "dest": [to],
                "size": str(row.get("size") or "").strip(),
                "ctype": idp_extract._norm_ctype(row.get("ctype") or "XXX"),
                "docs": [], "wires": [], "fill": fill,
                "deviations": str(row.get("notes") or "").strip(),
                "flags": ["from_ocr_schedule", "ocr_conduit_only_fill_from_cables"],
            })
            continue

        cq = _int(row.get("cond_qty"))
        if cq:
            kind = "POWER" if name[:1].upper() in "PHL" else "CONTROL"
            fill.append({"type": kind, "count": cq, "wire_ct": cq,
                         "gauge": str(row.get("cond_gauge") or "").strip(),
                         "colors": [], "slots": cq})

        gnd = str(row.get("gnd") or "").strip()
        if gnd:
            has_ground = True
            fill.append({"type": "POWER", "count": 1, "wire_ct": 1, "gauge": gnd,
                         "colors": ["GRN"], "slots": 1, "is_ground": True,
                         "s_symbol": "GND_L", "d_symbol": "GND_R",
                         "s_symbol_conf": 0.95, "d_symbol_conf": 0.95})

        cabq = _int(row.get("cable_qty"))
        if cabq:
            fill.append({"type": _norm_cable(row.get("cable_type")),
                         "count": cabq, "wire_ct": cabq, "gauge": "",
                         "colors": [], "slots": cabq})

        # attach device symbols to the real conductor/cable groups (not ground)
        non_ground = [g for g in fill if not g.get("is_ground")]
        try:
            idp_extract._attach_symbols(non_ground, frm, to)
        except Exception:
            pass

        recs.append({
            "name": name, "source": [frm], "dest": [to],
            "size": str(row.get("size") or "").strip(),
            "ctype": idp_extract._norm_ctype(row.get("ctype") or "XXX"),
            "docs": [], "wires": [], "fill": fill,
            "deviations": str(row.get("notes") or "").strip(),
            "flags": ["from_manual_schedule"],
        })

    if has_ground:
        for r in recs:
            r["ground_authoritative"] = True
    return recs
