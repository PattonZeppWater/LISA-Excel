"""
IDP Extractor — Web Control Panel (pywebview + Flask), styled to match LISA exactly.

Same architecture as LISA (Flask on a background thread + a pywebview window loading
it), and it reuses the EXACT extractor backend the Tkinter panel uses — the scan core
below is a faithful port of idp_control_panel.App._worker, so behavior is identical.

The Tkinter panel (idp_control_panel.py) is kept intact and reachable with `--classic`,
so no functionality is ever lost if a feature isn't yet surfaced in the web UI.

Run:  python idp_web_panel.py            (web UI, LISA look)
      python idp_web_panel.py --classic  (original Tkinter UI, full feature set)
"""
import os
import sys
import csv
import threading
import traceback

from flask import Flask, Blueprint, send_from_directory, request, jsonify

from idp_ingest import (extract_source, merge_records, collect_wiring_bindings,
                        apply_wiring_terms, apply_project_dwg_symbols,
                        derive_teach_candidates, learn_from_finished_idps)
from idp_write import write_workbook, degrey, versioned_path
import idp_project
import idp_schedule
import logic_store
import kb_expand

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB = os.path.join(_HERE, "webui")

# ── Blueprint: the whole IDP-Extraction module as a mountable unit ───────────
# Standalone (the exe) creates its own Flask app and registers this. To MERGE into
# LISA, LISA just calls idp_web_panel.register(lisa_app, prefix="/idp") — the routes,
# static assets and JSON API mount under /idp with NO port or route collision, and the
# HTTP endpoints below let LISA's React call the backend directly.
idp_bp = Blueprint("idp", __name__, static_folder=os.path.join(_WEB, "static"),
                   template_folder=os.path.join(_WEB, "templates"),
                   static_url_path="/static")


@idp_bp.after_request
def _no_cache(resp):
    """Never cache the Autofill panel or its JS/CSS. Without this, after an Update overwrites
    app.js / index.html the WebView keeps serving the CACHED copy — so a machine reports the new
    version but shows none of the new UI. Force a fresh load every time."""
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@idp_bp.route("/")
def _index():
    return send_from_directory(os.path.join(_WEB, "templates"), "index.html")


# ── HTTP JSON API (for LISA's React to call over HTTP — no pywebview needed) ──
@idp_bp.route("/api/scan", methods=["POST"])
def _api_scan():
    opts = request.get_json(force=True, silent=True) or {}
    if STATE.running or STATE.train_running:
        return jsonify(ok=False, error="busy (scan or training in progress)")
    STATE.log_lines = []
    STATE.cancel = False
    STATE.running = True                       # set BEFORE spawning to close the check→spawn race
    threading.Thread(target=_scan_core, args=(opts, STATE), daemon=True).start()
    return jsonify(ok=True)


@idp_bp.route("/api/cancel", methods=["POST"])
def _api_cancel():
    STATE.cancel = True
    return jsonify(ok=True)


@idp_bp.route("/api/poll")
def _api_poll():
    return jsonify(log="\n".join(STATE.log_lines), running=STATE.running)


@idp_bp.route("/api/logic", methods=["GET", "POST"])
def _api_logic():
    if request.method == "POST":
        rule = request.get_json(force=True, silent=True) or {}
        try:
            data = logic_store.load()
            data.setdefault("rules", []).append(rule)
            logic_store.save(data)
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    try:
        return jsonify(rules=logic_store.load().get("rules", []))
    except Exception:
        return jsonify(rules=[])


@idp_bp.route("/api/provenance")
def _api_provenance():
    return jsonify(rows=STATE.provenance)


@idp_bp.route("/api/suggest_output", methods=["POST"])
def _api_suggest():
    files = (request.get_json(force=True, silent=True) or {}).get("files") or []
    try:
        import idp_settings
        site = idp_project.detect_site_name(files) or "IDP"
        return jsonify(path=idp_settings.resolve_output_path(site, None, files))
    except Exception:
        return jsonify(path="")


@idp_bp.route("/api/route", methods=["POST"])
def _api_route():
    """Classify a project folder's files → the field→source routing report (for a UI
    preview before running)."""
    files = (request.get_json(force=True, silent=True) or {}).get("files") or []
    try:
        import idp_router
        m = idp_router.classify(files)
        return jsonify(report=idp_router.routing_report(m))
    except Exception as e:
        return jsonify(report=f"(routing error: {e})")


@idp_bp.route("/api/call/<method>", methods=["POST"])
def _api_call(method):
    """Generic bridge: expose every Api method over HTTP so the web panel works whether it's
    the standalone pywebview app OR embedded in LISA's iframe — where window.pywebview is
    LISA's api, not the extractor's, so window.pywebview.api.* has none of these methods.
    File-dialog methods run on whatever pywebview window is hosting the process (LISA's)."""
    if method.startswith("_"):
        return jsonify(ok=False, error="unknown method"), 404
    fn = getattr(API, method, None)
    if not callable(fn):
        return jsonify(ok=False, error=f"unknown method: {method}"), 404
    payload = request.get_json(force=True, silent=True) or {}
    args = payload.get("args") or []
    try:
        return jsonify(ok=True, result=fn(*args))
    except Exception as e:
        return jsonify(ok=False, error=str(e))


def register(target_app, prefix=""):
    """Mount the IDP-Extraction module onto any Flask app (e.g. LISA) under `prefix`."""
    target_app.register_blueprint(idp_bp, url_prefix=prefix)
    return target_app


app = Flask(__name__, static_folder=None)   # no default /static — the blueprint serves it
register(app, prefix="")                     # standalone: mount at root


# Chromium blocks certain ports (SIP/X11/etc.) with ERR_UNSAFE_PORT — pick a free one
# that the embedded WebView2 will actually load, so a merge/relaunch never dead-pages.
_UNSAFE_PORTS = {1719, 1720, 1723, 2049, 3659, 4045, 5060, 5061, 6000, 6566, 6665, 6666,
                 6667, 6668, 6669, 6697, 10080}


def _free_port():
    import socket
    for p in (5057, 5075, 5090, 5123, 5199, 5237, 5299, 8137, 8231, 8317, 8399):
        if p in _UNSAFE_PORTS:
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return 5057


def _walk_sources(folder):
    """Scannable sources (PDF / Excel) under a project folder, recursively — with
    obviously-irrelevant subtrees (photos, quotes, estimating, correspondence) dropped by
    PATH so we don't even list them. Cheap: no file is opened here; the deeper priority-
    ordered, early-stopping targeting happens at scan time in idp_router.discover_sources."""
    try:
        import idp_layouts as _L
    except Exception:
        _L = None
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if not f.lower().endswith((".pdf", ".xlsm", ".xlsx")):
                continue
            p = os.path.join(root, f)
            if _L is not None:
                try:
                    if _L.folder_relevance(p) == "skip":
                        continue
                except Exception:
                    pass
            out.append(p)
    return out


# ── shared run state (log buffer + flags), polled by the front-end ───────────
class _State:
    def __init__(self):
        self.log_lines = []
        self.running = False
        self.cancel = False
        self.last_records = []
        self.poor_yield = False
        self.last_training = None
        self.update_lines = []
        self.update_running = False
        self.update_applied = False   # an Update just copied a newer version → offer auto-reload
        self.provenance = []
        self.train_lines = []
        self.train_running = False
        self.train_unlocked = False   # set True only after the training password is verified
        self.logic_undo = []          # last-deleted Remembered-Logic rules (for Undo delete)

    def log(self, m):
        self.log_lines.append(str(m))

    def tlog(self, m):
        self.train_lines.append(str(m))


