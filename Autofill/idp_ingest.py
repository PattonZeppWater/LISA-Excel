"""
idp_ingest.py — one entry point that ingests any supported source into records.

Routes each file by extension:
  .pdf              -> idp_extract.extract_conduits (schedule/drawings/cables)
  .xlsx/.xlsm/.xls  -> idp_excel.read_source (IDP workbook or conduit list)

Records from all sources are merged (dedup by conduit name, richer wins), then
handed to idp_write.write_workbook, whose gate (normalize_types ->
normalize_connections -> anatomy -> flag) makes LISA render them like the
finished IDPs.
"""
from __future__ import annotations

import os

import re

import idp_extract
import idp_excel
import idp_project
try:
    import idp_idp_pdf
except Exception:
    idp_idp_pdf = None
try:
    import idp_vision
except Exception:
    idp_vision = None
try:
    import idp_wiring
except Exception:
    idp_wiring = None
try:
    import idp_project_symbols
except Exception:
    idp_project_symbols = None

EXCEL_EXT = (".xlsx", ".xlsm", ".xls")
PDF_EXT = (".pdf",)

# generic words that don't identify a device (kept OUT of match tokens)
_STOP = {"START", "CALL", "STOP", "CONTROL", "SIGNAL", "POWER", "FROM", "TO",
         "THE", "AND", "FOR", "FEEDBACK", "SPEED", "STATUS", "RUN", "FAIL",
         "001", "01", "1", "NO", "NC", "COM", "SPARE", "FUTURE", "NEW", "SW"}


def _tokens(s):
    toks = re.split(r"[^A-Za-z0-9]+", str(s or "").upper())
    return {t for t in toks if len(t) >= 3 and t not in _STOP}


def extract_source(path, mode="auto", infer_symbols=True, ocr_refine=False, log=None):
    """Return (records, method) for a single file, routed by type. Every record
    (and fill group) is stamped with the exact file it came from, so the run
    can report per-cell provenance. `log` streams OCR progress to the UI."""
    ext = os.path.splitext(path)[1].lower()
    if ext in EXCEL_EXT:
        recs = idp_excel.read_source(path)
        shape = "excel-idp" if any("from_excel_idp" in r.get("flags", []) for r in recs) else "excel-list"
    elif ext in PDF_EXT and idp_idp_pdf is not None and idp_idp_pdf.is_idp_package(path):
        # a finished AIC interconnection-diagram IDP package — read it back into
        # records so the workbook reproduces the finished drawings through LISA
        recs = idp_idp_pdf.read_source(path)
        shape = "idp-package"
    elif ext in PDF_EXT and _pdf_role(path) == "scl":
        # a Submittal Cover Letter — not a conduit schedule and not a drawing
        recs, shape = [], "cover-letter"
    elif ext in PDF_EXT and _pdf_role(path) == "edc":
        # AIC EDC drawing package — TERMINALS, not a conduit schedule. Skip the
        # (slow) conduit-schedule scan AND the scanned-schedule vision misfire;
        # its terms attach later via apply_edc_terms_from_paths.
        recs, shape = [], "edc-source"
    elif ext in PDF_EXT and idp_vision is not None and _big_pdf(path):
        # LARGE plan set (e.g. a 2211-page Bluebeam project set): recognize the
        # conduit-schedule sheet by its Bluebeam markup / title FIRST (fast, text
        # only) instead of running the slow full-document table scan. If that
        # sheet has text-layer conduit rows, parse them; if it's vector (Crows
        # E-3), render just that page for the vision transcription.
        sched = idp_vision.find_schedule_pages(path)
        if sched and _page_has_conduit_text(path, sched[0]):
            recs, shape = idp_extract.extract_conduits(path, mode=mode, infer_symbols=infer_symbols)
            if not recs:
                recs, shape = _ocr_schedule_pdf(path, sched, refine=ocr_refine, log=log)
        elif sched:
            recs, shape = _ocr_schedule_pdf(path, sched, refine=ocr_refine, log=log)  # vector -> OCR
        else:
            recs, shape = idp_extract.extract_conduits(path, mode=mode, infer_symbols=infer_symbols)
    else:
        recs, shape = idp_extract.extract_conduits(path, mode=mode, infer_symbols=infer_symbols)
        # genuinely a scanned/vector conduit-schedule sheet (Lennar / Crows E-3)
        if ext in PDF_EXT and not recs and idp_vision is not None:
            recs, shape = _ocr_schedule_pdf(path, refine=ocr_refine, log=log)
    idp_project.tag_source(recs, path)
    return recs, shape


