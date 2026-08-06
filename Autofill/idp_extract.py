"""
IDP Extractor - core engine.

Reads Interconnection Diagram (IDP) PDFs and fills a copy of the IDP workbook
template (.xlsm). Populates:
  - ConduitIndex : one row per drawing (name, source/dest names, size, type, ref docs)
  - Ref Documents: deduped global list of supporting documents
  - FillIndex    : basic wire rows grouped by Type+Gauge (conduit, wire ct, type, gauge, colors)

Symbol / ISA-tag / terminal columns are intentionally left blank (out of scope for v1).
"""

import os
import re
import warnings

warnings.filterwarnings("ignore")

import pdfplumber
import openpyxl

# ---- watermark handling -------------------------------------------------
# The drawings carry a light "NOT FOR CONSTRUCTION" watermark. Its letters get
# picked up as stray single-character tokens. Drop single alphabetic tokens but
# keep single digits (e.g. the "2" in "INFLUENT PUMP 2").

def _clean_cell(text):
    """Clean a table cell that may contain watermark line-noise."""
    if text is None:
        return ""
    parts = [p.strip() for p in str(text).split("\n")]
    keep = [p for p in parts if not (len(p) == 1 and p.isalpha())]
    return " ".join(x for x in keep if x).strip()


def _is_noise_token(t):
    return len(t) == 1 and t.isalpha()


# ---- header band (NAME/TYPE/SIZE + source/dest names) -------------------

def _extract_header(page):
    """Return dict with name, ctype, size and source/dest name lists (len 4).

    The drawing header band carries three visual name rows; the workbook's
    ConduitIndex has four Source/Destination Name columns, so the 4th slot is
    padded blank here. If drawings start carrying a 4th name row, widen the band
    bucketing below against a new sample PDF."""
    words = [w for w in page.extract_words() if 120 < w["top"] < 160]
    # bucket into the three visual rows
    rows = {0: [], 1: [], 2: []}
    for w in words:
        t = w["text"]
        if _is_noise_token(t):
            continue
        if t.upper() in ("SOURCE", "FIELD", "DESTINATION"):
            continue
        top = w["top"]
        if top < 135:
            r = 0
        elif top < 149:
            r = 1
        else:
            r = 2
        rows[r].append(w)

    def zone(row_words, lo, hi):
        toks = [w for w in row_words if lo <= w["x0"] < hi]
        toks.sort(key=lambda w: w["x0"])
        return " ".join(w["text"] for w in toks).strip()

    def field_value(row_words):
        # value tokens sit just right of the NAME:/TYPE:/SIZE: label (~x0 600-800)
        toks = [w for w in row_words if 601 <= w["x0"] < 800
                and w["text"].rstrip(":") not in ("NAME", "TYPE", "SIZE")
                and not (len(w["text"]) == 1 and w["text"].isdigit())]
        toks.sort(key=lambda w: w["x0"])
        return " ".join(w["text"] for w in toks).strip()

    # source zone x0 < 520 ; destination zone x0 > 900
    # Three name rows come off the drawing; pad a 4th blank to match the 4
    # Source/Destination Name columns on the ConduitIndex sheet.
    source = [zone(rows[i], 0, 520) for i in range(3)] + [""]
    dest = [zone(rows[i], 900, 100000) for i in range(3)] + [""]
    name = field_value(rows[0])
    ctype = field_value(rows[1])
    size = field_value(rows[2])
    return {
        "name": name,
        "ctype": ctype,
        "size": size,
        "source": source,
        "dest": dest,
    }


# ---- fill table ---------------------------------------------------------

_GAUGE_RE = re.compile(r"#?\s*([0-9]+(?:/[0-9]+)?(?:KCMIL)?|[0-9]+AWG)", re.I)


def _norm_gauge(size_cell):
    s = _clean_cell(size_cell)
    if not s:
        return ""
    s = s.replace("AWG", "").replace("#", "").strip()
    return s