STATE = _State()


# ── SCAN CORE — faithful port of idp_control_panel.App._worker ───────────────
def _write_incomplete_skeleton(opts, pdfs, st, log):
    """GUARANTEE an output artifact even when NO conduit was extracted: write a clearly
    marked, project-named INCOMPLETE workbook (an empty template copy) so the user always
    gets a started, correctly-named file to fill from the rendered schedule sheets — never
    a silent blank. Fabricates NOTHING: no invented conduit rows. The rendered candidate
    schedule sheet images were already written by the yield-guard above."""
    import shutil, idp_settings
    st.poor_yield = True
    try:
        site = idp_project.detect_site_name(pdfs) or "IDP"
        base = idp_settings.resolve_output_path(site, opts.get("output"), pdfs)
        stem, ext = os.path.splitext(base)
        if stem.endswith("_FILLED"):        # drop the (contradictory) "FILLED" suffix on an empty skeleton
            stem = stem[: -len("_FILLED")]
        target = versioned_path(stem + "_INCOMPLETE" + ext)
        tpl = opts.get("template") or idp_settings.get_template_path()
        try:
            wrote = write_workbook([], tpl, target,
                                   clear_rows=bool(opts.get("clear_rows", True)),
                                   add_flags=False, clear_deviations=True)
        except Exception:
            shutil.copyfile(tpl, target); wrote = target          # last-resort: raw template copy
        log("")
        log("── GUARANTEED OUTPUT (INCOMPLETE) ──────────────────────")
        log("   No machine-readable conduit schedule was found in these sources, so NO")
        log("   conduits were invented. Wrote an empty, project-named workbook to start from:")
        log(f"   → {os.path.basename(wrote)}")
        log("   Fill it from the rendered schedule sheet image(s) noted above, or supply a")
        log("   TEXT / EXCEL conduit schedule (or enable Vision-assist + an API key) and re-scan.")
        log("───────────────────────────────────────────────────────")
        try:
            st.last_records = []
            st.provenance = []
        except Exception:
            pass
    except Exception as e:
        log(f"   (could not write the INCOMPLETE skeleton: {e}) — no conduits found.")


