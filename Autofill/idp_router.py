"""
idp_router.py — smart PROJECT-FOLDER routing.

Drop a whole project folder and this classifies every file by ROLE, then maps each
IDP-workbook FIELD to the source that actually carries it — so each field is populated
from the right document instead of the user hand-picking files. It composes the existing
detectors (schedule / cable-schedule / EDC / panelboard / finished-IDP / cover-letter)
into one manifest + a human-readable routing report for the scan log.

Classification is a cheap TEXT sniff (title/head text + filename) — no OCR — so it stays
fast on large folders. The heavy extraction still happens downstream; this just decides
WHAT each source is and WHICH fields it feeds, and lets the pipeline skip files that carry
no conduit/terminal data (cover letters, cut sheets).
"""
import os
import re

# ── the field → source contract ("each field knows what to look for") ────────
# (field label, role that carries it, plain-English source description)
FIELD_SOURCES = [
    ("Conduit Name · Source · Dest · Size · Type", "conduit_schedule",
     "plans CONDUIT SCHEDULE (e.g. MC-E-8 / E-3)"),
    ("Fill Type · Wire Ct · Gauge · Colors", "cable_schedule",
     "CABLE SCHEDULE (e.g. MC-E-9); else the conduit schedule's fill columns"),
    ("Ground (S/D Tag = GND)", "cable_schedule",
     "cable schedule W/GND column; else convention (every real feeder grounded)"),
    ("S/D Symbol", "cut_sheet",
     "device inference from the end names + submittal cut sheets + this project's own DWGs"),
    ("S/D Term — 3φ feeder (ØA/ØB/ØC)", "edc_three_line",
     "EDC THREE-LINE / one-line diagram"),
    ("S/D Term — branch circuit (CKT-##)", "panelboard",
     "EDC PANELBOARD SCHEDULE"),
    ("S/D Term — discrete control (TBDI)", "edc_plc_io",
     "EDC PLC I/O digital-input sheet"),
    ("S/D Term — analog (TBAI)", "edc_plc_io",
     "EDC PLC I/O analog-input sheet"),
    ("Supporting Documents", "*",
     "plan sheets + EDC drawings, selected per conduit by its fill type"),
]

# files that carry NO conduit/terminal data — routed out of the conduit-extraction loop
_SKIP_ROLES = {"cover_letter", "cut_sheet", "other", "skip"}
# roles that DO seed conduits (the conduit-extraction inputs)
_CONDUIT_ROLES = {"conduit_schedule", "cable_schedule", "finished_idp"}
_EDC_ROLES = {"edc_plc_io", "edc_three_line", "panelboard", "edc_other"}

_EXCEL = (".xlsx", ".xlsm", ".xls")


def _head_text(path, max_pages=14):
    try:
        import fitz
        d = fitz.open(path)
        try:
            return " ".join(d[i].get_text("text") for i in range(min(len(d), max_pages)))
        finally:
            d.close()
    except Exception:
        return ""


def _rev_key(name):
    """Sort key to pick the LATEST revision of a single-source role: prefer a higher
    R##/RS## and a later yyyymmdd date embedded in the filename."""
    u = name.upper()
    m = re.findall(r"\bR[S]?(\d{1,2})\b", u)
    rev = max((int(x) for x in m), default=-1)
    d = re.findall(r"(20\d{2})[._-]?(\d{2})[._-]?(\d{2})", name)
    date = max(("".join(x) for x in d), default="")
    return (rev, date, name)


def _pages_text(path, pages):
    """Combined text of specific page indices (for cheap EDC sub-classification)."""
    try:
        import fitz
        d = fitz.open(path)
        try:
            return " ".join(d[i].get_text("text") for i in pages
                            if 0 <= i < d.page_count).upper()
        finally:
            d.close()
    except Exception:
        return ""