def _extract_fill(page):
    """Return list of grouped fill rows: dict(type, gauge, colors[list], count)."""
    tables = page.extract_tables()
    fill_rows = []
    for t in tables:
        if not t or not t[0]:
            continue
        header = [(_clean_cell(c) or "").upper() for c in t[0]]
        if "FILL TYPE" in header and "QUANTITY" in header:
            ci_type = header.index("FILL TYPE")
            ci_size = header.index("SIZE") if "SIZE" in header else ci_type + 1
            ci_color = next((i for i, h in enumerate(header) if "COLOR" in h), ci_type + 2)
            ci_qty = header.index("QUANTITY")
            for row in t[1:]:
                ftype = _clean_cell(row[ci_type]) if ci_type < len(row) else ""
                color = _clean_cell(row[ci_color]) if ci_color < len(row) else ""
                gauge = _norm_gauge(row[ci_size]) if ci_size < len(row) else ""
                qtxt = _clean_cell(row[ci_qty]) if ci_qty < len(row) else ""
                if not ftype:
                    continue
                try:
                    qty = int(re.sub(r"[^0-9]", "", qtxt) or "1")
                except ValueError:
                    qty = 1
                fill_rows.append({"type": ftype, "gauge": gauge,
                                  "color": color, "qty": qty})
            break

    # group consecutive rows by (type, gauge); collect up to 4 colors per group
    grouped = []
    for r in fill_rows:
        g = grouped[-1] if grouped else None
        if (g and g["type"] == r["type"] and g["gauge"] == r["gauge"]
                and len(g["colors"]) < 4):
            g["colors"].append(r["color"])
            g["count"] += r["qty"]
        else:
            grouped.append({"type": r["type"], "gauge": r["gauge"],
                            "colors": [r["color"]], "count": r["qty"]})
    return grouped


# ---- supporting documents ----------------------------------------------

def _extract_docs(page):
    """Return list of (dwg, description, manufacturer)."""
    tables = page.extract_tables()
    docs = []
    for t in tables:
        # find the header row that contains DRAWING NUMBER + DESCRIPTION
        hdr_idx = None
        for i, row in enumerate(t):
            cells = [(_clean_cell(c) or "").upper() for c in row]
            if "DRAWING NUMBER" in cells and "DESCRIPTION" in cells:
                hdr_idx = i
                break
        if hdr_idx is None:
            continue
        hdr = [(_clean_cell(c) or "").upper() for c in t[hdr_idx]]
        # both left and right sub-tables share these labels; gather each occurrence
        col_sets = []
        idx = 0
        while True:
            try:
                d = hdr.index("DRAWING NUMBER", idx)
            except ValueError:
                break
            desc = hdr.index("DESCRIPTION", d) if "DESCRIPTION" in hdr[d:] else None
            manu = hdr.index("MANUFACTURER", d) if "MANUFACTURER" in hdr[d:] else None
            col_sets.append((d, desc, manu))
            idx = d + 1
        for row in t[hdr_idx + 1:]:
            cells = [_clean_cell(c) for c in row]
            for (d, desc, manu) in col_sets:
                dwg = cells[d] if d is not None and d < len(cells) else ""
                de = cells[desc] if desc is not None and desc < len(cells) else ""
                ma = cells[manu] if manu is not None and manu < len(cells) else ""
                if dwg or de:
                    docs.append((dwg, de, ma))
        break
    return docs


# ---- wire connection strings -------------------------------------------
# Printed on the drawing as srcName:srcTag:srcTerm/dstName:dstTag:dstTerm
# (the LISP wire-label grammar). Each side may omit the tag, giving a
# 2-part name:term form. The last field is always the terminal.

_WIRE_RE = re.compile(
    r"[^\s/:]+:[^\s/:]+(?::[^\s/:]+)?/[^\s/:]+:[^\s/:]+(?::[^\s/:]+)?")


def _norm_phase(s):
    # The drawings print the phase mark as the glyph "Ø"; the workbook stores
    # it as the AutoCAD control code "%%C" (e.g. "ØA" -> "%%CA").
    return s.replace("\u00d8", "%%C")