def _scan_core(opts, st):
    import time
    _t0 = time.time()
    log = st.log
    try:
        st.running = True
        st.cancel = False
        st.poor_yield = False
        log(logic_store.apply())
        mode = opts.get("mode", "auto")
        infer = bool(opts.get("infer", True))
        unk = opts.get("unknown", "XXX")
        hi_ocr = bool(opts.get("hi_ocr", True))
        pdfs = list(opts.get("files") or [])
        # ── smart, EARLY-STOPPING targeting: inspect files in priority order (ENGINEERING
        # first), stop the instant we hold the conduit schedule + a terminations source, and
        # extract ONLY those — never the whole project. Fully offline; no Claude. ──
        conduit_srcs, edc_srcs = pdfs, []
        try:
            import idp_router
            manifest = idp_router.discover_sources(pdfs, log=log,
                                                   is_cancelled=lambda: st.cancel)
            for _ln in idp_router.routing_report(manifest).split("\n"):
                log(_ln)
            conduit_srcs = idp_router.conduit_sources(manifest) or []
            edc_srcs = idp_router.edc_sources(manifest) or []
            if not conduit_srcs:
                # nothing recognized as a schedule — fall back to the inspected source files
                conduit_srcs = [p for role, items in manifest.items()
                                if role not in ("skip", "cover_letter", "cut_sheet", "other")
                                for (p, _r) in items] or pdfs
            # extract only the best conduit source(s) — the list is already latest-revision
            # first, so this avoids re-OCRing every revision/duplicate of the schedule.
            conduit_srcs = conduit_srcs[:3]
            # never apply another project's EDC to these conduits (multi-project folders)
            _before = len(edc_srcs)
            edc_srcs = idp_router.scope_edc_to_conduit(conduit_srcs, edc_srcs)
            if len(edc_srcs) != _before:
                log(f"Scope: kept {len(edc_srcs)} of {_before} EDC source(s) matching the "
                    "conduit schedule's project (dropped cross-project EDC).")
        except Exception as e:
            log(f"(routing skipped: {e})")
            conduit_srcs, edc_srcs = pdfs, []
        # GUARDRAIL — warn if the targeted sources span more than one project number (a
        # co-located/mixed folder holding two jobs), so a wrong-project scan is caught early.
        try:
            import idp_router as _R2
            _nums = sorted({n for n in (_R2._project_number(p)
                                        for p in (conduit_srcs or []) + (edc_srcs or [])) if n})
            if len(_nums) > 1:
                log(f"⚠ Multiple project numbers in the scanned sources: {', '.join(_nums)}. "
                    "This folder may hold more than one project — verify the output is a single "
                    "job (scope the scan to one project's subfolder if not).")
        except Exception:
            pass
        all_recs = []
        for src in conduit_srcs:
            if st.cancel:
                log("⛔ Scan cancelled."); return
            name = os.path.basename(src)
            log(f"Scanning {name} …")
            try:
                recs, method = extract_source(src, mode=mode, infer_symbols=infer, log=log,
                                              ocr_refine=hi_ocr)
            except Exception as e:
                log(f"   ! error: {e}")
                continue
            if unk != "XXX":
                for r in recs:
                    if str(r.get("ctype", "")).strip() in ("", "XXX"):
                        r["ctype"] = unk
            flagged = sum(1 for r in recs if r.get("flags"))
            if recs:
                log(f"   → {len(recs)} conduits via {method}"
                    + (f"  ({flagged} flagged)" if flagged else ""))
            elif method == "cover-letter":
                log("   → submittal cover letter (no conduit data) — skipped.")
            elif method == "edc-source":
                log("   → EDC drawing package (terminals, not a conduit schedule). Its "
                    "S/D terms attach to the conduit-schedule source during this scan.")
            elif method == "ocr-schedule":
                lc = sum(1 for r in recs if "ocr_low_confidence" in r.get("flags", []))
                log(f"   → vector conduit schedule read by offline OCR: {len(recs)} conduits"
                    + (f"  ({lc} flagged amber — verify against the sheet)" if lc else ""))
            elif method == "scanned-needs-vision":
                import idp_escalate
                log("   → conduit schedule is on a SCANNED/vector sheet (no text layer). "
                    f"Rendered page images to: {idp_escalate._localappdata_dir()}")
                log("     Open ASK_CLAUDE_VISION.md there and paste the images into Claude.")
            else:
                log("   → nothing extractable")
            all_recs.extend(recs)

        if not all_recs:
            # Don't dead-end here: fall through to the yield guard so the OTHER candidates'
            # schedule pages get re-targeted (the primary source may simply have been the
            # wrong file), and — if still empty — the schedule sheets get rendered and a
            # guaranteed INCOMPLETE artifact is written. Never a silent blank.
            log("No conduits from the primary source(s) — self-correcting: re-checking the "
                "other candidates before giving up …")
        all_recs = merge_records(all_recs)
        st.last_records = all_recs
        # ── YIELD GUARD — never present a near-empty extraction as a finished workbook. If the
        # schedule wasn't captured (few conduits / no fill), try every other candidate source,
        # then a vision transcription of the schedule sheet, then flag it LOUDLY. ──
        def _filln(rs):
            return sum(len(r.get("fill") or []) for r in rs)
        if len(all_recs) < 3 or _filln(all_recs) == 0:
            log(f"⚠ Only {len(all_recs)} conduit(s) / {_filln(all_recs)} fill group(s) read — "
                "self-correcting: checking the schedule pages of the other candidates …")
            # BOUNDED re-target: OCR only the finder-flagged schedule page(s) of the other
            # candidates (Excel read directly), so a targeting false-positive self-corrects
            # without a slow whole-document re-OCR.
            try:
                import idp_router as _R, idp_layouts as _L, idp_ingest as _ing2
                rel = [p for p in pdfs if _L.folder_relevance(p) != "skip"]
                cand_conduit, _ct = _R._name_preselect(rel)
                better = _ing2.retarget_schedule(cand_conduit, tried=set(conduit_srcs),
                                                 infer=infer, hi_ocr=hi_ocr, log=log,
                                                 is_cancelled=lambda: st.cancel)
                if len(better) > len(all_recs):
                    all_recs = merge_records(better)
                    st.last_records = all_recs
            except Exception as e:
                log(f"   (re-target skipped: {e})")
        # OPT-IN SPECIALIZED READER (fully offline, "Specialized reader" checkbox): a conduit
        # schedule embedded in a busy plan sheet / rotated / stacked-header / numeric-ID drawn
        # table that the standard readers can't isolate. Reads it from the drawn cell GRID
        # (vector text, no OCR, no API). Its output is FLAGGED for verification and ADOPTED
        # only if it beats the current read, so it can never make a good scan worse.
        if opts.get("specialized"):
            # (a) DRAWN-GRID reader — only when the normal read is poor (a plan-embedded schedule
            # the standard readers couldn't isolate). Gated so it never slows a good scan.
            if len(all_recs) < 3 or _filln(all_recs) == 0:
                try:
                    import idp_specialized_schedule as _SP
                    sp = []
                    for s in (conduit_srcs or pdfs):
                        if str(s).lower().endswith(".pdf"):
                            sp += _SP.read_specialized(s, log=log)[0]
                    sp = merge_records(sp)
                    if len(sp) > len(all_recs):
                        log(f"Specialized reader: adopted {len(sp)} conduit(s) from the drawn table "
                            "grid — flagged amber; verify against the sheet.")
                        all_recs = sp
                        st.last_records = all_recs
                except Exception as e:
                    log(f"   (drawn-grid reader skipped: {e})")
            # (b) FINISHED-IDP OCR — whenever a finished-IDP PDF is present, even if the text
            # reader already returned a few WITH fill: a GRAPHICAL AIC IDP under-reads by text
            # (7 of 24), so text-with-fill is NOT a reliable "done" signal. Fast now (data-column
            # crop + page skip, ~2s/sheet); adopted only if it recovers MORE than the current read.
            try:
                import idp_idp_ocr as _IO, idp_idp_pdf as _IP
                io = []
                for s in (conduit_srcs or pdfs):
                    if str(s).lower().endswith(".pdf") and _IP.is_idp_package(s):
                        log(f"Specialized reader: OCR-reading finished IDP {os.path.basename(s)} "
                            "(conduit sheets only) …")
                        io += _IO.read_interconnection_idp(s, log=log)[0]
                io = merge_records(io)
                if len(io) > len(all_recs):
                    log(f"Specialized reader: adopted {len(io)} conduit(s) from finished-IDP OCR "
                        "(was {}); flagged amber — verify against the sheets.".format(len(all_recs)))
                    all_recs = io
                    st.last_records = all_recs
            except Exception as e:
                log(f"   (finished-IDP OCR skipped: {e})")
        # graphical / plan-embedded schedule → transcribe via Claude vision (opt-in: key set)
        if ((len(all_recs) < 3 or _filln(all_recs) == 0)
                and opts.get("vision_assist") and os.environ.get("ANTHROPIC_API_KEY")):
            try:
                import idp_vision_schedule as _VS
                vrecs = _VS.read_schedule_via_vision(conduit_srcs or pdfs, log=log)
                if len(vrecs) > len(all_recs):
                    all_recs = merge_records(vrecs)
                    st.last_records = all_recs
            except Exception as e:
                log(f"   (vision schedule-read skipped: {e})")
        st.poor_yield = (len(all_recs) < 3 or _filln(all_recs) == 0)
        if st.poor_yield:
            log("⚠⚠ COULD NOT READ THE CONDUIT SCHEDULE — this workbook is INCOMPLETE and should "
                "not be used as-is. The schedule is likely embedded in a busy plan sheet "
                "(graphical) that offline OCR can't parse.")
            log("   → FIX: supply a TEXT or EXCEL conduit schedule for this project (the exe scan "
                "is offline and cannot read a purely graphical schedule).")
            try:
                import idp_vision_schedule as _VS, idp_escalate
                refs = _VS.find_schedule_pages(conduit_srcs or pdfs)
                if refs:
                    _od = os.path.join(idp_escalate._localappdata_dir(), "_schedule_pages")
                    _imgs = _VS.render_schedule_pages(refs, _od)
                    log(f"   Rendered {len(_imgs)} candidate schedule sheet(s) → {_od}. Transcribe "
                        "them, or set an Anthropic API key + check 'Vision-assist' to auto-read them.")
            except Exception as e:
                log(f"   (schedule-page render skipped: {e})")
        if not all_recs:
            _write_incomplete_skeleton(opts, pdfs, st, log)
            return
        # Apply the engineer's taught OCR/text corrections (text_fix rules) to the read names
        # FIRST — e.g. a misread "KLDS" → the correct "MDS" — so every downstream step (note
        # reading, symbol inference, terminations) sees the corrected text.
        try:
            logic_store.apply_text_fixes(all_recs, log=log)
        except Exception as e:
            log(f"   (text fixes skipped: {e})")
        # UNDERSTAND the engineer's notes / context left on each conduit — fill a missing
        # conduit type from a material named in the note, seed a pull-rope on a spare run, and
        # flag ground callouts / existing-new / other instructions. Additive only: it never
        # overwrites a value already read, so it can't change a conduit that was read correctly.
        try:
            import idp_notes
            idp_notes.interpret_notes(all_recs, log=log)
        except Exception as e:
            log(f"   (note interpretation skipped: {e})")
        term_srcs = edc_srcs or [p for p in conduit_srcs if p.lower().endswith(".pdf")]
        try:
            binds = collect_wiring_bindings(term_srcs)
            if binds:
                n, ex = apply_wiring_terms(all_recs, binds)
                log(f"Wiring diagrams: {len(binds)} I/O bindings → {n} conduit(s) term-backfilled"
                    + (f"  (e.g. {ex[0]})" if ex else ""))
        except Exception as e:
            log(f"   (wiring backfill skipped: {e})")
        try:
            import idp_ingest as _ing
            _ing.apply_edc_terms_from_paths(all_recs, term_srcs, log=log, allow_api=False,
                                            write_packet=bool(opts.get("vision_assist", False)))
        except Exception as e:
            log(f"   (EDC term extraction skipped: {e})")
        # bridge the OCR-hard cells (exact channels / circuits / dense cable lists) to an
        # OFFLINE pinpoint packet. allow_api=False → a SCAN NEVER contacts Claude; the exe
        # produces its output fully offline. (Live Claude learning lives in the Training tab.)
        if opts.get("vision_assist", False):
            try:
                import idp_vision_assist
                idp_vision_assist.assist(all_recs, conduit_srcs + edc_srcs, log=log,
                                         allow_api=False)
            except Exception as e:
                log(f"   (vision-assist skipped: {e})")
        # Symbol confirmation reads the BLOCK-LIBRARY folder (what the blocks look like) and
        # infers — NO AutoCAD scan, ever. The symbols don't change, so the library is read
        # once and applied every run.
        if infer:
            # read the actual device BLOCKS off the EDC diagrams and CONFIRM symbols against
            # the library (component sequence → library block); populates only real matches.
            try:
                import idp_edc_symbols, idp_project_symbols as _ps
                _lib = _ps.load_symbol_library()
                idp_edc_symbols.read_symbols_from_edc(all_recs, term_srcs, _lib, log=log)
            except Exception as e:
                log(f"   (EDC block symbol read skipped: {e})")
            try:
                n, src, _ = apply_project_dwg_symbols(all_recs, pdfs)
                if src:
                    log(f"Symbols: confirmed {n} against the block library "
                        f"({os.path.basename(str(src).rstrip('/\\\\'))}) — no AutoCAD scan.")
                else:
                    log("Symbols: block library folder not found — inferred from device "
                        "names/cut sheets only. Set its path in settings if it moved.")
            except Exception as e:
                log(f"   (symbol confirmation skipped: {e})")
            # Vision block-read (opt-in via Vision-assist + API key): render the EDC landing
            # sheets and have Claude match GRAPHICAL blocks to the library for any conduit
            # still missing a confident symbol. No key ⇒ no-op (scan stays offline).
            if opts.get("vision_assist", False):
                try:
                    import idp_edc_symbols, idp_project_symbols as _ps
                    idp_edc_symbols.confirm_symbols_via_vision(
                        all_recs, term_srcs, _ps.load_symbol_library(), log=log)
                except Exception as e:
                    log(f"   (vision block-read skipped: {e})")
        else:
            log("Symbol confirmation skipped (enable 'Infer S/D symbols').")
        log(kb_expand.expand_from_records(all_recs))

        if bool(opts.get("learn", True)):
            try:
                data = logic_store.load()
                existing = [r.get("match", "") for r in data.get("rules", [])]
                cands = derive_teach_candidates(all_recs, existing_matches=existing)
                if cands:
                    data.setdefault("rules", []).extend(cands)
                    logic_store.save(data)
                    log(f"Logic: {len(cands)} new item(s) added to Remembered Logic to teach.")
            except Exception as e:
                log(f"   (learn-logic skipped: {e})")

        if st.cancel:
            log("⛔ Scan cancelled before writing — no workbook saved."); return
        import idp_settings
        site = idp_project.detect_site_name(pdfs) or "IDP"
        out_path = idp_settings.resolve_output_path(site, opts.get("output"), pdfs)
        log(f"Site '{site}' → saving to dictated folder: {os.path.dirname(out_path)}")
        target = versioned_path(out_path)
        if target != out_path:
            log(f"(output exists — writing new version: {os.path.basename(target)})")
        clr = bool(opts.get("clear_dev", True))
        if clr:
            log("Deviation-notes column will be written blank (clean sheets).")
        log(f"Writing {len(all_recs)} conduits → {os.path.basename(target)} …")
        out = write_workbook(all_recs, opts.get("template"), target,
                             clear_rows=bool(opts.get("clear_rows", True)),
                             add_flags=bool(opts.get("flags", True)), clear_deviations=clr)
        log(f"Saved: {out}")
        if bool(opts.get("nogrey", True)):
            ng = versioned_path(os.path.splitext(out)[0] + "_NoGrey.xlsm")
            n = degrey(out, ng)
            log(f"De-greyed copy: {os.path.basename(ng)}  ({n} cells)")
        try:
            st.provenance = idp_project.build_provenance(all_recs)
        except Exception:
            pass
        _confidence(all_recs, out, log, poor=st.poor_yield)
        _elapsed_min = (time.time() - _t0) / 60.0
        log(f"⏱ Generated the Excel in {_elapsed_min:.1f} min "
            f"({time.time() - _t0:.0f}s) for {len(all_recs)} conduit(s).")
        log("Done. ✔")
    except Exception as e:
        log("ERROR: " + str(e))
        log(traceback.format_exc())
    finally:
        st.running = False