def classify_file(path):
    """Return (role, reason). FAST path first — skip irrelevant subtrees (quotes/photos/
    estimating) without opening them, and recognize a document by its learned LAYOUT
    signature from the first page — so a whole project folder isn't deep-scanned file by
    file. Only relevant files whose type isn't recognized by signature fall through to the
    authoritative full-document detectors, and each confirmed type is LEARNED for next
    time. No OCR."""
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1].lower()
    if ext in _EXCEL:
        return "workbook_source", "Excel workbook (conduit/fill list or prior IDP)"
    if ext != ".pdf":
        return "other", "not a PDF/Excel source"

    _upname = base.upper()
    _low = path.replace("\\", "/").lower()
    # cover letters / transmittals never carry conduit/terminal data — decide by name
    # FIRST so a learned signature can't mistake them for the EDC package they cover.
    if re.search(r"_SCL_|_CL_|COVER\s*LETTER|TRANSMITTAL", _upname):
        return "cover_letter", "cover letter / transmittal (by name) — no conduit data"

    # ── FAST targeting: skip irrelevant folders/types instantly (no open) ──
    try:
        import idp_layouts as _L
        if _L.folder_relevance(path) == "skip":
            return "skip", "irrelevant folder/type — not scanned (quotes/photos/estimating)"
    except Exception:
        _L = None

    # vendor CUT SHEETS resemble EDC/PLC content (relay I/O, terminals) — decide them by
    # NAME/FOLDER context, NOT by the content fingerprint, so a switch datasheet isn't
    # mistaken for an EDC PLC sheet. A vendor PANEL/breaker drawing is still a panelboard.
    _in_cut = ("cut sheet" in _low or "cutsheet" in _low or "vendor cut" in _low)
    if re.search(r"DATASHEET|DATA\s*SHEET|CATALOG|SPEC\s*SHEET", _upname):
        return "cut_sheet", "vendor datasheet (by name)"
    if _in_cut and not re.search(r"PANEL|BREAKER|SWITCHBOARD|\bMCC\b|SCHEDULE", _upname):
        return "cut_sheet", "vendor cut-sheet folder (device submittal)"

    # recognize by learned/seed layout signature (first page only)
    if _L is not None:
        try:
            _fp = _L.fingerprint(path)
            _role, _score, _confident = _L.best_role(_fp)
            if _confident and _role:
                return _role, f"recognized by learned layout signature (match {_score})"
        except Exception:
            pass

    up = base.upper()
    head = _head_text(path).upper()

    def _slow():
        # cover letter — pure review doc, no conduit/terminal data
        if "_SCL_" in up or up.endswith("_SCL.PDF") or "SUBMITTAL COVER LETTER" in head:
            return "cover_letter", "submittal cover letter — no conduit/terminal data"
        # finished AIC IDP package (reference / possible conduit source of last resort)
        try:
            import idp_idp_pdf
            if idp_idp_pdf.is_idp_package(path) or re.search(r"SUB[_ ]?IDP|IDP[_ ]?DWG", up):
                return "finished_idp", "finished AIC IDP (reference; conduits only if no schedule)"
        except Exception:
            pass
        # conduit / cable schedule — authoritative page finders (scan the whole doc)
        try:
            import idp_vision
            if idp_vision.find_schedule_pages(path):
                return "conduit_schedule", "CONDUIT SCHEDULE (conduit metadata + cable list)"
        except Exception:
            pass
        try:
            import idp_cable_schedule
            if idp_cable_schedule.find_cable_schedule_pages(path):
                return "cable_schedule", "CABLE SCHEDULE (per-conductor specs + grounds)"
        except Exception:
            pass
        # panelboard schedule (AIC or vendor Eaton/SqD drawings)
        if ("PANELBOARD SCHEDULE" in head or re.search(r"\bPANEL\s*:", head)
                or ("PANEL" in up and re.search(r"MAIN BREAKER|POW-?R-?LINE|\bCKT\b", head))):
            return "panelboard", "panelboard schedule (branch-circuit breakers)"
        # EDC drawing package — confirm via the EDC sheet finder, then sub-classify
        edc_pages = []
        try:
            import idp_edc
            edc_pages = idp_edc.find_edc_sheets(path)
        except Exception:
            edc_pages = []
        if edc_pages or "_EDC_" in up:
            et = _pages_text(path, edc_pages[:40]) or head
            if any(k in et for k in ("PLC I/O", "TBDI", "TBAI", "TBDO", "DIGITAL INPUT",
                                     "ANALOG INPUT", "DIGITAL OUTPUT")):
                return "edc_plc_io", "EDC PLC I/O sheets (control/analog terminations)"
            if "THREE-LINE" in et or "THREE LINE" in et or "ONE-LINE" in et or "ONE LINE" in et:
                return "edc_three_line", "EDC three-/one-line (feeder phase terminations)"
            return "edc_other", "EDC drawing package (terminal landings)"
        if any(k in up for k in ("CUT SHEET", "CUTSHEET", "DATASHEET", "DATA SHEET",
                                 "SUBMITTAL", "CATALOG", "SPEC SHEET")):
            return "cut_sheet", "vendor cut sheet / submittal (device terminals)"
        return "other", "no conduit/terminal/schedule content detected"

    role, reason = _slow()
    # LEARN this confirmed layout so a similar one is recognized fast next time
    try:
        if _L and role in _L.SEED:
            _L.learn(path, role)
    except Exception:
        pass
    return role, reason


