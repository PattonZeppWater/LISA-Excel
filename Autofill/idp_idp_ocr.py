"""
idp_idp_ocr.py — OCR reader for AIC INTERCONNECTION-DIAGRAM finished IDPs.

A finished AIC IDP is a set of one-conduit-per-sheet interconnection diagrams. The conduit
identity and fill live as GRAPHICAL text the PDF text layer doesn't carry, so the text-only
finished-IDP reader (idp_idp_pdf) recovers almost nothing and the conduit-SCHEDULE OCR reader
(idp_ocr_schedule) can't match its header. But offline OCR reads these sheets fine — each has:

    FIELD header:   "NAME: <tag>"  "TYPE: <PVC/RGS/…>"  "SIZE: <4"/2"/3-4"/…>"
    a FILL TYPE table:  FILL TYPE | SIZE | COLOR | QUANTITY   (POWER/CONTROL/GROUND/TSP/
                        PULL ROPE/ETHERNET/NONE rows)
    SOURCE (top-left equipment) and DESTINATION (top-right equipment) labels.

This module renders each sheet, OCRs it, and turns it into one conduit record — the offline
path that finally reads this document class. Learned from vision-reading the City of Gonzales
Industrial WWTF IDP (project 56.1113); see idp_training FINISHED_IDP_OCR conventions.

Fully offline (RapidOCR via idp_ocr_schedule._ocr_image). Best-effort + flagged for review.
"""
import os
import re

import idp_ocr_schedule as _O

# fill-type keyword (as it reads in the FILL TYPE column) -> canonical LISA fill group type
_FILL_CANON = [
    ("PULL", "PULL_ROPE"), ("ROPE", "PULL_ROPE"), ("MULE", "PULL_ROPE"),
    ("ETHERNET", "CAT-6"), ("CAT6", "CAT-6"), ("CAT-6", "CAT-6"), ("CAT 6", "CAT-6"),
    ("FIBER", "FIBER"), ("OM3", "FIBER"), ("OM4", "FIBER"), ("SMFO", "FIBER"), ("MMFO", "FIBER"),
    ("TSP", "TSP"),
    ("GROUND", "GROUND"), ("GND", "GROUND"),
    ("POWER", "POWER"),
    ("CONTROL", "CONTROL"),
    ("MFG", "MFG_CABLE"), ("MFR", "MFG_CABLE"),
    ("NONE", "NONE"), ("SPARE", "NONE"),
]
_KNOWN_FILL = {"POWER", "CONTROL", "GROUND", "TSP", "PULL_ROPE", "CAT-6", "FIBER",
               "MFG_CABLE", "NONE"}


def _canon_fill(t):
    u = re.sub(r"\s+", " ", str(t or "").upper()).strip()
    for k, v in _FILL_CANON:
        if k in u:
            return v
    return u


def _clean_name(s):
    s = str(s or "").strip().strip(":").strip()
    s = re.sub(r'["*’”]', "", s)          # OCR renders " as * sometimes
    s = re.sub(r"\s+", "", s)                        # a conduit tag has no spaces
    return s


def _clean_type(s):
    s = str(s or "").strip().strip(":").strip()
    u = s.upper()
    if u in ("", "XX", "XXX"):
        return ""                                    # TBD on the sheet
    return u


def _clean_size(s):
    s = str(s or "").strip().strip(":").strip()
    s = s.replace("*", '"').replace("''", '"')       # OCR ' " ' artifacts
    if s.upper() in ("XX", "XXX"):
        return ""
    return re.sub(r"\s+", " ", s)


# OCR letter→digit confusions seen in conductor gauges (a gauge is numeric once the AWG/kcmil
# unit is stripped, so any leftover letter is an OCR slip).
_GAUGE_OCR = {"B": "8", "S": "5", "O": "0", "I": "1", "L": "1", "Z": "2", "Q": "0", "D": "0"}


def _repair_gauge(s):
    """Return a clean conductor gauge from a (possibly OCR-mangled) SIZE cell:
    '8AWG' misread 'BAWG' → #8, '2/0AWG' → #2/0, '400KCMIL' → 400 MCM. A pure-letter cell that
    is a single known digit-confusion (a lone 'B') is treated as that digit; a multi-letter cell
    with no digit (e.g. a color that leaked into the column) yields NO gauge."""
    raw = str(s or "").strip()
    if not raw:
        return ""
    u = re.sub(r"(?i)AWG|KCMIL|MCM", " ", raw.upper()).strip()
    if not re.search(r"\d", u):
        st = u.lstrip("#").strip()
        return ("#" + _GAUGE_OCR[st]) if len(st) == 1 and st in _GAUGE_OCR else ""
    u2 = "".join(_GAUGE_OCR.get(c, c) if c.isalpha() else c for c in u)   # fix letter-for-digit
    m = re.search(r"(\d{3,4})", u2)
    if m and int(m.group(1)) >= 250:
        return m.group(1) + " MCM"                       # kcmil (render MCM, no '#')
    m = re.search(r"(\d+/\d+)", u2) or re.search(r"(\d+)", u2)
    return ("#" + m.group(1)) if m else ""