def _confidence(all_recs, out, log, poor=False):
    try:
        _grps = [g for r in all_recs for g in (r.get("fill") or [])]
        _nc = len(all_recs) or 1
        _ng = len(_grps) or 1
        # A near-empty extraction is never "HIGH" — the schedule wasn't read. Report it as
        # INCOMPLETE so a junk workbook is never mistaken for a good one.
        if poor or len(all_recs) < 3 or not _grps:
            log("")
            log("── WORKBOOK FILL CONFIDENCE ────────────────────────────")
            log(f"   Workbook: {os.path.basename(out)}")
            log("   Confidence this workbook is correctly filled: INCOMPLETE  (0%)")
            log(f"   Only {len(all_recs)} conduit(s) / {len(_grps)} fill group(s) were read — "
                "the conduit schedule was NOT captured. Do not use this workbook as-is.")
            log("───────────────────────────────────────────────────────")
            return
        # count BOTH schedule-OCR and finished-IDP-OCR rows — an all-graphical-OCR read must not
        # report HIGH (every cell still needs a human check against the sheet).
        _ocr = sum(1 for r in all_recs if {"from_ocr_schedule", "from_idp_ocr"} & set(r.get("flags") or []))
        _ocr_low = sum(1 for r in all_recs
                       if {"ocr_low_confidence", "idp_ocr_low_confidence"} & set(r.get("flags") or []))
        _low_sym = sum(1 for g in _grps
                       if min(g.get("s_symbol_conf", 1.0), g.get("d_symbol_conf", 1.0)) < 0.6)
        _assumed = sum(1 for g in _grps if g.get("connection_remodel") or g.get("type_note"))
        _mfg = sum(1 for g in _grps if str(g.get("type")) == "MFG_CABLE")
        _assumed_gnd = sum(1 for r in all_recs if not r.get("ground_authoritative")
                           for g in (r.get("fill") or []) if g.get("auto_ground"))
        _flags = sum(len(r.get("flags") or []) for r in all_recs)
        _cable = sum(1 for r in all_recs if "fill_from_cable_schedule" in (r.get("flags") or []))
        _termed = sum(1 for r in all_recs if any(
            (w.get("src") or ("", "", ""))[2] or (w.get("dst") or ("", "", ""))[2]
            for w in (r.get("wires") or [])))
        _score = max(0.0, min(100.0, 100.0 - 22 * (_ocr_low / _nc) - 25 * (_low_sym / _ng)
                              - 10 * (_assumed / _ng) - 8 * (_mfg / _ng) - 12 * (_assumed_gnd / _ng)))
        _level = "HIGH" if _score >= 88 else "MEDIUM" if _score >= 70 else "LOW"
        log("")
        log("── WORKBOOK FILL CONFIDENCE ────────────────────────────")
        log(f"   Workbook: {os.path.basename(out)}")
        log(f"   Confidence this workbook is correctly filled: {_level}  ({_score:.0f}%)")
        log(f"   conduits filled: {len(all_recs)}   fill groups: {len(_grps)}")
        log(f"   fill from CABLE schedule (precise types + real grounds): {_cable}/{len(all_recs)}")
        log(f"   conduits with EDC terminal landings: {_termed}/{len(all_recs)}")
        log(f"   OCR-sourced rows: {_ocr}  (low-confidence: {_ocr_low})")
        log(f"   uncertain symbols: {_low_sym}   type assumptions: {_assumed}"
            f"   whole-cable: {_mfg}   synthesized grounds: {_assumed_gnd}")
        log(f"   amber flags to verify: {_flags}")
        log("───────────────────────────────────────────────────────")
    except Exception as e:
        log(f"(confidence summary skipped: {e})")