def _big_pdf(path, threshold=60):
    """True for a large multi-sheet plan set (worth pinpointing the schedule page
    instead of scanning every page)."""
    try:
        import fitz
        d = fitz.open(path)
        try:
            return len(d) >= threshold
        finally:
            d.close()
    except Exception:
        return False


def _page_has_conduit_text(path, page_index):
    """Does the recognized schedule page carry text-layer conduit rows (tags like
    P001/H002/C010)? If yes it's parseable as text; if no it's a vector sheet."""
    try:
        import fitz
        d = fitz.open(path)
        try:
            t = d[page_index].get_text("text")
        finally:
            d.close()
        return len(re.findall(r"\b[PHLCX]\d{3}[A-Z]?\b", t)) >= 3
    except Exception:
        return False


def _pdf_role(path, max_pages=12):
    """CHEAP classifier (filename + first pages, no full scan):
    'scl' = Submittal Cover Letter, 'edc' = AIC EDC drawing package, else ''."""
    base = os.path.basename(path).upper()
    if "_SCL_" in base or base.endswith("_SCL.PDF"):
        return "scl"
    if "_EDC_" in base or "_EDC." in base:
        return "edc"
    try:
        import fitz
        d = fitz.open(path)
    except Exception:
        return ""
    try:
        head = " ".join(d[i].get_text("text") for i in range(min(len(d), max_pages))).upper()
    finally:
        d.close()
    if "SUBMITTAL COVER LETTER" in head:
        return "scl"
    if "ELECTRICAL DISTRIBUTION AND CONTROL" in head or (
            "ADVANCED INTEGRATION" in head and ("THREE-LINE" in head or "PLC I/O" in head
                                                or "TERMINAL" in head)):
        return "edc"
    return ""


def _ocr_schedule_pdf(path, sched_pages=None, refine=False, cable_pages=None, log=None):
    """Read a VECTOR conduit-schedule sheet automatically via offline OCR (no API,
    no internet): locate the table, render it big, OCR it, bin into columns, and
    return conduit records. Low-confidence cells are flagged for review. Falls
    back to the vision packet only if OCR yields nothing.

    If a CABLE schedule (MC-E-9) is present — auto-detected by sheet title, or passed
    as `cable_pages` — its per-conductor specs replace the coarse conduit-only fill so
    types are precise and grounds authoritative instead of synthesized on every run."""
    _log = log or (lambda *a: None)
    try:
        import idp_ocr_schedule as _O
        import idp_schedule as _S
    except Exception:
        return _extract_scanned_pdf(path)
    if sched_pages is None:
        try:
            sched_pages = idp_vision.find_schedule_pages(path) or [0]
        except Exception:
            sched_pages = [0]
    # Accumulate across ALL detected schedule pages, not just the first — a schedule that
    # spans two sheets (each with its own header band) previously lost every row after the
    # first page. First page wins on a duplicate conduit name (earliest revision/most complete).
    all_recs, seen = [], set()
    for pi in sched_pages:
        try:
            _log(f"Reading conduit schedule (page {pi + 1}) by offline OCR …")
            rows, meta = _O.read_schedule(path, pi, clip=None, refine=refine, log=_log)
        except Exception:
            continue
        if not rows:
            continue
        recs = _S.rows_to_records(rows)
        thr = meta.get("low_conf_threshold", 0.8)
        by = {r["name"]: r for r in rows}
        low_fields = ("name", "src", "dst", "size", "ctype", "cond_gauge", "gnd")
        for rec in recs:
            src_row = by.get(rec["name"], {})
            conf = src_row.get("_conf", {})
            bad = [f for f in low_fields
                   if conf.get(f, 1.0) < thr and str(src_row.get(f, "")).strip()]
            rec.setdefault("flags", []).append("from_ocr_schedule")
            if bad:
                rec["flags"].append("ocr_low_confidence")
                rec["deviations"] = ((rec.get("deviations") or "")
                                     + f" [OCR: verify {', '.join(bad)}]").strip()
        # ── CABLE schedule (MC-E-9): upgrade the coarse conduit-only fill to precise
        # per-conductor fill + authoritative grounds, so we don't over-ground. ──
        try:
            _apply_cable_schedule(path, recs, by, cable_pages, refine, _log, near_page=pi)
        except Exception as e:
            _log(f"   (cable-schedule merge skipped: {e})")
        added = 0
        for rec in recs:
            nm = str(rec.get("name") or "").strip()
            if nm and nm not in seen:
                seen.add(nm); all_recs.append(rec); added += 1
        if added and len(sched_pages) > 1:
            _log(f"   → +{added} conduit(s) from page {pi + 1}")
    if all_recs:
        return all_recs, "ocr-schedule"
    return _extract_scanned_pdf(path)   # nothing readable → vision packet


