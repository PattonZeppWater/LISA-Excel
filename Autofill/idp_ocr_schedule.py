"""
idp_ocr_schedule.py — read a conduit schedule off a VECTOR PDF sheet by
rendering just the table region at high DPI and OCR-ing it, then binning the
recognized text into the schedule's columns.

Why this works when whole-page OCR fails: the schedule table is tiny relative to
the full sheet, so a full-page render starves each glyph of pixels. Cropping to
the table first and rendering it large (≈500 DPI) gives clean glyphs — measured
~90% conduit-ID recovery vs <10% full-page. Fully offline: no API, no internet.

Column model comes from the OCR'd HEADER row (ID# / FROM / TO / QNTY / SIZE /
TYPE / GND / NOTES), so the layout is learned per sheet, not hard-coded.
"""
import os
import re

_ENGINE = None

# canonical column order of an AIC conduit & cable schedule, left→right, mapped
# to the grid-row fields idp_schedule.rows_to_records consumes
_FIELDS = ["name", "src", "dst", "cdt_qty", "size", "ctype",
           "cond_qty", "cond_gauge", "gnd", "cable_qty", "cable_type", "notes"]

# header tokens (regex) in the same left→right order, to anchor each column.
# The reader supports multiple real-world schedule SCHEMAS — it OCRs the header row
# and picks whichever schema its tokens match best, so a project with a different
# column layout (e.g. Moccasin MC-E-8) reads without any per-project tuning.
_HEADER_SEQ = [   # AIC / Crows E-3: cdt qty+size+type, cond qty+gauge+gnd, cable qty+type
    (r"ID|RACEWAY", "name"), (r"FROM|SOURCE|ORIGIN", "src"), (r"TO|DEST", "dst"),
    (r"QN?TY|QUAN", "cdt_qty"), (r"SIZE|TRADE|DIA", "size"), (r"TYPE|MATERIAL|MATL", "ctype"),
    (r"QN?TY|QUAN", "cond_qty"), (r"SIZE", "cond_gauge"), (r"GND|GROUND", "gnd"),
    (r"QN?TY|QUAN", "cable_qty"), (r"TYPE", "cable_type"), (r"NOTE", "notes"),
]
_HEADER_SEQ_MCE8 = [   # SFPUC / Moccasin MC-E-8: TYP, size+material combined, cables list
    (r"ID|RACEWAY", "name"), (r"TYP", "typ"), (r"FROM|SOURCE|ORIGIN", "src"), (r"\bTO\b|^TO|DEST", "dst"),
    (r"SIZE|TRADE|DIA", "size"), (r"CABLE", "cables"), (r"ROUT", "routing"),
    (r"COMMENT|NOTE", "notes"),
]
_SCHEMAS = [("e3", _HEADER_SEQ), ("mce8", _HEADER_SEQ_MCE8)]


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def _ocr_image(png):
    """Return fragments as dicts: cx, cy, x0,y0,x1,y1, text, conf (image px)."""
    res, _ = _engine()(png)
    frags = []
    for box, text, conf in (res or []):
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        frags.append({"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                      "cx": sum(xs) / 4.0, "cy": sum(ys) / 4.0,
                      "text": text.strip(), "conf": float(conf)})
    return frags


def _detect_table_bbox(page):
    """Find the conduit-schedule table's bounding box (PDF pts) from the vector
    grid: the widest band of stacked horizontal rules sharing an x-span. Falls
    back to None (caller must supply a clip)."""
    H = []
    for dr in page.get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                p, q = it[1], it[2]
                if abs(p.y - q.y) < 0.6 and abs(p.x - q.x) > 60:
                    H.append((min(p.x, q.x), max(p.x, q.x), (p.y + q.y) / 2.0))
    if len(H) < 8:
        return None
    # cluster horizontal rules by near-identical x-span (same table)
    H.sort(key=lambda r: (round(r[0] / 10), round(r[1] / 10)))
    best, cur = [], [H[0]]
    for r in H[1:]:
        if abs(r[0] - cur[-1][0]) < 12 and abs(r[1] - cur[-1][1]) < 12:
            cur.append(r)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [r]
    if len(cur) > len(best):
        best = cur
    if len(best) < 8:
        return None
    x0 = min(r[0] for r in best); x1 = max(r[1] for r in best)
    y0 = min(r[2] for r in best); y1 = max(r[2] for r in best)
    import fitz
    return fitz.Rect(x0 - 2, y0 - 4, x1 + 2, y1 + 4)