def classify(paths):
    """Classify many files into {role: [(path, reason), …]}."""
    manifest = {}
    for p in paths or []:
        try:
            role, reason = classify_file(p)
        except Exception as e:
            role, reason = "other", f"classify error: {e}"
        manifest.setdefault(role, []).append((p, reason))
    return manifest


def _folder_priority(path):
    """Search order for a file by its folder — ENGINEERING is always top priority (it
    holds the EDC submittal that carries the terminations), then the plans/bid drawings
    that carry the conduit + cable schedule, then everything else."""
    low = path.replace("\\", "/").lower()
    if any(k in low for k in ("engineering", "/eng/", "\\eng\\", "/edc", "edc_",
                              "_edc", "submittal")):
        return 0
    if any(k in low for k in ("plans", "bid document", "drawings", "/dwg", "electrical")):
        return 1
    return 2


# terminations sources (any one of these gives us S/D terms for the IDP)
_TERM_ROLES = {"edc_plc_io", "edc_three_line", "panelboard", "edc_other"}


def _idp_needs_met(have):
    """True once we hold everything an IDP needs: the CONDUIT SCHEDULE (its fill columns
    carry types/wire-cts, and the cable schedule rides the same PDF) AND at least one
    TERMINATIONS source. Finished IDPs alone also satisfy the conduit side."""
    has_conduit = bool(have & {"conduit_schedule", "cable_schedule", "finished_idp"})
    has_terms = bool(have & _TERM_ROLES)
    return has_conduit and has_terms


# ── name-based pre-selection (NO file opened) — the skill's convention targeting ──
# Names that never carry conduit/terminal data even inside Engineering — dropped up front
# so we don't OCR a 300-page O&M manual or a comment log looking for a schedule.
# token boundaries here are "not a letter" (so _BOM_, RFI-, etc. all match — underscore is
# NOT a regex word boundary, which is why \bBOM\b missed _BOM_)
def _tok(*words):
    return r"(?<![A-Z])(?:" + "|".join(words) + r")(?![A-Z])"


_DROP_NAME = re.compile(
    r"COMMENT\s*LOG|COMMENTLOG|MEETING|TRANSMITTAL|COVER\s*LETTER|DATASHEET|DATA\s*SHEET|"
    r"CATALOG|SPEC\s*SHEET|MANUAL|SOFTWARE|NARRATIVE|INVOICE|SCHEDULE\s*OF\s*VALUES|"
    r"SUBMITTAL\s*COVER|AS-?BUILT|" + "|".join(
        _tok(w) for w in ("RFI", "BOM", "O&M", "OM", "HMI", "UPS", "PCN", "FFTP",
                          "IODB", "ASB", "SCL", "CL", "REDLINE", "REDLINES", "MARKUP",
                          "MARKUPS", "CALCS", "TESTFORMS")),
    re.I)
# Names worth opening: drawing sets / schedules / EDC terminations packages
_KEEP_NAME = re.compile(
    r"\bEDC\b|\bPLC\b|\bVFD\b|\bMSB\b|XFMR|PNLBD|PANEL|THREE[\s-]*LINE|ONE[\s-]*LINE|"
    r"\bPLANS?\b|CONDUIT|\bCABLE\b|\bIDP\b|SCHEDULE|ELECTRICAL|\bE-?\d|"
    r"(?<![A-Za-z0-9])RTP(?![A-Za-z0-9])", re.I)   # + RTP (Released-To-Production drawing
        # sets), underscore-safe so it matches "_RTP_". NOT bare DWG/DRAWING — that floods the
        # candidate pool with every production DWG and breaks conduit-source selection. RTP
        # BOMs are still dropped first by _DROP_NAME's BOM rule.