def _read_cable_rows(path, cable_pages, refine, near_page, log):
    """OCR the CABLE schedule pages → ({cable_id: row}, [pages_used]). Pure OCR, no merge, so
    it can run on a background thread concurrently with the conduit-schedule OCR. Page
    selection: explicit `cable_pages` → sheet-title detection → probe the page(s) just after
    the conduit schedule. Probing is safe: read_cable_schedule returns nothing unless the
    page actually carries an MC-E-9 (C-### / SIZE / TYPE-INSUL) table."""
    import idp_cable_schedule as _C
    if cable_pages is not None:
        pages = list(cable_pages)
    else:
        pages = _C.find_cable_schedule_pages(path)
        if not pages and near_page is not None:
            try:
                import fitz
                n = fitz.open(path).page_count
            except Exception:
                n = near_page + 3
            pages = [p for p in (near_page + 1, near_page + 2) if 0 <= p < n]
    cab_rows, used = [], []
    for cpi in pages or []:
        try:
            cr, _ = _C.read_cable_schedule(path, cpi, refine=refine, log=log)
        except Exception:
            continue
        if cr:
            cab_rows += cr
            used.append(cpi)
    return {r["id"]: r for r in cab_rows}, used


def _apply_cable_schedule(path, recs, rows_by_name, cable_pages, refine, log, near_page=None,
                          precomputed=None):
    """Merge the CABLE schedule's per-conductor specs into the conduit records (precise fill
    + authoritative grounds, so we don't over-ground). `precomputed` is the (cab, used) tuple
    from a background _read_cable_rows; if absent, read it here sequentially. No-op if none."""
    import idp_cable_schedule as _C
    cab, used = (precomputed if precomputed is not None
                 else _read_cable_rows(path, cable_pages, refine, near_page, log))
    if not cab:
        return
    log(f"Cable schedule: OCR read {len(cab)} cable(s) from page(s) "
        f"{[p + 1 for p in used]}.")
    _C.apply_cable_fill(recs, rows_by_name, cab, log=log)


def retarget_schedule(candidates, tried=None, infer=True, hi_ocr=True, log=lambda *a: None,
                      is_cancelled=None, page_budget=4, cand_cap=8):
    """Cheap self-correction when the primary conduit source yielded almost nothing (e.g.
    targeting early-stopped on a learned-signature false positive). Scans the OTHER
    name-selected candidates, but ONLY OCRs the specific page(s) the offline finders flag as
    a conduit/cable schedule — never a whole-document OCR. Excel candidates are read directly
    (instant). Bounded by a total OCR PAGE BUDGET so it can't run away on a huge plan set.
    Returns the richest conduit records found, or []."""
    tried = set(tried or [])
    best, used_pages, n = [], 0, 0
    excel_first = sorted(candidates or [],
                         key=lambda p: 0 if str(p).lower().endswith((".xlsx", ".xlsm", ".xls")) else 1)
    for src in excel_first:
        if n >= cand_cap or used_pages >= page_budget:
            break
        if src in tried or (is_cancelled and is_cancelled()):
            continue
        low = str(src).lower()
        try:
            if low.endswith((".xlsx", ".xlsm", ".xls")):
                recs, _m = extract_source(src, mode="auto", infer_symbols=infer, log=log)
                tried.add(src); n += 1
            elif low.endswith(".pdf"):
                pages = set()
                for mod, fn in (("idp_vision", "find_schedule_pages"),
                                ("idp_cable_schedule", "find_cable_schedule_pages")):
                    try:
                        pages |= set(getattr(__import__(mod), fn)(src) or [])
                    except Exception:
                        pass
                # drop the page-0 cover/index false positive; cap to the remaining budget
                pages = [p for p in sorted(pages) if p > 0][:max(1, page_budget - used_pages)]
                if not pages:
                    continue
                tried.add(src); n += 1; used_pages += len(pages)
                recs, _shape = _ocr_schedule_pdf(src, sched_pages=pages, refine=False, log=log)
            else:
                continue
        except Exception:
            continue
        if len(recs) > len(best):
            log(f"   ↳ {os.path.basename(src)} → {len(recs)} conduit(s) (richer schedule source)")
            best = recs
    return best


