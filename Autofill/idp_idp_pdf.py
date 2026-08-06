"""
idp_idp_pdf.py — reader for AIC *finished* Interconnection-Diagram IDP PDFs
===========================================================================
AIC's submitted IDP package (e.g. AIC73.1142_Prj_IDP_20260123.pdf) is a clean,
text-layer PDF: a drawing-index page listing every conduit, then one
interconnection-diagram page per conduit. Each diagram page is rotated 90°, so
the fill-summary table (headers FILL TYPE / SIZE / COLOR / QUANTITY) reads as a
set of horizontal y-bands, with each successive fill marching LEFT along x.

This module reads those pages back into the extractor's record shape so the
workbook can be repopulated to reproduce the finished drawings through LISA.
It is deliberately separate from `idp_extract` (raw conduit-schedule PDFs) and
`idp_wiring` (PLC I/O schematics) — this is the finished-IDP-package profile.

read_source(path) -> list[record]     # same contract idp_ingest expects
is_idp_package(path) -> bool           # cheap sniff for routing
"""
from __future__ import annotations

import re

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

# Fill-type abbreviations used on the drawings → nothing normalized here; the
# write pipeline (lisa_contract.normalize_types) owns the final dropdown mapping.
_TYPE_ABBR = {
    "PWR": "POWER", "CTRL": "CONTROL", "CONT": "CONTROL",
    "GND": "GROUND", "TSP": "TSP", "MFG": "MFG_CABLE", "MFG_CABLE": "MFG_CABLE",
    "FIBER": "FIBER", "CAT-6": "CAT-6", "CAT6": "CAT-6",
    "PULL": "PULL_ROPE", "PULL_ROPE": "PULL_ROPE",
}
_TYPE_TOKENS = set(_TYPE_ABBR) | set(_TYPE_ABBR.values())
_COLOR_TOKENS = {"BLK", "BLU", "GRN", "ORG", "RED", "WHT", "YEL", "BRN", "PRP",
                 "RED/BLK", "RED/WHT", "N/A", "TBD"}
_SIZE_RE = re.compile(r'^(#\d+|\d+/\d+|\d+"?|TBD)$')
_CONDUIT_RE = re.compile(r'^[A-Z]{1,2}\d{3}[A-Z]?(?:\([EN]\))?$')   # P001, C061, A071, H008A, C019(E)
_HEADER_WORDS = {"FILL", "TYPE", "SIZE", "COLOR", "QUANTITY"}


def is_idp_package(path):
    """Cheap sniff: a finished AIC IDP package has an INTERCONNECTION DIAGRAMS
    index and the FILL TYPE/SIZE/COLOR/QUANTITY table header."""
    if fitz is None:
        return False
    try:
        d = fitz.open(path)
    except Exception:
        return False
    try:
        hits = 0
        for i in range(min(len(d), 20)):
            t = d[i].get_text("text").upper()
            if "INTERCONNECTION DIAGRAM" in t:
                hits += 1
            if "FILL" in t and "QUANTITY" in t and "COLOR" in t:
                hits += 1
            if hits >= 2:
                return True
        return False
    finally:
        d.close()


def _cluster(xs, tol=6.0):
    """Cluster x-centers into columns; return sorted-descending cluster centers
    (drawing reads top-to-bottom fill order as decreasing x on the rotated page)."""
    xs = sorted(xs, reverse=True)
    cols, cur = [], []
    for x in xs:
        if cur and abs(cur[-1] - x) > tol:
            cols.append(sum(cur) / len(cur)); cur = []
        cur.append(x)
    if cur:
        cols.append(sum(cur) / len(cur))
    return cols


def _nearest(words, xc, tol=7.0):
    best, bd = None, tol
    for w in words:
        cx = (w[0] + w[2]) / 2.0
        if abs(cx - xc) <= bd:
            best, bd = w[4], abs(cx - xc)
    return best