def _split_side(side):
    parts = [_norm_phase(p) for p in side.split(":")]
    if len(parts) >= 3:
        return (parts[0], parts[1], parts[2])
    if len(parts) == 2:
        return (parts[0], "", parts[1])
    return (parts[0], "", "")


def _extract_wires(page):
    """Return list of dicts {src:(name,tag,term), dst:(name,tag,term)}.

    The string is printed twice per wire (one at each end); dedup by value,
    preserving first-seen order.
    """
    txt = page.extract_text() or ""
    out = []
    seen = set()
    for m in _WIRE_RE.finditer(txt):
        s = m.group(0)
        if s in seen:
            continue
        seen.add(s)
        left, right = s.split("/", 1)
        out.append({"src": _split_side(left), "dst": _split_side(right)})
    return out


# ---- title block (project/dwg no) --------------------------------------

def _extract_titleblock(page):
    txt = page.extract_text() or ""
    dwg = ""
    m = re.search(r"\b\d{2}\.\d{4}-\d+[eE]\b", txt)
    if m:
        dwg = m.group(0)
    return {"dwg_no": dwg}


# ---- page classification -----------------------------------------------

def parse_pdf(path):
    """Parse an IDP pdf, returning a list of drawing dicts (skips non-drawing pages)."""
    results = []
    with pdfplumber.open(path) as pdf:
        for pi, page in enumerate(pdf.pages):
            hdr = _extract_header(page)
            # A real drawing page has a conduit NAME and the SOURCE/FIELD/DEST band.
            txt = page.extract_text() or ""
            if not hdr["name"] or "DESTINATION" not in txt.upper():
                continue
            rec = {
                "page": pi + 1,
                "name": hdr["name"],
                "ctype": hdr["ctype"],
                "size": hdr["size"],
                "source": hdr["source"],
                "dest": hdr["dest"],
                "fill": _extract_fill(page),
                "docs": _extract_docs(page),
                "wires": _extract_wires(page),
                "title": _extract_titleblock(page),
            }
            results.append(rec)

    # merge drawings that share a conduit name (e.g. a diagram continued on the
    # next sheet): combine fill groups and dedup supporting documents.
    merged = {}
    order = []
    for rec in results:
        key = rec["name"]
        if key not in merged:
            merged[key] = rec
            order.append(key)
        else:
            m = merged[key]
            m["fill"].extend(rec["fill"])
            for w in rec["wires"]:
                if w not in m["wires"]:
                    m["wires"].append(w)
            for d in rec["docs"]:
                if d not in m["docs"]:
                    m["docs"].append(d)
            for i in range(4):
                if not m["source"][i] and rec["source"][i]:
                    m["source"][i] = rec["source"][i]
                if not m["dest"][i] and rec["dest"][i]:
                    m["dest"][i] = rec["dest"][i]
    return [merged[k] for k in order]


# ========================================================================
# SCHEDULE MODE — tabular Conduit/Cable schedules embedded in drawing sets
# ========================================================================
# Many contractor packages carry the conduit data as a "CONDUIT SCHEDULE" table
# (often with a linked "CABLE SCHEDULE") on one or two pages of a large PDF,
# rather than as AIC IDP interconnection drawings. parse_pdf() finds 0 drawing
# pages on those. parse_schedule_pdf() handles that format.
#
# Reliability note: these tables have wrapped multi-line cells that bleed across
# column seams (e.g. FROM "239-EPS-LP-053A" / TO "239-FCS-LCP-400" dumps as
# "239-F EPS-LP-053A" / "CS-LCP-400"). NO./CONDUIT SIZE/TYPE extract cleanly;
# FROM/TO are recovered from the cable schedule (cross-referenced by ROUTING =
# conduit NO.) when available, and rows that still look garbled are flagged.