def _extract_scanned_pdf(path):
    """Vision fallback for a scanned/vector plan PDF. If ANTHROPIC_API_KEY is set,
    Claude vision transcribes the conduit schedule directly; otherwise the pages
    are rendered and an ASK_CLAUDE_VISION.md packet is written for an attached
    Claude Code chat to transcribe. Returns (records, shape)."""
    # PINPOINT the conduit-schedule sheet (Bluebeam markup 'ConduitSchedule' or a
    # titled schedule on a drawing) so a huge plan set renders ONE page, not
    # hundreds. Fall back to the low-text/vector heuristic for small sets.
    pages = idp_vision.find_schedule_pages(path) or idp_vision.find_scanned_pages(path)
    if not pages:
        return [], "empty"
    try:
        import idp_escalate
        out_dir = idp_escalate._localappdata_dir()
    except Exception:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "_vision")
    imgs = idp_vision.render_pages(path, pages[:6], out_dir)
    recs = idp_vision.transcribe_via_api(imgs)     # None unless a key is set
    if recs:
        return recs, "scanned-vision"
    project = idp_project.detect_project_name([path]) or ""
    idp_vision.build_vision_packet(imgs, out_dir, project=project)
    return [], "scanned-needs-vision"


def merge_records(recs):
    """Dedup by conduit name; the record with more fill wins, flags combine,
    empty conduit-level fields backfill from the other."""
    by, order = {}, []
    for r in recs:
        n = str(r.get("name", "")).strip()
        if not n:
            continue
        if n not in by:
            by[n] = r
            order.append(n)
            continue
        ex = by[n]
        flags = sorted(set(ex.get("flags", []) + r.get("flags", []) + ["merged_sources"]))
        richer, poorer = (r, ex) if len(r.get("fill", [])) > len(ex.get("fill", [])) else (ex, r)
        for k in ("size", "ctype", "source", "dest", "deviations"):
            v = richer.get(k)
            empty = (not v) or (isinstance(v, list) and not any(str(x).strip() for x in v))
            # 'XXX' is the universal unknown-conduit-type placeholder — treat it as
            # empty so a real type on the poorer record (e.g. a schedule's 'RMC')
            # backfills instead of being discarded in favor of the placeholder.
            if k == "ctype" and str(v).strip().upper() == "XXX":
                empty = True
            if empty and poorer.get(k) and str(poorer.get(k)).strip().upper() != "XXX":
                richer[k] = poorer[k]
        richer["flags"] = flags
        by[n] = richer
    return [by[n] for n in order]


def _kind_match(fill_type, binding_kind):
    ft = str(fill_type or "").upper()
    if binding_kind == "TSP":
        return ft in ("TSP", "CAT-6", "FIBER")
    return ft in ("CONTROL", "POWER", "MFG_CABLE")   # discrete/control