def _parse_page_fills(page):
    """Return (conduit_name, conduit_type, conduit_size, fills, flags) for a
    diagram page, or None if it isn't one. fills = dicts type/gauge/color/count."""
    words = page.get_text("words")   # (x0,y0,x1,y1, word, blk, line, wno)
    if not words:
        return None
    # locate the stacked FILL/TYPE/SIZE/COLOR/QUANTITY header column
    hdr = {}
    for w in words:
        if w[4] in _HEADER_WORDS:
            hdr.setdefault(w[4], w)
    if not {"TYPE", "SIZE", "COLOR", "QUANTITY"} <= set(hdr):
        return None
    hdr_x = hdr["TYPE"][0]
    y_type, y_qty = hdr["TYPE"][1], hdr["QUANTITY"][1]
    bands = {"type": hdr["TYPE"][1], "size": hdr["SIZE"][1],
             "color": hdr["COLOR"][1], "count": hdr["QUANTITY"][1]}
    # data words sit just LEFT of the header x (table ~96px wide) and within the
    # summary band vertically — restrict to the header span so the terminal-detail
    # words elsewhere on the sheet can't leak in. Data y0 trails its header y0 by
    # up to ~12px, so use an asymmetric window keyed off each header's y0.
    y_lo, y_hi = y_type - 14, y_qty + 30

    def band_words(hy):
        return [w for w in words
                if (hy - 10) <= w[1] <= (hy + 26)
                and (hdr_x - 96) < w[0] < (hdr_x - 2)
                and y_lo <= w[1] <= y_hi]

    tw = band_words(bands["type"]); sw = band_words(bands["size"])
    cw = band_words(bands["color"]); qw = band_words(bands["count"])
    # fill columns = x-clusters over the TYPE band (keep TBD/blank cells too so a
    # per-utility service row like P001 survives)
    cols = _cluster([(w[0] + w[2]) / 2.0 for w in tw])

    # conduit name/type/size — first try the value nearest the NAME:/TYPE:/SIZE:
    # labels (rotation-agnostic; handles index-less packages like San Rafael whose
    # names are S1/E05 in labeled fields), then fall back to the linear Pxxx layout.
    lines = [ln.strip() for ln in page.get_text("text").splitlines() if ln.strip()]
    name = ctype = size = ""
    _CID = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]?(?:\([EN]\))?$")   # + optional (E)/(N) existing/new

    def _nearest_to_label(label, pred):
        lbl = next((w for w in words if w[4].upper().rstrip(":") == label), None)
        if not lbl:
            return ""
        lx, ly = (lbl[0] + lbl[2]) / 2.0, (lbl[1] + lbl[3]) / 2.0
        cands = sorted(((((w[0] + w[2]) / 2 - lx) ** 2 + ((w[1] + w[3]) / 2 - ly) ** 2), w[4])
                       for w in words if w is not lbl and pred(w[4]))
        return cands[0][1] if cands else ""

    name = _nearest_to_label("NAME", lambda t: bool(_CID.match(t)))
    if name:
        ctype = _nearest_to_label("TYPE", lambda t: bool(re.match(r"^(PVC|RMC|RGS|GRC|EMT|LFMC|IMC)", t.upper())))
        size = _nearest_to_label("SIZE", lambda t: bool(re.match(r'^\d', t)) and ('"' in t or "/" in t or t.replace('.', '').isdigit()))
    if not name:
        for i, ln in enumerate(lines):
            if _CONDUIT_RE.match(ln):
                name = ln
                ctype = lines[i + 1] if i + 1 < len(lines) else ""
                size = lines[i + 2] if i + 2 < len(lines) else ""
                break

    fills, flags = [], []
    for xc in cols:
        t = (_nearest(tw, xc) or "").upper()
        cnt = _nearest(qw, xc)
        try:
            count = int(re.sub(r"\D", "", cnt)) if cnt and re.search(r"\d", cnt) else 0
        except ValueError:
            count = 0
        if t in _TYPE_TOKENS:
            typ = _TYPE_ABBR.get(t, t)
        elif count and (t in ("TBD", "") or t in _COLOR_TOKENS):
            # per-utility / undetermined cell — infer from the conduit series
            typ = "POWER" if name[:1] == "P" else "CONTROL"
            flags.append(f"{name}: fill type undetermined on drawing "
                         f"('{t or 'blank'}') — inferred {typ}")
        else:
            continue
        fills.append({"type": typ,
                      "gauge": (_nearest(sw, xc) or "").strip(),
                      "color": (_nearest(cw, xc) or "").strip().upper(),
                      "count": max(count, 1)})
    if not name and not fills:
        return None
    return name, ctype, size, fills, flags


def _group_fills(fills):
    """Collapse the drawing's per-conductor fill rows into LISA wire groups
    (Type + Wire Ct + colors), matching the shape idp_excel produces. Consecutive
    same-type/same-gauge POWER phase rows (each qty 1, BRN/ORG/YEL) become ONE
    POWER group of Wire Ct = #phases; other rows keep their quantity as Wire Ct.
    Grounds stay their own group (the write pipeline's normalize_types /
    ensure_ground own the GND encoding)."""
    groups, i = [], 0
    while i < len(fills):
        f = fills[i]
        t, g = f["type"], f["gauge"]
        run = [f]; j = i + 1
        while (t == "POWER" and j < len(fills)
               and fills[j]["type"] == "POWER" and fills[j]["gauge"] == g):
            run.append(fills[j]); j += 1
        if len(run) > 1:
            colors = []
            for r in run:
                colors += [r["color"]] * max(r["count"], 1)
            groups.append({"type": t, "gauge": g, "wire_ct": len(colors),
                           "count": len(colors), "colors": [c for c in colors if c]})
            i = j
        else:
            cnt = max(f["count"], 1)
            groups.append({"type": t, "gauge": g, "wire_ct": cnt, "count": cnt,
                           "colors": [f["color"]] * cnt if f["color"] else []})
            i += 1
    return groups


