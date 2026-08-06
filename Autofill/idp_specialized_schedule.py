"""
idp_specialized_schedule.py — OPT-IN offline reader for a conduit/cable schedule that is
EMBEDDED in a busy plan sheet (site/deck plan) where the table is drawn as vector graphics
and the ordinary OCR/text readers can't isolate it from the surrounding notes.

Why a separate reader: on these sheets the schedule table, the general/key notes, and the
title block all share one text layer, so text-only clustering conflates notes with table
cells. The reliable signal is the DRAWN CELL GRID — the horizontal/vertical rules that box
the table. This reader:
  1. collects the drawn line-work and finds the densest rectangular lattice (the table),
  2. derives column boundaries (vertical rules) and row boundaries (horizontal rules),
  3. bins the exact VECTOR TEXT (get_text("words"), no OCR) into cells,
  4. maps the header row's cell titles to schedule fields (multi-line / multi-word titles
     like "CONDUIT\\nSIZE" are joined per cell, so stacked headers work),
  5. reads the data rows, accepting NUMERIC-only conduit IDs (1,2,3…) as well as tagged
     ones (C-001, K-12), which the standard readers reject.

HARD-GATED and NON-FABRICATING: it returns [] unless it confidently maps a conduit-schedule
header (an ID/NO column PLUS at least one of FROM / TO / SIZE). Every row it does return is
flagged low-confidence + "specialized_reader" so it surfaces for verification, and the caller
only ADOPTS its output when it beats the normal read — so a good project can never be made
worse. Fully offline: PyMuPDF only, no OCR, no API, no internet.
"""
import re

# Column-title → schedule field. Order = priority; multi-word titles are matched whole.
_FIELD_ALIASES = [
    ("name",    r"(CONDUIT\s*(NO|NUMBER|#|ID|TAG)|RACEWAY\s*(NO|NUMBER)?|^\s*NO\.?\s*$|^\s*ID\s*$|TAG)"),
    ("src",     r"(FROM|SOURCE|ORIGIN)"),
    ("dst",     r"(\bTO\b|DEST)"),
    ("size",    r"(SIZE|TRADE|DIA)"),
    ("ctype",   r"(TYPE|MATERIAL|MATL)"),
    ("gnd",     r"(GROUND|GND|EGC)"),
    ("cables",  r"(CABLE|CONDUCTOR)"),
    ("routing", r"(ROUT)"),
    ("notes",   r"(NOTE|COMMENT|REMARK)"),
]


def _collect_lines(page):
    """Horizontal rules [(x0,x1,y)] and vertical rules [(y0,y1,x)] from the vector drawing
    layer (both explicit lines and rectangle edges)."""
    H, V = [], []
    try:
        drawings = page.get_drawings()
    except Exception:
        return H, V
    for dr in drawings:
        for it in dr.get("items", []):
            t = it[0]
            if t == "l":
                p, q = it[1], it[2]
                if abs(p.y - q.y) < 0.8 and abs(p.x - q.x) >= 15:
                    H.append((min(p.x, q.x), max(p.x, q.x), (p.y + q.y) / 2.0))
                elif abs(p.x - q.x) < 0.8 and abs(p.y - q.y) >= 15:
                    V.append((min(p.y, q.y), max(p.y, q.y), (p.x + q.x) / 2.0))
            elif t == "re":
                r = it[1]
                H.append((r.x0, r.x1, r.y0)); H.append((r.x0, r.x1, r.y1))
                V.append((r.y0, r.y1, r.x0)); V.append((r.y0, r.y1, r.x1))
    return H, V


def _merge_close(vals, tol):
    """Collapse near-duplicate sorted coordinates to single boundaries."""
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
        else:
            out[-1] = (out[-1] + v) / 2.0
    return out


def _find_table(H, V):
    """Densest rectangular lattice: the widest STACK of horizontal rules sharing an x-span
    (rows), plus the vertical rules crossing that band (columns). Returns None if no lattice
    with >=4 columns and >=3 rows is found."""
    if len(H) < 4 or len(V) < 3:
        return None
    H = sorted(H, key=lambda r: (round(r[0] / 8), round(r[1] / 8)))
    best, cur = [], [H[0]]
    for r in H[1:]:
        if abs(r[0] - cur[-1][0]) < 18 and abs(r[1] - cur[-1][1]) < 18:
            cur.append(r)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [r]
    if len(cur) > len(best):
        best = cur
    if len(best) < 3:
        return None
    x0 = min(r[0] for r in best); x1 = max(r[1] for r in best)
    row_ys = _merge_close([r[2] for r in best], 4)
    y0, y1 = row_ys[0], row_ys[-1]
    span = max(1.0, y1 - y0)
    col_xs = _merge_close([v[2] for v in V
                           if x0 - 4 <= v[2] <= x1 + 4
                           and (min(v[1], y1) - max(v[0], y0)) > span * 0.4], 6)
    if len(col_xs) < 4 or len(row_ys) < 3:
        return None
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "cols": col_xs, "rows": row_ys}