def apply_wiring_terms(records, bindings):
    """Match wiring-diagram bindings to conduits BY DEVICE NAME and backfill the
    S Tag / S Term the conduit list left blank. Returns (matched, examples)."""
    matched, examples = 0, []
    for b in bindings:
        dev = _tokens(b.get("field_desc")) | _tokens(b.get("field_inst"))
        if not dev:
            continue
        best, score = None, -1
        tag = str(b.get("plc_tag", "")).upper()
        for rec in records:
            nm = str(rec.get("name", "")).strip().upper()
            if nm in ("", "XXX"):                 # skip only truly-empty conduits
                continue
            d_ovl = len(dev & _tokens(" ".join(rec.get("dest", []))))
            s_ovl = len(dev & _tokens(" ".join(rec.get("source", []))))
            names = " ".join(list(rec.get("source", [])) + list(rec.get("dest", []))).upper()
            tagtext = names + " " + " ".join(str(t) for w in rec.get("wires", [])
                                             for t in (w["src"][1], w["dst"][1])).upper()
            tag_hit = bool(tag) and len(tag) >= 4 and tag in tagtext
            # qualify only on a strong signal: >=2 shared DEST tokens, or exact tag.
            # (a lone generic token like "PUMP" is not enough)
            if not (d_ovl >= 2 or tag_hit):
                continue
            sc = (5 if tag_hit else 0) + d_ovl * 2 + s_ovl
            if sc > score:
                best, score = rec, sc
        if best is None:
            continue
        rows = best.get("fill", []) or []
        target = next((g for g in rows if _kind_match(g.get("type"), b["kind"])), None) \
            or (rows[0] if rows else None)
        if target is None:
            continue
        terms = [t.rstrip("+-") for t in (b.get("terminals") or [])]
        wires = best.setdefault("wires", [])
        filled = False
        for i, w in enumerate(wires):
            if i >= len(terms):
                break
            s = list(w["src"])
            if not str(s[2]).strip():
                s[2] = terms[i]
                filled = True
            if not str(s[1]).strip() and b.get("plc_tag"):
                s[1] = b["plc_tag"]
            w["src"] = tuple(s)
        if filled or terms:
            best.setdefault("flags", []).append(f"terms_from_wiring:{b['plc_tag']}")
            target["wiring_note"] = (
                f"S Term backfilled from PLC I/O binding {b['plc_tag']} "
                f"(terminals {','.join(terms)})")
            if b.get("_src_pdf"):
                target["wiring_src"] = b["_src_pdf"]
            matched += 1
            if len(examples) < 8:
                examples.append(f"{b['plc_tag']}({','.join(terms)}) -> {best['name']}")
    return matched, examples


def collect_wiring_bindings(paths):
    """Extract PLC-I/O term bindings from any wiring-diagram PDFs in `paths`,
    each tagged with the exact PDF it came from (for provenance)."""
    if idp_wiring is None:
        return []
    binds = []
    for p in paths:
        if p.lower().endswith(".pdf"):
            try:
                for b in idp_wiring.extract_bindings(p):
                    b["_src_pdf"] = p
                    binds.append(b)
            except Exception:
                continue
    return binds


def apply_project_dwg_symbols(records, paths, rescan=False):
    """Confirm S/D Symbol against the BLOCK-LIBRARY folder (the 'dummy tool library' of
    per-block .dwg files in the Claude Files directory) — 'what the blocks look like'. The
    symbols don't change, so we read the library folder's block names and INFER from them;
    we never scan project AutoCAD drawings. Returns (upgraded_count, library_folder, 0)."""
    if idp_project_symbols is None:
        return 0, None, 0
    _ps = idp_project_symbols
    lib = _ps.load_symbol_library(refresh=rescan)
    if not lib:
        return 0, None, 0
    src = _ps.block_library_source()
    n = _ps.apply_project_symbols(records, lib, source_label="block library")
    return n, src, 0