# Self-learning mapping table / knowledge base (optional — parser still works
# without it). It remembers header aliases, value normalizations, and schedule
# titles across runs so extraction gets more deterministic over time.
try:
    from mapping_table import KnowledgeBase
    _KB_INSTANCE = None

    def _get_kb():
        global _KB_INSTANCE
        if _KB_INSTANCE is None:
            try:
                _KB_INSTANCE = KnowledgeBase()
            except Exception:
                _KB_INSTANCE = False
        return _KB_INSTANCE or None
except Exception:
    def _get_kb():
        return None


_CONDUIT_TITLES = ["CONDUIT SCHEDULE", "CABLE AND CONDUIT SCHEDULE",
                   "CONDUIT AND CABLE SCHEDULE", "RACEWAY SCHEDULE"]

# Optional symbol inference (infer S/D Symbol from the device name + library shape)
try:
    from symbol_infer import infer_symbol as _infer_symbol, load_cascade as _load_casc
    _SYM_CASCADE = None

    def _cascade():
        global _SYM_CASCADE
        if _SYM_CASCADE is None:
            try:
                _SYM_CASCADE = _load_casc()
            except Exception:
                _SYM_CASCADE = {}
        return _SYM_CASCADE
except Exception:
    def _infer_symbol(*a, **k):
        return {"symbol": "", "confidence": 0.0}

    def _cascade():
        return {}


def _attach_symbols(fill, src_name, dst_name):
    """Infer S Symbol (source/L) and D Symbol (dest/R) for each fill row from the
    end-device names + the symbol library. Stores symbol + confidence."""
    casc = _cascade()
    for g in fill:
        ct = g.get("count", 1)
        wt = g.get("type", "")
        s = _infer_symbol(src_name, wt, ct, "L", casc)
        d = _infer_symbol(dst_name, wt, ct, "R", casc)
        g["s_symbol"] = s.get("symbol", "")
        g["d_symbol"] = d.get("symbol", "")
        g["s_symbol_conf"] = s.get("confidence", 0.0)
        g["d_symbol_conf"] = d.get("confidence", 0.0)
        g["s_symbol_token"] = s.get("token")
        g["d_symbol_token"] = d.get("token")
    return fill

_TABLE_SETTINGS = {
    "vertical_strategy": "lines", "horizontal_strategy": "lines",
    "snap_tolerance": 4, "join_tolerance": 4,
}


def _norm_hdr(s):
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def _find_table(page, title):
    """Return the first extracted table whose cells contain the given title."""
    title = title.upper()
    for settings in (_TABLE_SETTINGS, None):
        try:
            tables = page.extract_tables(settings) if settings else page.extract_tables()
        except Exception:
            continue
        for t in tables:
            if any(title in _norm_hdr(c) for row in t for c in row):
                return t
    return None


def _header_map(table, required):
    """Locate the header row (first row containing all `required` tokens) and
    return (header_row_index, {canonical: col_index})."""
    req = [_norm_hdr(r) for r in required]
    for ri, row in enumerate(table):
        cells = [_norm_hdr(c) for c in row]
        if all(any(tok == c or tok in c for c in cells) for tok in req):
            cmap = {}
            for ci, c in enumerate(cells):
                cmap[c] = ci
            return ri, cmap
    return None, {}


def _col(cmap, *aliases):
    for a in aliases:
        a = _norm_hdr(a)
        for key, idx in cmap.items():
            if key == a or a in key:
                return idx
    return None


