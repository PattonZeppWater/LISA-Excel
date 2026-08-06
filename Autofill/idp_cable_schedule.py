"""
idp_cable_schedule.py — read a CABLE schedule (MC-E-9 / the C-### cable list) off a
vector plan sheet and merge its per-conductor specs into the conduit records.

Why this exists: the CONDUIT schedule (MC-E-8) lists only WHICH cables run in a
conduit plus a coarse TYP (POWER/CONTROL). The conductor count, gauge, insulation/
type (TSP vs CAT-6 vs FIBER vs a mfg cable) and — critically — whether a run carries
a green EGC all live on the CABLE schedule. Reading the conduit schedule alone forces
the writer to (a) synthesize a ground on every circuit (over-grounding) and (b)
collapse everything to POWER/CONTROL. This module closes both gaps automatically,
matching what a curated by-hand build does.

Pipeline:
  read_cable_schedule(pdf, page)      -> [{id,size,type_insul,src,dst,_conf}, …]
  apply_cable_fill(recs, rows, cab)   -> replaces each conduit's coarse fill with the
                                         precise cable-derived fill; sets ground-
                                         authoritative so ensure_ground stops guessing.

Fully offline (RapidOCR), same machinery as idp_ocr_schedule (header-band detection +
column binning), so a project with a different cable-schedule layout reads without
per-project tuning.
"""
import re

import idp_ocr_schedule as _O

try:
    import symbol_infer
except Exception:                                       # pragma: no cover
    symbol_infer = None
try:
    import idp_extract
except Exception:                                       # pragma: no cover
    idp_extract = None

# MC-E-9 cable schedule columns, left→right. The cable ID lands in "name" so it reuses
# idp_ocr_schedule.read_schedule's ID-row filter (^[A-Z]{1,3}-?\d) unchanged.
_HEADER_SEQ_MCE9 = [
    (r"\bID\b|CABLE\s*(NO|#|ID|TAG)|^C$|^CABLE$|TAG", "name"),
    (r"SIZE|CONDUCTOR|COND\b", "size"),
    (r"TYPE|INSUL", "type_insul"),
    (r"FROM", "src"),
    (r"\bTO\b|^TO", "dst"),
]
_CABLE_SCHEMAS = [("mce9", _HEADER_SEQ_MCE9)]


def find_cable_schedule_pages(path, max_pages=1200):
    """Page indices whose TEXT layer titles them a CABLE schedule (MC-E-9 etc.).
    Vector sheets still carry an extractable title even when the table is graphical,
    so this is a cheap text scan — no OCR. Excludes the conduit schedule."""
    try:
        import fitz
    except Exception:
        return []
    out = []
    try:
        d = fitz.open(path)
    except Exception:
        return []
    try:
        for i in range(min(len(d), max_pages)):
            u = d[i].get_text("text").upper()
            if not u:
                continue
            # a CABLE schedule sheet, not the CONDUIT schedule
            if re.search(r"CABLE\s+SCHEDULE", u) or re.search(r"\bMC-?E-?9\b", u):
                if "CONDUIT SCHEDULE" not in u or "CABLE SCHEDULE" in u:
                    out.append(i)
    finally:
        d.close()
    return out


def _cable_clip(page):
    """Cable schedules are often full-width (FROM/TO on the right), so use a generous
    full-width region rather than the conduit reader's upper-left crop. The row parser
    drops any non-cable rows that sneak in, so over-inclusion is harmless."""
    import fitz
    W, H = page.rect.width, page.rect.height
    return fitz.Rect(0, H * 0.015, W * 0.99, H * 0.9)


def read_cable_schedule(pdf_path, page_idx, refine=False, log=None):
    """OCR one cable-schedule page → [{id,size,type_insul,src,dst,_conf}]."""
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[max(0, min(page_idx, doc.page_count - 1))]
    clip = _O._detect_table_bbox(page) or _cable_clip(page)
    doc.close()
    rows, meta = _O.read_schedule(pdf_path, page_idx, clip=clip, refine=refine,
                                  log=log, schemas=_CABLE_SCHEMAS)
    out = []
    for r in rows:
        cid = _norm_cable_id(r.get("name") or "")
        if not cid:
            continue
        out.append({"id": cid, "size": (r.get("size") or "").strip(),
                    "type_insul": (r.get("type_insul") or "").strip(),
                    "src": (r.get("src") or "").strip(), "dst": (r.get("dst") or "").strip(),
                    "_conf": r.get("_conf", {})})
    return out, meta


# ── cable spec → LISA fill groups (the C-### conductor grammar) ──────────────
def _fmt_gauge(raw):
    """AWG gauges keep '#'; kcmil sizes (≥250) render as 'nnn MCM' so LISA's _with_awg
    doesn't tack 'AWG' onto them."""
    r = str(raw).strip().lstrip("#")
    if re.fullmatch(r"[0-9]+", r) and int(r) >= 250:
        return r + " MCM"
    return "#" + r


def _norm_cable_id(raw):
    """Normalize an OCR'd cable id to C-### (fixes 'C 001', 'C0001', 'C-0025')."""
    m = re.search(r"C[\s-]?0*(\d{1,4})([A-Z]?)", str(raw).upper())
    if not m:
        return ""
    return "C-%03d%s" % (int(m.group(1)), m.group(2))