def _read_fill_table(frags):
    """Reconstruct the FILL TYPE / SIZE / COLOR / QUANTITY table into LISA fill groups."""
    hdr = {}
    for f in frags:
        u = re.sub(r"\s+", " ", f["text"].upper()).strip()
        if u in ("FILL TYPE", "FILLTYPE") and "type" not in hdr:
            hdr["type"] = f
        elif u == "SIZE" and "size" not in hdr:
            hdr["size"] = f
        elif u == "COLOR" and "color" not in hdr:
            hdr["color"] = f
        elif u in ("QUANTITY", "QTY", "QUAN") and "qty" not in hdr:
            hdr["qty"] = f
    if "type" not in hdr or "qty" not in hdr:
        return []
    cols = [(k, hdr[k]["cx"]) for k in ("type", "size", "color", "qty") if k in hdr]
    hy = hdr["type"]["cy"]
    x_lo = hdr["type"]["cx"] - 60
    x_hi = hdr["qty"]["cx"] + 60
    data = [f for f in frags if f["cy"] > hy + 5 and x_lo <= f["cx"] <= x_hi]
    data.sort(key=lambda f: f["cy"])
    rows, cur = [], []
    for f in data:
        if cur and f["cy"] - cur[-1]["cy"] > 16:
            rows.append(cur); cur = [f]
        else:
            cur.append(f)
    if cur:
        rows.append(cur)
    fills = []
    for r in rows:
        cell = {k: [] for k, _ in cols}
        for f in sorted(r, key=lambda f: f["cx"]):
            k = min(cols, key=lambda c: abs(f["cx"] - c[1]))[0]
            cell[k].append(f["text"])
        ft = _canon_fill(" ".join(cell.get("type", [])))
        if ft not in _KNOWN_FILL or ft == "NONE":
            continue                                 # not a fill row (junk) / empty conduit
        size_raw = " ".join(cell.get("size", []))
        # FIBER precedence: an OM3/OM4/SMFO/MMFO size means FIBER even if the row is labeled
        # ETHERNET (fiber-ethernet link) — so it isn't mis-typed CAT-6.
        if re.search(r"(?i)[O0]M\s*/?\s*\d|SMFO|MMFO|FIBER", size_raw):
            ft = "FIBER"
        color = " ".join(cell.get("color", [])).strip()
        qn = re.search(r"\d+", " ".join(cell.get("qty", [])))
        q = int(qn.group()) if qn else 1
        colors = [] if color.upper() in ("", "N/A", "NA", "TBD") else [color]
        # a wire gauge only applies to conductor types; repair OCR digit slips (8 read as 'B')
        gauge = _repair_gauge(size_raw) if ft in ("POWER", "CONTROL", "GROUND", "TSP") else ""
        is_gnd = (ft == "GROUND")
        g = {"type": ("POWER" if is_gnd else ft), "count": q, "wire_ct": q,
             "gauge": gauge, "colors": colors}
        if is_gnd:
            g.update({"is_ground": True, "s_symbol": "GND_L", "d_symbol": "GND_R",
                      "colors": ["GRN"], "count": 1, "wire_ct": 1})
        fills.append(g)
    return fills


_LABEL_SKIP = ("SOURCE", "DESTINATION", "FIELD", "SUPPORTING DOCUMENTS", "DRAWING NUMBER",
               "DESCRIPTION", "MANUFACTURER", "DEVIATIONS & NOTES", "DEVIATIONS", "NOTES:",
               "CHANGE ORDERS & ERRORS:", "CHANGE ORDERS", "FILL TYPE", "SIZE", "COLOR",
               "QUANTITY", "NAME", "TYPE")


def _field_label(frags, x_min, x_max, y_min, y_max):
    """The equipment label in the SOURCE (top-left) / DESTINATION (top-right) band — the text
    run in that box, excluding table headings and the NAME/TYPE/SIZE field labels."""
    cand = []
    for f in frags:
        t = f["text"].strip()
        u = t.upper()
        if not (x_min <= f["cx"] <= x_max and y_min <= f["cy"] <= y_max):
            continue
        if u in _LABEL_SKIP or re.match(r"(?i)(NAME|TYPE|SIZE)\s*:", t):
            continue
        cand.append(f)
    cand.sort(key=lambda f: (round(f["cy"] / 12), f["cx"]))
    parts = [f["text"].strip() for f in cand[:3]]
    return " ".join(p for p in parts if p).strip()