def _looks_garbled(s):
    """Heuristic: lots of isolated single chars => column-bleed noise."""
    toks = str(s).split()
    if not toks:
        return False
    singles = sum(1 for t in toks if len(t) == 1)
    return singles >= max(3, len(toks) // 2)


def _split_endpoint(s):
    """Split a raw endpoint into up to 4 name parts (tag, then parenthetical)."""
    s = _clean_cell(s)
    if not s:
        return ["", "", "", ""]
    if "(" in s:
        head, rest = s.split("(", 1)
        return [head.strip(), ("(" + rest).strip(), "", ""]
    return [s, "", "", ""]


def _parse_cable_spec(spec):
    """'2/C #12, 1/G #12' -> [{'count':2,'gauge':'#12','kind':'C'},
                              {'count':1,'gauge':'#12','kind':'G'}]"""
    out = []
    for seg in re.split(r"[;,]", _clean_cell(spec)):
        m = re.search(r"(\d+)\s*/\s*([CGcg])\s*#?\s*([0-9/]+)", seg)
        if m:
            out.append({"count": int(m.group(1)),
                        "gauge": "#" + m.group(3),
                        "kind": m.group(2).upper()})
    return out


def _fill_kind(spec, remarks=""):
    """Return (Type, WireCt) for the FillIndex, where Type is the workbook's
    Type-column domain (Type_<n> named ranges), NOT the PickList FillType:
        POWER, CONTROL, TSP, MFG_CABLE   (FIBER, CAT-6, PULL_ROPE only at Ct 1)
    Wire Ct is the CONNECTION count (1..8), never the raw conductor count — a
    multi-conductor cable is ONE MFG_CABLE, matching how LISA draws it."""
    s = (spec or "").upper()
    r = (remarks or "").upper()
    if "FIBER" in s or "FIBER" in r or "F/O" in s or "ST-ST" in s:
        return ("FIBER", 1)
    if "CAT" in s or "ETHERNET" in r or "NETWORK" in r:
        return ("CAT-6", 1)
    if "ROPE" in s or "PULL ROPE" in s:
        return ("PULL_ROPE", 1)
    if "COAX" in s:
        return ("MFG_CABLE", 1)
    if "TSP" in s or "SHIELD" in s or "TWISTED" in s or "SH PR" in s:
        return ("TSP", 1)                      # one shielded/pair cable
    # plain conductors: N/C ...
    m = re.search(r"(\d+)\s*/?\s*C", s)
    n = int(m.group(1)) if m else 1
    if n > 4:
        return ("MFG_CABLE", 1)                # multi-conductor = one manufactured cable
    kind = "POWER" if "POWER" in r else "CONTROL"
    return (kind, n if n in (1, 2, 3, 4) else 1)


def _cable_fill_rows(cables):
    """One FillIndex fill row per cable, mapped to LISA's model: Type in the
    Type-column domain (POWER/CONTROL/TSP/MFG_CABLE/...), a valid Wire Ct (1..8),
    and Wire Gauge. Terminations (S/D Tag/Term) are left blank — they come from
    the wiring diagrams, not the cable schedule."""
    rows = []
    for cab in cables:
        spec = cab.get("spec", "")
        kind, ct = _fill_kind(spec, cab.get("remarks", ""))
        g = re.search(r"#\s*([0-9/]+)", spec)
        gauge = "#" + g.group(1) if g else ""
        rows.append({"type": kind, "gauge": gauge, "colors": [], "count": ct})
    return rows


_CTYPE_ENUM = {"XXX", "PVC", "RGS", "PVC/RGS", "FLEX", "RMC", "PER SPEC", "PCS", "RMC-PVC"}


def _norm_ctype(s):
    s = _clean_cell(s).upper()
    if not s:
        return "XXX"
    if s in _CTYPE_ENUM:
        return s
    if s.startswith("PVC"):
        return "PVC"
    if s in ("GRC", "RIGID", "RGC"):
        return "RGS"
    return s  # leave as-is; the writer/skill can flag it


def _extract_cable_schedule(page):
    """Return {routing_conduit_no: [ {no, spec, ctype, frm, to, remarks} ]}."""
    t = _find_table(page, "CABLE SCHEDULE")
    if not t:
        return {}
    hr, cmap = _header_map(t, ["NO.", "CABLE", "ROUTING"])
    if hr is None:
        return {}
    kb = _get_kb()
    km = kb.map_header_row(t[hr], context="cable") if kb else {}

    def col(field, *aliases):
        return km[field] if field in km else _col(cmap, *aliases)

    c_no = col("cable_no", "NO.")
    c_cab = col("cable_spec", "CABLE")
    c_typ = col("cable_type", "TYPE")
    c_frm = col("source", "FROM")
    c_to = col("destination", "TO")
    c_rt = col("routing", "ROUTING")
    c_rem = col("remarks", "REMARKS")
    by_routing = {}
    for row in t[hr + 1:]:
        def g(i):
            return _clean_cell(row[i]) if i is not None and i < len(row) else ""
        routing = g(c_rt)
        if not routing and not g(c_no):
            continue
        entry = {"no": g(c_no), "spec": g(c_cab), "ctype": g(c_typ),
                 "frm": g(c_frm), "to": g(c_to), "remarks": g(c_rem)}
        # a cable may list several routing conduits
        for rt in re.split(r"[;,]", routing) or [routing]:
            rt = rt.strip()
            if rt:
                by_routing.setdefault(rt, []).append(entry)
    return by_routing


def parse_schedule_pdf(path, infer_symbols=True):
    """Parse conduit/cable schedule tables into the same record shape parse_pdf
    returns (name, ctype, size, source[4], dest[4], fill[], docs[], wires[])."""
    kb = _get_kb()
    results = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            u = (page.extract_text() or "").upper()
            if "SCHEDULE" not in u or ("CONDUIT" not in u and "RACEWAY" not in u):
                continue
            # try each known conduit-schedule title (learned titles too)
            t = title = None
            for cand in _CONDUIT_TITLES:
                t = _find_table(page, cand)
                if t:
                    title = cand
                    break
            if not t:
                continue
            # A conduit schedule always has a NO. column; TYPE (material) is
            # sometimes omitted (e.g. Blower's "CABLE AND CONDUIT SCHEDULE",
            # where material is governed by a general note). Only NO. is required.
            hr, cmap = _header_map(t, ["NO."])
            if hr is None:
                continue
            if kb and title:
                # remember the actual title text seen on the page
                cell = next((c for r in t for c in r
                             if c and "SCHEDULE" in c.upper()), title)
                kb.learn_title(cell)
            km = kb.map_header_row(t[hr], context="conduit") if kb else {}

            def col(field, *aliases):
                return km[field] if field in km else _col(cmap, *aliases)

            c_no = col("conduit_name", "NO.")
            c_size = col("conduit_size", "CONDUIT SIZE", "SIZE")
            c_type = col("conduit_type", "TYPE")
            c_cab = col("cable_number", "CABLE NUMBER", "CABLE")
            c_frm = col("source", "FROM")
            c_to = col("destination", "TO")
            c_rem = col("remarks", "REMARKS")
            cables = _extract_cable_schedule(page)

            for row in t[hr + 1:]:
                def g(i):
                    return _clean_cell(row[i]) if i is not None and i < len(row) else ""
                name = g(c_no)
                if not name or _norm_hdr(name) in ("NO.", "CONDUIT SCHEDULE"):
                    continue
                flags = []
                if c_type is None:
                    flags.append("no_conduit_type_column")
                # endpoints: prefer the cable schedule (cleaner) when this conduit
                # has routed cables; fall back to the (often bleed-prone) row values
                routed = cables.get(name, [])
                frm_raw, to_raw = g(c_frm), g(c_to)
                if routed:
                    frm_raw = routed[0]["frm"] or frm_raw
                    to_raw = routed[0]["to"] or to_raw
                if _looks_garbled(frm_raw) or _looks_garbled(to_raw):
                    flags.append("endpoint_bleed")
                # fill: one group per routed cable (conductor groups only)
                fill = _cable_fill_rows(routed)
                if infer_symbols:
                    _attach_symbols(fill, frm_raw, to_raw)
                if fill:
                    flags.append("fill_terminations_manual_review")
                elif routed:
                    flags.append("fill_unparsed")
                # conduit type: prefer a learned normalization, else the built-in
                raw_type = g(c_type)
                kb_t = kb.normalize_value("conduit_type", raw_type) if kb else None
                ctype = kb_t.value if (kb_t and kb_t.value) else _norm_ctype(raw_type)
                rec = {
                    "name": name,
                    "ctype": ctype,
                    "size": g(c_size),
                    "source": _split_endpoint(frm_raw),
                    "dest": _split_endpoint(to_raw),
                    "fill": fill,
                    "docs": [],
                    "wires": [],
                    "remarks": g(c_rem),
                    "flags": flags,
                }
                results.append(rec)

    # merge duplicate conduit names across pages
    merged, order = {}, []
    for rec in results:
        k = rec["name"]
        if k not in merged:
            merged[k] = rec
            order.append(k)
        else:
            merged[k]["fill"].extend(rec["fill"])
    return [merged[k] for k in order]


def derive_conduits_from_cables(path, infer_symbols=True):
    """Extract conduits when there is NO conduit schedule, by reading the CABLE
    SCHEDULE: every distinct value in its ROUTING column is a conduit. Endpoints
    come from the routed cables; conduit size/type are unknown (flagged). This is
    the fallback for sheets that list cables (with routing) but no conduit table.
    """
    by_routing = {}
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            u = (page.extract_text() or "").upper()
            if "CABLE SCHEDULE" not in u and "CABLE AND CONDUIT" not in u:
                continue
            for rt, entries in _extract_cable_schedule(page).items():
                by_routing.setdefault(rt, []).extend(entries)

    # a real conduit appears in the ROUTING column but not as a FROM/TO endpoint;
    # a panel/equipment tag that leaks into routing WILL appear as an endpoint —
    # exclude those so we don't mint bogus conduits from mis-parsed cells.
    endpoints = set()
    for entries in by_routing.values():
        for e in entries:
            for v in (e.get("frm"), e.get("to")):
                v = _clean_cell(v)
                if v:
                    endpoints.add(v.upper())

    results, order = {}, []
    for rt, entries in by_routing.items():
        name = _clean_cell(rt)
        if not name or _looks_garbled(name) or name.upper() in endpoints:
            continue
        frm = next((e["frm"] for e in entries if e.get("frm")), "")
        to = next((e["to"] for e in entries if e.get("to")), "")
        fill = _cable_fill_rows(entries)
        if infer_symbols:
            _attach_symbols(fill, frm, to)
        flags = ["derived_from_cable_schedule", "no_conduit_size", "no_conduit_type"]
        if fill:
            flags.append("fill_terminations_manual_review")
        if _looks_garbled(frm) or _looks_garbled(to):
            flags.append("endpoint_bleed")
        rec = {"name": name, "ctype": "XXX", "size": "",
               "source": _split_endpoint(frm), "dest": _split_endpoint(to),
               "fill": fill, "docs": [], "wires": [], "flags": flags}
        if name not in results:
            results[name] = rec
            order.append(name)
        else:
            results[name]["fill"].extend(fill)
    return [results[k] for k in order]


def extract_conduits(path, mode="auto", infer_symbols=True):
    """Best-effort conduit extraction with a selectable mode.
    mode: 'auto' (drawings -> conduit schedule -> derive-from-cables),
          'drawings', 'schedule', or 'cables'."""
    if mode in ("auto", "drawings"):
        recs = parse_pdf(path)
        if recs:
            return recs, "drawings"
        if mode == "drawings":
            return [], "none"
    if mode in ("auto", "schedule"):
        recs = parse_schedule_pdf(path, infer_symbols=infer_symbols)
        if recs:
            return recs, "conduit_schedule"
        if mode == "schedule":
            return [], "none"
    if mode in ("auto", "cables"):
        recs = derive_conduits_from_cables(path, infer_symbols=infer_symbols)
        if recs:
            return recs, "derived_from_cables"
    return [], "none"


if __name__ == "__main__":
    import sys, json
    mode = "--schedule" if "--schedule" in sys.argv else "--drawings"
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fn = args[0]
    recs = parse_schedule_pdf(fn) if mode == "--schedule" else parse_pdf(fn)
    print(f"parsed {len(recs)} records ({mode})")
    for r in recs:
        print(json.dumps(r, indent=1, ensure_ascii=False))