# EDC sub-packages that are NOT terminations sources (kept out of the term pass)
_EDC_NONTERM = re.compile(r"\bHMI\b|SOFTWARE|\bUPS\b|NARRATIVE", re.I)


def _base_key(name):
    """Revision-agnostic key for a filename, so many revisions of the same document collapse
    to one 'base' and we keep only the latest. Strips R##/RS##, dates, and QC markers."""
    u = os.path.splitext(name)[0].upper()
    u = re.sub(r"\bR[S]?\d{1,2}\b", " ", u)
    u = re.sub(r"20\d{2}[._-]?\d{2}[._-]?\d{2}", " ", u)
    u = re.sub(r"\bREV\s*\d+\b", " ", u)
    u = re.sub(r"\b(QCCV|QC|NET|R&R|MCN|SB|REVBG|ALT\d?)\b", " ", u)
    u = re.sub(r"[^A-Z0-9]+", " ", u).strip()
    return u


# NB: use _tok (non-letter boundaries) not \b — underscore is a regex word char, so \bEDC\b
# would MISS "_EDC_" and the EDC package would wrongly fall into the conduit track.
_EDC_NAME = re.compile(
    "|".join(_tok(w) for w in ("EDC", "PLC", "VFD", "MSB", "PANEL"))
    + r"|XFMR|PNLBD|PANELBOARD|THREE[\s-]*LINE|ONE[\s-]*LINE", re.I)
# vendor cut-sheet noise — brands / part-number families that are datasheets, not drawings
_VENDOR_NAME = re.compile(
    r"SCHNEIDER|MODICON|\bBMX|\bEATON\b|\bABB\b|SQUARE\s*D|ALLEN[\s-]*BRADLEY|"
    r"DESIGN[\s-]*GUIDE|SPECS?\b", re.I)


def _is_cut_sheet_path(p):
    low = p.replace("\\", "/").lower()
    return "cut sheet" in low or "cutsheet" in low or "vendor" in low


def _name_preselect(paths):
    """From filenames + folders ALONE (no file opened), split the plausible IDP sources into
    two tracks and keep only the LATEST revision of each distinct document:
      • conduit  — the PLANS/IDP drawing set that carries THE conduit schedule
      • term     — the ENGINEERING EDC packages that carry the S/D terminations
    Vendor cut sheets, logs, manuals, RFIs, etc. are dropped by name/path up front."""
    conduit, term = [], []
    for p in paths or []:
        base = os.path.basename(p)
        if _DROP_NAME.search(base) or _is_cut_sheet_path(p) or _VENDOR_NAME.search(base):
            continue
        ext = os.path.splitext(base)[1].lower()
        if ext in _EXCEL:
            if re.search(r"IDP|CONDUIT|SCHEDULE|IODB", base, re.I):
                conduit.append(p)
            continue
        if not _KEEP_NAME.search(base):
            continue
        # EDC-named drawings are the TERMINATIONS track; everything else (Plans/IDP/…) is the
        # CONDUIT-SCHEDULE track — so an EDC doc's internal schedule can't masquerade as THE
        # conduit schedule and short-circuit the search before Plans is opened. An RTP
        # (Released-To-Production) DRAWING set is the production version of the EDC package, so
        # it carries the same three-line/PLC/panelboard terminations — route it to the term
        # track too, so those terminals get read (previously RTP drawings were ignored).
        _is_rtp_dwg = ("RTP" in base.upper() and re.search(r"DWG|DRAWING", base, re.I))
        (term if (_EDC_NAME.search(base) or _is_rtp_dwg) else conduit).append(p)

    def _latest(lst):
        latest = {}
        for p in lst:
            k = _base_key(os.path.basename(p))
            cur = latest.get(k)
            if cur is None or _rev_key(os.path.basename(p)) > _rev_key(os.path.basename(cur)):
                latest[k] = p
        return list(latest.values())

    return _latest(conduit), _latest(term)