def derive_teach_candidates(records, existing_matches=()):
    """Scan a run's records for gaps worth TEACHING via Remembered Logic,
    rather than silently relying on a low-confidence fallback guess forever.

    Two kinds of gap:
      - a device name whose S/D Symbol match fell back (confidence < 0.6) —
        the tool guessed a generic terminal block/etc. because it didn't
        recognize the device; a human can supply the right token once.
      - a conduit whose Type never resolved off the source (still 'XXX') —
        needs a real value, not a placeholder.

    Returns a list of candidate rule dicts (type/match/result/context/note)
    ready to append to the Remembered Logic store; `result` is left blank
    for the user to fill in. Skips anything already in `existing_matches`
    (case-insensitive) so re-running the same folder doesn't pile up dupes.
    """
    seen = {m.upper() for m in existing_matches}
    candidates = []

    def _add(rule):
        key = rule["match"].strip().upper()
        if key and key not in seen:
            seen.add(key)
            candidates.append(rule)

    for rec in records or []:
        names = [n for n in (list(rec.get("source", [])) + list(rec.get("dest", []))) if n]
        for g in rec.get("fill", []) or []:
            for side, conf_key, sym_key, name_i in (
                    ("S", "s_symbol_conf", "s_symbol", 0),
                    ("D", "d_symbol_conf", "d_symbol", -1)):
                conf = g.get(conf_key)
                if conf is not None and conf < 0.6 and g.get(sym_key):
                    text = names[name_i] if names else rec.get("name", "")
                    if text:
                        _add({"type": "symbol_keyword", "match": text, "result": "",
                             "context": "",
                             "note": f"NEEDS REVIEW ({rec['name']}, {side} side) — low-"
                                     f"confidence match, fell back to {g[sym_key]}. "
                                     f"Set Result to the correct device token (e.g. CB, "
                                     f"DISC, TB_Square, MTR)."})
        if str(rec.get("ctype", "")).strip() in ("XXX", ""):
            _add({"type": "value_rule", "match": rec.get("name", ""), "result": "",
                 "context": "conduit_type",
                 "note": "NEEDS REVIEW — Conduit Type never resolved from the source; "
                         "set Result to the correct type (RMC, PVC, RGS, ...)."})
    return candidates


def learn_from_finished_idps(paths, rescan=False):
    """Scan any finished-IDP DWGs reachable from `paths` and persist what they
    teach — real (device text -> symbol token) pairs harvested from drawings
    that already shipped — as Remembered Logic rules. This is durable across
    EVERY future project, not just whatever's being extracted right now: point
    this at any folder of finished IDPs (a past job's CAD folder) and its
    conventions become logic the tool applies from then on.
    Returns (rules_added, dwgs_found). Degrades to (0, 0) if no DWGs are found
    or AutoCAD isn't reachable — never raises."""
    if idp_project_symbols is None:
        return 0, 0
    root = idp_project.project_root(paths)
    dwgs = idp_project_symbols.find_project_dwgs(root) if root else []
    if not dwgs:
        return 0, 0
    scan = idp_project_symbols.load_or_scan(root, dwgs, rescan=rescan)
    learned = idp_project_symbols.extract_learned_rules(scan)
    if not learned:
        return 0, len(dwgs)
    import logic_store
    data = logic_store.load()
    existing = {r.get("match", "").strip().upper() for r in data.get("rules", [])}
    added = 0
    for rule in learned:
        key = rule["match"].strip().upper()
        if key in existing:
            continue
        existing.add(key)
        data.setdefault("rules", []).append(rule)
        added += 1
    if added:
        logic_store.save(data)
    return added, len(dwgs)