def _index_pairs(doc):
    """From the drawing-index page, map conduit_name -> (source, destination)
    using the 'Pxxx SOURCE... DEST...' description lines."""
    pairs = {}
    for i in range(len(doc)):
        t = doc[i].get_text("text")
        if "DWG DESCRIPTION" not in t.upper() and "DRAWING INDEX" not in t.upper():
            continue
        for ln in t.splitlines():
            m = re.match(r'^([A-Z]{1,2}\d{3}[A-Z]?(?:\([EN]\))?)\s+(.*)$', ln.strip())
            if m:
                pairs[m.group(1)] = m.group(2).strip()
    return pairs


def _page_header_only(page):
    """Fallback for a diagram page whose fill-TABLE didn't parse (non-standard/graphical
    fill block): pull JUST the conduit NAME/TYPE/SIZE from their labels, rotation-agnostic.
    Returns (name, ctype, size); name is '' unless it matches the conduit-id pattern, so
    index / legend / title pages (which have no conduit-id NAME value) never leak in."""
    words = page.get_text("words")
    if not words:
        return "", "", ""
    _CID = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]?(?:\([EN]\))?$")

    def near(label, pred):
        lbl = next((w for w in words if w[4].upper().rstrip(":") == label), None)
        if not lbl:
            return ""
        lx, ly = (lbl[0] + lbl[2]) / 2.0, (lbl[1] + lbl[3]) / 2.0
        cands = sorted(((((w[0] + w[2]) / 2 - lx) ** 2 + ((w[1] + w[3]) / 2 - ly) ** 2), w[4])
                       for w in words if w is not lbl and pred(w[4]))
        return cands[0][1] if cands else ""

    name = near("NAME", lambda t: bool(_CID.match(t)))
    if not name:
        return "", "", ""
    ctype = near("TYPE", lambda t: bool(re.match(r"^(PVC|RMC|RGS|GRC|EMT|LFMC|IMC)", t.upper())))
    size = near("SIZE", lambda t: bool(re.match(r'^\d', t))
                and ('"' in t or "/" in t or t.replace('.', '').isdigit()))
    return name, ctype, size


def read_source(path):
    """Parse a finished AIC IDP package PDF into extractor records."""
    if fitz is None:
        return []
    doc = fitz.open(path)
    try:
        idx = _index_pairs(doc)
        recs, seen = [], set()
        for i in range(len(doc)):
            parsed = _parse_page_fills(doc[i])
            if parsed:
                name, ctype, size, fills, flags = parsed
            else:
                # RECOVERY — a diagram page the fill-table parser couldn't read. Keep the
                # conduit (name/type/size) rather than silently dropping it; flag its fill
                # for review. Gated to a real conduit-id NAME + a type/size, so non-diagram
                # pages don't leak in.
                name, ctype, size = _page_header_only(doc[i])
                fills, flags = [], ["idp_fill_table_not_parsed"]
                # Recover ONLY a conduit that is in the drawing INDEX. Real conduits are
                # indexed; stray component/wire labels (RJ45, GND2, …) that also match the id
                # pattern are not. Require a non-empty index, so index-less packages get NO
                # recovery here (their pages are already handled by _parse_page_fills's
                # index-less path) — that's what was leaking junk on index-less IDPs.
                if not (name and idx and name in idx):
                    name = ""
            if not name or name in seen:
                continue
            if not fills and not (ctype or size):
                continue        # nothing usable on this page
            if not fills and "idp_fill_table_not_parsed" not in flags:
                flags = list(flags or []) + ["idp_fill_empty"]  # parsed page, fill not read
            seen.add(name)
            desc = idx.get(name, "")
            rec = {"name": name, "ctype": ctype, "size": size,
                   "source": [desc] if desc else [], "dest": [],
                   "fill": [], "_src_page": i + 1,
                   # a finished IDP is the authoritative record of its own fills:
                   # the write pipeline must not synthesize grounds or re-merge
                   # analog pairs on top of what AIC actually drew.
                   "fill_authoritative": True}
            if flags:
                rec["flags"] = list(flags)
            rec["fill"] = _group_fills(fills)
            recs.append(rec)
        return recs
    finally:
        doc.close()


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for p in sys.argv[1:]:
        print(f"### {p}  package={is_idp_package(p)}")
        for r in read_source(p):
            print(f"  {r['name']:6} {r['ctype']:10} {r['size']:5} "
                  f"src={r['source']}")
            for f in r["fill"]:
                print(f"        {f['type']:9} {f['gauge']:5} Ct{f['wire_ct']} "
                      f"{'/'.join(f.get('colors') or [])}")
        for r in read_source(p):
            for fl in r.get("flags", []):
                print("   FLAG:", fl)