def _conduit_priority(path):
    """Search order for the conduit schedule — a file NAMED like the conduit schedule wins
    outright, then Plans/Bid-Documents, then the rest."""
    low = path.replace("\\", "/").lower()
    base = os.path.basename(low)
    # the authoritative conduit SCHEDULE (or the E-3 sheet) wins outright…
    if re.search(r"conduit[\s_]*schedule|conduitschedule|(?<![a-z0-9])e-?3(?![a-z0-9])", base):
        return -2
    # …then PLANS / bid docs / drawings, which carry the fill-bearing schedule…
    if any(k in low for k in ("plans", "bid document", "/dwg", "drawings")):
        return 0
    # …then a bare conduit LIST (usually a names-only Excel roster): it beats a finished-IDP or
    # anything else, but a real plans schedule (above) must win so per-conductor fill isn't lost.
    if re.search(r"conduit[\s_]*list|conduitlist", base):
        return 0.5
    return 1


def _conduit_sort_key(path):
    """Order conduit candidates: strongest name first, then SHALLOWER path (a project's
    primary schedule sits near the top; nested copies under a sub-project folder are
    secondary), then latest revision/date, then name. Depth beats the rev/name tiebreak so a
    multi-project folder doesn't pick a nested sub-project's schedule over the top-level one."""
    base = os.path.basename(path)
    norm = path.replace("\\", "/")
    depth = norm.count("/")
    rev = max((int(x) for x in re.findall(r"\bR[S]?(\d{1,2})\b", base.upper())), default=-1)
    date = max((int("".join(x)) for x in
                re.findall(r"(20\d{2})[._-]?(\d{2})[._-]?(\d{2})", base)), default=0)
    return (_conduit_priority(path), depth, -rev, -date, base.lower())


def discover_sources(paths, log=None, is_cancelled=None):
    """Fast, offline targeting the way the skill does it by convention — no whole-project
    scan. Pre-select by NAME into a conduit track (Plans) and a terminations track
    (Engineering EDC), keep only the latest revision of each, then open only those:
      • conduit track: open Plans/IDP candidates until THE conduit schedule is confirmed;
      • term track: open the Engineering EDC packages to collect PLC-I/O / three-line /
        panelboard terminations.
    Returns a manifest {role: [(path, reason)…]} of only what was inspected."""
    _log = log or (lambda *a: None)
    try:
        import idp_layouts as _L
        rel = [p for p in (paths or []) if _L.folder_relevance(p) != "skip"]
    except Exception:
        rel = list(paths or [])
    conduit_cands, term_cands = _name_preselect(rel)
    conduit_cands.sort(key=_conduit_sort_key)
    _log(f"Targeting: {len(paths or [])} file(s) → {len(rel)} in relevant folders → "
         f"{len(conduit_cands)} plans/IDP + {len(term_cands)} Engineering EDC candidate(s) "
         "by name; opening only those.")
    manifest = {}
    have = set()
    opened = 0

    def _record(p, term_track=False):
        nonlocal opened
        try:
            role, reason = classify_file(p)
        except Exception as e:
            role, reason = "other", f"classify error: {e}"
        base = os.path.basename(p)
        # A conduit/IDP-named EXCEL is a conduit list — extract it as the conduit schedule
        # (workbook_source would otherwise be excluded from conduit extraction).
        if (not term_track and role == "workbook_source"
                and re.search(r"IDP|CONDUIT|SCHEDULE", base, re.I)):
            role = "conduit_schedule"
            reason = "conduit-schedule workbook (Excel conduit list)"
        # An EDC-named doc's internal schedule is a PANEL schedule (terminations), never THE
        # conduit schedule — so it can't pollute the conduit side or trip the early stop.
        if term_track and role in ("conduit_schedule", "cable_schedule"):
            role = ("panelboard" if re.search(r"PANEL|PNLBD|XFMR|MSB", base.upper())
                    else "edc_other")
            reason += " (EDC package — treated as terminations, not the conduit schedule)"
        manifest.setdefault(role, []).append((p, reason))
        have.add(role)
        opened += 1
        return role

    # Track A — the conduit schedule (stop as soon as one is confirmed)
    for p in conduit_cands:
        if is_cancelled and is_cancelled():
            break
        role = _record(p)
        if role in ("conduit_schedule", "cable_schedule", "finished_idp"):
            break
    # Track B — the Engineering terminations packages (open each EDC drawing once)
    for p in term_cands:
        if is_cancelled and is_cancelled():
            break
        _record(p, term_track=True)
    _log(f"Targeting: opened {opened} candidate(s) — "
         + ("conduit schedule + terminations found; stopped searching."
            if _idp_needs_met(have) else
            "no full conduit+terminations set; using what was found."))
    return manifest