def apply_edc_terms_from_paths(records, paths, log=lambda *a: None, allow_api=True,
                               write_packet=True):
    """Pull FillIndex terminal landings off any AIC EDC drawing PDFs in `paths`.
    Text-layer EDCs are always read fully offline. If the EDC has NO text layer, the
    fallback renders the sheets and — with `allow_api` + ANTHROPIC_API_KEY — transcribes
    via Claude vision, else — with `write_packet` — writes an ASK_CLAUDE_EDC.md question
    packet for a later Claude pass. Also parses any text-layer panelboard schedule for
    breaker ratings. Returns (terms_applied, packet_path_or_'').

    In a SCAN: allow_api=False (never a live Claude call, output stays offline) and
    write_packet follows the Vision-assist checkbox — checked ⇒ render + write the Claude
    question packet; unchecked ⇒ skip the render entirely (fast, no packet)."""
    try:
        import idp_edc
    except Exception:
        return 0, ""
    # panelboard ratings (text-layer only) -> hand to idp_write via idp_terms
    try:
        import idp_panelboard, idp_write
        pb = idp_panelboard.find_and_parse([p for p in paths if str(p).lower().endswith(".pdf")])
        if pb:
            idp_write._panelboard_map = pb
            log(f"Panelboard schedule: {len(pb)} circuits parsed (breaker ratings).")
    except Exception:
        pass
    edc_pdfs = []
    for p in paths:
        if not str(p).lower().endswith(".pdf"):
            continue
        try:
            if idp_edc.find_edc_sheets(p):
                edc_pdfs.append(p)
        except Exception:
            continue
    if not edc_pdfs:
        return 0, ""
    # PRIMARY, fully-offline path: AIC EDC drawings are AutoCAD-Electrical exports
    # WITH a text layer, so parse the PLC-I/O terminals straight from text +
    # coordinates and match them to conduits — no vision, no API key.
    points = []
    for p in edc_pdfs:
        try:
            points += idp_edc.parse_io_sheets(p)
        except Exception:
            continue
    applied = idp_edc.match_io_to_conduits(records, points, log=log) if points else 0
    # OFFLINE POSITIONAL I/O read (no OCR, no vision): the channel # is graphical but the
    # device text + sequential channel order give the EXACT channel via the sheet's
    # reading order. This is the fast, self-contained way to close the channel gap.
    positional = []
    for p in edc_pdfs:
        try:
            positional += idp_edc.parse_io_positional(p)
        except Exception:
            continue
    if positional:
        applied += idp_edc.match_io_positional(records, positional, log=log)
    # LADDER fallback (TB tag only, channel flagged) for any signal conduit the positional
    # read didn't resolve — e.g. a sheet whose descriptions were too sparse to match.
    ladder = []
    for p in edc_pdfs:
        try:
            ladder += idp_edc.parse_io_ladder(p)
        except Exception:
            continue
    if ladder:
        applied += idp_edc.match_io_ladder(records, ladder, log=log)
    if applied:
        return applied, ""
    # FALLBACK only when the EDC has NO text layer (pure vector). Rendering sheets to images
    # is the expensive part, so only do it if something will consume it: a live vision pass
    # (allow_api + key) or a written question packet (write_packet). Otherwise skip.
    _has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not ((allow_api and _has_key) or write_packet):
        log("EDC has no text layer — skipped offline (check Vision-assist to render + write "
            "a Claude question packet for its terminals).")
        return 0, ""
    try:
        import idp_escalate
        out_dir = idp_escalate._localappdata_dir()
    except Exception:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(edc_pdfs[0])), "_edc")
    imgs = []
    for p in edc_pdfs:
        imgs += idp_edc.render_sheets(p, idp_edc.find_edc_sheets(p), out_dir)
    if allow_api and _has_key:
        term_map = idp_edc.transcribe_via_api(imgs)     # live vision only when explicitly allowed
        if term_map:
            n = idp_edc.apply_edc_terms(records, term_map)
            log(f"EDC terms: {n} conduit(s) term-backfilled (vision) from {len(edc_pdfs)} package(s).")
            return n, ""
    if write_packet:
        packet = idp_edc.build_edc_packet(imgs, out_dir,
                                          project=idp_project.detect_project_name(paths) or "")
        log(f"EDC has no text layer — rendered {len(imgs)} sheet(s) to {out_dir}; open "
            f"ASK_CLAUDE_EDC.md there (Vision-assist) to transcribe terminals.")
        return 0, packet
    return 0, ""


def ingest(paths, mode="auto", infer_symbols=True, wiring=True, project_symbols=True,
           edc_terms=True):
    """Read + merge all sources; optionally backfill terms from wiring diagrams,
    upgrade symbols against this project's own scanned DWGs, and pull terminal
    landings off AIC EDC drawing sheets."""
    all_recs = []
    for p in paths:
        try:
            recs, _ = extract_source(p, mode=mode, infer_symbols=infer_symbols)
            all_recs += recs
        except Exception:
            continue
    records = merge_records(all_recs)
    if wiring:
        apply_wiring_terms(records, collect_wiring_bindings(paths))
    if project_symbols:
        apply_project_dwg_symbols(records, paths)
        try:
            learn_from_finished_idps(paths)   # persist as Remembered Logic for every future run
        except Exception:
            pass
    if edc_terms:
        try:
            apply_edc_terms_from_paths(records, paths)
        except Exception:
            pass
    return records


if __name__ == "__main__":
    import sys
    recs = ingest(sys.argv[1:])
    print(f"{len(recs)} conduits, {sum(len(r['fill']) for r in recs)} fill rows")