def cable_to_fill(size, type_insul):
    """Turn one cable's SIZE spec + TYPE/INSUL into LISA fill groups, with the green
    EGC broken out as its own ground group where the spec says 'W/ #n GND'. Mirrors
    the finished-IDP conductor conventions (TSP, COMM→CAT-6, FIBER, GROUND, nCC-#g)."""
    ti = (type_insul or "").upper()
    spec = (size or "").upper().strip()
    gnd = None
    m = re.search(r"W/\s*#?([0-9/]+)\s*GND", spec)
    if m:
        gnd = _fmt_gauge(m.group(1))
    base = re.sub(r"\s*W/.*$", "", spec).strip()
    groups = []
    if "PULL" in ti or "PULL" in base or "ROPE" in base or "MULE TAPE" in base or "MULETAPE" in base:
        groups.append({"type": "PULL_ROPE", "count": 1, "wire_ct": 1, "gauge": "", "colors": ["N/A"]})
    elif "FIBER" in ti or base.startswith("SMFO") or "SMFO" in base:
        groups.append({"type": "FIBER", "count": 1, "wire_ct": 1, "gauge": "", "colors": ["N/A"]})
    elif "GROUND" in ti:
        mg = re.search(r"#?([0-9/]+)", base)
        g = _fmt_gauge(mg.group(1) if mg else "2")
        groups.append({"type": "POWER", "count": 1, "wire_ct": 1, "gauge": g, "colors": ["GRN"],
                       "is_ground": True, "s_symbol": "GND_L", "d_symbol": "GND_R", "slots": 1})
    elif "PAIR" in base:
        mp = re.match(r"([0-9]+)-?PAIR\s*#?([0-9]+)", base)
        pr = int(mp.group(1)) if mp else 1
        g = "#" + (mp.group(2) if mp else "18")
        groups.append({"type": "TSP", "count": pr, "wire_ct": pr, "gauge": g, "colors": ["RED/BLK"]})
    elif "COMM" in ti:
        groups.append({"type": "CAT-6", "count": 1, "wire_ct": 1, "gauge": "", "colors": ["N/A"]})
    elif "CABLE" in ti or "SPECIAL" in base or base == "":
        groups.append({"type": "MFG_CABLE", "count": 1, "wire_ct": 1, "gauge": "", "colors": []})
    else:
        mc = re.match(r"([0-9]+)CC-?#?([0-9/]+)", base)
        if mc:
            n = int(mc.group(1))
            g = _fmt_gauge(mc.group(2))
            t = "POWER" if "POWER" in ti else "CONTROL"
            groups.append({"type": t, "count": n, "wire_ct": n, "gauge": g, "colors": []})
        else:
            groups.append({"type": "CONTROL", "count": 1, "wire_ct": 1, "gauge": "", "colors": []})
    if gnd:
        groups.append({"type": "POWER", "count": 1, "wire_ct": 1, "gauge": gnd, "colors": ["GRN"],
                       "is_ground": True, "s_symbol": "GND_L", "d_symbol": "GND_R", "slots": 1})
    return groups


def _cable_ids(text):
    """Extract cable ids from a conduit's CABLES-IN-CONDUIT text ('C-001, C-002' /
    'CABLES: C001 C002' / 'C-0025'), normalized and de-duped in order, spares dropped."""
    ids, seen = [], set()
    for tok in re.findall(r"C[\s-]?0*\d{1,4}[A-Z]?", str(text or ""), re.I):
        cid = _norm_cable_id(tok)
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


def apply_cable_fill(recs, rows_by_name, cab, log=None):
    """Replace each conduit's coarse (conduit-schedule) fill with the precise fill
    derived from the CABLE schedule, and mark it ground-authoritative so the writer
    stops synthesizing grounds. Conduits with no matching cable keep their coarse
    fill (fallback). Returns (n_conduits_upgraded, n_cables_matched)."""
    n_up, n_cab = 0, 0
    for rec in recs or []:
        row = rows_by_name.get(rec.get("name"), {})
        raw = str(row.get("cables") or row.get("notes") or rec.get("deviations") or "")
        ids = _cable_ids(raw)
        if not ids:
            continue
        fill = []
        for cid in ids:
            info = cab.get(cid)
            if not info:
                continue
            n_cab += 1
            cfrm = info.get("src") or (rec.get("source") or [""])[0]
            cto = info.get("dst") or (rec.get("dest") or [""])[0]
            for g in cable_to_fill(info.get("size"), info.get("type_insul")):
                g = dict(g)
                g["s_desc"] = [cfrm]
                g["d_desc"] = [cto]
                g["_cable"] = cid
                if not g.get("s_symbol") and symbol_infer is not None:
                    ct = g.get("wire_ct") or g.get("count") or 1
                    try:
                        g["s_symbol"] = symbol_infer.infer_symbol(cfrm, g["type"], ct, "L").get("symbol") or ""
                        g["d_symbol"] = symbol_infer.infer_symbol(cto, g["type"], ct, "R").get("symbol") or ""
                    except Exception:
                        pass
                fill.append(g)
        if not fill:
            continue
        rec["fill"] = fill
        rec["ground_authoritative"] = True
        flags = rec.setdefault("flags", [])
        if "fill_from_cable_schedule" not in flags:
            flags.append("fill_from_cable_schedule")
        # the coarse conduit-only seed is now superseded — drop its caveat flag
        rec["flags"] = [f for f in flags if f != "ocr_conduit_only_fill_from_cables"]
        n_up += 1
    if log and n_up:
        log(f"Cable schedule: {n_cab} cable(s) matched → {n_up} conduit(s) upgraded to "
            f"precise fill (types + authoritative grounds).")
    return n_up, n_cab