def read_interconnection_idp(pdf_path, pages=None, dpi=200, log=None, cap=150):
    """OCR an AIC interconnection-diagram finished IDP → conduit records (one per sheet).
    Returns (records, "idp-ocr"). Non-fabricating: a sheet with no NAME + no FILL table is
    skipped; every record is flagged for verification. Fully offline."""
    _log = log or (lambda *a: None)
    try:
        import fitz
    except Exception:
        return [], "idp-ocr-unavailable"
    try:
        import idp_schedule as _S
    except Exception:
        _S = None
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return [], "idp-ocr-open-fail"
    if pages is None:
        pages = range(doc.page_count)
    recs, seen = [], set()
    tmp = os.path.join(os.environ.get("TEMP", "."), "idp_ocr_page.png")
    _skipped = _ocrd = 0
    try:
        for pi in pages:
            if pi < 0 or pi >= doc.page_count:
                continue
            page = doc[pi]
            # CHEAP PRE-FILTER — only OCR pages that ARE conduit sheets. The template boilerplate
            # ("FILL TYPE" fill-table header) is in the TEXT layer even when the conduit data is
            # graphical, so we skip cover / notes / CONTINUATION / non-conduit pages WITHOUT the
            # expensive render+OCR. This is the fix for finished IDPs taking forever: a 200-sheet
            # submittal now OCRs only its ~24 conduit sheets, not all 200.
            try:
                tl = page.get_text("text").upper()
            except Exception:
                tl = ""
            if "FILL TYPE" not in tl:
                _skipped += 1
                continue                              # cover / continuation / not a fill sheet
            if _ocrd >= cap:                          # runaway guard for huge submittals
                _log(f"IDP-OCR: reached the {cap}-sheet cap — stopping (raise `cap` to read more).")
                break
            try:
                r = page.rect
                # OCR only the NARROW CENTER COLUMN — the FIELD header (NAME/TYPE/SIZE) and the
                # FILL TYPE table both sit center-of-sheet. Cropping to it (vs the full-width
                # band) cuts each sheet's OCR from ~16s to ~7s: RapidOCR is slow on wide images,
                # and the far-left SOURCE / far-right DESTINATION labels are what forced the full
                # width. This fast path reads the conduit IDENTITY + FILL (the core index); the
                # from/to equipment labels are left for a slower opt-in pass.
                # Crop to the DATA COLUMN only (x 0.38–0.64): the FIELD header (NAME/TYPE/SIZE ~x
                # 0.49) and the FILL TYPE table (~x 0.40–0.62) live there. This EXCLUDES the dense
                # wiring text on the far left/right — which is both irrelevant AND the real speed
                # sink (a 30-conductor sheet has hundreds of wiring text boxes to OCR). ~2s/sheet.
                clip = fitz.Rect(r.width * 0.36, r.height * 0.10, r.width * 0.68, r.height * 0.50)
                page.get_pixmap(dpi=dpi, clip=clip).save(tmp)
                frags = _O._ocr_image(tmp)
                _ocrd += 1
            except Exception:
                continue
            # Parse NAME/TYPE/SIZE from the JOINED reading-order text, so an OCR split of the
            # label from its value ("NAME:" + "BLR-405" as two boxes) still resolves. Require the
            # COLON so the FILL-table "SIZE"/"TYPE" column HEADERS (which have no colon) can't match.
            joined = " ".join(f["text"] for f in
                              sorted(frags, key=lambda f: (round(f["cy"] / 8), f["cx"])))

            def _grab(lbl):
                m = re.search(r"(?i)\b" + lbl + r"\s*:\s*([^\s|]+)", joined)
                return m.group(1) if m else ""
            name = _clean_name(_grab("NAME"))
            typ = _grab("TYPE")
            size = _grab("SIZE")
            if not name or name.upper() in ("XXX", "XX"):
                continue                              # unnamed / TBD "suggested" conduit
            if name in seen:
                continue
            seen.add(name)
            # the center-column crop excludes the far-edge SOURCE/DESTINATION labels — leave
            # them blank in this fast path (flagged), rather than pay the wide-image OCR cost.
            fill = _read_fill_table(frags)
            rec = {"name": name, "ctype": _clean_type(typ), "size": _clean_size(size),
                   "source": [], "dest": [],
                   "fill": fill, "deviations": "",
                   "flags": ["from_idp_ocr", "idp_ocr_low_confidence"]}
            recs.append(rec)
            _log(f"IDP-OCR: sheet {pi + 1} → {name} ({rec['ctype'] or '?'} {rec['size'] or '?'}, "
                 f"{len(fill)} fill group(s))")
    finally:
        doc.close()
    if _ocrd or _skipped:
        _log(f"IDP-OCR: read {len(recs)} conduit(s) — OCR'd {_ocrd} conduit sheet(s), skipped "
             f"{_skipped} non-conduit page(s) cheaply (no OCR).")
    return recs, "idp-ocr"