# ── JS API exposed to the webview front-end ──────────────────────────────────
class Api:
    def _win(self):
        import webview
        return webview.windows[0] if webview.windows else None

    def pick_files(self):
        import webview
        w = self._win()
        r = w.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                 file_types=("Sources (*.pdf;*.xlsm;*.xlsx)", "All files (*.*)"))
        return list(r) if r else []

    def pick_folder(self):
        import webview, idp_settings
        w = self._win()
        recents = idp_settings.get_recent_projects()
        start = os.path.dirname(recents[0]) if recents else ""
        r = w.create_file_dialog(webview.FOLDER_DIALOG, directory=start)
        if not r:
            return {"folder": "", "files": []}
        folder = r[0]
        idp_settings.add_recent_project(folder)   # remember this project folder
        return {"folder": folder, "files": _walk_sources(folder)}

    def scan_folder(self, folder):
        """Re-open a remembered project folder (from a Recent chip) without a dialog."""
        import idp_settings
        if not folder or not os.path.isdir(folder):
            return {"folder": "", "files": []}
        idp_settings.add_recent_project(folder)   # bump to most-recent
        return {"folder": folder, "files": _walk_sources(folder)}

    def pick_template(self):
        import webview, idp_settings
        cur = idp_settings.get_template_path()
        start = os.path.dirname(cur) if cur else ""
        r = self._win().create_file_dialog(webview.OPEN_DIALOG, directory=start,
                                           file_types=("Template (*.xlsm)", "All files (*.*)"))
        path = r[0] if r else ""
        if path:
            idp_settings.set_template_path(path)   # remember as the designated template
        return path

    def _block_library_info(self):
        import idp_settings
        try:
            import idp_project_symbols
            src = (idp_settings.get_block_library_dir()
                   or idp_project_symbols.block_library_source())
            n = idp_project_symbols.symbol_count()
        except Exception:
            src, n = idp_settings.get_block_library_dir(), 0
        return {"dir": src or "", "blocks": n}

    def pick_block_library(self):
        """Pick the block-library folder (one .dwg per symbol block). Remembered so the exe
        keeps inferring symbols from it — no AutoCAD scan — even if it moves."""
        import webview, idp_settings
        info = self._block_library_info()
        start = info["dir"] if info["dir"] and os.path.isdir(info["dir"]) else ""
        r = self._win().create_file_dialog(webview.FOLDER_DIALOG, directory=start)
        folder = r[0] if r else ""
        if folder:
            idp_settings.set_block_library_dir(folder)
            try:
                import idp_project_symbols
                return {"dir": folder,
                        "blocks": idp_project_symbols.symbol_count(refresh=True)}
            except Exception:
                return {"dir": folder, "blocks": 0}
        return info

    # ── UPDATE check: is the built exe current with the source? ──────────────────────
    def _source_dir(self):
        """The IDP Extractor folder that holds the .py/.spec/webui a rebuild uses."""
        import sys
        for base in (os.path.dirname(os.path.abspath(__file__)),
                     os.path.dirname(os.path.abspath(sys.executable))):
            d = base
            for _ in range(6):
                if os.path.isfile(os.path.join(d, "IDP_ControlPanel.spec")):
                    return d
                nd = os.path.dirname(d)
                if nd == d:
                    break
                d = nd
        return os.path.dirname(os.path.abspath(__file__))

    def _rebuild_target(self, src=None):
        """The exe path a rebuild writes to (remembered in rebuild_target.txt, else dist/)."""
        src = src or self._source_dir()
        dest = ""
        cfg = os.path.join(src, "rebuild_target.txt")
        try:
            if os.path.isfile(cfg):
                dest = open(cfg, encoding="utf-8").read().strip()
        except OSError:
            pass
        if not dest:
            dest = os.path.join(src, "dist", "IDP_ControlPanel.exe")
        if not dest.lower().endswith(".exe"):
            dest = os.path.join(dest, "IDP_ControlPanel.exe")
        return dest

    def _newest_source_mtime(self, src):
        """Newest mtime among everything a rebuild bundles (.py/.spec/webui + data JSON)."""
        newest = 0.0
        for root, dirs, files in os.walk(src):
            low = root.replace("\\", "/").lower()
            if any(s in low for s in ("/dist", "/build", "/dist_staging",
                                      "__pycache__", "/.git")):
                dirs[:] = []
                continue
            for f in files:
                if f.lower().endswith((".py", ".spec", ".json", ".html", ".css", ".js")):
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                    except OSError:
                        pass
        return newest

    def check_update(self, path=""):
        """Version-compare the local install against the shared PULL folder (or `path`).
        Returns {current, latest, up_to_date, out_of_date, reachable, path}. Cheap — just a
        directory listing of the shared folder + a local version.json read."""
        import idp_versioning, idp_settings
        pull = (path or "").strip().strip('"') or idp_settings.get_version_pull_dir()
        return idp_versioning.status(pull)

    def run_update(self, path=""):
        """Pull the latest published version from the shared folder over the local files
        (source/data/dist only — never .venv/node_modules). Applied on the next launch.
        Streams progress to poll_update(). Safe to run while the app is open here and on
        other computers."""
        if STATE.update_running:
            return False
        import idp_versioning, idp_settings, threading
        pull = (path or "").strip().strip('"') or idp_settings.get_version_pull_dir()
        STATE.update_lines = []

        STATE.update_applied = False

        def _u():
            STATE.update_running = True
            try:
                res = idp_versioning.apply_update(
                    pull, log=lambda m: STATE.update_lines.append(str(m)))
                if not res.get("ok"):
                    STATE.update_lines.append("Update failed: " + res.get("error", "unknown"))
                elif not res.get("updated"):
                    STATE.update_lines.append(res.get("note", "Already up to date."))
                else:
                    STATE.update_applied = True
                    STATE.update_lines.append(
                        f"✔ Updated to v{res['version']} — reloading LISA to finish (no manual "
                        "restart needed) …")
            except Exception as e:
                STATE.update_lines.append(f"update error: {e}")
            finally:
                STATE.update_running = False
        threading.Thread(target=_u, daemon=True).start()
        return True

    def poll_update(self):
        return {"log": "\n".join(STATE.update_lines), "running": STATE.update_running,
                "applied": STATE.update_applied}

    def restart_app(self):
        """Relaunch LISA in a FRESH process (so just-updated code actually loads) and close this
        window — so an Update finishes without the user manually quitting + reopening. New code
        can't hot-swap into a running Python process, so a fresh process is the only correct way;
        this just automates it. Best-effort: on failure, tells the user to restart manually."""
        import subprocess
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # LISA fused root
            app_py = os.path.join(root, "app.py")
            if not os.path.isfile(app_py):
                return {"ok": False, "error": "app.py not found — please restart LISA manually."}
            # launch a fresh, detached instance (it loads the updated code + picks a free port)
            flags = 0
            if os.name == "nt":
                flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([sys.executable, app_py], cwd=root, close_fds=True,
                             creationflags=flags)

            # close THIS window a moment later, so the HTTP response returns and the new instance
            # can start binding before we release. Hard-exit guarantees the old process ends.
            def _close():
                import time
                time.sleep(1.5)
                try:
                    import webview
                    for w in list(getattr(webview, "windows", []) or []):
                        try:
                            w.destroy()
                        except Exception:
                            pass
                except Exception:
                    pass
                os._exit(0)
            threading.Thread(target=_close, daemon=True).start()
            return {"ok": True, "restarting": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── version-control paths + training password (used by the Autofill panel) ───────
    def get_version_paths(self):
        import idp_settings
        return {"pull": idp_settings.get_version_pull_dir(),
                "push": idp_settings.get_version_push_dir()}

    def set_version_paths(self, pull="", push=""):
        """Set the SHARED version-control folder. Writes it to version_source.txt (which travels
        INSIDE the app folder), so setting it once here propagates to every copied machine when
        the folder is copied — and clears any per-machine override so the shared path governs."""
        import idp_settings
        shared = ((pull or "").strip() or (push or "").strip()).strip('"')
        idp_settings.set_version_source_path(shared)
        try:                                     # drop stale per-machine overrides so the shared file wins
            s = idp_settings.load()
            s.pop("version_pull_dir", None)
            s.pop("version_push_dir", None)
            idp_settings.save(s)
        except Exception:
            pass
        return self.get_version_paths()

    def pick_version_dir(self):
        """Pick a folder for the version pull/push path (native dialog)."""
        import webview
        r = self._win().create_file_dialog(webview.FOLDER_DIALOG)
        return r[0] if r else ""

    def check_training_password(self, pw):
        """Gate the Training tab. On success, UNLOCK the training actions server-side — the
        UI hiding alone is cosmetic (the HTTP bridge is callable directly), so the real gate
        is STATE.train_unlocked, enforced by run_training/ask_claude/set_api_key below. (The
        password does live in this source, so this is access-control convenience, not secrecy.)"""
        ok = (str(pw or "") == "Fr1ends@Lyles")
        if ok:
            STATE.train_unlocked = True
        return {"ok": ok}

    def get_settings(self):
        """Startup state for the UI: the remembered template + recent project folders +
        the block-library folder the exe infers symbols from.

        FAST BY DESIGN: this is on the first-render path (LISA loads it the instant you open
        the Autofill tab), so it must NOT compute the exe-update status. check_update() walks
        the whole source tree with getmtime — minutes on a OneDrive/cloud-synced folder — and
        would block the render (blank screen). The update bar checks lazily, on demand only."""
        import idp_settings
        return {"template": idp_settings.get_template_path(),
                "output_dir": idp_settings.get_output_dir(),
                "recent_projects": idp_settings.get_recent_projects(),
                "block_library": self._block_library_info(),
                "update": None,
                "version": {"pull": idp_settings.get_version_pull_dir(),
                            "push": idp_settings.get_version_push_dir()}}

    def pick_output(self):
        import webview
        r = self._win().create_file_dialog(webview.FOLDER_DIALOG)
        folder = r[0] if r else ""
        if folder:
            try:
                import idp_settings
                idp_settings.set_output_dir(folder)   # remember as the dictated folder
            except Exception:
                pass
        return folder

    def suggest_output(self, files):
        """'<dictated base>/<Project>/<Site>_FILLED.xlsm' — the site name from the project
        folder, saved into a per-project subfolder under the DICTATED base (never the
        project's own source folder)."""
        try:
            import idp_settings
            site = idp_project.detect_site_name(files or []) or "IDP"
            return idp_settings.resolve_output_path(site, None, files)
        except Exception:
            return ""

    def run_scan(self, opts):
        if STATE.running or STATE.train_running:
            return False
        STATE.log_lines = []
        STATE.cancel = False
        STATE.running = True                    # set BEFORE spawning to close the check→spawn race
        threading.Thread(target=_scan_core, args=(opts, STATE), daemon=True).start()
        return True

    def cancel_scan(self):
        STATE.cancel = True
        return True

    def poll(self):
        return {"log": "\n".join(STATE.log_lines), "running": STATE.running}

    def logic_rules(self):
        try:
            rules = logic_store.load().get("rules", [])
            for r in rules:
                r["source"] = logic_store.rule_source(r)   # 'manual' or 'generated'
            return rules
        except Exception:
            return []

    def logic_save(self, rule):
        try:
            logic_store.add_rule(rule or {}, source="manual")   # panel-saved => MANUAL
            return True
        except Exception:
            return False

    def logic_add_rule(self, rule):
        """Add one MANUAL Remembered-Logic rule from the panel's Add-rule form + apply it."""
        try:
            logic_store.add_rule(rule or {}, source="manual")
            logic_store.apply()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def logic_delete(self, keys):
        """Delete rules by key ([type,match,context,result] each); stash them for undo."""
        try:
            removed = logic_store.delete_rules(keys or [])
            STATE.logic_undo = list(removed)
            logic_store.apply()
            return {"ok": True, "removed": len(removed)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def logic_undo_delete(self):
        """Restore the most recently deleted batch of rules."""
        try:
            n = len(STATE.logic_undo or [])
            if not n:
                return {"ok": True, "restored": 0, "note": "nothing to undo"}
            logic_store.undo_delete(STATE.logic_undo)
            STATE.logic_undo = []
            logic_store.apply()
            return {"ok": True, "restored": n}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def logic_add(self):
        return True   # legacy no-op (kept for back-compat)

    def provenance(self):
        return STATE.provenance

    @staticmethod
    def _prov_row(row):
        """A provenance entry as (Conduit, Sheet, Field, Value, Source). build_provenance emits
        DICTS; tolerate a legacy tuple too so this never renders key-names or blanks again."""
        if isinstance(row, dict):
            return [row.get("conduit", ""), row.get("sheet", ""), row.get("field", ""),
                    row.get("value", ""), row.get("source", "")]
        row = list(row) + ["", "", "", "", ""]
        return row[:5]

    def provenance_csv(self):
        """Provenance as CSV TEXT for an in-browser download (a native Save dialog is
        unreliable inside LISA's iframe, so the panel downloads this text instead)."""
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(("Conduit", "Sheet", "Field", "Value", "Source"))
        for row in (STATE.provenance or []):
            w.writerow(self._prov_row(row))
        return {"ok": True, "text": buf.getvalue(), "rows": len(STATE.provenance or [])}

    def provenance_export(self):
        import webview
        if not STATE.provenance:
            return ""
        r = self._win().create_file_dialog(webview.SAVE_DIALOG, save_filename="provenance.csv")
        path = r if isinstance(r, str) else (r[0] if r else "")
        if not path:
            return ""
        rows = [("Conduit", "Sheet", "Field", "Value", "Source")]
        rows += [self._prov_row(row) for row in STATE.provenance]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        return path

    def conduit_index(self):
        """The last scan's CONDUIT INDEX mapping (name -> source/dest/size/type + fill count),
        for the Conduit Index Mapping tab. Reads STATE.last_records."""
        def _j(v):
            if isinstance(v, (list, tuple)):
                return " ".join(str(x) for x in v if str(x).strip())
            return str(v or "")
        out = []
        for r in (STATE.last_records or []):
            out.append({
                "name": r.get("name") or r.get("conduit_name") or "",
                "source": _j(r.get("source") or r.get("src") or r.get("from")),
                "dest": _j(r.get("dest") or r.get("dst") or r.get("to")),
                "size": _j(r.get("size") or r.get("csize") or r.get("conduit_size")),
                "type": _j(r.get("ctype") or r.get("conduit_type") or r.get("type")),
                "fills": len(r.get("fill") or []),
            })
        return {"rows": out, "count": len(out)}

    def conduit_index_csv(self):
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(("Conduit", "Source", "Dest", "Size", "Type", "Fill groups"))
        for r in self.conduit_index()["rows"]:
            w.writerow([r["name"], r["source"], r["dest"], r["size"], r["type"], r["fills"]])
        return {"ok": True, "text": buf.getvalue(), "rows": self.conduit_index()["count"]}

    def logic_ruleset_csv(self):
        """Export the CURRENT Remembered-Logic rule set as CSV. Reads the store LIVE from disk
        every call (logic_store.load()), so the download always reflects the latest rules —
        never a stale in-memory copy. Read-only: touches nothing else."""
        import io
        try:
            data = logic_store.load()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        rules = data.get("rules", []) or []
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(("Type", "Match", "Result", "Context", "Note", "Source"))
        for r in rules:
            w.writerow([r.get("type", ""), r.get("match", ""), r.get("result", ""),
                        r.get("context", ""), r.get("note", ""),
                        logic_store.rule_source(r)])
        return {"ok": True, "text": buf.getvalue(), "rows": len(rules)}

    @staticmethod
    def _parse_ruleset(text, fmt=None):
        """Parse imported rule-set text into rule dicts {type,match,result,context,note,source}.
        Accepts the CSV that 'Download rule set' emits (Type,Match,Result,Context,Note,Source) OR
        a learned_logic.json ({"rules":[...]} or a bare [...]). Only rows with a KNOWN type and a
        non-empty match survive, so a stray/garbage file can't inject junk rules."""
        import io, json as _json
        t = (text or "").strip()
        raw = []
        looks_json = (fmt == "json") or t[:1] in ("{", "[")
        if looks_json:
            data = _json.loads(t)
            src = data.get("rules", []) if isinstance(data, dict) else (data or [])
            raw = [r for r in src if isinstance(r, dict)]
        else:
            for row in csv.DictReader(io.StringIO(t)):
                low = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
                raw.append({"type": low.get("type", ""), "match": low.get("match", ""),
                            "result": low.get("result", ""), "context": low.get("context", ""),
                            "note": low.get("note", ""), "source": low.get("source", "")})
        out = []
        for r in raw:
            typ = str(r.get("type", "")).strip().lower()
            match = str(r.get("match", "")).strip()
            if typ not in logic_store.RULE_TYPES or not match:
                continue
            src = "manual" if str(r.get("source", "")).strip().lower() == "manual" else "generated"
            out.append({"type": typ, "match": match, "result": str(r.get("result", "")).strip(),
                        "context": str(r.get("context", "")).strip(),
                        "note": str(r.get("note", "")).strip(), "source": src})
        return out

    def logic_import(self, text, fmt=None):
        """Upload a rule set (CSV or JSON) and MERGE it into the current Remembered Logic — adds
        new rules, skips ones already present (by type/match/context/result), and un-suppresses any
        that had been deleted. Pairs with 'Clear all rules' for a clean replace. Returns
        {ok, added, skipped, total}."""
        try:
            parsed = self._parse_ruleset(text or "", fmt)
        except Exception as e:
            return {"ok": False, "error": "could not read the file (%s)" % e}
        if not parsed:
            return {"ok": False, "error": "no valid rules found in the file."}
        try:
            existing = {logic_store._rkey(r) for r in logic_store._raw().get("rules", [])}
            added = 0
            for r in parsed:
                k = logic_store._rkey(r)
                if k in existing:
                    continue
                logic_store.add_rule(r, source=r.get("source") or "manual")
                existing.add(k)
                added += 1
            logic_store.apply()
            return {"ok": True, "added": added, "skipped": len(parsed) - added, "total": len(parsed)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def logic_clear_all(self):
        """Remove EVERY Remembered-Logic rule (including built-in defaults — suppressed via the
        store's `removed` list so they don't re-merge on reload). Stashes the cleared rules so
        '↺ Undo delete' restores them all. Returns {ok, removed}."""
        try:
            visible = logic_store.load().get("rules", [])
            keys = [list(logic_store._rkey(r)) for r in visible]
            removed = logic_store.delete_rules(keys)
            STATE.logic_undo = list(removed)
            logic_store.apply()
            return {"ok": True, "removed": len(removed)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _uncertainties_packet(self):
        """(text, count, note) for the open uncertainties — also writes the .md packet to disk.
        text='' with a note when there's nothing open. Shared by the download + save-as paths."""
        import idp_escalate, idp_project
        items = []
        if STATE.last_training:
            items = idp_escalate.from_training_report(STATE.last_training)
        if not items and STATE.last_records:
            items = idp_escalate.collect_uncertain(STATE.last_records)
        if not items:
            return "", 0, "No open uncertainties — run a scan or Compare & Learn first."
        try:
            project = idp_project.detect_site_name([]) or ""
        except Exception:
            project = ""
        path = idp_escalate.build_packet(items, project=project)     # writes the .md packet too
        text = ""
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            pass
        return text, len(items), ""

    def download_uncertainties(self):
        """Build the 'questions for Claude' packet and return it as text (in-browser download —
        the fallback used when the native Save-As dialog isn't available)."""
        try:
            text, count, note = self._uncertainties_packet()
            if not text:
                return {"ok": True, "text": "", "note": note}
            return {"ok": True, "text": text, "count": count}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_uncertainties(self):
        """Let the user CHOOSE where to save the uncertainties packet via a native Save-As
        dialog, then write it there. Returns {ok, path, count} on success, {ok, cancelled} if
        the dialog was dismissed, {ok, note} if there's nothing open, or {ok:False, error}."""
        try:
            text, count, note = self._uncertainties_packet()
            if not text:
                return {"ok": True, "note": note}
            import webview, idp_settings
            try:
                start = idp_settings.get_output_dir() or ""
            except Exception:
                start = ""
            r = self._win().create_file_dialog(
                webview.SAVE_DIALOG, directory=start,
                save_filename="uncertainties_for_claude.txt",
                file_types=("Text file (*.txt)", "All files (*.*)"))
            dest = (r[0] if isinstance(r, (list, tuple)) else r) or ""
            if not dest:
                return {"ok": True, "cancelled": True}       # user closed the dialog
            if not dest.lower().endswith(".txt"):
                dest += ".txt"
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text)
            return {"ok": True, "path": dest, "count": count}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pick_dir(self):
        """Pick a single FOLDER and return its path (no walk) — for training inputs
        that are DWG/output folders passed to the comparator as-is."""
        import webview
        r = self._win().create_file_dialog(webview.FOLDER_DIALOG)
        return r[0] if r else ""

    def run_training(self, plans, finished, generated):
        """Compare finished IDPs (ground truth) against our generated output, learn the
        safe gaps into Remembered Logic + the Claude skills, and stash the report so
        'Ask Claude' can escalate the rest."""
        if not STATE.train_unlocked:
            STATE.train_lines = ["🔒 Training is locked — enter the training password first."]
            return False
        if STATE.train_running:
            return False
        if STATE.running:
            STATE.train_lines = ["A scan is running — wait for it to finish before training "
                                 "(training snapshots the app and must not race a scan)."]
            return False
        STATE.train_lines = []

        def _t():
            try:
                STATE.train_running = True
                import idp_training
                if not (finished and generated):
                    STATE.tlog("Add at least the Finished IDPs and the Generated IDP to compare.")
                    return
                STATE.tlog("Comparing finished IDPs against our output …")
                rep = idp_training.run_training(
                    plans=list(plans or []), finished=list(finished or []),
                    generated=list(generated or []), learn=True, log=STATE.tlog)
                STATE.last_training = rep
                STATE.tlog(rep.get("summary", ""))
                if rep.get("mismatch"):
                    # different-project pair: the warning summary is already logged. Nothing was
                    # learned, so skip the gap dump, the 'rules applied' confirmation, and the
                    # version publish — the user just needs to load the matching pair and retrain.
                    return
                for g in (rep.get("gaps") or [])[:40]:
                    STATE.tlog(f"  gap [{g['conduit']}] {g['field']}: "
                               f"finished={g['ground']!r} ours={g['ours']!r}")
                extra = len(rep.get("gaps") or []) - 40
                if extra > 0:
                    STATE.tlog(f"  … +{extra} more gaps")
                if rep.get("learned"):
                    STATE.tlog(f"Learned {len(rep['learned'])} rule(s) → Remembered Logic "
                               f"+ skill references updated.")
                if rep.get("uncertain"):
                    STATE.tlog(f"{len(rep['uncertain'])} item(s) need judgment — "
                               f"click 'Ask Claude about uncertainties'.")
                # Re-apply the store to the LIVE engines NOW so the just-learned rules take
                # effect immediately, and echo the applied counts so the user SEES that training
                # actually changed what the next scan will do (this is the confirmation that was
                # missing when 'training had no effect' was reported).
                try:
                    STATE.tlog(logic_store.apply())
                    STATE.tlog("↳ Saved to Remembered Logic — these rules apply automatically on "
                               "your next Scan & Fill (no restart needed).")
                except Exception as e:
                    STATE.tlog(f"(re-apply skipped: {e})")
                # NEW: every training run also ARCHIVES the whole LISA fused folder as a new
                # version and publishes it to the shared folder, so the just-learned Remembered
                # Logic (and any other change) propagates to every computer via their Update
                # button. The auto-learn above still runs — this is in addition to it.
                try:
                    import idp_versioning, idp_settings
                    push = idp_settings.get_version_push_dir()
                    res = idp_versioning.create_version(push_dir=push, log=STATE.tlog)
                    if res.get("published"):
                        STATE.tlog(f"✔ Archived this state as v{res['version']} and published "
                                   f"to {push} — other computers can now Update to it.")
                    else:
                        STATE.tlog(f"✔ Archived this state as v{res['version']} locally "
                                   f"(set a version-control folder to publish it to others).")
                except Exception as e:
                    STATE.tlog(f"(versioning skipped: {e})")
            except Exception as e:
                STATE.tlog("training error: " + str(e))
            finally:
                STATE.train_running = False
        threading.Thread(target=_t, daemon=True).start()
        return True

    def publish_version(self):
        """PUBLISH the CURRENT build to the shared/server Version Control folder as a new
        version — snapshots the whole app folder AS IT IS RIGHT NOW (so ANY change, including
        edits made via Claude, is captured), bumps the version, and copies it to the server so
        every other install's Update button can pull it. No training/Compare-&-Learn needed."""
        if not STATE.train_unlocked:
            STATE.train_lines = ["🔒 Training is locked — enter the training password first."]
            return {"ok": False, "error": "locked"}
        if STATE.train_running or STATE.running:
            return {"ok": False, "error": "A scan or training is running — wait for it to finish."}
        STATE.train_lines = []

        def _pub():
            try:
                STATE.train_running = True
                import idp_versioning, idp_settings
                push = idp_settings.get_version_push_dir()
                STATE.tlog(f"Publishing the current build to: {push} …")
                res = idp_versioning.create_version(push_dir=push, log=STATE.tlog)
                if res.get("published"):
                    STATE.tlog(f"✔ Published v{res['version']} to {push}.")
                    STATE.tlog("   Everyone else can now press UPDATE to get this version.")
                else:
                    STATE.tlog(f"✔ Archived v{res['version']} locally, but NOTHING was published — "
                               "set the Version-control folder above to your SERVER path "
                               "(\\\\server\\share\\…\\Version Control) and publish again.")
            except Exception as e:
                STATE.tlog(f"Publish error: {e}")
            finally:
                STATE.train_running = False
        threading.Thread(target=_pub, daemon=True).start()
        return {"ok": True, "started": True}

    def ask_claude(self):
        """Escalate the open uncertainties to Claude. If an API key is configured the
        exe asks Claude directly and applies the returned Remembered-Logic rules; else
        it writes an ASK_CLAUDE.md packet for an attached Claude Code chat to resolve."""
        if not STATE.train_unlocked:
            STATE.train_lines = ["🔒 Training is locked — enter the training password first."]
            return False
        if STATE.train_running:
            return False
        STATE.train_running = True

        def _t():
            try:
                import idp_escalate, idp_project
                items = []
                if STATE.last_training:
                    items = idp_escalate.from_training_report(STATE.last_training)
                if not items and STATE.last_records:
                    items = idp_escalate.collect_uncertain(STATE.last_records)
                if not items:
                    STATE.tlog("Nothing to ask — run a scan or Compare & Learn first "
                               "(no open uncertainties).")
                    return
                try:
                    project = idp_project.detect_site_name([]) or ""
                except Exception:
                    project = ""
                path = idp_escalate.build_packet(items, project=project)
                STATE.tlog(f"Wrote {len(items)} question(s) for Claude → {path}")
                reply = None
                try:
                    with open(path, encoding="utf-8") as fh:
                        reply = idp_escalate.ask_claude_api(fh.read())
                except Exception:
                    reply = None
                if reply:
                    added = idp_escalate.apply_rule_lines(reply)
                    STATE.tlog(f"✔ Claude answered (API): {added} rule(s) applied to "
                               f"Remembered Logic.")
                else:
                    STATE.tlog("No API key set (or the call failed). Packet written — "
                               "tell your attached Claude Code chat \"resolve the "
                               "extractor's open questions\" to learn them, or set an "
                               "Anthropic API key below to have the exe do it automatically.")
            except Exception as e:
                STATE.tlog("ask-claude error: " + str(e))
            finally:
                STATE.train_running = False
        threading.Thread(target=_t, daemon=True).start()
        return True

    def get_api_key_status(self):
        import idp_settings
        return {"has_key": idp_settings.has_api_key()}

    def set_api_key(self, key):
        if not STATE.train_unlocked:
            return {"has_key": False, "locked": True}
        import idp_settings
        idp_settings.set_api_key(key)
        return {"has_key": idp_settings.has_api_key()}

    def poll_training(self):
        return {"log": "\n".join(STATE.train_lines), "running": STATE.train_running}


# Shared Api instance the HTTP bridge (/api/call/<method>) dispatches to. Constructing it is
# cheap and touches no GUI — every method imports `webview` lazily only when it needs a dialog.
API = Api()


def launch():
    import time
    import webview
    try:
        import pythoncom
        _co = True
    except Exception:
        _co = False

    port = _free_port()

    def _run_flask():
        if _co:
            pythoncom.CoInitialize()
        app.run(port=port, debug=False, threaded=True, use_reloader=False)

    threading.Thread(target=_run_flask, daemon=True).start()
    time.sleep(1.2)
    webview.create_window("LISA · IDP Extractor", url=f"http://127.0.0.1:{port}",
                          width=1320, height=880, min_size=(1000, 680),
                          background_color="#0d1a28", js_api=Api())
    webview.start()


if __name__ == "__main__":
    if "--classic" in sys.argv:
        import idp_control_panel
        idp_control_panel.App().mainloop()
    else:
        launch()