def _header_centers(frags, schemas=None):
    """Find the header band and return ordered [(field, cx)] for the columns of the
    best-matching schema, interpolating the x-center of any the OCR missed. Plus
    header_y. Tries each schema in `schemas` (default _SCHEMAS) and keeps whichever
    the header row hits — so a caller can pass the cable-schedule schemas instead."""
    schemas = schemas or _SCHEMAS
    cand = [f for f in frags if re.search(
        r"\b(ID|TYP|FROM|TO|QN?TY|QUAN|SIZE|TYPE|CABLE|ROUT|GND|GROUND|NOTE|COMMENT"
        r"|INSUL|CONDUCTOR|TAG"
        r"|RACEWAY|SOURCE|ORIGIN|DEST|TRADE|DIA|MATERIAL|MATL)\b",   # header aliases
        f["text"].upper())]
    if len(cand) < 4:
        return None
    # Cluster candidates into cy-bands (rows), then pick the band+schema with the most
    # matched columns. This beats a median-cy guess: data cells that happen to contain
    # a header word (e.g. "EXPOSED/SURFACE MOUNT TO" matching \bTO\b) form their own
    # low-scoring bands and can't drag the header row off the real column-title band.
    cand.sort(key=lambda f: f["cy"])
    bands, cur = [], [cand[0]]
    for f in cand[1:]:
        if f["cy"] - cur[-1]["cy"] > 30:
            bands.append(cur); cur = [f]
        else:
            cur.append(f)
    bands.append(cur)
    best = None                                       # (nmatched, matched, seq, hy)
    _min_cols = 4 if schemas is not _SCHEMAS else 5   # cable schema has fewer columns
    for band in bands:
        hdr = sorted(band, key=lambda f: f["cx"])
        hy = sorted(f["cy"] for f in band)[len(band) // 2]
        for _sname, seq in schemas:
            matched, si = {}, 0
            for f in hdr:
                t = f["text"].upper()
                while si < len(seq):
                    pat, field = seq[si]
                    if re.search(pat, t):
                        matched.setdefault(field, f["cx"]); si += 1
                        break
                    si += 1
            if len(matched) >= _min_cols and (best is None or len(matched) > best[0]):
                best = (len(matched), matched, seq, hy)
    if best is None:
        return None
    _nm, matched, seq, hy = best
    # interpolate any missing field's center from known neighbours by index
    order = [field for _, field in seq]
    known = [(i, matched[order[i]]) for i in range(len(order)) if order[i] in matched]
    centers = []
    for i, fld in enumerate(order):
        if fld in matched:
            centers.append((fld, matched[fld])); continue
        left = max((k for k in known if k[0] < i), default=None)
        right = min((k for k in known if k[0] > i), default=None)
        if left and right:
            cx = left[1] + (right[1] - left[1]) * (i - left[0]) / (right[0] - left[0])
        elif left:
            cx = left[1] + (i - left[0]) * 60
        elif right:
            cx = right[1] - (right[0] - i) * 60
        else:
            continue
        centers.append((fld, cx))
    return centers, hy


def _group_rows(frags, header_y):
    """Group data fragments (below the header) into rows by y-gaps."""
    data = sorted([f for f in frags if f["cy"] > header_y + 8], key=lambda f: f["cy"])
    if not data:
        return []
    # typical row height from median vertical gap
    rows, cur = [], [data[0]]
    for f in data[1:]:
        if f["cy"] - cur[-1]["cy"] > 10:            # new row band
            rows.append(cur); cur = [f]
        else:
            cur.append(f)
    rows.append(cur)
    return rows


def _assign(row_frags, centers):
    """Assign each fragment to the nearest column center; join per cell in x-order."""
    cells = {field: [] for field, _ in centers}
    confs = {field: [] for field, _ in centers}
    for f in sorted(row_frags, key=lambda f: f["cx"]):
        field = min(centers, key=lambda c: abs(f["cx"] - c[1]))[0]
        cells[field].append(f["text"]); confs[field].append(f["conf"])
    row = {field: " ".join(cells[field]).strip() for field, _ in centers}
    row["_conf"] = {field: (min(confs[field]) if confs[field] else 1.0)
                    for field, _ in centers}
    return row


def _locate_clip(page, log=None):
    """Auto-region for the conduit schedule: the upper-left of the sheet, where
    AIC schedules sit — excludes the right title block and the (lower) panelboard
    schedule. A fixed generous region is far more robust than trying to bound the
    table exactly on a text-less vector sheet; the row parser drops any non-
    conduit rows that sneak in, so over-inclusion is harmless."""
    import fitz
    W, H = page.rect.width, page.rect.height
    # y to 0.82 covers longer schedules (Moccasin MC-E-8 runs to ~0.82H, 67 rows); the
    # row parser drops any non-conduit rows that sneak in, so over-inclusion is harmless.
    return fitz.Rect(0, H * 0.015, W * 0.64, H * 0.82)


def _finalize_mce8(rows):
    """Post-process MC-E-8 rows: split the combined SIZE cell (`2" PVC`) into a trade
    size + material `ctype`, and fold the CABLES-IN-CONDUIT list + ROUTING TYPE into
    the notes so they reach Phase 1b (the conductor specs live on the cable schedule)."""
    for row in rows:
        sz = str(row.get("size") or "").strip()
        # trade size may be a mixed fraction WITH a space/hyphen ("2 1/2\"", "1-1/4\""),
        # a bare fraction ("3/4\""), or a decimal ("1.25\"") — capture the whole size, then the
        # trailing material. (The old [\d./]+ regex split "2 1/2\" PVC" into size="2".)
        m = re.match(r'(\d+(?:[\s\-]\d+/\d+|/\d+|\.\d+)?\s*"?)\s*(.*)$', sz)
        if m:
            row["size"] = re.sub(r"\s+", " ", m.group(1).strip())
            mat = re.sub(r"[^A-Z0-9]", "", m.group(2).upper())
            if "PVC" in mat:
                row["ctype"] = "PVC"
            elif "GRC" in mat or "RIGID" in mat or "RGS" in mat:
                row["ctype"] = "RGS"
            elif "RMC" in mat:
                row["ctype"] = "RMC"
            elif mat:
                row["ctype"] = m.group(2).strip()   # keep an unknown material verbatim
            else:
                row["ctype"] = "XXX"
        parts = []
        if str(row.get("cables") or "").strip():
            parts.append("CABLES: " + str(row["cables"]).strip())
        if str(row.get("routing") or "").strip():
            parts.append(str(row["routing"]).strip())
        if str(row.get("notes") or "").strip():
            parts.append(str(row["notes"]).strip())
        row["notes"] = "  ".join(parts)
    return rows


def _repair_ids(rows):
    """Fix OCR digit slips in the conduit ID using the schedule's structure:
    conduit numbers are small (≤ ~40 here) and run sequentially within a prefix
    (H001,H002,… L001,L002,…). An implausibly large number (e.g. 600 read for 009)
    is snapped to the running sequence — a B/C/D suffix reuses the prior base, an
    unsuffixed row takes prior+1. Corrected cells are marked low-confidence so
    they still surface for a glance. Rows must be in schedule (y) order."""
    last = {}
    for row in rows:
        m = re.match(r"^([A-Za-z]+)(\d+)([A-Za-z]?)$", str(row.get("name", "")))
        if not m:
            continue
        pre, num, suf = m.group(1).upper(), int(m.group(2)), m.group(3).upper()
        if num > 40:                                   # implausible → OCR digit slip
            prev = last.get(pre, 0)
            num = prev if suf in ("B", "C", "D") else prev + 1
            row["name"] = f"{pre}{num:03d}{suf}"
            row.setdefault("_conf", {})["name"] = min(row.get("_conf", {}).get("name", 1.0), 0.55)
        last[pre] = num
    return rows


def _repair_names(rows):
    """Fix OCR misreads in the FROM/TO equipment-name columns by self-consistency:
    the same equipment recurs across the schedule (PLC, MCC1-SEC.n, PANEL L, the
    vaults, the pumps …), so the majority spelling is the canonical one. A rare
    variant that's a near-match of a frequent name is snapped to it (e.g.
    `1000A M5B`→`1000A MSB`, `NEW T.1.D. XFMER`→`NEW T.I.D. XFMER`). This lifts
    both the description AND the downstream symbol inference, which keys off the
    device name. Snapped cells are marked low-confidence."""
    import difflib
    from collections import Counter
    vals = Counter()
    for r in rows:
        for k in ("src", "dst"):
            v = str(r.get(k) or "").strip()
            if len(v) >= 3:
                vals[v] += 1
    canon = [v for v, c in vals.items() if c >= 2] or list(vals)

    def alpha(s):                                     # letters only, for skeleton compare
        return re.sub(r"[^A-Za-z]", "", s).upper()

    for r in rows:
        for k in ("src", "dst"):
            v = str(r.get(k) or "").strip()
            if not v or vals.get(v, 0) >= 2:
                continue                              # already a frequent/canonical form
            for hit in difflib.get_close_matches(v, canon, n=3, cutoff=0.86):
                # NEVER change a distinguishing number (SEC. 2 must not become
                # SEC. 1, PMP-P-01 not -02): the identifying digit groups must be
                # identical — only alphabetic OCR slips (S↔5, I↔1) get corrected.
                if re.findall(r"\d+", hit) != re.findall(r"\d+", v):
                    continue
                if alpha(hit) == alpha(v) or difflib.SequenceMatcher(None, v, hit).ratio() >= 0.9:
                    r[k] = hit
                    r.setdefault("_conf", {})[k] = min(r.get("_conf", {}).get(k, 1.0), 0.6)
                    break
    return rows


def _normalize_text(rows):
    """Clean OCR punctuation/spacing noise in the equipment-name cells so the
    descriptions match the clean-schedule convention — WITHOUT touching meaning.
    Only three surgical rules, each safe against unit names and decimals:
      • en/em dash → hyphen  (ZS-01—A → ZS-01-A)
      • space after a letter-period-digit  (SEC.2 → SEC. 2; leaves 1.5 / 150.0 alone)
      • single spaces around '@'  (@EYE → @ EYE)
    Never inserts a letter↔digit space (would break MCC1, H2202, ATS1000A)."""
    def norm(s):
        if not s:
            return s
        s = s.replace("–", "-").replace("—", "-")
        s = re.sub(r"(?<=[A-Za-z])\.(?=\d)", ". ", s)
        s = re.sub(r"\s*@\s*", " @ ", s)
        return re.sub(r"\s{2,}", " ", s).strip()
    for r in rows:
        for k in ("src", "dst", "notes"):
            if r.get(k):
                r[k] = norm(str(r[k]))
    return rows


# canonical NEC conduit trade sizes (inches), fractional form. A recognized size is
# left EXACTLY as the drawing wrote it (decimal or fraction) — we only add a missing
# inch-mark; an UN-recognized size is flagged low-confidence so it surfaces for review.
_TRADE_SIZES = {"1/2", "3/4", "1", "1-1/4", "1-1/2", "2", "2-1/2", "3",
                "3-1/2", "4", "4-1/2", "5", "6"}
_DEC_TO_FRAC = {"0.5": "1/2", ".5": "1/2", "0.75": "3/4", ".75": "3/4",
                "1.25": "1-1/4", "1.5": "1-1/2", "2.5": "2-1/2", "3.5": "3-1/2",
                "4.5": "4-1/2"}


def _canon_trade_size(s):
    """(canonical_size, is_recognized). Accepts decimal ('1.5\"') or fractional
    ('1-1/2\"', '2 1/2\"') notation and recognizes both as the same trade size,
    WITHOUT rewriting the drawing's own number (so the workbook keeps matching the
    source). Only adds a missing inch-mark on a numeric size."""
    raw = str(s or "").strip()
    if not raw:
        return raw, True                              # empty handled elsewhere
    core = raw.replace('"', "").replace("”", "").replace("″", "").strip()
    key = _DEC_TO_FRAC.get(core, core.replace(" ", "-"))
    recognized = key in _TRADE_SIZES
    out = raw
    if recognized and re.fullmatch(r'[\d./\- ]+', core) and '"' not in raw:
        out = core + '"'                              # 1.25 -> 1.25"  (number unchanged)
    return out, recognized


def read_schedule(pdf_path, page_idx, clip=None, dpi=500, low_conf=0.80, refine=False,
                  log=None, schemas=None):
    """OCR a conduit-schedule table off a vector sheet.

    clip: fitz.Rect in PDF points bounding the table (auto-detected if None).
    schemas: header-schema list to try (default the conduit _SCHEMAS); pass the
    cable-schedule schemas to read an MC-E-9 cable list with the same machinery.
    Returns (rows, meta) where rows are dicts keyed by _FIELDS plus a per-cell
    `_conf` map, and meta reports the clip used and low-confidence cell count.
    """
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[max(0, min(page_idx, doc.page_count - 1))]
    full = fitz.Rect(0, 0, page.rect.width, page.rect.height)
    if clip is None:
        clip = _locate_clip(page, log=log) or _detect_table_bbox(page) or full
    import PIL.Image
    PIL.Image.MAX_IMAGE_PIXELS = None
    # CLIP ATTEMPTS: try the auto/region crop first; if no header is found there, fall
    # back to the WHOLE sheet — a schedule positioned on the right or lower part of the
    # sheet (outside the usual upper-left block) is otherwise missed entirely. The full
    # sheet is added only when it fits the pixel budget (a giant ARCH sheet would blow
    # MuPDF's limit), and only when the crop actually fails — a sheet that reads on the
    # crop never renders full-page, so this cannot change a project that already works.
    def _fits_at(rect, d):
        return (rect.width / 72.0 * d) * (rect.height / 72.0 * d) < 130_000_000
    def _fit_dpi(rect, want):
        # Largest DPI <= want whose render stays under the pixel budget. A crop bigger than
        # the budget (a huge ARCH-E sheet) would otherwise throw MuPDF's "overly large image"
        # on the FIRST render and silently fall through to INCOMPLETE; clamping renders it a
        # touch coarser instead. Never raises a small crop (Moccasin fits at 500 -> unchanged).
        d = want
        while d > 200 and not _fits_at(rect, d):
            d -= 50
        return d
    clip_attempts = [clip]
    if clip != full and _fits_at(full, dpi):
        clip_attempts.append(full)
    frags = centers = hy = None
    # DPI LADDER (per clip): render at the requested DPI first (the happy path). ONLY if
    # the header row can't be located do we re-render finer — tiny type that starves
    # glyphs at 500 DPI often resolves at 650/800. Finer rungs are added only under the
    # same pixel budget (an ARCH-E sheet at 800 DPI blows MuPDF's "overly large image").
    for _clip in clip_attempts:
        base = _fit_dpi(_clip, dpi)                   # clamp base so a huge crop can't throw
        ladder = [base] + [d for d in (650, 800) if d > base + 40 and _fits_at(_clip, d)]
        for i, d in enumerate(ladder):
            if log:
                where = "table crop" if _clip is not full else "FULL sheet (crop had no header)"
                tail = "" if i == 0 else " (retry finer — header not found)"
                log(f"OCR: rendering {where} {tuple(round(c) for c in _clip)} at {d} DPI …{tail}")
            try:
                pix = page.get_pixmap(dpi=d, clip=_clip)
                tmp = os.path.join(os.environ.get("TEMP", "."), "idp_ocr_table.png")
                pix.save(tmp)
                fr = _ocr_image(tmp)
            except Exception as e:
                if log:
                    log(f"OCR: render at {d} DPI skipped ({e}).")
                continue
            for f in fr:                              # unify curly quotes / primes
                f["text"] = (f["text"].replace("”", '"').replace("“", '"').replace("’", "'")
                             .replace("′", "'").replace("″", '"').replace("‘", "'"))
            hdr = _header_centers(fr, schemas)
            if hdr:
                frags = fr
                centers, hy = hdr
                dpi = d                               # frag coords are in THIS render's px;
                clip = _clip                          # keep dpi+clip in sync for refine pass
                break
        if centers:
            break
    if not centers:
        raise ValueError("OCR could not find the schedule header row (ID/FROM/TO/…). "
                         "Try a tighter crop around the table.")
    if log:
        log(f"OCR: {len(frags)} text fragments recognized.")
    rows, low = [], 0
    bx0 = by1 = None
    bx1 = -1e9
    for rf in _group_rows(frags, hy):
        row = _assign(rf, centers)
        name = row.get("name", "").strip().replace(" ", "")
        # a suffix letter (A/B/C/D) often lands as the first token of FROM —
        # stitch it back onto the conduit ID (e.g. L005 + "A PANEL L" → L005A)
        m = re.match(r"^([A-Da-d])\s+(\S.*)$", row.get("src", "") or "")
        if m and re.match(r"^[A-Z]{1,3}-?\d+$", name.upper()):
            name += m.group(1).upper()
            row["src"] = m.group(2).strip()
        if not re.match(r"^[A-Z]{1,3}-?\d", name.upper()):   # allow a tag separator (K-001)
            continue                                  # not a real conduit row
        row["name"] = name
        low += sum(1 for f in _FIELDS if row.get("_conf", {}).get(f, 1.0) < low_conf
                   and str(row.get(f, "")).strip())
        rows.append(row)
        for f in rf:                                  # track extent of real rows
            bx0 = f["x0"] if bx0 is None else min(bx0, f["x0"])
            bx1 = max(bx1, f["x1"])
            by1 = f["y1"] if by1 is None else max(by1, f["y1"])

    _schema_fields = {f for f, _ in centers}
    if "type_insul" in _schema_fields:
        pass                          # MC-E-9 cable schedule: keep size + type verbatim
    elif ("cables" in _schema_fields) or ("typ" in _schema_fields):
        _finalize_mce8(rows)          # MC-E-8: split size, fold cables/routing → notes
    else:
        _repair_ids(rows)             # E-3-tuned (small sequential IDs); skip other schemas
    _repair_names(rows)
    _normalize_text(rows)

    # trade-size sanity: normalize a missing inch-mark on a RECOGNIZED trade size
    # (keeping the drawing's own number), and flag any UN-recognized size low-
    # confidence so a genuinely-garbled read surfaces in the uncertainties review.
    # Cable schedules carry conductor sizes (#12, 500 MCM), not conduit trade sizes —
    # skip them so we never mis-flag those.
    if "type_insul" not in _schema_fields:
        for r in rows:
            sz = str(r.get("size") or "").strip()
            if not sz:
                continue
            canon, ok = _canon_trade_size(sz)
            r["size"] = canon
            if not ok:
                r.setdefault("_conf", {})["size"] = min(r.get("_conf", {}).get("size", 1.0), 0.5)

    # ── high-accuracy 2nd pass: re-crop TIGHT to where the rows actually landed
    # (from pass-1 fragment positions) and re-OCR at higher DPI. Reliable because
    # it uses measured positions, not guesses. Only worth it when the first pass
    # actually left low-confidence cells — a clean first pass needs no re-OCR, so we
    # skip it and save ~half the OCR time with no accuracy loss. ──
    if refine and low > 0 and rows and bx0 is not None:
        sc = 72.0 / dpi                               # crop-image px → PDF pt
        tight = fitz.Rect(clip.x0 + bx0 * sc - 6, clip.y0 + hy * sc - 22,
                          clip.x0 + bx1 * sc + 8, clip.y0 + by1 * sc + 8)
        if log:
            log("OCR: high-accuracy 2nd pass on tightened crop …")
        try:
            # never refine BELOW the DPI that actually located the header — if pass 1 had to
            # climb the ladder to 650/800, re-OCR at least that fine (a coarser 560 would
            # garble exactly the low-confidence cells the refine exists to fix).
            r2, m2 = read_schedule(pdf_path, page_idx, clip=tight, dpi=max(560, dpi),
                                   low_conf=low_conf, refine=False, log=log, schemas=schemas)
            if len(r2) >= len(rows):                  # keep the better pass
                return r2, m2
        except Exception as e:
            if log:
                log(f"OCR: 2nd pass skipped ({e}); using first pass.")

    meta = {"clip": tuple(round(c) for c in clip), "rows": len(rows),
            "low_conf_cells": low, "low_conf_threshold": low_conf}
    if log:
        log(f"OCR: parsed {len(rows)} conduit rows ({low} low-confidence cells to verify).")
    return rows, meta