def _project_number(path):
    """The AIC project number (NN.NNNN) that identifies a file's OWN project — read from the
    FILENAME first (e.g. 56.1125_EDC_…), else the nearest folder. Filename-first matters: a
    file can sit under a parent folder named for a different project, and we must not let the
    parent's number mask the file's own. Used to keep a scan from mixing one project's
    conduit schedule with another's EDC when a folder holds several projects."""
    pat = r"\b(\d{2}\.\d{3,4})\b"
    m = re.search(pat, os.path.basename(path))
    if m:
        return m.group(1)
    # else the deepest (most-specific) folder number, not the shallow parent
    for seg in reversed(path.replace("\\", "/").split("/")[:-1]):
        m = re.search(pat, seg)
        if m:
            return m.group(1)
    return None


def scope_edc_to_conduit(conduit_srcs, edc_srcs):
    """Keep only EDC/terminations sources that belong to the SAME project as the conduit
    schedule. If the conduit source carries a project number, drop EDC files that carry a
    DIFFERENT one (files with no number are kept — they're ambiguous, not cross-project).
    No-op when the conduit source has no number. Prevents cross-project channel garbage."""
    nums = {n for n in (_project_number(p) for p in (conduit_srcs or [])) if n}
    if not nums:
        return edc_srcs
    kept = []
    for p in edc_srcs or []:
        n = _project_number(p)
        if n is None or n in nums:
            kept.append(p)
    return kept


def conduit_sources(manifest):
    """Files that seed conduits (schedules + finished IDPs), best/most-complete first."""
    files = [p for role in _CONDUIT_ROLES for (p, _r) in manifest.get(role, [])]
    return sorted(set(files), key=lambda p: _rev_key(os.path.basename(p)), reverse=True)


def edc_sources(manifest):
    """EDC + panelboard files that carry terminal landings, latest revision first."""
    files = [p for role in _EDC_ROLES for (p, _r) in manifest.get(role, [])]
    return sorted(set(files), key=lambda p: _rev_key(os.path.basename(p)), reverse=True)


def routing_report(manifest):
    """Human-readable summary for the scan log: what each role found, then the
    field → source contract with a ✓/— for whether that source is present."""
    order = ["conduit_schedule", "cable_schedule", "edc_three_line", "panelboard",
             "edc_plc_io", "edc_other", "finished_idp", "cut_sheet", "workbook_source",
             "cover_letter", "other"]
    present = {r for r in manifest if manifest.get(r)}
    n_skip = len(manifest.get("skip", []))
    n_total = sum(len(v) for v in manifest.values())
    lines = ["── SOURCE ROUTING (project folder) ─────────────────────"]
    if n_skip:
        lines.append(f"   Targeting: deep-inspected {n_total - n_skip} of {n_total} files; "
                     f"skipped {n_skip} irrelevant (quotes/photos/estimating) without opening.")
    for role in order:
        items = manifest.get(role) or []
        if not items:
            continue
        lines.append(f"   [{role}]  {len(items)} file(s)")
        for p, reason in items[:6]:
            lines.append(f"       • {os.path.basename(p)}  — {reason}")
        if len(items) > 6:
            lines.append(f"       … +{len(items) - 6} more")
    lines.append("   ── field → source ──")
    for field, role, desc in FIELD_SOURCES:
        ok = "✓" if (role == "*" or role in present
                     or (role == "cut_sheet" and "cut_sheet" in present)) else "—"
        # cable_schedule may be folded into the conduit-schedule PDF (same file)
        if role == "cable_schedule" and "conduit_schedule" in present and ok == "—":
            ok = "✓*"
        lines.append(f"   {ok} {field}")
        lines.append(f"       ↳ {desc}")
    lines.append("   (✓ source found · ✓* likely on the conduit-schedule sheet · — falls back to convention/vision)")
    lines.append("─────────────────────────────────────────────────────")
    return "\n".join(lines)