def _words_in(page, x0, y0, x1, y1):
    """Vector-text words whose center lands inside the rectangle → dicts with coords."""
    out = []
    for w in page.get_text("words"):
        cx, cy = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
        if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
            t = w[4].strip()
            if t:
                out.append({"cx": cx, "cy": cy, "text": t})
    return out


def _cell_text(words, cx0, cy0, cx1, cy1):
    """Join words inside a cell, top→bottom then left→right (so a stacked title reads in
    order)."""
    inside = [w for w in words if cx0 <= w["cx"] < cx1 and cy0 <= w["cy"] < cy1]
    inside.sort(key=lambda w: (round(w["cy"] / 4), w["cx"]))
    return " ".join(w["text"] for w in inside).strip()


def _map_columns(titles):
    """Map each column's assembled title to a schedule field (first matching, no reuse)."""
    mapping = {}
    used = set()
    for ci, title in enumerate(titles):
        up = re.sub(r"\s+", " ", title.upper()).strip()
        if not up:
            continue
        for field, pat in _FIELD_ALIASES:
            if field in used:
                continue
            if re.search(pat, up):
                mapping[ci] = field
                used.add(field)
                break
    return mapping


_ID_RE = re.compile(r"^(?:[A-Z]{1,3}-?\d{1,4}[A-Z]?|\d{1,4})$")


def read_specialized(pdf_path, pages=None, log=None):
    """Read conduit rows from plan-embedded schedule table(s) using the drawn grid. Returns
    (records, method). Non-fabricating: [] unless a conduit-schedule header is confidently
    mapped. Records carry flags ['specialized_reader','specialized_low_confidence']."""
    _log = log or (lambda *a: None)
    try:
        import fitz
    except Exception:
        return [], "specialized-unavailable"
    try:
        import idp_schedule as _S
    except Exception:
        _S = None
    if pages is None:
        try:
            import idp_vision
            pages = idp_vision.find_schedule_pages(pdf_path) or []
        except Exception:
            pages = []
    if not pages:
        return [], "specialized-no-pages"
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return [], "specialized-open-fail"
    all_rows, seen = [], set()
    try:
        for pi in pages:
            if pi < 0 or pi >= doc.page_count:
                continue
            page = doc[pi]
            H, V = _collect_lines(page)
            tab = _find_table(H, V)
            if not tab:
                continue
            cols, rows = tab["cols"], tab["rows"]
            words = _words_in(page, tab["x0"], tab["y0"], tab["x1"], tab["y1"])
            if len(words) < len(cols):
                continue
            col_bounds = list(zip(cols[:-1], cols[1:]))       # (left,right) per column
            # header row = first inter-row band; its per-cell text titles the columns
            hy0, hy1 = rows[0], rows[1]
            titles = [_cell_text(words, cx0, hy0, cx1, hy1) for cx0, cx1 in col_bounds]
            colmap = _map_columns(titles)
            fields = set(colmap.values())
            # GATE: need an ID column AND at least one of FROM / TO / SIZE, else bail (no fab)
            if "name" not in fields or not ({"src", "dst", "size"} & fields):
                continue
            page_rows = 0
            for ri in range(1, len(rows) - 1):
                ry0, ry1 = rows[ri], rows[ri + 1]
                row = {}
                for ci, (cx0, cx1) in enumerate(col_bounds):
                    fld = colmap.get(ci)
                    if not fld:
                        continue
                    row[fld] = _cell_text(words, cx0, ry0, cx1, ry1)
                name = re.sub(r"\s+", "", str(row.get("name", "")).upper())
                if not _ID_RE.match(name):
                    continue                                  # not a conduit-ID row
                if name in ("", "NO", "ID"):
                    continue
                # normalize a bare numeric ID to a stable tag so downstream keys don't collide
                row["name"] = name if re.match(r"^[A-Z]", name) else f"C-{int(name):03d}"
                key = (pi, row["name"])
                if key in seen:
                    continue
                seen.add(key)
                row["_conf"] = {k: 0.5 for k in row}          # everything amber for review
                all_rows.append(row)
                page_rows += 1
            if page_rows:
                _log(f"Specialized reader: page {pi + 1} → {page_rows} conduit row(s) from the "
                     f"drawn table grid ({len(col_bounds)} columns).")
    finally:
        doc.close()
    if not all_rows:
        return [], "specialized-no-table"
    if _S is not None:
        recs = _S.rows_to_records(all_rows)
    else:
        recs = all_rows
    for rec in recs:
        fl = rec.setdefault("flags", [])
        for f in ("specialized_reader", "specialized_low_confidence"):
            if f not in fl:
                fl.append(f)
        rec["deviations"] = ((rec.get("deviations") or "")
                             + " [SPECIALIZED READER: plan-embedded schedule — verify every"
                               " cell against the sheet]").strip()
    return recs, "specialized-grid"
