"""
autocad_bridge.py — AutoCAD COM bridge for IDP DWG generation.

Uses win32com.client directly (NOT pyautocad) — pyautocad's InsertBlock wrapper
cannot unmarshal the AE-subclassed IAcadBlockReference COM return type, so blocks
would insert but return None, preventing attribute population.

This module must run in a single-threaded STA apartment — guaranteed by
app.py using threaded=False.

TUNABLE CONSTANTS
-----------------
All X/Y coordinates and spacing values are defined below.
Template geometry (from IDP_Start_20260409.dwg):
  - Sheet width: 33 units
  - Zone dividers at x=12.6875 and x=20.3125
  - Left zone:   x = 0 … 12.6875   (source / _L blocks)
  - Center zone: x = 12.6875 … 20.3125  (Conduit block)
  - Right zone:  x = 20.3125 … 33   (destination / _R blocks)
"""

import os
import re
import sys
import json
import math
import time
import shutil
import difflib
import tempfile
import datetime
import importlib
import threading

import win32com.client
import pythoncom

# ── Debug log ─────────────────────────────────────────────────────────────────
_DEBUG_LOG = os.getenv("IDP_DEBUG_LOG", r"C:\__Delete\idp_debug.txt")


def _log(msg: str):
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ── Template path ─────────────────────────────────────────────────────────────

_TEMPLATE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "_Templates")
)


_REV_RE = re.compile(r"rev0*(\d+)", re.IGNORECASE)


def _get_template_path() -> str | None:
    """Return the path to the current .dwg template, or None.

    Picks the highest "RevNN" in the filename when more than one candidate has one
    (e.g. IDP.TEMPLATE.Rev04.dwg beats Rev03) -- NOT filesystem mtime. A fresh git
    checkout writes every file at ~the same instant, so "most recently modified"
    resolves to whichever file the filesystem happened to list last: effectively
    arbitrary, and it silently picked an old/incomplete template that broke block
    inserts and left the title block missing. Only when nothing has a "RevNN" does
    this fall back to mtime, so a single unversioned template still works."""
    override = os.getenv("IDP_TEMPLATE_PATH")
    if override:
        return override
    try:
        candidates = [
            f for f in os.listdir(_TEMPLATE_DIR)
            if f.lower().endswith(".dwg") and not f.startswith("~$")
        ]
    except FileNotFoundError:
        return None
    if not candidates:
        return None
    revved = [(int(m.group(1)), f) for f in candidates if (m := _REV_RE.search(f))]
    if revved:
        chosen = max(revved, key=lambda t: t[0])[1]
    else:
        chosen = max(candidates, key=lambda f: os.path.getmtime(os.path.join(_TEMPLATE_DIR, f)))
    return os.path.join(_TEMPLATE_DIR, chosen)


# ── Tunable layout constants ──────────────────────────────────────────────────

WIRE_X   = float(os.getenv("IDP_WIRE_X",   "12.0"))
CENTER_X = float(os.getenv("IDP_CENTER_X", "0.0"))
LEFT_X   = float(os.getenv("IDP_LEFT_X",   "6.5"))
RIGHT_X  = float(os.getenv("IDP_RIGHT_X",  "26.5"))

CONDUIT_Y = float(os.getenv("IDP_CONDUIT_Y", "0.0"))
START_Y   = float(os.getenv("IDP_START_Y",  "14.0"))

GAP = float(os.getenv("IDP_GAP", "0.25"))

FALLBACK_BLOCK_HEIGHT = float(os.getenv("IDP_FALLBACK_HEIGHT", "1.0"))

# Grid pitch: blocks are spaced by whole increments of this so they land evenly on
# the AutoCAD grid (a short block takes one increment; a tall one rounds up).
GRID_STEP = float(os.getenv("IDP_GRID_STEP", "0.5"))


GROUP_GAP = float(os.getenv("IDP_GROUP_GAP", "0.0"))  # extra clear space below a group (default: none)

# A block's drawing art may extend slightly past its nominal grid rows (e.g. a
# 3-position block 1.52 tall spans 3 wire rows = 1.5). Tolerate this much overhang
# when spacing groups so it does NOT cost an extra 0.5 step; genuinely tall blocks
# (well beyond their rows) still round up normally.
BLOCK_ART_MARGIN = float(os.getenv("IDP_BLOCK_ART_MARGIN", "0.1"))

# A block's art may run slightly past its grid slot. Add one more 0.5 step ONLY when
# the art OVERLAPS into the next symbol's space by more than this tolerance (genuinely
# nearly overlapping). BlockIndex heights are bounding boxes with padding, so measuring
# real overlap (not raw gap) keeps small symbols tight on the 0.5 grid.
SYMBOL_OVERLAP_TOL = float(os.getenv("IDP_SYMBOL_OVERLAP_TOL", "0.05"))

# Some symbols extend well ABOVE their wire terminal (e.g. an HOA switch's box sits above
# the contacts). When a symbol's top overhang exceeds this, the group above it reserves
# that much extra clearance so the two don't collide. Below the threshold it's ignored so
# ordinary symbols stay tight.
TOP_OVERHANG_MIN = float(os.getenv("IDP_TOP_OVERHANG_MIN", "0.35"))
# Minimum clear gap to guarantee ABOVE a top-heavy next symbol (e.g. an HOA switch's box):
# if it would clear the symbol above by less than this, drop the symbol above a full step.
# Kept just above zero: many symbols are mildly top-heavy and sit ~0.02 above the one above
# them (fine visually) -- only a genuine OVERLAP (negative clearance, e.g. the HOA switch's
# box biting into the symbol above) should trigger the extra drop.
TOPHEAVY_MIN_CLEAR = float(os.getenv("IDP_TOPHEAVY_MIN_CLEAR", "0.01"))

# ── Sheet / continuation geometry ─────────────────────────────────────────────
# Blocks stack DOWN from START_Y. The 1_BORDER layer bottom sits at Y=0.0; a block
# must not get closer than PAGE_MARGIN to it. When the next instrument group would
# drop below PAGE_FLOOR it is pushed onto a continuation sheet (start/middle/end).
BORDER_BOTTOM_Y = float(os.getenv("IDP_BORDER_BOTTOM", "0.0"))
PAGE_MARGIN     = float(os.getenv("IDP_PAGE_MARGIN", "1.0"))
PAGE_FLOOR      = BORDER_BOTTOM_Y + PAGE_MARGIN     # a group's bottom must stay >= this

# Conduit-block visibility states for multi-sheet conductors.
CONT_STATE_START  = "TBL10_Continuation_Start"
CONT_STATE_MIDDLE = "Continuation_Middle"
CONT_STATE_END    = "Continuation_End"


def _round_up_step(x: float) -> float:
    """Round x UP to the next GRID_STEP increment (min one step)."""
    import math
    steps = max(1, math.ceil((float(x) - 1e-9) / GRID_STEP))
    return steps * GRID_STEP

TITLEBLOCK_NAME  = "WML-SI_TITLEBLOCK_SCHEMATIC"
CONDUIT_NAME     = "Conduit"
WIRE_BLOCK_NAME  = "Wire_IDP"

# Power-to-instrument connector: a small line block that routes the power feed onto
# the instrument's Line(+) / Neutral(-) terminals. Placed (in addition to the
# instrument) whenever a Power loop lands on an instrument. The L/R variant matches
# the instrument side. Insert offset from the instrument origin is tunable.
INST_PWR_WIRE_L = "Inst_Pwr_Wire_L"
INST_PWR_WIRE_R = "Inst_Pwr_Wire_R"
PWRWIRE_DX = float(os.getenv("IDP_PWRWIRE_DX", "0.0"))
PWRWIRE_DY = float(os.getenv("IDP_PWRWIRE_DY", "0.0"))
# The Inst_Pwr_Wire connector is placed AT the wire rows: its two entry points sit on
# the two power wires, and its L-shaped legs route the feed down ~0.75" to the
# instrument's Line(+)/Neutral(-) terminals. The instrument is therefore dropped this
# far below the wires so its L/N land at the connector's exit (verified against the
# real Inst_4W block on a generated drawing). All tunable.
INST_PWR_DROP = float(os.getenv("IDP_INST_PWR_DROP", "1.5"))

# An instrument's Line/Neutral terminal boxes extend ~0.75" ABOVE its insertion point.
# When the NEXT group is a (signal) instrument, reserve this much clearance below the
# current group so those terminals don't collide. A power instrument is dropped (its
# top is just the wire rows), so it needs no reserve. Tunable.
INST_TOP_RESERVE = float(os.getenv("IDP_INST_TOP_RESERVE", "0.75"))

# A TSP/signal instrument group starts too high on the sheet -> drop the whole group.
TSP_GROUP_DROP = float(os.getenv("IDP_TSP_GROUP_DROP", "1.0"))

# ── Cross-conduit REF. DWG note (yellow bracket + "REF. DWG / <#>" callout) ──────
# Placed only on a 4-wire instrument whose power/TSP counterpart is in another conduit
# (loop["ref_dwg"] set by note_refs). The note = the PWR_Vertical-Bracket dynamic block
# (the "yellow bracket") bracketing the terminal landings whose wire is NOT on this sheet
# + a separate "REF. DWG / <#>" MTEXT. Offsets/rotation/flip below are measured straight
# from the right-side example on Inst_spacing.dwg (relative to the instrument insertion):
#   TSP drawing  (signal here) -> bracket ABOVE the not-here L/N terminals   (rot 180)
#   POWER drawing(power here)  -> bracket beside the not-here signal terminals(rot 270)
NOTE_BRACKET = "PWR_Vertical-Bracket_for Description"   # the yellow bracket dynamic block
NOTE_TEXT_H  = float(os.getenv("IDP_NOTE_TEXT_H", "0.125"))   # REF.DWG MTEXT height
NOTE_TEXT_W  = float(os.getenv("IDP_NOTE_TEXT_W", "3.0"))     # REF.DWG MTEXT box width
# Bracket span (Distance1). 2.0 is the 2-terminal reference value. A taller instrument
# needs a longer bracket to enclose its N stacked signal terminals: span = 0.5*N + 0.5
# (one 0.5 grid row per terminal + a half-row overhang top & bottom), e.g. 2.5 for a
# 4-terminal stack. See _build_note_items.
NOTE_BRACKET_DIST = float(os.getenv("IDP_NOTE_BRACKET_DIST", "2.0"))   # Distance1 (span, 2 terminals)

# (bracket_dx, bracket_dy, rotation_deg, flip, text_dx, text_dy, text_attach) per
# (is_power, side); offsets relative to the instrument's insertion point. From the
# right-side example on Inst_spacing.dwg. text_attach is the AutoCAD MTEXT attachment
# point (6 = middle-right, 4 = middle-left, 8 = bottom-center) so the text centers/aligns
# exactly as the template: beside the bracket on a POWER drawing, centered above on a TSP.
_ATTACH_MIDDLE_LEFT, _ATTACH_MIDDLE_RIGHT, _ATTACH_BOTTOM_CENTER = 4, 6, 8
_NOTE_LAYOUT = {
    (True,  "R"): (-0.5, -0.25, 270.0, 0, -0.8, -0.25, _ATTACH_MIDDLE_RIGHT),
    # Left is the mirror of right (bracket 0.5 from the instrument on the +x side). The
    # flip shifts the block's reported insertion by ~0.3, so insert at +0.8 to land the
    # flipped bracket at instrument+0.5 (symmetric with the right side's instrument-0.5).
    (True,  "L"): ( 0.8, -0.25, 270.0, 1,  0.8, -0.25, _ATTACH_MIDDLE_LEFT),
    (False, "R"): ( 1.0,  1.25, 180.0, 0,  1.0,  1.55, _ATTACH_BOTTOM_CENTER),
    (False, "L"): (-1.0,  1.25, 180.0, 0, -1.0,  1.55, _ATTACH_BOTTOM_CENTER),
}


def _build_note_items(side: str, is_power: bool, side_x: float, inst_y: float,
                      ref_text: str, n_terms: int = 2) -> list:
    """Plan items for a cross-conduit note: the yellow bracket (PWR_Vertical-Bracket,
    rotated/flipped to bracket the NOT-here terminals) + the two-line 'REF. DWG / <#>'
    MTEXT (matching the template: "REF. DWG" over the number, \\P = line break). side is
    'R' (dst) or 'L' (src). The two items share a note_group id so render_plan groups them
    into one AutoCAD group (move the bracket, the text follows). Returns [] if no ref.

    n_terms is the instrument's terminal count (from its Field_<N>Term visibility). On a
    POWER drawing the bracket encloses the un-wired signal (TSP) terminals, which stack DOWN
    the field-facing edge one 0.5-grid row apart, the topmost at the insertion; N of them
    centre at inst_y - 0.25*(N-1). The _NOTE_LAYOUT table holds the 2-terminal reference
    geometry; a taller (e.g. 4-terminal) instrument drops the bracket + text to that lower
    centre and widens the span to enclose the whole stack. The TSP-side note (rot 180, above
    the L/N pair) is always a fixed 2-terminal bracket and is left untouched."""
    if not ref_text:
        return []
    bdx, bdy, rot, flip, tdx, tdy, attach = _NOTE_LAYOUT[(bool(is_power), side)]
    dist = NOTE_BRACKET_DIST
    if is_power and n_terms and n_terms > 2:
        bdy = tdy = -0.25 * (n_terms - 1)     # centre on the N stacked signal terminals
        dist = 0.5 * n_terms + 0.5            # span encloses the taller stack (2.5 for N=4)
    gid = f"{side}{int(round(side_x))}_{int(round(inst_y * 100))}"   # unique per note on the sheet
    bracket = {"role": "note", "name": NOTE_BRACKET, "x": side_x + bdx, "y": inst_y + bdy,
               "visibility": None, "attrs": {}, "kind": "block", "height": 0.0,
               "rotation_deg": rot, "dyn_props": {"Distance1": dist, "Flip state1": flip},
               "note_group": gid}
    text = {"role": "refdwg", "name": None, "x": side_x + tdx, "y": inst_y + tdy,
            "visibility": None, "attrs": {}, "kind": "mtext",
            "text": "REF. DWG\\P" + str(ref_text), "attach": attach,
            "text_height": NOTE_TEXT_H, "width": NOTE_TEXT_W, "height": 0.0,
            "note_group": gid}
    return [bracket, text]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _close_if_open(acad_app, path: str) -> None:
    """Close the AutoCAD document whose full path matches `path`, if open."""
    norm = os.path.normcase(os.path.normpath(path))
    try:
        for i in range(acad_app.Documents.Count):
            try:
                d = acad_app.Documents.Item(i)
                if os.path.normcase(os.path.normpath(d.FullName)) == norm:
                    _log(f"  _close_if_open: closing '{d.Name}'")
                    d.Close(False)
                    return
            except Exception:
                pass
    except Exception as e:
        _log(f"  _close_if_open scan failed: {e}")


def _err(msg: str, warnings: list | None = None) -> dict:
    return {
        "success":     False,
        "output_path": None,
        "warnings":    warnings or [],
        "error":       msg,
    }


def _pt(x: float, y: float, z: float = 0.0):
    """Create a VARIANT 3-element double array (AutoCAD insertion point)."""
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(x), float(y), float(z)],
    )


# ── Main entry point ──────────────────────────────────────────────────────────

# AutoCAD COM is a single shared instance; concurrent generate_dwg calls would
# race on ActiveDocument (one call's Documents.Add changes the active doc while
# another is mid-clear).  Serialize all generation through this lock.
_GEN_LOCK = threading.Lock()

# How many times to retry a COM call AutoCAD rejected because it's busy (mid-command, or the
# moment a dialog is closing). Tuned for speed: 15 tries x ~1s = ~15s window (was 20 x 2s =
# ~40s). Enough to ride out the brief busy window between sheets on a single idle AutoCAD;
# override with IDP_ACAD_CONNECT_RETRIES / IDP_ACAD_CONNECT_DELAY if a heavily-loaded session
# (several drawings / a second AutoCAD.exe) needs the old, more forgiving window back.
_ACAD_CONNECT_RETRIES = int(os.getenv("IDP_ACAD_CONNECT_RETRIES", "15"))
_ACAD_CONNECT_DELAY   = float(os.getenv("IDP_ACAD_CONNECT_DELAY", "1.0"))

# Cached AutoCAD Application COM handle, reused across every sheet of a Generate All so we
# don't GetActiveObject + revalidate from scratch on each drawing. Revalidated cheaply
# (Version) on each use and transparently reconnected if it went stale.
_ACAD_APP = None


def _connect_acad(retries: int = None, delay: float = None):
    """GetActiveObject the running AutoCAD, retrying while it's momentarily unreachable.
    Returns (app, err). GetActiveObject can only ever reach ONE AutoCAD instance."""
    retries = _ACAD_CONNECT_RETRIES if retries is None else retries
    delay = _ACAD_CONNECT_DELAY if delay is None else delay
    app, err = None, None
    for _ in range(max(1, retries)):
        try:
            app = win32com.client.GetActiveObject("AutoCAD.Application")
            _ = str(app.Version)
            return app, None
        except Exception as e:
            app, err = None, e
            time.sleep(delay)
    return None, err


def _get_acad_app(force: bool = False):
    """Return a reused AutoCAD Application handle so a batch doesn't reconnect per sheet.
    Revalidates the cached handle (Version) and transparently reconnects if AutoCAD was
    closed/reopened. `force=True` drops the cache and reconnects. Returns (app, err)."""
    global _ACAD_APP
    if not force and _ACAD_APP is not None:
        try:
            _ = str(_ACAD_APP.Version)
            return _ACAD_APP, None
        except Exception:
            _ACAD_APP = None
    app, err = _connect_acad()
    _ACAD_APP = app
    return app, err


def _acad_busy_hint(msg: str) -> str:
    """Append an actionable hint to a COM connection failure. GetActiveObject can only
    ever reach ONE AutoCAD instance -- if several drawings are open (especially across
    more than one AutoCAD.exe process, e.g. AutoCAD running in SDI mode), automation
    calls can land on an instance that's busy the whole retry window and this is by far
    the most common real-world cause, so say so instead of surfacing the bare COM
    error text."""
    return (
        f"{msg}\n\n"
        "This usually means AutoCAD was busy (mid-command, or a dialog open) for the "
        "entire ~40s retry window, most often because more than one drawing is open at "
        "once. Close any AutoCAD windows/documents you don't need, make sure the "
        "remaining one is idle (no active command, no dialog), then try Generate again."
    )


# COM HRESULTs AutoCAD raises when it's momentarily busy -- mid-command, redrawing, or
# still finishing the previous drawing during a rapid Generate All. These are transient:
# the correct response is to wait a beat and retry, not to fail the sheet.
#   -2147418111 = RPC_E_CALL_REJECTED   ("Call was rejected by callee.")
#   -2147417846 = RPC_E_SERVERCALL_RETRYLATER
_COM_BUSY_HRESULTS = (-2147418111, -2147417846)


def _is_busy_error(e) -> bool:
    """True if e is a transient 'AutoCAD is busy' COM error (worth retrying). Accepts an
    exception or an already-stringified error message."""
    try:
        if isinstance(e, BaseException) and getattr(e, "args", None):
            if e.args[0] in _COM_BUSY_HRESULTS:
                return True
    except Exception:
        pass
    s = str(e).lower()
    return ("rejected by callee" in s or "retrylater" in s
            or "server is busy" in s or "call was rejected" in s)


def _com_retry(fn, tries=8, delay=0.25, any_error=False):
    """Run fn(); if it raises a transient 'AutoCAD busy' COM error, wait and retry up to
    `tries` times. Re-raises the last error if none succeed; a non-busy error is raised
    immediately (not retried).

    any_error=True also retries NON-busy exceptions -- for idempotent, non-critical COM
    calls (e.g. AcadTable.SetText) that, during a busy Generate All, can fail with a
    transient dynamic-dispatch error ('Item.SetText') that isn't a classic busy HRESULT.
    A genuinely persistent failure still exhausts the retries and re-raises, so nothing
    is masked -- it just becomes resilient to transient hiccups."""
    last = None
    for _ in range(tries):
        try:
            return fn()
        except Exception as e:
            if not any_error and not _is_busy_error(e):
                raise
            last = e
            time.sleep(delay)
    if last is not None:
        raise last


_WAIT_QUIESCENT_TIMEOUT = float(os.getenv("IDP_WAIT_QUIESCENT_TIMEOUT", "6.0"))


def _wait_quiescent(app, timeout=None):
    """Poll until AutoCAD reports it's idle, or timeout. Best-effort -- never raises.
    Called before each sheet so a rapid Generate All doesn't start hammering AutoCAD while
    it's still finishing the previous drawing (the main source of 'Call was rejected by
    callee'). Returns fast the instant AutoCAD is idle; the timeout only caps how long we
    wait when it stays busy. Trimmed to ~6s (was 15s) for speed -- if it's still busy after
    that, the per-call _com_retry backstops the actual COM calls. Override with
    IDP_WAIT_QUIESCENT_TIMEOUT."""
    timeout = _WAIT_QUIESCENT_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if app.GetAcadState().IsQuiescent:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _wait_doc_ready(app, doc, timeout=6.0, stable=2):
    """Poll until a just-opened document is *sustainably* usable -- model space reachable AND
    AutoCAD idle for `stable` consecutive reads -- then return True. Returns the instant it's
    stably ready (so a normal sheet is still faster than the old flat time.sleep(1.5)) but waits
    up to `timeout` for a slow open instead of proceeding on a transient idle blip. Returns False
    if it never stabilises. Never raises.

    A SINGLE quiescent reading right after Open is unreliable: AutoCAD reports idle for a moment,
    then resumes background regen/layer-load and starts rejecting COM calls again. Catching that
    blip left _unlock_all_layers / _clear_model_space to run against a still-busy doc ('Call was
    rejected by callee' + an uncleared template), so the saved sheet kept the template's leftover
    Conduit block (with its continuation visibility). Requiring consecutive good reads avoids it."""
    deadline = time.time() + timeout
    good = 0
    while time.time() < deadline:
        try:
            _ = doc.ModelSpace.Count
            if app.GetAcadState().IsQuiescent:
                good += 1
                if good >= stable:
                    return True
            else:
                good = 0
        except Exception:
            good = 0
        time.sleep(0.1)
    return False


_SHEET_RETRIES = 3   # whole-sheet attempts before giving up
_SHEET_RETRY_DELAY = float(os.getenv("IDP_SHEET_RETRY_DELAY", "1.0"))   # was 2.0


def generate_dwg(*args, **kwargs) -> dict:
    """Thread-safe wrapper: serialize AutoCAD access across concurrent requests. Retries
    the whole sheet if it fails, since almost every generation failure during a rapid
    Generate All is a transient 'AutoCAD busy' COM error and a fresh re-render (from a
    clean template copy, after waiting for AutoCAD to go idle) succeeds. Each attempt is
    fully idempotent -- it re-copies the template and overwrites the output -- so a retry
    can't leave a half-drawn sheet. A genuinely broken conduit still fails all attempts
    and returns its error, so nothing is masked; it just isn't randomly skipped.

    Fast path (DEFAULT; kill-switch IDP_REUSE_DOC=0): reuse ONE open, pre-cleared template doc
    across every sheet -- render/save/UNDO-reset instead of copy+open+clear per sheet (~17s/sheet
    saved). It is tried FIRST; ANY failure or anomaly falls straight through to the proven
    fresh-open path below, so the reuse path can never make output worse than the default."""
    with _GEN_LOCK:
        if _reuse_enabled():
            try:
                r = _REUSE.generate_sheet(*args, **kwargs)
                if r and r.get("success"):
                    return r
                _log(f"reuse path returned failure ({r.get('error') if r else None!r}); "
                     f"falling back to fresh-open path")
            except Exception as e:
                _log(f"reuse path raised ({e!r}); falling back to fresh-open path")
            # any reuse trouble -> tear the engine down and use the untouched fresh-open path
            try:
                _REUSE.discard()
            except Exception:
                pass

        r = None
        for attempt in range(_SHEET_RETRIES):
            r = _generate_dwg_impl(*args, **kwargs)
            if r.get("success"):
                return r
            if attempt < _SHEET_RETRIES - 1:
                _log(f"generate_dwg: sheet failed ({r.get('error')!r}); re-rendering the "
                     f"whole sheet (attempt {attempt + 2}/{_SHEET_RETRIES})")
                time.sleep(_SHEET_RETRY_DELAY)
        return r


# ── Doc-reuse fast path (DEFAULT; kill-switch IDP_REUSE_DOC=0) ────────────────────────────────
# The fresh-open path copies+opens+clears the template on EVERY sheet (~17s of identical prep).
# Doc-reuse instead reuses ONE open, pre-cleared template doc for the whole run:
#   open+clear ONCE  ->  per sheet: UNDO Mark -> render -> SaveAs(output) -> UNDO Back
# AutoCAD's own UNDO reverts the ENTIRE sheet (added blocks, in-place table-cell edits, title-
# block attrs, groups, dyn props) back to the cleared baseline in one shot -- proven on all three
# mutation types. A count-verify after each reset guards against a partial/failed UNDO, and ANY
# anomaly makes generate_dwg discard the engine and fall back to the fresh-open path.
#
# This is the DEFAULT: an A/B diff proved reuse output byte-identical to the fresh-open path
# across a full 24-sheet run, a 96-sheet stress run was clean (0 errors / 0 bleed / 0 fallbacks),
# and it is ~1.7x faster. Because it always falls back to the proven path on any anomaly, output
# correctness never depends on it. IDP_REUSE_DOC is only a KILL-SWITCH -- set it to 0/false/off
# to force the old per-sheet copy+open+clear path.

def _reuse_enabled() -> bool:
    return os.getenv("IDP_REUSE_DOC", "1").strip().lower() not in ("0", "false", "no", "off")


_REUSE_SCRATCH = os.path.join(tempfile.gettempdir(), "lisa_idp_reuse_scratch.dwg")


def _render_and_save_sheet(model, doc, conduit_data, loop_list, output_path, warnings,
                           ref_doc_rows=None, dev_rows=None, block_heights=None,
                           cont_state=None, cont_prev=None, cont_next=None,
                           project_desc=None, sheet_number=None, drawing_no=None):
    """Render one sheet onto `model`/`doc` and SaveAs(output_path). This is the SAME sequence the
    fresh-open path runs inline (build plan -> continuation -> render -> completeness -> ref-docs
    -> title block -> SaveAs); the reuse engine calls it against the shared baseline doc. Raises on
    an incomplete render or a missing save so the caller can reset+fall back. Returns `placed`."""
    plan = build_layout_plan(conduit_data, loop_list, block_heights)
    warnings.extend(plan.get("warnings", []))
    if cont_state:
        plan["conduit"]["visibility"] = cont_state
        plan["conduit"]["attrs"]["CONT_Previous_DWG"] = cont_prev or ""
        plan["conduit"]["attrs"]["CONT_Next_DWG"]     = cont_next or ""
        _log(f"  continuation sheet: state={cont_state} prev={cont_prev!r} next={cont_next!r}")
    _log(f"  layout plan: {len(plan.get('items', []))} block(s)")
    placed = render_plan(model, plan, warnings)
    expected_blocks = (1 if plan.get("conduit") else 0) + \
        sum(1 for it in plan.get("items", []) if it.get("kind") != "mtext")
    if len(placed) < expected_blocks:
        raise RuntimeError("Sheet incomplete: %d of %d blocks placed (AutoCAD busy) — not saved"
                           % (len(placed), expected_blocks))
    if ref_doc_rows or dev_rows:
        _fill_ref_docs_table(model, ref_doc_rows or [], dev_rows or [], warnings)
    else:
        _log("  ref_docs table: skipped (no ref_doc_rows or dev_rows)")
    title_block_values = _build_title_block_values(conduit_data, project_desc, sheet_number, drawing_no)
    if title_block_values:
        _set_title_block_attrs(doc, title_block_values, warnings)
    _log("Calling SaveAs...")
    _com_retry(lambda: doc.SaveAs(output_path))
    if not os.path.exists(output_path):
        raise RuntimeError(f"SaveAs completed but file not found at: {output_path}")
    return placed


class _ReuseEngine:
    """Holds ONE open, pre-cleared template doc and reuses it across sheets. Module-singleton so
    it persists across the per-conduit /generate POSTs (Flask stays single-threaded on one COM
    thread, serialized by _GEN_LOCK). Never trusted blindly: a stale doc or a reset that doesn't
    restore the baseline count makes the caller discard + fall back to the fresh-open path."""

    def __init__(self):
        self.app = self.doc = self.model = None
        self.template = None
        self.baseline_count = None

    def _alive(self) -> bool:
        if self.doc is None or self.app is None:
            return False
        try:
            # Tolerate a transient busy hiccup: a momentary 'rejected by callee' on this cheap
            # touch must NOT trigger a wasteful full re-open+clear (~17s). Only a genuinely dead
            # handle (AutoCAD closed / doc gone) fails all the retries.
            _com_retry(lambda: (str(self.doc.Name), str(self.app.Version)), any_error=True)
            return True
        except Exception:
            return False

    def discard(self):
        try:
            if self.doc is not None:
                _com_retry(lambda: self.doc.Close(False), any_error=True)
        except Exception:
            pass
        self.app = self.doc = self.model = None
        self.template = self.baseline_count = None

    def _ensure_baseline(self, template_path):
        if self._alive() and self.template == template_path and self.baseline_count is not None:
            return
        self.discard()
        app = None
        for _ in range(_ACAD_CONNECT_RETRIES):
            try:
                app = win32com.client.GetActiveObject("AutoCAD.Application")
                _ = str(app.Version)
                break
            except Exception:
                app = None
                time.sleep(2)
        if app is None:
            raise RuntimeError("reuse engine: AutoCAD not accessible")
        _wait_quiescent(app)
        _close_if_open(app, _REUSE_SCRATCH)
        shutil.copyfile(template_path, _REUSE_SCRATCH)
        doc = None
        for _ in range(_ACAD_CONNECT_RETRIES):
            try:
                doc = app.Documents.Open(_REUSE_SCRATCH)
                break
            except Exception:
                doc = None
                time.sleep(2)
        if doc is None:
            raise RuntimeError("reuse engine: could not open template scratch copy")
        if not _wait_doc_ready(app, doc, timeout=6.0):
            time.sleep(1.5)
        model = _com_retry(lambda: doc.ModelSpace)
        _unlock_all_layers(doc, [])
        _clear_model_space(model, [])
        _wait_quiescent(app)
        self.app, self.doc, self.model, self.template = app, doc, model, template_path
        self.baseline_count = int(_com_retry(lambda: model.Count))
        _log(f"reuse engine: baseline ready (count={self.baseline_count}, template={os.path.basename(template_path)})")

    def _mark(self):
        _wait_quiescent(self.app)
        _com_retry(lambda: self.doc.SendCommand("_.UNDO\n_Mark\n"), any_error=True)
        _wait_quiescent(self.app)

    def _reset_to_baseline(self):
        """Revert everything rendered since the last _mark() via AutoCAD UNDO, then VERIFY the
        model is back to the exact baseline entity count. A mismatch means the UNDO didn't fully
        revert -- raise so the caller discards the engine (correctness over reuse)."""
        _wait_quiescent(self.app)
        _com_retry(lambda: self.doc.SendCommand("_.UNDO\n_Back\n"), any_error=True)
        _wait_quiescent(self.app)
        self.model = _com_retry(lambda: self.doc.ModelSpace)
        cnt = int(_com_retry(lambda: self.model.Count))
        if cnt != self.baseline_count:
            raise RuntimeError(f"reuse reset did not restore baseline (count {cnt} != {self.baseline_count})")

    def generate_sheet(self, conduit_data, loop_list, output_path, **kw):
        self._ensure_baseline(_get_template_path())
        warnings = []
        _close_if_open(self.app, output_path)   # release a stale doc holding the OUTPUT (not scratch)
        self._mark()
        try:
            placed = _render_and_save_sheet(self.model, self.doc, conduit_data, loop_list,
                                            output_path, warnings, **kw)
        except Exception:
            # render/save failed -> revert the partial render so the shared baseline stays clean;
            # if even the revert can't be trusted, discard the whole engine. Re-raise so the caller
            # runs the fresh-open path for this sheet.
            try:
                self._reset_to_baseline()
            except Exception:
                self.discard()
            raise
        # saved OK -> revert to baseline for the next sheet, then rebind scratch OFF the output so
        # the output file isn't left locked when emit_and_validate copies it.
        self._reset_to_baseline()
        _com_retry(lambda: self.doc.SaveAs(_REUSE_SCRATCH), any_error=True)
        validation = emit_and_validate(placed, output_path, warnings)
        _log(f"reuse engine: sheet done -> {os.path.basename(output_path)}")
        return {"success": True, "output_path": output_path, "warnings": warnings,
                "error": None, "validation": validation}


_REUSE = _ReuseEngine()


def _is_bigtb(nm) -> bool:
    """A 4-position TB strip (TB-TB-TB-TB): one block spanning the whole group."""
    return bool(nm) and ("tb-tb-tb-tb" in str(nm).lower())


def _group_wire_count(group: list) -> int:
    """Total conductors across a group's rows."""
    return sum(max(1, int(g.get("Wire_Count") or 1)) for g in group)


def _inst_terminal_capacity(loop: dict):
    """Max conductors an instrument anchor can hold, from its 'Field_<N>Term'
    visibility (e.g. Field_2Term -> 2, Field_4Term -> 4). Returns None when the
    anchor is NOT an instrument (so terminal blocks keep absorbing continuation
    rows as before). This caps continuation grouping so a 2-terminal instrument
    doesn't pile extra wire pairs onto itself -- the overflow rows become their
    own instrument instead."""
    src_inst = "inst" in str(loop.get("src_block") or "").lower()
    dst_inst = "inst" in str(loop.get("dst_block") or "").lower()
    if not (src_inst or dst_inst):
        return None
    vis = loop.get("src_block_visibility") if src_inst else loop.get("dst_block_visibility")
    m = re.search(r"(\d+)", str(vis or ""))
    return int(m.group(1)) if m else None


def _is_power_group(group: list) -> bool:
    """True if this loop/group is a power circuit (drives the instrument power
    connector). Reads the FillIndex 'Type' (Wire_Type) of any row in the group."""
    return any("power" in str(g.get("Wire_Type") or "").lower() for g in group)


def _is_pullbox(nm) -> bool:
    """A pull/junction box symbol (PullBox_L / PullBox_R): unlike a TB strip it does
    NOT span the group -- one box is placed per conductor (wire), so a 2-wire loop
    gets 2 boxes, a 3-wire loop gets 3, etc."""
    return bool(nm) and ("pullbox" in str(nm).lower())


def build_layout_plan(conduit_data: dict, loop_list: list,
                      block_heights: dict | None = None) -> dict:
    """
    PURE layout planner — compute the entire drawing as data (every block, its
    position, visibility state and attributes) WITHOUT touching AutoCAD.
    render_plan() then executes it via COM.  Keeping the layout pure makes the whole
    drawing assertable end-to-end (block count, positions, colours, attrs, spacing)
    in tests with no AutoCAD running.

    Returns:
        {
          "conduit": {name, x, y, visibility, attrs},
          "items":   [ {role, name, x, y, visibility, attrs}, ... ]  # in draw order
          "warnings":[ ... ],
        }
    """
    warnings: list = []
    items: list = []
    groups_meta: list = []
    plan = {
        "conduit": {"name": CONDUIT_NAME, "x": CENTER_X, "y": CONDUIT_Y,
                    "visibility": None, "attrs": dict(conduit_data or {})},
        "items": items,
        "groups": groups_meta,   # per-group placement geometry (drives pagination)
        "warnings": warnings,
    }

    current_y = START_Y
    gidx = 0
    i, n_loops = 0, len(loop_list)
    while i < n_loops:
        loop = loop_list[i]
        src_name = loop.get("src_block")
        dst_name = loop.get("dst_block")

        # this anchor + any following continuation rows = one group (one block).
        # An instrument only absorbs continuation rows up to its terminal capacity
        # (Field_<N>Term); rows beyond that become their own instrument instead of
        # over-stuffing one device. Terminal blocks (cap is None) absorb all as before.
        group = [loop]
        j = i + 1
        cap = _inst_terminal_capacity(loop)
        while j < n_loops and loop_list[j].get("is_continuation"):
            if cap is not None and _group_wire_count(group) >= cap:
                break
            group.append(loop_list[j])
            j += 1

        if not src_name and not dst_name:
            warnings.append(f"Loop has no src_block or dst_block — skipped: {loop}")
            i = j
            continue

        anchor_y = current_y
        gstart = len(items)
        src_is_inst = bool(src_name) and ("inst" in src_name.lower())
        dst_is_inst = bool(dst_name) and ("inst" in dst_name.lower())

        def _add(role, name, x, y, visibility, attrs):
            """Append a plan item, tagging it with its BlockIndex height (so the
            report/validator can reason about extent) and its group index (so the
            overlap validator only flags BETWEEN-group overlaps, not legitimate
            within-group stacking)."""
            items.append({"role": role, "name": name, "x": x, "y": y,
                          "visibility": visibility, "attrs": attrs, "group": gidx,
                          "height": _bi_height(block_heights, name, visibility)})

        # ── instrument: ONE block for the whole group ──
        # Power feed to an instrument: the Inst_Pwr_Wire connector sits AT the wire rows
        # (its entry points land on the two power wires) and routes the feed DOWN onto
        # the instrument's Line(+)/Neutral(-) terminals; the instrument is dropped
        # INST_PWR_DROP so its L/N meet the connector's exit. Term attrs are left as-is.
        is_power = _is_power_group(group)
        # A TSP/signal instrument group starts a touch high; drop the WHOLE group by
        # TSP_GROUP_DROP so the instrument + its wires + note all shift down together
        # (lowering anchor_y keeps everything aligned). Power groups already drop via
        # INST_PWR_DROP, so this is signal-instrument-only.
        if (src_is_inst or dst_is_inst) and not is_power:
            anchor_y = anchor_y - TSP_GROUP_DROP
        inst_y = anchor_y - (INST_PWR_DROP if is_power else 0.0)
        if dst_is_inst:
            _add("instrument", dst_name, RIGHT_X, inst_y, loop.get("dst_block_visibility"),
                 _maybe_hide_terms(_build_dst_attrs_group(group), group[0]))
            if is_power:
                _add("pwrwire", INST_PWR_WIRE_R, RIGHT_X + PWRWIRE_DX, anchor_y + PWRWIRE_DY, None, {})
            for _ni in _build_note_items("R", is_power, RIGHT_X, inst_y, loop.get("ref_dwg"),
                                         n_terms=(_inst_terminal_capacity(loop) or 2)):
                _ni["group"] = gidx
                items.append(_ni)
        elif src_is_inst:
            _add("instrument", src_name, LEFT_X, inst_y, loop.get("src_block_visibility"),
                 _maybe_hide_terms(_build_src_attrs_group(group), group[0]))
            if is_power:
                _add("pwrwire", INST_PWR_WIRE_L, LEFT_X + PWRWIRE_DX, anchor_y + PWRWIRE_DY, None, {})
            for _ni in _build_note_items("L", is_power, LEFT_X, inst_y, loop.get("ref_dwg"),
                                         n_terms=(_inst_terminal_capacity(loop) or 2)):
                _ni["group"] = gidx
                items.append(_ni)

        # Colours live on the anchor (Color 1-4); wire labels are per-row (each row
        # carries its two wires in Wire Label 1 & 2).
        group_colors  = [group[0].get(f"Wire{k}_Color") for k in range(1, 5)]
        group_slabels = [group[ci // 2].get(f"Wire{(ci % 2) + 1}_SrcLabel")
                         for ci in range(2 * len(group))]
        group_dlabels = [group[ci // 2].get(f"Wire{(ci % 2) + 1}_DstLabel")
                         for ci in range(2 * len(group))]
        ci = 0

        # ── 4-position TB strip: ONE block for the whole group ──
        src_is_bigtb = (not src_is_inst) and _is_bigtb(src_name)
        dst_is_bigtb = (not dst_is_inst) and _is_bigtb(dst_name)
        if src_is_bigtb:
            _add("src", src_name, LEFT_X, anchor_y + _shld_offset(src_name),
                 loop.get("src_block_visibility"),
                 _maybe_hide_terms(_build_side_group_tb(group, "src"), group[0]))
        if dst_is_bigtb:
            _add("dst", dst_name, RIGHT_X, anchor_y + _shld_offset(dst_name),
                 loop.get("dst_block_visibility"),
                 _maybe_hide_terms(_build_side_group_tb(group, "dst"), group[0]))

        # Pull/junction boxes are placed ONE PER WIRE (not one per row), so each
        # conductor enters its own box and the stacked boxes form the JB enclosure.
        src_is_pullbox = (not src_is_inst) and (not src_is_bigtb) and _is_pullbox(src_name)
        dst_is_pullbox = (not dst_is_inst) and (not dst_is_bigtb) and _is_pullbox(dst_name)

        # ── per-row: wires + any regular (single-row) side block ──
        gy = anchor_y
        for gi_row, g in enumerate(group):
            wc = max(1, int(g.get("Wire_Count") or 1))
            # A heater (HTR) is a 2-wire load; draw both conductors even when the
            # fill row only specified one.
            _gblk = str(g.get("src_block") or g.get("dst_block") or "").lower()
            if "htr" in _gblk or "heater" in _gblk:
                wc = max(2, wc)
            # A POWER instrument carries ONLY its L/N power feed (the anchor row) into the
            # conduit; the signal-pair continuation rows stay part of the one instrument
            # symbol but their wires are not drawn -- so a 4W power instrument reads as a
            # 2-wire conduit group, not 4-wire.
            _skip_wires = (is_power and (src_is_inst or dst_is_inst) and gi_row > 0)
            for w in range(wc):
                col = group_colors[ci]  if ci < len(group_colors)  else None
                sl  = group_slabels[ci] if ci < len(group_slabels) else None
                dl  = group_dlabels[ci] if ci < len(group_dlabels) else None
                ci += 1
                if _skip_wires:
                    continue
                wy = gy - (w * 0.5)
                _wattrs = _build_wire_attrs(g, w, color_override=col,
                                            src_label_override=sl, dst_label_override=dl)
                if g.get("Wire_Type"):
                    _wattrs["Wire_Type"] = g.get("Wire_Type")   # lets the harness see power vs signal
                _add("wire", WIRE_BLOCK_NAME, WIRE_X, wy, None, _wattrs)
                # One pull box per conductor, aligned to the wire. The box's
                # description (JB name) rides only the first box so it reads as a
                # single header rather than repeating down the stack.
                if src_is_pullbox and g.get("src_block"):
                    _add("src", g.get("src_block"), LEFT_X, wy, g.get("src_block_visibility"),
                         _maybe_hide_terms(_build_src_attrs(g), g) if w == 0 else {})
                if dst_is_pullbox and g.get("dst_block"):
                    _add("dst", g.get("dst_block"), RIGHT_X, wy, g.get("dst_block_visibility"),
                         _maybe_hide_terms(_build_dst_attrs(g), g) if w == 0 else {})
            if not src_is_inst and not src_is_bigtb and not src_is_pullbox and g.get("src_block"):
                _add("src", g.get("src_block"), LEFT_X, gy + _shld_offset(g.get("src_block")),
                     g.get("src_block_visibility"), _maybe_hide_terms(_build_src_attrs(g), g))
            if not dst_is_inst and not dst_is_bigtb and not dst_is_pullbox and g.get("dst_block"):
                _add("dst", g.get("dst_block"), RIGHT_X, gy + _shld_offset(g.get("dst_block")),
                     g.get("dst_block_visibility"), _maybe_hide_terms(_build_dst_attrs(g), g))
            gy = gy - wc * 0.5

        # Advance below the group, snapped to the 0.5 grid. A block extends DOWN from
        # its insertion by its height, so the group's true bottom is the lowest
        # (y - height) over its blocks. Clear that, add a gap, and RESERVE for the
        # next group's shield offset (shield blocks sit 0.5 above their anchor) -- the
        # missing reserve is what let consecutive instrument groups overlap.
        gitems = items[gstart:]
        # A block's DOWNWARD reach is its below-wire extent (|Insertion_ShiftY|), not its
        # total height: a tall-but-mostly-above symbol (ANT, RJ45) barely drops below its
        # wire. Using below-extent here is what makes the drop track real geometry instead
        # of a noisy total-height number. The above-wire part is reserved by the previous
        # group via _bi_top_overhang / next_offset.
        # Instruments keep the full-height model (their terminal drop is tuned separately);
        # everything else drops by real below-wire extent.
        use_below = not (src_is_inst or dst_is_inst)
        group_bottom = min([anchor_y] + [
            it["y"] - (_bi_below_extent(block_heights, it["name"], it["visibility"])
                       if use_below else it["height"])
            for it in gitems])
        next_offset = 0.0
        nxt_topheavy = False
        if j < n_loops:
            nxt = loop_list[j]
            next_offset = max(_shld_offset(nxt.get("src_block")), _shld_offset(nxt.get("dst_block")))
            # A signal instrument's L/N terminals sit above its anchor -> reserve room so
            # the next instrument doesn't poke into this group. A POWER instrument is
            # dropped (its top is just the wire rows), so it needs no reserve.
            nxt_inst = "inst" in (str(nxt.get("src_block") or "") + "|" +
                                  str(nxt.get("dst_block") or "")).lower()
            if nxt_inst and not _is_power_group([nxt]):
                next_offset = max(next_offset, INST_TOP_RESERVE)
            # A top-heavy next symbol (art extends well above its wire, e.g. an HOA
            # switch's box) needs that overhang reserved here so it doesn't collide with
            # this group. Only kicks in past TOP_OVERHANG_MIN so ordinary symbols stay tight.
            if not nxt_inst:
                oh = max(_bi_top_overhang(block_heights, nxt.get("src_block"), nxt.get("src_block_visibility")),
                         _bi_top_overhang(block_heights, nxt.get("dst_block"), nxt.get("dst_block_visibility")))
                if oh > TOP_OVERHANG_MIN:
                    next_offset = max(next_offset, oh)
                    nxt_topheavy = True
        # Advance to the next group by its GRID rows, not its raw art height: allow a
        # block to overhang its rows by BLOCK_ART_MARGIN before it costs another 0.5
        # step (so a 1.52-tall block over 3 wire rows advances 1.5, not 2.0). next_offset
        # reserves room above the next group's anchor (shield / instrument terminals);
        # folding it INTO the round keeps every group on the 0.5 grid.
        depth = (anchor_y - group_bottom) - BLOCK_ART_MARGIN + GROUP_GAP + next_offset
        advance = _round_up_step(depth)
        # Nearly-overlapping guard: only add another 0.5 step when the group's real art
        # OVERLAPS into the next symbol's space by more than SYMBOL_OVERLAP_TOL (a genuine
        # collision, not just a small on-grid gap). Symbols that fit their slot stay tight.
        # When the NEXT symbol is top-heavy (its box pokes up, e.g. an HOA switch), demand a
        # small POSITIVE clearance instead of mere non-overlap -- a box that just grazes the
        # symbol above reads as touching, so guarantee visible breathing room above it.
        overlap = (anchor_y - group_bottom) + next_offset - advance
        _tol = -TOPHEAVY_MIN_CLEAR if nxt_topheavy else SYMBOL_OVERLAP_TOL
        if overlap > _tol:
            advance += GRID_STEP
        # Clamp the advance to a tight window tied to wire count: at LEAST the space the
        # group's own wire rows occupy (floor -- so a multi-wire block always clears its
        # rows even if its below-extent is short), and at MOST one 0.5 step beyond that
        # (cap -- so a top-heavy single-row symbol never balloons). Where a group lands in
        # [floor, floor+step] is set by its real below-extent + the next symbol's overhang.
        # Instruments are exempt: their terminal drop is tuned separately.
        if not (src_is_inst or dst_is_inst):
            wire_rows_adv = _round_up_step(max(1, ci) * GRID_STEP)
            advance = max(wire_rows_adv, min(advance, wire_rows_adv + GRID_STEP))
            # 2-wire-and-up blocks get one extra 0.5 of breathing room below them (per
            # request). Only single-wire blocks stay tight.
            if ci >= 2:
                advance += GRID_STEP
        # Record placement-invariant geometry for the paginator: rel_bottom is how far
        # the group's lowest extent sits below its anchor (<= 0); advance is the drop
        # to the next group's anchor. Both are independent of WHERE the anchor is, so
        # the paginator can re-bin groups onto fresh sheets from START_Y with no drift.
        groups_meta.append({
            "gidx": gidx, "loop_start": i, "loop_end": j,
            "anchor_y": anchor_y, "group_bottom": group_bottom,
            "rel_bottom": group_bottom - anchor_y, "advance": advance,
        })
        current_y = anchor_y - advance
        i = j
        gidx += 1

    return plan


def paginate_loops(loop_list: list, block_heights: dict | None = None,
                   page_floor: float = PAGE_FLOOR) -> list:
    """PURE pagination — split loop_list into per-sheet [start, end) index ranges so
    every block on a sheet stays above `page_floor` (1_BORDER bottom + margin).

    Reuses build_layout_plan's recorded group geometry (no duplicated formula), bins
    groups onto sheets from START_Y, and NEVER splits an instrument group (an anchor
    plus its continuation rows). A single group taller than one sheet is placed alone
    (it would overflow regardless — paginating can't help, so we don't loop forever).

    Returns a list of (start, end) index pairs; a single-sheet conduit returns one pair.
    """
    plan = build_layout_plan({}, loop_list, block_heights)
    groups = plan.get("groups", [])
    if len(groups) <= 1:
        return [(0, len(loop_list))]

    chunks: list = []
    cur_y = START_Y
    page_start = 0
    first_on_page = True
    gi, ng = 0, len(groups)
    while gi < ng:
        g = groups[gi]
        bottom = cur_y + g["rel_bottom"]          # this group's bottom if placed here
        if bottom < page_floor and not first_on_page:
            # Doesn't fit; this group opens a new sheet. Re-evaluate it at START_Y.
            chunks.append((page_start, g["loop_start"]))
            page_start = g["loop_start"]
            cur_y = START_Y
            first_on_page = True
            continue
        cur_y = cur_y - g["advance"]
        first_on_page = False
        gi += 1
    chunks.append((page_start, len(loop_list)))
    return chunks


def _apply_dyn_props(block_ref, props: dict, warnings: list):
    """Set named dynamic-block properties (e.g. the bracket's Distance1 / Flip state1).
    Best-effort + busy-safe: a property that isn't present or won't take the value is
    logged and skipped, never fatal."""
    try:
        if not _com_retry(lambda: block_ref.IsDynamicBlock, any_error=True):
            return
        dps = _com_retry(lambda: block_ref.GetDynamicBlockProperties(), any_error=True)
        by_name = {}
        for p in dps:
            try:
                by_name[str(_com_retry(lambda: p.PropertyName, any_error=True))] = p
            except Exception:
                pass
        for name, val in (props or {}).items():
            p = by_name.get(name)
            if p is None:
                continue
            # Flip parameters reject a plain Python int ("Invalid input"); they take a
            # VT_I2 (short) VARIANT. Try the raw value first (works for the linear
            # Distance1 double), then fall back to a VT_I2 short for flips/lookups.
            try:
                _com_retry(lambda pp=p, vv=val: setattr(pp, "Value", vv), any_error=True)
            except Exception:
                try:
                    sval = win32com.client.VARIANT(pythoncom.VT_I2, int(val))
                    _com_retry(lambda pp=p, vv=sval: setattr(pp, "Value", vv), any_error=True)
                except Exception as e:
                    warnings.append(f"note bracket dyn prop {name!r} not set: {e}")
        _com_retry(lambda: block_ref.Update(), any_error=True)
    except Exception as e:
        warnings.append(f"note bracket dyn props failed: {e}")


def _add_mtext(model, x: float, y: float, text: str, height: float, width: float,
               warnings: list, attach=None):
    """Add an MTEXT entity (the REF. DWG callout) and return it (or None). Block-only
    render_plan can't place text, so this is the one direct AddMText path. `attach` is the
    AutoCAD attachment point (e.g. 6 middle-right, 8 bottom-center) so the text centers on
    (x, y) like the template. Busy-safe; a failure is logged and skipped, never fatal."""
    try:
        mt = _com_retry(lambda: model.AddMText(_pt(x, y), float(width), str(text)), any_error=True)
        try:
            # Retry the height write like every other COM call here: a transient 'rejected by
            # callee' on this one raw setter used to be eaten by the except-pass, leaving the
            # REF. DWG callout at AutoCAD's default text height on a sheet that still saved ✓.
            _com_retry(lambda: setattr(mt, "Height", float(height)), any_error=True)
        except Exception:
            pass
        if attach is not None:
            # Set the attachment, then re-assert the insertion point so (x, y) is the
            # attachment location (AutoCAD otherwise keeps the old top-left anchor).
            try:
                _com_retry(lambda: setattr(mt, "AttachmentPoint", int(attach)), any_error=True)
                _com_retry(lambda: setattr(mt, "InsertionPoint", _pt(x, y)), any_error=True)
            except Exception:
                pass
        _com_retry(lambda: mt.Update(), any_error=True)
        return mt
    except Exception as e:
        warnings.append(f"could not add REF.DWG text {text!r}: {e}")
        _log(f"  REF.DWG mtext failed: {e}")
        return None


def _group_entities(model, ents: list, name: str, warnings: list):
    """Put the note's bracket + text into one AutoCAD group so moving one moves both
    (matches how the note behaves on the template). Best-effort / non-fatal."""
    ents = [e for e in ents if e is not None]
    if len(ents) < 2:
        return
    try:
        # Reach the document from a placed ENTITY (AcadEntity has .Document; AcadModelSpace
        # does not expose it reliably). Groups live on the document.
        doc = _com_retry(lambda: ents[0].Document, any_error=True)
        grp = _com_retry(lambda: doc.Groups.Add(name), any_error=True)
        arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, ents)
        _com_retry(lambda: grp.AppendItems(arr), any_error=True)
    except Exception as e:
        warnings.append(f"note group {name!r} failed (non-fatal): {e}")


def _force_populated_attrs_visible(ref, warnings: list, pattern: str, label: str) -> None:
    """Force every attribute of `ref` whose TAG matches `pattern` and that carries a VALUE to
    be visible, overriding a mis-authored block that hides it in the current visibility state.

    Several library blocks the same author touched share one defect: an attribute holds the
    right value but its Visible flag is off in the active dynamic-block state, so the value
    never shows. LISA writes the value, then re-asserts Visible=True here; the override
    persists through SaveAs (verified). Only attributes that actually carry a value are
    touched, so empty/blanked slots stay hidden. Best-effort: any COM failure is logged and
    skipped, never fatal."""
    try:
        if not _com_retry(lambda: ref.HasAttributes, any_error=True):
            return
        for a in _com_retry(lambda: ref.GetAttributes(), any_error=True):
            try:
                tag = str(_com_retry(lambda: a.TagString, any_error=True) or "")
                if not re.match(pattern, tag):
                    continue
                val = str(_com_retry(lambda: a.TextString, any_error=True) or "").strip()
                if val and not _com_retry(lambda: a.Visible, any_error=True):
                    _com_retry(lambda: setattr(a, "Visible", True), any_error=True)
                    _com_retry(lambda: a.Update(), any_error=True)
            except Exception:
                continue
    except Exception as e:
        warnings.append(f"force {label} visibility failed (non-fatal): {e}")


def _force_terminal_labels_visible(ref, warnings: list) -> None:
    """Instrument terminal numbers: a mis-authored symbol hides Term03+ in its 4-term state,
    so a 4-wire TSP instrument writes terminals 3 & 4 but only shows 1 & 2. Force the
    populated Term boxes visible (empty ones, incl. 'Hide from Generation', stay hidden; a
    POWER instrument's all-blank terminals are untouched)."""
    _force_populated_attrs_visible(ref, warnings, r"^Term\d+$", "terminal-label")


def render_plan(model, plan: dict, warnings: list) -> list:
    """Execute a layout plan against AutoCAD model space — the only COM-coupled
    part of generation. Returns the list of blocks actually placed (with each
    block's live layer read from the open doc) for the generation report. A block
    that fails to insert is skipped (with a warning already logged)."""
    placed = []
    note_groups = {}   # note_group id -> [entities to group together]
    c = plan.get("conduit")
    if c:
        ref = _insert_block(model, c["x"], c["y"], c["name"], warnings)
        if ref is None:
            # The Conduit block is the sheet's backbone. If it couldn't be placed -- even
            # after _insert_block's own busy-retries -- the sheet would be blank/incomplete
            # (this is the "<unknown>.InsertBlock" transient stall). Raise so _generate_dwg_impl
            # fails the sheet and generate_dwg re-renders it from a fresh template copy, instead
            # of saving a broken drawing that reports success.
            raise RuntimeError("Conduit block could not be placed (AutoCAD busy) — sheet not saved")
        if c.get("visibility"):                       # continuation sheets set TBL10_Continuation_Start / Middle / End
            _apply_visibility(ref, c["visibility"], warnings)
        _set_attrs(ref, c["attrs"], warnings)
        # The Conduit block hides its CONT_Previous_DWG / CONT_Next_DWG reference in the
        # continuation visibility state (same mis-authoring as the instrument terminals),
        # so a populated number wouldn't show. Re-assert visibility on the one(s) we set.
        _force_populated_attrs_visible(ref, warnings, r"^CONT_(Previous|Next)_DWG$", "continuation-ref")
        placed.append(_placed_record("conduit", 0, c, ref))
    for idx, it in enumerate(plan.get("items", []), start=1):
        gid = it.get("note_group")
        if it.get("kind") == "mtext":
            # REF. DWG callout text — not a block, so it goes through AddMText, not InsertBlock.
            mt = _add_mtext(model, it["x"], it["y"], it.get("text", ""),
                            it.get("text_height", 0.125), it.get("width", 3.0), warnings,
                            attach=it.get("attach"))
            if gid and mt is not None:
                note_groups.setdefault(gid, []).append(mt)
            continue
        ref = _insert_block(model, it["x"], it["y"], it["name"], warnings)
        if ref is None:
            continue
        if it.get("rotation_deg") is not None:
            _com_retry(lambda: setattr(ref, "Rotation", math.radians(it["rotation_deg"])), any_error=True)
        if it.get("dyn_props"):
            _apply_dyn_props(ref, it["dyn_props"], warnings)
        if it.get("visibility"):
            _apply_visibility(ref, it["visibility"], warnings)
        _set_attrs(ref, it["attrs"], warnings)
        if it.get("role") == "instrument":
            _force_terminal_labels_visible(ref, warnings)
        if gid:
            note_groups.setdefault(gid, []).append(ref)
        placed.append(_placed_record(it["role"], idx, it, ref))
    for gid, ents in note_groups.items():
        _group_entities(model, ents, f"IDPNOTE_{gid}", warnings)
    return placed


def _placed_record(role: str, n: int, item: dict, ref) -> dict:
    """Capture one inserted block for the generation report — reading its layer
    LIVE from the open doc (reliable; reopening a saved DWG is the part that's flaky)."""
    layer = "0"
    try:
        layer = str(ref.Layer)
    except Exception:
        pass
    return {
        "id": f"{role}-{n}",
        "role": role,
        "block": item["name"],
        "x": float(item["x"]),
        "y": float(item["y"]),
        "height": float(item.get("height") or 0.0),
        "group": item.get("group"),
        "visibility": item.get("visibility"),
        "layer": layer,
        "attrs": item.get("attrs") or {},
    }


def build_generation_report(records: list, job_id: str, units: str = "inches",
                            generator: str = "lisa-idp", version: str = "1.0") -> dict:
    """Build a cad_ai_harness schema-compliant report. Accepts either render's
    `placed` records (with live layer) or raw plan items. Everything LISA draws is
    a block insert, so every object is a 'blockref'; `height` (block extent) and
    `attributes` (terminals/tags) are carried so the harness can check overlap and
    terminal completeness, not just block/layer names."""
    objects = []
    for idx, p in enumerate(records, start=1):
        block = p.get("block") or p.get("name")
        attrs = p.get("attrs") or p.get("attributes") or {}
        # keep only non-null attribute values (drop the report bloat)
        attrs = {k: v for k, v in attrs.items() if v not in (None, "")}
        obj = {
            "id":         p.get("id") or f"{p.get('role', 'obj')}-{idx}",
            "type":       "blockref",
            "layer":      p.get("layer") or "0",
            "block":      block,
            "position":   [round(float(p["x"]), 4), round(float(p["y"]), 4)],
            "height":     round(float(p.get("height") or 0.0), 4),
            "group":      p.get("group"),
            "attributes": attrs,
        }
        if p.get("visibility"):
            obj["visibility"] = p.get("visibility")   # instrument terminal capacity (Field_<N>Term)
        objects.append(obj)
    return {
        "job_id":    job_id,
        "generator": generator,
        "version":   version,
        "units":     units,
        "objects":   objects,
    }


def report_from_plan(plan: dict, job_id: str, units: str = "inches") -> dict:
    """Build the report straight from a layout plan (pure; no AutoCAD). Layer
    defaults to '0' (where LISA inserts). Used for tests and offline checks."""
    records = []
    c = plan.get("conduit")
    if c:
        records.append({**c, "role": "conduit"})
    records.extend(plan.get("items", []))
    return build_generation_report(records, job_id, units)


# ── Validation harness integration (additive; never breaks generation) ────────

def _find_harness_root() -> str | None:
    """Locate the cad_ai_harness project: $IDP_HARNESS_DIR, else an upward search
    from this file and the CWD, else a known repo fallback. Returns None if absent."""
    env = os.getenv("IDP_HARNESS_DIR")
    if env and os.path.isdir(env):
        return env
    for start in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        d = start
        for _ in range(10):
            cand = os.path.join(d, "cad_ai_harness")
            if os.path.isdir(cand):
                return cand
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    fallback = (r"C:\Users\patton.zepp\OneDrive - Lyles Group\Desktop"
                r"\IDP Generation Repository\APP2\cad_ai_harness")
    return fallback if os.path.isdir(fallback) else None


def emit_and_validate(placed: list, output_path: str, warnings: list) -> dict:
    """Write the harness job folder (generation_report.json + drawing.dwg copy +
    placeholder drawing.pdf) and run the validators. Fully additive: any failure
    here is logged and returned, never raised — a missing/broken harness must not
    break a real generation."""
    result = {"status": "skipped", "errors": [], "job_dir": None, "report_path": None}
    try:
        harness = _find_harness_root()
        if not harness:
            # The cad_ai_harness is an OPTIONAL, dev-only post-generation validator that
            # isn't part of this repo (found via $IDP_HARNESS_DIR or a local folder). Its
            # absence is normal on any machine that doesn't have it -- the drawing still
            # generates fine -- so log it rather than surface a user-facing "missing"
            # warning that alarms people for no reason.
            _log("validation skipped: cad_ai_harness not present (optional dev tool)")
            return result

        job_name = os.path.splitext(os.path.basename(output_path))[0]
        job_dir = os.path.join(harness, "output", job_name)
        os.makedirs(job_dir, exist_ok=True)

        report = build_generation_report(placed, job_name)
        report_path = os.path.join(job_dir, "generation_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        result["report_path"] = report_path

        try:
            if os.path.exists(output_path):
                shutil.copyfile(output_path, os.path.join(job_dir, "drawing.dwg"))
        except Exception as e:
            warnings.append(f"could not copy dwg into job folder: {e}")

        pdf_path = os.path.join(job_dir, "drawing.pdf")
        if not os.path.exists(pdf_path):   # placeholder; real plot-to-PDF deferred
            with open(pdf_path, "w", encoding="utf-8") as fh:
                fh.write("%PDF-1.4\n%placeholder - plot-to-PDF not wired yet\n%%EOF\n")

        if harness not in sys.path:
            sys.path.insert(0, harness)
        rv = importlib.import_module("run_validation")
        findings = rv.validate_job(job_dir)
        result["job_dir"] = job_dir
        result["status"] = "pass" if not findings else "fail"
        result["errors"] = [str(f) for f in findings]

        if findings:
            _log(f"  VALIDATION FAIL ({len(findings)} error(s)) — {job_dir}")
            for f in findings:
                _log(f"    - {f}")
        else:
            _log(f"  VALIDATION PASS — {job_dir}")
    except Exception as e:
        warnings.append(f"validation step error (non-fatal): {e}")
        result["status"] = "error"
        result["errors"] = [str(e)]
    return result


def _generate_dwg_impl(conduit_data: dict, loop_list: list, output_path: str,
                 ref_doc_rows: list | None = None,
                 dev_rows: list | None = None,
                 block_heights: dict | None = None,
                 cont_state: str | None = None,
                 cont_prev: str | None = None,
                 cont_next: str | None = None,
                 project_desc: dict | None = None,
                 sheet_number=None, drawing_no=None) -> dict:
    """
    Generate one AutoCAD DWG for a single conduit (or one sheet of a multi-sheet
    conduit). For continuation sheets the caller passes:
      cont_state — conduit visibility (TBL10_Continuation_Start / Continuation_Middle
                   / Continuation_End); None for a normal single-sheet conduit.
      cont_prev / cont_next — adjacent sheet drawing numbers (informational / logged only;
                   the continuation drawing-number references are handled outside this tool).
      project_desc — the workbook's "Project Description" sheet, as {"lines": [...]}
                   (positionally Owner/Job Title/Content/Proj No/Status/Date/Engineer/
                   Drafter — same shape wdp_writer uses for the .wdp's *[1]..*[8]).
      sheet_number — this sheet's running number in the whole deliverable (cover = 1,
                   then up with no restart), written to the title block's SHEET attribute.
      drawing_no — this sheet's drawing number (its filename stem), written to DRAWING_NO.

    Both feed _set_title_block_attrs, which writes straight to the title block's own
    attributes using the same tag mapping AutoCAD Electrical's "Update Title Block"
    command uses (_Templates/Project/default.wdt) — so every drawing's title block is
    populated as part of generation itself, with no separate update step and no dialog.

    Returns:
        { "success": bool, "output_path": str, "warnings": list, "error": str|None }
    """
    warnings = []
    _log(f"generate_dwg START  output={output_path}")
    _log(f"  conduit_data (non-null): { {k: v for k, v in conduit_data.items() if v is not None} }")
    _log(f"  loop_list count: {len(loop_list)}")
    if loop_list:
        _log(f"  loop[0] src={loop_list[0].get('src_block')!r}  dst={loop_list[0].get('dst_block')!r}  ISATag_FunctIdent={loop_list[0].get('ISATag_FunctIdent')!r}")

    template_path = _get_template_path()
    if not template_path:
        return _err(f"No DWG template found in: {_TEMPLATE_DIR}")

    # ── 1. Connect to a running AutoCAD instance ──────────────────────────────
    # Retry: on a multi-sheet run AutoCAD is often momentarily busy between sheets
    # (COM raises "Call was rejected by callee" / drops the Version call). A fresh
    # GetActiveObject after a short wait almost always succeeds. GetActiveObject can
    # only ever reach ONE AutoCAD instance -- if more than one is running (several
    # drawings open, each its own process) automation calls can land on an instance
    # that's mid-command and never quiesce for the whole retry window, so the error
    # below spells out that specific, actionable cause rather than a bare COM error.
    # Reuse the cached AutoCAD handle across sheets (a Generate All doesn't reconnect every
    # drawing); it revalidates cheaply and reconnects transparently if AutoCAD went stale.
    acad_app, _conn_err = _get_acad_app()
    if acad_app is None:
        return _err(_acad_busy_hint(f"AutoCAD not running or not accessible: {_conn_err}"))
    _log(f"  AutoCAD version: {acad_app.Version}")

    # Wait for AutoCAD to go idle before we start hammering it. On a rapid Generate All
    # it's often still finishing the previous sheet; starting now is what most often
    # triggers "Call was rejected by callee" mid-render. Best-effort (proceeds after the
    # timeout regardless; the retries below are the backstop).
    _wait_quiescent(acad_app)

    # ── 2. Close any previously open document at the target path ─────────────
    _close_if_open(acad_app, output_path)

    # ── 3. New DWG from template = COPY the template to the output path, then OPEN
    # it. We deliberately do NOT use Documents.Add(template): Add treats the file as
    # a prototype and silently produces a BLANK drawing for some DWGs (e.g. files not
    # re-saved by genuine Autodesk software), which would drop the border + block
    # library. Copy + Open uses the file's real contents and never touches the
    # template on disk. Use the doc Open RETURNS (not ActiveDocument) so a concurrent
    # call can't hand us the wrong doc.
    try:
        shutil.copyfile(template_path, output_path)
    except Exception as e:
        return _err(f"Failed to copy template '{template_path}': {e}")
    # Retry the Open too: it's the other point AutoCAD rejects while busy. Re-acquire
    # the app handle between tries in case the connection was dropped.
    doc, _open_err = None, None
    for _attempt in range(_ACAD_CONNECT_RETRIES):
        try:
            doc = acad_app.Documents.Open(output_path)
            break
        except Exception as e:
            doc, _open_err = None, e
            time.sleep(_ACAD_CONNECT_DELAY)
            acad_app, _ = _get_acad_app(force=True)   # reconnect if the handle dropped
            if acad_app is None:
                break
    if doc is None:
        return _err(_acad_busy_hint(f"Failed to open template '{template_path}': {_open_err}"))
    try:
        # Wait for the freshly-opened doc to be usable, but only as long as needed. Poll until
        # model space is reachable AND AutoCAD is *stably* idle, returning the instant it is (so
        # the common sheet is faster than the old flat time.sleep(1.5)). If it never stabilises
        # within the cap, fall back to the old guaranteed settle so we never operate on a doc
        # that's LESS ready than before -- a not-ready doc made unlock/clear fail silently and
        # leave the template's leftover geometry in the saved sheet.
        if not _wait_doc_ready(acad_app, doc, timeout=6.0):
            time.sleep(1.5)
        # Capturing model space and reading doc.Name both touch the just-opened doc over COM;
        # while AutoCAD is still finishing the open these can be rejected ('Call was rejected by
        # callee'). Keep them INSIDE the try so a rejection becomes a whole-sheet retry (return
        # _err) instead of an uncaught 500 with no retry -- this unprotected open/prep region was
        # a source of hard "Generation failed" that the whole-sheet retry never got to recover.
        model = _com_retry(lambda: doc.ModelSpace)
        try:
            _doc_name = str(_com_retry(lambda: doc.Name))
        except Exception:
            _doc_name = "<pending>"
        _log(f"  Opened template copy — doc: {_doc_name}")

        # ── 4. Unlock all layers ──────────────────────────────────────────────
        _unlock_all_layers(doc, warnings)

        # ── 5. Clear model space ──────────────────────────────────────────────
        _clear_model_space(model, warnings)

        # ── 6+7. Plan the layout (pure) then render it ───────────────────────
        # build_layout_plan computes every block + position + attrs as data;
        # render_plan is the only part that talks to AutoCAD. This split is what
        # makes the layout testable end-to-end without AutoCAD.
        plan = build_layout_plan(conduit_data, loop_list, block_heights)
        warnings.extend(plan.get("warnings", []))
        # Continuation: set the conduit table's visibility state and the prev/next
        # sheet links so the drawing reads as part of a multi-sheet conductor.
        if cont_state:
            plan["conduit"]["visibility"] = cont_state
            # Fill the Conduit block's OWN continuation-reference attributes with the adjacent
            # sheet's drawing number. The dynamic block's visibility state draws the "CONTINUED
            # FROM PREVIOUS DWG" / "CONTINUED ON NEXT DWG" label; CONT_Previous_DWG /
            # CONT_Next_DWG supply the number that goes with it. (The old messed-up block hid
            # these attributes, so an earlier workaround drew the number as separate text and a
            # later change dropped that text entirely; with the block fixed we now populate the
            # real attribute and re-assert its visibility in render_plan.)
            plan["conduit"]["attrs"]["CONT_Previous_DWG"] = cont_prev or ""
            plan["conduit"]["attrs"]["CONT_Next_DWG"]     = cont_next or ""
            _log(f"  continuation sheet: state={cont_state} prev={cont_prev!r} next={cont_next!r}")
        _log(f"  layout plan: {len(plan.get('items', []))} block(s)")
        placed = render_plan(model, plan, warnings)

        # Completeness check: every planned BLOCK (the conduit + each non-text item) should
        # have landed. If fewer were placed, a block insert silently failed mid-render (a
        # transient COM stall) and the sheet is incomplete -- raise so generate_dwg re-renders
        # it from a fresh template copy instead of saving a partial drawing that reports
        # success. MTEXT callouts aren't in `placed`, so they're excluded from the count.
        expected_blocks = (1 if plan.get("conduit") else 0) + \
            sum(1 for it in plan.get("items", []) if it.get("kind") != "mtext")
        if len(placed) < expected_blocks:
            raise RuntimeError(
                "Sheet incomplete: %d of %d blocks placed (AutoCAD busy) — not saved"
                % (len(placed), expected_blocks))

        # ── 8. Fill supporting documents + deviation-notes table ──────────────
        _log(f"  ref_doc_rows count={len(ref_doc_rows or [])}  dev_rows count={len(dev_rows or [])}")
        if ref_doc_rows or dev_rows:
            _fill_ref_docs_table(model, ref_doc_rows or [], dev_rows or [], warnings)
        else:
            _log("  ref_docs table: skipped (no ref_doc_rows or dev_rows)")

        # ── 8b. Title block ────────────────────────────────────────────────────
        title_block_values = _build_title_block_values(conduit_data, project_desc, sheet_number, drawing_no)
        if title_block_values:
            _set_title_block_attrs(doc, title_block_values, warnings)

        # ── 9. Save ───────────────────────────────────────────────────────────
        _log("Calling SaveAs...")
        _com_retry(lambda: doc.SaveAs(output_path))   # retry a transient busy rejection

        if not os.path.exists(output_path):
            return _err(f"SaveAs completed but file not found at: {output_path}")

        _log("Closing document to release file lock...")
        try:
            doc.Close(False)   # False = do not save changes (already saved)
        except Exception as ec:
            _log(f"  doc.Close() raised (non-fatal): {ec}")

        # ── 10. Emit the harness job folder + auto-validate (additive) ────────
        validation = emit_and_validate(placed, output_path, warnings)

        _log("Done.")
        return {
            "success":     True,
            "output_path": output_path,
            "warnings":    warnings,
            "error":       None,
            "validation":  validation,
        }

    except Exception as e:
        _log(f"generate_dwg EXCEPTION: {e}")
        try:
            doc.Close(False)
        except Exception:
            pass
        return _err(
            f"Something went wrong while drawing this sheet in AutoCAD, so it wasn't saved. "
            f"Make sure AutoCAD is open and idle (no active command or dialog) and the output "
            f"folder is writable, then try again. Technical detail: {e}", warnings)


# ── AutoCAD helpers ───────────────────────────────────────────────────────────

def _unlock_all_layers(doc, warnings: list):
    # A HARD failure here (can't enumerate the layer table even after busy-retries) means the doc
    # isn't ready to be operated on -- and letting it slide leaves layers LOCKED, which then makes
    # the deletes in _clear_model_space fail silently and leaves the template's leftover geometry
    # in the sheet. So a persistent failure raises (whole-sheet retry from a fresh copy). A single
    # stubborn layer (per-layer set) stays soft -- some layers legitimately refuse unlock.
    #
    # Iterate `doc.Layers` DIRECTLY -- the proven, shipped form. Do NOT use list(doc.Layers):
    # under win32com late binding the AutoCAD Layers collection raises "object does not support
    # enumeration" for list(), so materialising it first is unreliable across binding states.
    def _unlock():
        for layer in doc.Layers:
            try:
                layer.Lock   = False
                layer.Freeze = False
            except Exception:
                pass
    # any_error: a just-opened doc can transiently raise AttributeError('Open.Layers') when the
    # .Layers collection isn't bound yet -- retry it like a busy hiccup instead of failing.
    _com_retry(_unlock, any_error=True)


def _clear_model_space(model, warnings: list):
    """
    Remove non-block geometry from model space, preserving block references
    (template catalog symbols, NOTES, WD_M, etc.) and border/title/table layers.
    EXCEPTION: pre-existing Conduit block(s) are deleted because the generator
    inserts its own Conduit at the same spot (0,0) and would otherwise overlay
    the template's leftover one.

    Raises if the model space can't be enumerated, or if a leftover template Conduit can't be
    removed. Both mean the sheet would render on top of un-cleared template geometry (the stray
    Conduit shows its own continuation visibility), so the caller must re-render from a fresh
    template copy rather than save a corrupt drawing. This used to be swallowed into a warning,
    which is exactly how a not-ready doc produced 'good'-looking sheets with the template's
    continuation block bleeding through.
    """
    count = _com_retry(lambda: model.Count)
    entities = [_com_retry(lambda: model.Item(i)) for i in range(count)]
    deleted = preserved = skipped = stray_conduit_failed = 0
    for e in entities:
        try:
            try:
                obj_name = str(_com_retry(lambda: e.ObjectName))
            except Exception:
                obj_name = ""

            if obj_name == "AcDbBlockReference":
                bname = ""
                try:
                    bname = str(e.EffectiveName)
                except Exception:
                    try:
                        bname = str(e.Name)
                    except Exception:
                        bname = ""
                if bname.upper() == CONDUIT_NAME.upper():
                    # This leftover template Conduit MUST go -- the generator inserts its own at
                    # 0,0 and a survivor overlays it (and shows the template's continuation
                    # visibility). A delete failure here is NOT a soft skip: count it so we can
                    # fail the sheet below instead of saving a drawing with a phantom conduit.
                    try:
                        _com_retry(lambda: e.Delete())
                        deleted += 1
                    except Exception:
                        stray_conduit_failed += 1
                else:
                    preserved += 1
                continue

            if obj_name == "AcDbTable":
                preserved += 1
                continue

            # Only NON-block, non-table geometry needs its layer inspected. Reading .Layer
            # for every entity -- including the hundreds of symbol-library blocks that are
            # preserved regardless -- was a large chunk of the per-sheet clear time (an
            # extra COM round-trip per block). Blocks/tables return above, so defer it.
            try:
                layer_name = str(_com_retry(lambda: e.Layer)).upper()
            except Exception:
                layer_name = ""
            if "BORDER" in layer_name or "TITLE" in layer_name or "TABLE" in layer_name:
                preserved += 1
                continue

            _com_retry(lambda: e.Delete())
            deleted += 1
        except Exception:
            skipped += 1
    _log(f"_clear_model_space: deleted={deleted} preserved={preserved} "
         f"skipped={skipped} stray_conduit_failed={stray_conduit_failed}")
    if stray_conduit_failed:
        raise RuntimeError(
            f"Could not remove {stray_conduit_failed} leftover template Conduit block(s) from "
            f"model space — the sheet would render on top of them; not saving")


# The section every IDP conduit drawing belongs to (matches the .wdp's ICD subsection).
_TITLEBLOCK_SECTION = "INTERCONNECTION DIAGRAMS"


def _project_line_values(project_desc: dict | None) -> dict:
    """Map the workbook's Project Description lines onto the title block's project tags
    OWNER/JOB_TITLE/CONTENT/PROJECT_NO/STATUS/DATE/ENGINEER/DRAFTER. Only non-blank lines
    are included, so a missing field keeps the template default rather than blanking it."""
    values = {}
    lines = (project_desc or {}).get("lines") or []
    for tag, idx in (("OWNER", 0), ("JOB_TITLE", 1), ("CONTENT", 2), ("PROJECT_NO", 3),
                      ("STATUS", 4), ("DATE", 5), ("ENGINEER", 6), ("DRAFTER", 7)):
        if idx < len(lines) and str(lines[idx] or "").strip():
            values[tag] = str(lines[idx]).strip()
    return values


def _build_title_block_values(conduit_data: dict, project_desc: dict | None,
                              sheet_number=None, drawing_no=None) -> dict:
    """Map our data onto the title block's own attribute tags (verified present on
    WML-SI_TITLEBLOCK_SCHEMATIC), the same tags AutoCAD Electrical's "Update Title Block"
    writes to:
        OWNER/JOB_TITLE/CONTENT/PROJECT_NO/STATUS/DATE/ENGINEER/DRAFTER  (project lines)
        DESC1/DESC2/DESC3   -> per-drawing description (Conduit / Source1 / Dest1 names)
        SECTION             -> the drawing section ("INTERCONNECTION DIAGRAMS")
        DRAWING_NO          -> this drawing's number (its filename stem, e.g. 73.1159-15e)
        SHEET               -> this drawing's running sheet number in the deliverable set
    Only includes tags we have a non-blank value for, so a missing Project Description
    field just leaves that attribute at its template default instead of blanking it.

    NOTE: DRAWING_NO and SHEET are independent -- the drawing number is the sheet's own id
    (matches its filename), while SHEET is its position in the whole deliverable (cover = 1,
    then up with no restart), so a drawing named '-15e' can legitimately be sheet 18."""
    values = _project_line_values(project_desc)
    for tag, key in (("DESC1", "Cdt_Name"), ("DESC2", "Src_Name01"), ("DESC3", "Dst_Name01")):
        val = (conduit_data or {}).get(key)
        if str(val or "").strip():
            values[tag] = str(val).strip()
    values["SECTION"] = _TITLEBLOCK_SECTION
    if drawing_no is not None and str(drawing_no).strip():
        values["DRAWING_NO"] = str(drawing_no).strip()
    if sheet_number is not None and str(sheet_number).strip():
        values["SHEET"] = str(sheet_number).strip()
    return values


# How many times to re-scan for the title block before giving up. The scan uses raw COM
# layout/entity enumeration, which on a busy Generate All can transiently fail or come back
# empty on an otherwise-fine sheet (the "no title block found" false alarm) -- so retry.
_TB_LOOKUP_TRIES = int(os.getenv("IDP_TB_LOOKUP_TRIES", "4"))


def _try_set_title_block_attrs_once(doc, values: dict, warnings: list) -> bool:
    """One scan for the title block (TITLEBLOCK_NAME) on a paper-space layout; if found, set
    its attributes and return True. Returns False if no title block turned up this pass. May
    raise on a COM enumeration error so the caller can retry a transient hiccup.

    The title block lives on a PAPER SPACE layout (confirmed live: "Layout1", on layer
    1_TITLEBLOCK), never in Model Space. A layout's entities live in ITS OWN block table
    record, reached via Layout.Block (not ModelSpace)."""
    for layout in doc.Layouts:
        try:
            if str(layout.Name).strip().lower() == "model":
                continue
            space = layout.Block
        except Exception:
            continue
        for e in space:
            try:
                if e.ObjectName != "AcDbBlockReference":
                    continue
                bname = str(getattr(e, "EffectiveName", "") or e.Name)
                if bname.upper() != TITLEBLOCK_NAME.upper():
                    continue
            except Exception:
                continue
            attrs = _com_retry(lambda: e.GetAttributes())
            # A tag can appear MORE THAN ONCE in the block (e.g. the cover sheet shows
            # OWNER/JOB_TITLE/PROJECT_NO/CONTENT both as big center text and again in the
            # small title block). Collect ALL attribute refs per tag and set every one, so
            # the center block fills too -- not just the last-seen copy.
            tags = {}
            for a in attrs:
                try:
                    # a.TagString is itself a COM read that can transiently fail on a busy
                    # session; retry it (any_error) so a hiccup doesn't drop a tag from the
                    # map -- which would silently leave that title-block attribute unwritten.
                    tags.setdefault(str(_com_retry(lambda a=a: a.TagString, any_error=True)).upper(), []).append(a)
                except Exception:
                    pass
            set_count = 0
            for tag, val in values.items():
                for a in tags.get(tag.upper(), []):
                    try:
                        # Retry the actual write on ANY transient error (same gap that once
                        # dropped _apply_visibility's set): a raw `a.TextString = val` that hits
                        # a momentary 'rejected by callee' used to be swallowed into the warning
                        # below, leaving the title-block attribute blank on a sheet that still
                        # saved ✓. _com_retry fires only on failure, so a good sheet pays nothing.
                        _com_retry(lambda a=a, val=val: setattr(a, "TextString", val), any_error=True)
                        set_count += 1
                    except Exception as ex:
                        warnings.append(f"Could not set title block attribute {tag!r}: {ex}")
            _log(f"  title block (layout {layout.Name!r}): set {set_count} attribute(s) for {len(values)} tag(s)")
            return True   # only one title block per sheet
    return False


def _set_title_block_attrs(doc, values: dict, warnings: list) -> None:
    """Write straight to the title block's own attributes -- no "Update Title Block" command,
    no dialog, just a direct COM attribute set. `values` is {tag: text}.

    Retries the scan: the raw COM layout/entity enumeration can transiently fail or return
    empty on a busy Generate All, which used to falsely report 'no title block found' on a
    sheet that has one. Since the title block is essentially always present, a miss almost
    always means a transient hiccup -- so re-scan a few times before warning."""
    for attempt in range(_TB_LOOKUP_TRIES):
        try:
            if _try_set_title_block_attrs_once(doc, values, warnings):
                return
        except Exception as ex:
            if attempt == _TB_LOOKUP_TRIES - 1:
                warnings.append(f"Could not set title block attributes: {ex}")
                return
        if attempt < _TB_LOOKUP_TRIES - 1:
            time.sleep(0.4)
    warnings.append(f"No '{TITLEBLOCK_NAME}' block found on any layout of this sheet — title block not updated.")


def fill_general_titleblocks(items: list, project_desc: dict | None = None) -> list:
    """Fill the title block of each GENERAL sheet (cover / drawing index / symbols legend).
    These sheets are copied straight from the project template, so without this they keep
    the template's placeholder DRAWING_NO ('XX.XXXX-G1') and default SHEET.

    `items` is a list of (dwg_path, drawing_no, sheet_number). For each, open the drawing
    in the running AutoCAD, write the project lines + DRAWING_NO + SHEET (leaving SECTION =
    'GENERAL' and the DESC label from the template untouched), save and close. Best-effort:
    returns a list of warning strings and never raises."""
    warnings = []
    items = [it for it in (items or []) if it and it[0] and os.path.exists(it[0])]
    if not items:
        return warnings
    base_vals = _project_line_values(project_desc)
    try:
        acad_app, _ = _get_acad_app()
        if acad_app is None:
            warnings.append("General sheets: AutoCAD not accessible; title blocks left as template.")
            return warnings
        _wait_quiescent(acad_app)
        for path, drawing_no, sheet_number in items:
            doc = None
            try:
                _close_if_open(acad_app, path)
                doc = _com_retry(lambda: acad_app.Documents.Open(path), any_error=True)
                vals = dict(base_vals)
                if drawing_no is not None and str(drawing_no).strip():
                    vals["DRAWING_NO"] = str(drawing_no).strip()
                if sheet_number is not None and str(sheet_number).strip():
                    vals["SHEET"] = str(sheet_number).strip()
                _set_title_block_attrs(doc, vals, warnings)
                # SaveAs can miss on a transient lock -- OneDrive syncing the sheet just after
                # it was copied in, the AutoCAD .dwl lock, or AutoCAD momentarily busy. Retry
                # a few times on ANY error so a transient miss doesn't fail the whole fill
                # (which leaves the once-per-project marker unset and re-opens these sheets on
                # every conduit of a Generate All).
                _com_retry(lambda: doc.SaveAs(path), tries=3, delay=0.5, any_error=True)
                _log(f"  general title block filled: {os.path.basename(path)} "
                     f"(DRAWING_NO={drawing_no!r}, SHEET={sheet_number})")
            except Exception as ex:
                warnings.append(f"General sheet {os.path.basename(path)}: title block not updated ({ex}).")
            finally:
                # ALWAYS close the sheet, even if the update/save failed. Previously a failed
                # save left the drawing OPEN and locked in AutoCAD, so a Generate All piled up
                # dozens of locked general drawings.
                if doc is not None:
                    try:
                        _com_retry(lambda: doc.Close(False), any_error=True)
                    except Exception:
                        pass
    except Exception as ex:
        warnings.append(f"General sheets: title-block update failed ({ex}).")
    return warnings


# Header row of the drawing-index table (matches the template's G2 AcDbTable).
_INDEX_TABLE_HEADER = ["SHEET NO.", "DRAWING NO.", "SECTION", "DRAWING DESCRIPTION"]

# Drawing-index table cell formatting. Text is auto-sized DOWN from _INDEX_TEXT_H_MAX so the
# longest string in each column fits on ONE line (no wrapping = uniform single-height rows),
# clamped to _INDEX_TEXT_H_MIN so it never turns microscopic. _INDEX_CHAR_W_AT_1 is a
# conservative per-character glyph advance at text height 0.1 for the template's Standard
# style (measured ~0.084-0.095/char; the high value slightly overestimates width so text
# always fits). Alignment matches the template: SHEET/DRAWING/SECTION centered, description
# left. AutoCAD AcCellAlignment: 4 = MiddleLeft, 5 = MiddleCenter.
_INDEX_TEXT_H_MAX   = 0.10
_INDEX_TEXT_H_MIN   = 0.055
_INDEX_CHAR_W_AT_1  = 0.095
_INDEX_ROW_HEIGHT   = 0.2533
_INDEX_CELL_MARGIN  = 0.06
_ALIGN_MIDDLE_LEFT  = 4
_ALIGN_MIDDLE_CENTER = 5


def _find_index_table(doc):
    """The first AcDbTable in a drawing-index sheet's model space (the DRAWING INDEX table),
    or None. The index table is the only AcDbTable on the G2 sheet.

    Index-based scan with every COM read _com_retry-wrapped, mirroring _fill_ref_docs_table's
    hardened search: a transient '-2147418111 Call was rejected by callee' on the real table's
    .ObjectName used to be swallowed by the `except: continue`, so the table was skipped and this
    returned None -- and populate_drawing_index then STILL SaveAs'd the sheet with the index left
    as the unfilled template placeholder while reporting ✓. Retrying the reads self-heals the
    hiccup; a genuine failure still exhausts the retries and is skipped exactly as before."""
    ms = _com_retry(lambda: doc.ModelSpace, any_error=True)
    n = int(_com_retry(lambda: ms.Count, any_error=True))
    for i in range(n):
        try:
            e = _com_retry(lambda i=i: ms.Item(i), any_error=True)
            if str(_com_retry(lambda e=e: e.ObjectName, any_error=True)) == "AcDbTable":
                return e
        except Exception:
            continue
    return None


def _index_fit_text_height(data_rows: list, colw: list, hmargin: float) -> float:
    """Largest uniform text height at which the longest string in every column still fits on
    one line (so no row wraps to double height), clamped to [_INDEX_TEXT_H_MIN, _MAX]."""
    h = _INDEX_TEXT_H_MAX
    for c in range(len(colw)):
        maxlen = max([len(row[c]) for row in data_rows] or [0])
        if maxlen <= 0:
            continue
        usable = max(0.1, colw[c] - 2 * hmargin)
        fit = usable * 0.1 / (_INDEX_CHAR_W_AT_1 * maxlen)
        if fit < h:
            h = fit
    return max(_INDEX_TEXT_H_MIN, min(_INDEX_TEXT_H_MAX, h))


def _fill_index_table(table, rows: list, warnings: list) -> None:
    """Resize the DRAWING INDEX AcDbTable to one header + len(rows) data rows, write every
    cell, and format it to match the AIC standard index: uniform single-height rows (text
    auto-sized so nothing wraps), SHEET/DRAWING/SECTION centered, description left-aligned.
    Grows/shrinks by insert/delete so any project size fits. COM calls are retried (any_error)
    since AcadTable dispatch can transiently hiccup on a busy session."""
    ncols = int(_com_retry(lambda: table.Columns))
    try:
        colw = [float(_com_retry(lambda c=c: table.GetColumnWidth(c))) for c in range(ncols)]
    except Exception:
        colw = [1.22, 1.42, 1.72, 3.02][:ncols]
    try:
        hmargin = float(table.HorzCellMargin) or _INDEX_CELL_MARGIN
    except Exception:
        hmargin = _INDEX_CELL_MARGIN

    data = [[str(r.get("sheet_no", "") or ""), str(r.get("drawing_no", "") or ""),
             str(r.get("section", "") or ""), str(r.get("description", "") or "")][:ncols]
            for r in rows]
    text_h = _index_fit_text_height(data, colw, hmargin)

    # Resize to header + data.
    target = 1 + len(data)
    cur = int(_com_retry(lambda: table.Rows))
    if target > cur:
        rh = _com_retry(lambda: table.GetRowHeight(cur - 1))
        _com_retry(lambda: table.InsertRows(cur, rh, target - cur), any_error=True)
    elif target < cur:
        _com_retry(lambda: table.DeleteRows(target, cur - target), any_error=True)

    # Header text (keep the template's header height/alignment).
    for c in range(min(ncols, len(_INDEX_TABLE_HEADER))):
        htext = _INDEX_TABLE_HEADER[c]
        _com_retry(lambda c=c, htext=htext: table.SetText(0, c, htext), any_error=True)

    # Data cells: text + uniform fit height + alignment. (Newly inserted rows otherwise
    # default to center alignment / the template height, so set every data cell explicitly.)
    shrink = text_h < _INDEX_TEXT_H_MAX - 1e-6
    for i, row in enumerate(data, start=1):
        for c in range(ncols):
            val = row[c]
            _com_retry(lambda i=i, c=c, val=val: table.SetText(i, c, val), any_error=True)
            if shrink:
                _com_retry(lambda i=i, c=c: table.SetCellTextHeight(i, c, text_h), any_error=True)
            align = _ALIGN_MIDDLE_LEFT if c == 3 else _ALIGN_MIDDLE_CENTER
            _com_retry(lambda i=i, c=c, align=align: table.SetCellAlignment(i, c, align), any_error=True)

    # Normalize every row to a single uniform height now that nothing should wrap.
    for r in range(target):
        _com_retry(lambda r=r: table.SetRowHeight(r, _INDEX_ROW_HEIGHT), any_error=True)


def populate_drawing_index(index_pages: list, project_desc: dict | None = None) -> list:
    """Fill the DRAWING INDEX sheet(s) of a project. Each index page's table lists every
    drawing in the deliverable, split across pages when the list overflows one sheet.

    `index_pages` is a list of dicts, one per index sheet, in page order:
        {"path", "drawing_no", "sheet_number", "rows": [ {sheet_no, drawing_no, section,
         description}, ... ]}
    For each page: open the DWG in the running AutoCAD, write its title block (project lines +
    DRAWING_NO + SHEET, leaving DESC1='DRAWING INDEX' / SECTION='GENERAL' from the template),
    fill its AcDbTable with that page's row slice, save and close. One AutoCAD connection is
    reused across all pages. Best-effort: returns a list of warning strings, never raises."""
    warnings = []
    pages = [p for p in (index_pages or []) if p and p.get("path") and os.path.exists(p["path"])]
    if not pages:
        return warnings
    base_vals = _project_line_values(project_desc)
    try:
        acad_app, _ = _get_acad_app()
        if acad_app is None:
            warnings.append("Drawing index: AutoCAD not accessible; index table left as template.")
            return warnings
        _wait_quiescent(acad_app)
        for pg in pages:
            path = pg["path"]
            doc = None
            try:
                _close_if_open(acad_app, path)
                doc = _com_retry(lambda: acad_app.Documents.Open(path), any_error=True)
                # Title block (same tags fill_general_titleblocks writes for other generals).
                vals = dict(base_vals)
                if str(pg.get("drawing_no") or "").strip():
                    vals["DRAWING_NO"] = str(pg["drawing_no"]).strip()
                if str(pg.get("sheet_number") or "").strip():
                    vals["SHEET"] = str(pg["sheet_number"]).strip()
                _set_title_block_attrs(doc, vals, warnings)
                # Index table.
                table = _find_index_table(doc)
                if table is None:
                    warnings.append(f"Drawing index {os.path.basename(path)}: no index table found; skipped.")
                else:
                    _fill_index_table(table, pg.get("rows") or [], warnings)
                _com_retry(lambda: doc.SaveAs(path), tries=3, delay=0.5, any_error=True)
                _log(f"  drawing index filled: {os.path.basename(path)} "
                     f"({len(pg.get('rows') or [])} row(s), SHEET={pg.get('sheet_number')})")
            except Exception as ex:
                warnings.append(f"Drawing index {os.path.basename(path)}: not populated ({ex}).")
            finally:
                if doc is not None:
                    try:
                        _com_retry(lambda: doc.Close(False), any_error=True)
                    except Exception:
                        pass
    except Exception as ex:
        warnings.append(f"Drawing index: population failed ({ex}).")
    return warnings


def _insert_block(model, x: float, y: float, block_name: str, warnings: list):
    """
    Insert a block at (x, y) and return a live COM reference.

    AutoCAD Electrical subclasses IAcadBlockReference; the COM marshaller
    sometimes raises on return even though the INSERT succeeded.  We detect
    that case by comparing model.Count before/after and grabbing model[-1].
    """
    insertion_pt = _pt(x, y)

    # Insert, retrying a transient "AutoCAD busy" rejection. We can't just wrap InsertBlock
    # in _com_retry blindly: the AE COM marshaller sometimes RAISES even though the insert
    # actually succeeded (count went up). So on each raise we check the count -- if it grew,
    # the block WAS placed (recover it, don't retry, or we'd double-insert); if it didn't
    # grow and the error is a transient busy rejection, wait and retry so the block isn't
    # silently skipped (which used to leave a "successful" sheet missing symbols).
    ref = None
    last_err = None
    for attempt in range(8):
        try:
            count_before = model.Count
        except Exception:
            count_before = -1
        try:
            ref = model.InsertBlock(insertion_pt, block_name, 1.0, 1.0, 1.0, 0.0)
            _log(f"_insert_block '{block_name}' at ({x},{y}) — normal return")
            break
        except Exception as e:
            last_err = e
            _log(f"_insert_block '{block_name}' — COM return raised: {e}")
            try:
                count_after = model.Count
            except Exception:
                count_after = count_before
            if count_after > count_before:
                ref = model.Item(count_after - 1)   # succeeded despite the raise
                _log(f"_insert_block '{block_name}' — recovered via model[-1]")
                break
            if _is_busy_error(e) and attempt < 7:
                time.sleep(0.5)   # AutoCAD busy -- wait and retry the insert
                continue
            break

    if ref is None:
        if _is_busy_error(last_err):
            warnings.append(
                f"Couldn't place the '{block_name}' symbol -- AutoCAD stayed busy through "
                f"several retries. Technical detail: {last_err}")
        else:
            warnings.append(
                f"Couldn't place the '{block_name}' symbol. This usually means that block "
                f"isn't in the current template's block library (check the symbol name in the "
                f"workbook matches a block in the template). Technical detail: {last_err}")
        return None

    try:
        ref.Update()
    except Exception as e:
        _log(f"  ref.Update() after insert raised: {e}")

    return ref


def _norm_vis(s: str) -> str:
    """Normalize a visibility-state name for tolerant matching (drop case and
    any non-alphanumerics so 'Field Auxiliary-4Term' == 'FieldAuxiliary_4Term')."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _match_vis_state(vis_state: str, allowed: list):
    """Resolve vis_state to one of the block's real states, tolerating case,
    punctuation, and misspellings (e.g. 'FieldAuxillary' -> 'FieldAuxiliary')."""
    if not allowed:
        return str(vis_state)          # nothing to validate against
    if vis_state in allowed:
        return vis_state
    nmap = {_norm_vis(a): a for a in allowed}
    nv = _norm_vis(vis_state)
    if nv in nmap:
        return nmap[nv]
    # closest spelling among the allowed states (handles typos like the double-L)
    m = difflib.get_close_matches(nv, list(nmap.keys()), n=1, cutoff=0.7)
    if m:
        return nmap[m[0]]
    return None


def _apply_visibility(block_ref, vis_state, warnings: list):
    """Set a dynamic block's visibility state (e.g. 'Field_4Term').

    Best-effort + busy-safe: finds the visibility dynamic-property and sets it to the closest
    matching real state. EVERY COM call is _com_retry-wrapped (like _apply_dyn_props) so a
    transient 'Call was rejected by callee' -- AutoCAD momentarily busy -- is retried instead of
    silently leaving the visibility unset (which dropped the continuation marker on split conduits
    and the terminal-capacity state on instruments). Retries only fire ON failure, so a normal
    sheet pays nothing. A genuine failure still never aborts generation.
    """
    if not vis_state:
        return
    try:
        if not _com_retry(lambda: block_ref.IsDynamicBlock, any_error=True):
            return
        props = _com_retry(lambda: block_ref.GetDynamicBlockProperties(), any_error=True)
        # find the visibility property (by name, else whichever lists the state)
        visprop = None
        for p in props:
            try:
                if "visibility" in str(_com_retry(lambda pp=p: pp.PropertyName, any_error=True)).lower():
                    visprop = p
                    break
            except Exception:
                continue
        if visprop is None:
            for p in props:
                try:
                    allowed = [str(a) for a in (_com_retry(lambda pp=p: pp.AllowedValues, any_error=True) or [])]
                except Exception:
                    allowed = []
                if _match_vis_state(str(vis_state), allowed) in allowed:
                    visprop = p
                    break
        if visprop is None:
            warnings.append(f"no visibility property for state '{vis_state}'")
            return
        try:
            allowed = [str(a) for a in (_com_retry(lambda: visprop.AllowedValues, any_error=True) or [])]
        except Exception:
            allowed = []
        target = _match_vis_state(str(vis_state), allowed)
        if target is None:
            warnings.append(f"visibility '{vis_state}' not in {allowed}")
            return
        _com_retry(lambda: setattr(visprop, "Value", target), any_error=True)
        _com_retry(lambda: block_ref.Update(), any_error=True)
    except Exception as e:
        warnings.append(f"apply visibility '{vis_state}' failed: {e}")


# Full color words -> the shop abbreviations used everywhere in the workbook/drawings.
# Defensive only: the workbook uses abbreviations, but a full word can slip in via Excel's
# "AutoComplete for cell values" (types "GRN", Excel fills a prior "GREEN"). Unknown words
# pass through untouched, so this never corrupts a value it doesn't recognize.
_COLOR_FULL_TO_ABBR = {
    "BLACK": "BLK", "WHITE": "WHT", "RED": "RED", "GREEN": "GRN",
    "BLUE": "BLU", "ORANGE": "ORG", "YELLOW": "YEL", "BROWN": "BRN",
    "VIOLET": "VIO", "GRAY": "GRY", "GREY": "GRY",
}


def _normalize_color(value):
    """Map full color words to abbreviations (GREEN->GRN), handling slash combos
    (GREEN/WHITE->GRN/WHT) and leaving already-abbreviated or unknown values as-is."""
    if value is None:
        return value
    parts = str(value).split("/")
    return "/".join(_COLOR_FULL_TO_ABBR.get(p.strip().upper(), p.strip()) for p in parts)


def _set_attrs(block_ref, attr_map: dict, warnings: list):
    """Set block attributes from attr_map (case-insensitive tag matching)."""
    try:
        # any_error=True: right after an insert, GetAttributes can transiently fail with a
        # dynamic-dispatch error ("InsertBlock.GetAttributes") that isn't a classic busy
        # HRESULT. It's a read (idempotent), so retrying any transient hiccup is safe and
        # stops a single blip from dropping EVERY attribute on the block.
        attrs = _com_retry(lambda: block_ref.GetAttributes(), any_error=True)
    except Exception as e:
        warnings.append(f"GetAttributes() failed: {e}")
        _log(f"  GetAttributes() failed: {e}")
        return

    if not attrs:
        # Not an error: some symbol blocks (simple connectors, etc.) simply have no
        # fill-in attributes, so there's nothing to set. Log it for debugging but don't
        # surface it as a user-facing warning -- it's just noise in the generation log.
        _log("  GetAttributes() returned empty (block has no attributes -- nothing to set)")
        return

    # Read each attribute's tag ONCE, resiliently. att.TagString is itself a COM call
    # that can transiently fail ("GetAttributes.TagString") while AutoCAD is busy during
    # a rapid Generate All. It used to be read unprotected in several places (the debug
    # log, the error message, the "no match" message), so one transient tag-read hiccup
    # threw an uncaught error and killed the WHOLE sheet. Reading it here with retry, and
    # reusing the cached string everywhere below, makes a tag-read hiccup a per-attribute
    # skip at worst -- never a lost sheet.
    tagged = []
    for att in attrs:
        try:
            tag = str(_com_retry(lambda: att.TagString, any_error=True))
        except Exception as e:
            _log(f"    (could not read an attribute tag, skipping it: {e})")
            continue
        tagged.append((att, tag))

    upper_map = {k.upper(): v for k, v in attr_map.items() if v is not None}
    # Single Rating cell fills EVERY rating-type attribute the block carries
    # (Rating, FU_Rating, Rating1, DISC_Rating, ... anything whose tag has "RATING").
    rating_val = upper_map.get("RATING")

    _log(f"  _set_attrs: {len(tagged)} attr(s), {len(upper_map)} map key(s)")
    _log(f"    tags : {[t for _, t in tagged]}")
    _log(f"    keys : {list(upper_map.keys())}")

    def _write(att, val):
        # Retry the actual COM writes on ANY transient error, not just classic "busy"
        # HRESULTs. During a busy Generate All these also hit transient dynamic-dispatch
        # errors ("Property 'GetAttributes.TextString' can not be set", "GetAttributes.
        # Update") that aren't busy HRESULTs -- which is why terminal/description attrs
        # sometimes came out blank. Writing the same value / calling Update again is
        # idempotent, so retrying any transient hiccup is safe (a genuinely unsettable
        # attr still fails after the retries, exactly as before).
        _com_retry(lambda: setattr(att, "TextString", val), any_error=True)
        _com_retry(lambda: att.Update(), any_error=True)

    assigned = 0
    for att, tag in tagged:
        key = tag.upper()
        try:
            if key in upper_map:
                val = str(upper_map[key])
                if "COLOR" in key or "COLOUR" in key:
                    val = _normalize_color(val)   # GREEN -> GRN safety net
                _write(att, val)
                assigned += 1
                _log(f"    SET  {tag!r} = {val!r}")
            elif "RATING" in key and rating_val is not None:
                val = str(rating_val)
                _write(att, val)
                assigned += 1
                _log(f"    SET* {tag!r} = {val!r}  (rating fill-all)")
            else:
                _log(f"    SKIP {tag!r}")
        except Exception as e:
            warnings.append(f"Could not set attr '{tag}': {e}")
            _log(f"    ERR  {tag!r}: {e}")

    _log(f"  assigned={assigned}/{len(tagged)}")

    if assigned == 0 and upper_map:
        warnings.append(
            f"No attrs matched. Tags: {[t for _, t in tagged]}. "
            f"Keys: {list(upper_map.keys())}"
        )

    try:
        block_ref.Update()
    except Exception:
        pass


def _bi_height(block_heights: dict | None, name, vis) -> float:
    """Block height from the parsed BlockIndex map (keyed 'NAME|VIS', then 'NAME').
    Returns 0.0 when unknown so the wire stack / minimum grid step governs."""
    if not name or not block_heights:
        return 0.0
    nm = str(name).strip().upper()
    vs = str(vis).strip().upper() if vis else "NA"
    h = block_heights.get(f"{nm}|{vs}")
    if h is None and vis:
        # Tolerant match: the BlockIndex visibility-state spelling can drift from the
        # block's / dropdown's (e.g. "FieldAuxillary" vs "FieldAuxiliary"). Resolve to
        # the closest same-block key BEFORE falling back to the whole-block height —
        # that fallback is much taller and was doubling those instruments' spacing.
        prefix = f"{nm}|"
        cand = {k[len(prefix):]: k for k in block_heights if k.startswith(prefix)}
        if cand:
            m = _match_vis_state(vs, list(cand.keys()))
            if m in cand:
                h = block_heights.get(cand[m])
    if h is None:
        h = block_heights.get(nm)
    return float(h) if h else 0.0


def _bi_top_overhang(block_heights: dict | None, name, vis) -> float:
    """How far a block's art extends ABOVE its wire terminal = height - |Insertion_ShiftY|.
    Used to reserve clearance so a top-heavy symbol (e.g. an HOA switch) doesn't collide
    with the symbol above it. 0.0 when unknown."""
    if not name or not block_heights:
        return 0.0
    nm = str(name).strip().upper()
    vs = str(vis).strip().upper() if vis else "NA"
    sy = block_heights.get(f"__SHIFTY__|{nm}|{vs}")
    if sy is None and vis:
        prefix = f"__SHIFTY__|{nm}|"
        cand = {k[len(prefix):]: k for k in block_heights if k.startswith(prefix)}
        if cand:
            m = _match_vis_state(vs, list(cand.keys()))
            if m in cand:
                sy = block_heights.get(cand[m])
    if sy is None:
        sy = block_heights.get(f"__SHIFTY__|{nm}")
    if sy is None:
        return 0.0
    return max(0.0, _bi_height(block_heights, name, vis) - abs(float(sy)))


def _bi_below_extent(block_heights: dict | None, name, vis) -> float:
    """How far a block's art extends BELOW its wire terminal = |Insertion_ShiftY|.
    This — NOT the block's total height — is what governs the drop to the next symbol:
    a block that is tall but sits mostly ABOVE its wire (e.g. ANT, RJ45) needs little
    downward room. The above-wire part is handled separately via _bi_top_overhang, which
    the previous group reserves. Falls back to full height when Insertion_ShiftY is absent
    (so blocks missing that value keep prior behavior until measured)."""
    if not name or not block_heights:
        return 0.0
    nm = str(name).strip().upper()
    vs = str(vis).strip().upper() if vis else "NA"
    sy = block_heights.get(f"__SHIFTY__|{nm}|{vs}")
    if sy is None and vis:
        prefix = f"__SHIFTY__|{nm}|"
        cand = {k[len(prefix):]: k for k in block_heights if k.startswith(prefix)}
        if cand:
            m = _match_vis_state(vs, list(cand.keys()))
            if m in cand:
                sy = block_heights.get(cand[m])
    if sy is None:
        sy = block_heights.get(f"__SHIFTY__|{nm}")
    if sy is None:
        return _bi_height(block_heights, name, vis)   # no ShiftY on record → prior behavior
    return abs(float(sy))


def _block_height(src_ref, dst_ref, warnings: list) -> float:
    """Return the height of the taller block, or FALLBACK_BLOCK_HEIGHT."""
    heights = []
    for ref in (src_ref, dst_ref):
        if ref is None:
            continue
        try:
            min_pt = ref.GetBoundingBox()[0]
            max_pt = ref.GetBoundingBox()[1]
            heights.append(float(max_pt[1]) - float(min_pt[1]))
        except Exception as e:
            warnings.append(f"GetBoundingBox failed: {e} — using fallback height")
            heights.append(FALLBACK_BLOCK_HEIGHT)
    return max(heights) if heights else FALLBACK_BLOCK_HEIGHT


# ── Attribute mapping helpers ─────────────────────────────────────────────────

_TYPE_TO_LABEL = {
    "pullrope": "PULL ROPE",
    "pull rope": "PULL ROPE",
    "fiber": "FIBER",
    "coax": "COAX",
    "cat5e": "CAT-5E",
    "cat-6": "CAT-6",
}


def _wire_type_label(wire_type: str | None) -> str | None:
    """Convert Wire_Type to a wire label when Wire Label column is blank."""
    if not wire_type:
        return None
    return _TYPE_TO_LABEL.get(str(wire_type).lower(), str(wire_type).upper())


def _shld_offset(block_name) -> float:
    """Shield-on-top blocks (name begins with 'Shld', e.g. Shld-TB-TB_Square) are
    raised one terminal so their top shield terminal sits above the conductor wires
    (the shield terminal is not connected to a wire)."""
    if block_name and str(block_name).strip().lower().startswith("shld"):
        return 0.5   # one wire-spacing up
    return 0.0


_WIRE_BLANK = {"N/A", "NA", "-", "--", "NONE", "NULL", ""}


def _clean_wire_val(v) -> str | None:
    """Return None for N/A placeholder values so wire block attributes stay blank."""
    if v is None:
        return None
    return None if str(v).strip().upper() in _WIRE_BLANK else v


def _split_gauge(v):
    """(kind, core) classifier for a wire gauge -- mirrors workbook_mapper._split_gauge.
    kind in {'KCMIL','MCM','AWG','TEXT','NA','EMPTY'}; core is the bare size (no '#', no
    'AWG'/'KCMIL'/'MCM' suffix, no spaces)."""
    if v is None:
        return ("EMPTY", "")
    s = str(v).strip()
    if not s:
        return ("EMPTY", "")
    if s.upper() in ("N/A", "NA", "N\\A"):
        return ("NA", s)
    core = s[1:].strip() if s.startswith("#") else s
    up = core.upper()
    if "KCMIL" in up:
        num = up.replace("KCMIL", "").replace(" ", "").strip()
        return ("KCMIL", num)
    if "MCM" in up:                       # keep MCM as MCM (do NOT convert to kcmil)
        num = up.replace("MCM", "").replace(" ", "").strip()
        return ("MCM", num)
    if up.endswith("AWG"):
        up = up[:-3]
    up = up.replace(" ", "").strip()
    if up[:1].isdigit():
        return ("AWG", up)
    return ("TEXT", s)


def _gauge_label(v):
    """Format a wire gauge for the wire LABEL / block size attribute:
       AWG sizes   -> '#<size>'      ('10 AWG' -> '#10', '4AWG' -> '#4', '3/0' -> '#3/0')
       KCMIL sizes -> '<size>KCMIL'  (no '#';  '300 KCMIL' -> '300KCMIL')
       MCM sizes   -> '<size>MCM'    (kept as MCM, not converted; '250 mcm' -> '250MCM')
       text gauges left as typed;  blanks / N/A -> None (attribute stays blank)."""
    v = _clean_wire_val(v)
    if v is None:
        return None
    kind, core = _split_gauge(v)
    if kind == "KCMIL":
        return f"{core}KCMIL"
    if kind == "MCM":
        return f"{core}MCM"
    if kind == "AWG":
        return f"#{core}"
    return v


def _build_wire_attrs(loop: dict, wire_index: int, color_override=None,
                      src_label_override=None, dst_label_override=None) -> dict:
    n = wire_index + 1
    color = color_override if color_override is not None else loop.get(f"Wire{n}_Color")
    # Per-wire label override mode (from the hidden WL{n}_Mode columns):
    #   "Blank"  -> no label (block still placed)
    #   "Custom" -> use exactly what's typed in the workbook (no fallback)
    #   "Default"/blank -> use the workbook label, falling back to the wire-type label
    mode = str(loop.get(f"Wire{n}_LabelMode") or "").strip().upper()
    if mode == "BLANK":
        src_label = None
        dst_label = None
    elif mode == "CUSTOM":
        src_label = loop.get(f"Wire{n}_SrcLabel")
        dst_label = loop.get(f"Wire{n}_DstLabel") or src_label
    else:
        # Fall back to Wire_Type label when no explicit wire label is in the workbook
        src_label = loop.get(f"Wire{n}_SrcLabel") or _wire_type_label(loop.get("Wire_Type"))
        dst_label = loop.get(f"Wire{n}_DstLabel") or src_label  # mirror src when no dst
    # Group label override (the instrument anchor holds all 4 wire labels)
    if src_label_override is not None:
        src_label = src_label_override
    if dst_label_override is not None:
        dst_label = dst_label_override or src_label_override
    return {
        "Src_WireLabel": src_label,
        "Dst_WireLabel": dst_label,
        "Src_Color":     _clean_wire_val(color),
        "Dst_Color":     _clean_wire_val(color),
        "Src_Size":      _gauge_label(loop.get(f"Wire{n}_Size")),
        "Dst_Size":      _gauge_label(loop.get(f"Wire{n}_Size")),
    }


_HIDE_TERM_SENTINEL = "##HIDETERM##"  # parser stamps this on a term flagged "hidden"


def _maybe_hide_terms(attrs: dict, loop: dict) -> dict:
    """Blank any terminal-number attribute the parser stamped as hidden (the
    user toggled 'Hide from Generation' on that term cell).  The slot position is
    preserved -- a hidden Term2 stays Term2 (blank), it does not shift the rest."""
    for k, v in list(attrs.items()):
        if k.startswith("Term") and str(v or "").strip() == _HIDE_TERM_SENTINEL:
            attrs[k] = ""
    return attrs


def _group_side_tags(group: list, side: str) -> list:
    """All non-empty per-conductor tags on one side across a whole group."""
    key = "Src" if side == "src" else "Dst"
    out = []
    for g in group:
        for n in (1, 2, 3, 4):
            v = g.get(f"Wire{n}_{key}Tag")
            if v not in (None, ""):
                out.append(str(v).strip())
    return out


def _collapse_tags(tags: list) -> list:
    """If every populated tag is identical, keep it only on the first slot and
    blank the rest (e.g. a 3-pole device tagged 'DISC-5' on all poles shows once)."""
    nonempty = [t for t in tags if t not in (None, "")]
    if len(nonempty) >= 2 and len({str(t).strip() for t in nonempty}) == 1:
        # Blank the rest explicitly ("" not None) so they overwrite the block's
        # default attribute text instead of falling back to it.
        return [nonempty[0], "", "", ""]
    # Blank empty slots ("" not None) too, so an unused Tag position clears the
    # block's default placeholder text (e.g. "MPR2") instead of leaving it shown.
    return [t if t not in (None, "") else "" for t in tags]


def _spare_qty_attr(v):
    """Spare quantity formatted as '(X#)' for the block attribute.
    Accepts 'X4' / '4' / '(X4)' / 4 -> '(X4)'.  Returns None when there's no number."""
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return f"(X{m.group()})" if m else None


def _spare_type_attr(v):
    """Spare 'Type' wrapped in parentheses for the block attribute, idempotently:
    'CONTROL' -> '(CONTROL)'; an already-parenthesised '(CONTROL)' is left as-is.
    None / blank passes through unchanged (no stray '()')."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return v
    if s.startswith("(") and s.endswith(")"):
        return s
    return f"({s})"


def _renumber_inst_terms(attrs: dict, loop: dict, side: str) -> dict:
    """Set an instrument's terminal boxes correctly for its physical block:

    * 2-wire instruments (Inst_2W*, Inst_Sensor_2W*) have ONLY Term01/Term02 to show their
      two terminals and NO LinePlus/NeutralMinus boxes. Their terminal values therefore
      MUST land in Term01/Term02 -- this is Term 1 / Term 2 (e.g. the field device's two
      terminals, or L/N for a 2-wire POWER instrument). (Previously these were blanked like
      4-term instruments, which -- since a 2-wire block has no LinePlus/NeutralMinus -- left
      the terminals showing nothing at all.)

    * 4-term (and any other) instruments: the numeric Term boxes generate BLANK and the
      powered boxes show L / N via LinePlus / NeutralMinus (a POWER instrument mirrors the
      Term 1 / Term 2 values, e.g. "L" / "N"; any other type shows plain L / N).

    A term value equal to _HIDE_TERM_SENTINEL is left in place here; _maybe_hide_terms
    (run after the group builders) blanks it, so the "Hide from Generation" toggle still
    works and the slot position is preserved."""
    blk = loop.get("src_block" if side == "src" else "dst_block") or ""
    blk_l = str(blk).lower()
    if "inst" not in blk_l:
        return attrs
    tkey = "Wire{n}_" + ("SrcTermNum" if side == "src" else "DstTermNum")

    if "2w" in blk_l:
        # 2-wire instrument: put the two terminal values in Term01/Term02 (Term1/Term2);
        # blank the unused Term03..Term08. Do NOT touch LinePlus/NeutralMinus (this block
        # has none). None -> "" so we never write the literal "None"; a hide-sentinel is
        # preserved for _maybe_hide_terms to blank.
        t1 = loop.get(tkey.format(n=1))
        t2 = loop.get(tkey.format(n=2))
        t1 = "" if t1 is None else t1
        t2 = "" if t2 is None else t2
        attrs["Term01"] = t1
        attrs["Term1"] = t1
        attrs["Term02"] = t2
        attrs["Term2"] = t2
        for n in range(3, 9):
            attrs[f"Term{n}"] = ""
            attrs[f"Term{n:02d}"] = ""
        return attrs

    # 4-term (and any other) instrument. Whether the numeric terminal boxes show depends on
    # WHAT lands on this drawing:
    #   * POWER drawing  -> the signal (TSP) terminals are NOT wired here, so blank every
    #     numeric box (no "XX" placeholder / stray number); the feed shows on L / N.
    #   * TSP/signal drawing -> the signal wires DO land on the numeric terminals, so
    #     populate them with their terminal numbers (blank only the unused boxes).
    # This is the asymmetry the user wants: a terminal shows its number when a TSP wire
    # connects to it, and is blanked only when power is being shown going to the block. A
    # hide sentinel is left in place for _maybe_hide_terms to blank afterwards.
    is_power = str(loop.get("Wire_Type") or "").strip().upper() == "POWER"
    for n in range(1, 9):
        v = "" if (is_power or n > 4) else loop.get(tkey.format(n=n))
        v = "" if v is None else v          # never write the literal "None"
        attrs[f"Term{n}"] = v
        attrs[f"Term{n:02d}"] = v
    # Powered L / N boxes: always overwrite the block's "L/+" / "N/-" default. Only a POWER
    # drawing carries the feed, so its Wire1/Wire2 terminal values (e.g. "L" / "N") show;
    # a TSP drawing leaves the default L / N labels.
    t1 = t2 = None
    if is_power:
        t1 = loop.get(tkey.format(n=1))
        t2 = loop.get(tkey.format(n=2))
    attrs["LinePlus"] = t1 if (t1 not in (None, "") and t1 != _HIDE_TERM_SENTINEL) else "L"
    attrs["NeutralMinus"] = t2 if (t2 not in (None, "") and t2 != _HIDE_TERM_SENTINEL) else "N"
    return attrs


def _build_src_attrs(loop: dict) -> dict:
    st = _collapse_tags([
        loop.get("Wire1_SrcTag"), loop.get("Wire2_SrcTag"),
        loop.get("Wire3_SrcTag"), loop.get("Wire4_SrcTag"),
    ])
    _r = loop.get("Src_Rating")
    attrs = {
        "Type": _spare_type_attr(loop.get("Src_SpareType")),  # spare 'Type' attr (Spare_L), parenthesised
        "Quantity": _spare_qty_attr(loop.get("Src_SpareQty")),   # 'X#' on Spare_L
        # device rating -> whichever rating attr the block carries
        "Rating": _r, "FU_Rating": _r, "DISC_Rating": _r,
        "CB_Rating": _r, "Rating_FU": _r,
        "ISATag_FunctIdent":   loop.get("ISATag_FunctIdent"),
        "ISATag_ElementIdent": loop.get("ISATag_ElementIdent"),
        "ISATag_ElementNum":   loop.get("ISATag_ElementNum"),
        "ISATag_LoopNum":      loop.get("ISATag_LoopNum"),
        "Term01": loop.get("Wire1_SrcTermNum"),
        "Term02": loop.get("Wire2_SrcTermNum"),
        "Term03": loop.get("Wire3_SrcTermNum"),
        "Term04": loop.get("Wire4_SrcTermNum"),
        "Desc01": loop.get("Src_Desc1"),
        "Desc02": loop.get("Src_Desc2"),
        "Desc03": loop.get("Src_Desc3"),
        "SOURCE":      loop.get("Loop_SrcDesc"),
        "DESTINATION": loop.get("Loop_DstDesc"),
        "Tag1": st[0],
        "Tag2": st[1],
        "Tag3": st[2],
        "Tag4": st[3],
        "Term1": loop.get("Wire1_SrcTermNum"),
        "Term2": loop.get("Wire2_SrcTermNum"),
        "Term3": loop.get("Wire3_SrcTermNum"),
        "Term4": loop.get("Wire4_SrcTermNum"),
        "Desc1": loop.get("Src_Desc1"),
        "Desc2": loop.get("Src_Desc2"),
        "Desc3": loop.get("Src_Desc3"),
    }
    return _renumber_inst_terms(attrs, loop, "src")


def _build_dst_attrs(loop: dict) -> dict:
    dt = _collapse_tags([
        loop.get("Wire1_DstTag"), loop.get("Wire2_DstTag"),
        loop.get("Wire3_DstTag"), loop.get("Wire4_DstTag"),
    ])
    _r = loop.get("Dst_Rating")
    attrs = {
        "Type": _spare_type_attr(loop.get("Dst_SpareType")),  # spare 'Type' attr (Spare_R), parenthesised
        "Quantity": _spare_qty_attr(loop.get("Dst_SpareQty")),   # 'X#' on Spare_R
        # device rating -> whichever rating attr the block carries
        "Rating": _r, "FU_Rating": _r, "DISC_Rating": _r,
        "CB_Rating": _r, "Rating_FU": _r,
        "ISATag_FunctIdent":   loop.get("ISATag_FunctIdent"),
        "ISATag_ElementIdent": loop.get("ISATag_ElementIdent"),
        "ISATag_ElementNum":   loop.get("ISATag_ElementNum"),
        "ISATag_LoopNum":      loop.get("ISATag_LoopNum"),
        "Term01": loop.get("Wire1_DstTermNum"),
        "Term02": loop.get("Wire2_DstTermNum"),
        "Term03": loop.get("Wire3_DstTermNum"),
        "Term04": loop.get("Wire4_DstTermNum"),
        "Desc01": loop.get("Dst_Desc1"),
        "Desc02": loop.get("Dst_Desc2"),
        "Desc03": loop.get("Dst_Desc3"),
        "SOURCE":      loop.get("Loop_SrcDesc"),
        "DESTINATION": loop.get("Loop_DstDesc"),
        "Tag1": dt[0],
        "Tag2": dt[1],
        "Tag3": dt[2],
        "Tag4": dt[3],
        "Term1": loop.get("Wire1_DstTermNum"),
        "Term2": loop.get("Wire2_DstTermNum"),
        "Term3": loop.get("Wire3_DstTermNum"),
        "Term4": loop.get("Wire4_DstTermNum"),
        "Desc1": loop.get("Dst_Desc1"),
        "Desc2": loop.get("Dst_Desc2"),
        "Desc3": loop.get("Dst_Desc3"),
    }
    return _renumber_inst_terms(attrs, loop, "dst")


def _group_terms(group: list, side: str) -> list:
    """Collect a group's terminal numbers (2 per instrument row) in order."""
    key = "Wire{n}_" + ("SrcTermNum" if side == "src" else "DstTermNum")
    terms = []
    for g in group:
        for n in range(1, 5):
            v = g.get(key.format(n=n))
            if v not in (None, ""):
                terms.append(v)
    return terms


def _fill_group_inst_terms(attrs: dict, group: list, side: str) -> dict:
    """Populate a multi-term instrument's signal-terminal boxes from the WHOLE group.

    A 4-term (or larger) instrument's terminals continue across the group's continuation
    rows -- in the workbook, a 4-term instrument stamps an extra row under the anchor whose
    terminals continue the numbering (row0 -> Term 1/2, row1 -> Term 3/4). _build_*_attrs
    only sees the anchor row (group[0]), so on a TSP/signal drawing it would show only the
    first two terminals; gather the whole group's terminal numbers here so all of them show.

    Only applies to a non-2W instrument on a TSP/signal drawing: a POWER drawing keeps the
    numeric boxes blank (the feed shows on L/N -- handled in _renumber_inst_terms), and a
    2-wire instrument has exactly Term01/Term02 (already set). A hide sentinel is left in
    place for _maybe_hide_terms."""
    loop0 = group[0]
    blk = str(loop0.get("src_block" if side == "src" else "dst_block") or "").lower()
    if "inst" not in blk or "2w" in blk:
        return attrs
    if str(loop0.get("Wire_Type") or "").strip().upper() == "POWER":
        return attrs
    terms = _group_terms(group, side)
    for i in range(8):
        v = terms[i] if i < len(terms) else ""
        attrs[f"Term{i + 1}"] = v
        attrs[f"Term{i + 1:02d}"] = v
    return attrs


def _build_src_attrs_group(group: list) -> dict:
    """Source-side attrs for a single instrument spanning the whole group. Terminal-box
    handling (blank for 4-term POWER, Term01/02 filled for 2-wire) is done by
    _renumber_inst_terms inside _build_src_attrs; _fill_group_inst_terms then continues the
    TSP terminals across the group's continuation rows so a 4-term signal instrument shows
    all its terminals, not just the anchor row's two."""
    return _fill_group_inst_terms(_build_src_attrs(group[0]), group, "src")


def _build_dst_attrs_group(group: list) -> dict:
    """Destination-side attrs for a single instrument spanning the whole group. Terminal-box
    handling is done by _renumber_inst_terms inside _build_dst_attrs; _fill_group_inst_terms
    then continues the TSP terminals across the group's continuation rows."""
    return _fill_group_inst_terms(_build_dst_attrs(group[0]), group, "dst")


def _build_side_group_tb(group: list, side: str) -> dict:
    """Attrs for a 4-position TB strip inserted once for the whole group: Tag1-4
    and Term1-4 are merged from each row's two terminals (row1 -> 1&2, row2 ->
    3&4).  If every tag is identical, collapse to a single Tag1."""
    key = "Dst" if side == "dst" else "Src"
    attrs = _build_dst_attrs(group[0]) if side == "dst" else _build_src_attrs(group[0])
    tags, terms = [], []
    for g in group:
        for n in (1, 2):
            tags.append(g.get(f"Wire{n}_{key}Tag"))
            terms.append(g.get(f"Wire{n}_{key}TermNum"))
    tags  = (tags  + [None, None, None, None])[:4]
    terms = (terms + [None, None, None, None])[:4]
    nonempty = [str(t).strip() for t in tags if t not in (None, "")]
    if nonempty and len(set(nonempty)) == 1:
        tags = [nonempty[0], "", "", ""]
    tags = [t if t not in (None, "") else "" for t in tags]   # blank unused slots
    for i in range(4):
        attrs[f"Tag{i + 1}"]  = tags[i]
        attrs[f"Term{i + 1}"] = terms[i]
        attrs[f"Term{i + 1:02d}"] = terms[i]
    return attrs


# ── Reference documents table ─────────────────────────────────────────────────

# Columns B/C/D in the SUPPORTING DOCUMENTS table (0-indexed: 2, 3, 4).
# The table has two thin unlabeled columns at indices 0 and 1 before DRAWING NUMBER.
_REF_COL_DWG  = 2
_REF_COL_DESC = 3
_REF_COL_MFR  = 4
_DEV_COL_NUM  = 0   # Deviations & Notes: incrementing number column (A)
_DEV_COL_TEXT = 1   # Deviations & Notes: note text column (B)
# Ref-doc data rows start after two header rows (row 0 = merged title, row 1 = column labels).
_REF_DATA_START_ROW = 2
# Deviation data rows start after only ONE header row: the deviations section has just the
# "DEVIATIONS & NOTES:" title at row 0 (cols 0/1 at row 1 are blank = first data slot).
_DEV_DATA_START_ROW = 1


def _fill_ref_docs_table(model, ref_doc_rows: list, dev_rows: list, warnings: list):
    """
    Find the SUPPORTING DOCUMENTS AcDbTable and populate it with the supplied
    pre-resolved ref doc rows and deviation notes.

    Ref docs   → DWG# col C (2), Description col D (3), Manufacturer col E (4).
    Deviations → number col A (0), note text col B (1).

    Ref-doc rows are resolved by generate.py from ConduitIndex col L (Ref_DocNames);
    deviation rows are (number, text) pairs resolved from ConduitIndex col K
    (Dev_Nums) against the Ref Documents #→text lookup — actual numbers, no renumber.
    """
    # Locate the supporting documents table by scanning for recognisable header text.
    # Every COM read in this scan is _com_retry(any_error)-wrapped: on a busy Generate All a
    # transient 'rejected by callee' on model.Count / model.Item / obj.ObjectName / obj.Rows /
    # obj.Columns / obj.GetText used to falsely report "table not found" (or skip the real
    # table object), so the sheet saved with an EMPTY supporting-documents / deviations table
    # while still reporting ✓. Retrying the reads self-heals the hiccup; a genuine failure
    # still exhausts the retries and is handled exactly as before (skip object / warn+return).
    table = None
    try:
        for i in range(int(_com_retry(lambda: model.Count, any_error=True))):
            try:
                obj = _com_retry(lambda i=i: model.Item(i), any_error=True)
                if str(_com_retry(lambda obj=obj: obj.ObjectName, any_error=True)) != "AcDbTable":
                    continue
                trows = int(_com_retry(lambda obj=obj: obj.Rows, any_error=True))
                tcols = int(_com_retry(lambda obj=obj: obj.Columns, any_error=True))
                found = False
                for r in range(min(3, trows)):
                    for c in range(tcols):
                        try:
                            txt = str(_com_retry(lambda r=r, c=c, obj=obj: obj.GetText(r, c), any_error=True)).upper()
                            if "DRAWING" in txt or "SUPPORTING" in txt or "DESCRIPTION" in txt:
                                found = True
                                break
                        except Exception:
                            pass
                    if found:
                        break
                if found:
                    table = obj
                    break
            except Exception:
                pass
    except Exception as ex:
        warnings.append(f"Ref docs: could not search model space for table: {ex}")
        return

    if table is None:
        warnings.append("Ref docs: SUPPORTING DOCUMENTS table not found in DWG")
        _log("_fill_ref_docs_table: table not found")
        return

    table_rows = int(_com_retry(lambda: table.Rows, any_error=True))
    table_cols = int(_com_retry(lambda: table.Columns, any_error=True))
    _log(f"_fill_ref_docs_table: table found  rows={table_rows} cols={table_cols}  filling {len(ref_doc_rows)} row(s)")

    written = 0
    for i, rd in enumerate(ref_doc_rows):
        row_idx = _REF_DATA_START_ROW + i
        if row_idx >= table_rows:
            warnings.append(
                f"Ref docs: table full after {written} row(s) "
                f"({table_rows - _REF_DATA_START_ROW} slots available)"
            )
            break
        try:
            if rd.get("dwg_num") is not None and _REF_COL_DWG < table_cols:
                _com_retry(lambda: table.SetText(row_idx, _REF_COL_DWG, str(rd["dwg_num"])), any_error=True)
            if rd.get("description") is not None and _REF_COL_DESC < table_cols:
                _com_retry(lambda: table.SetText(row_idx, _REF_COL_DESC, str(rd["description"])), any_error=True)
            if rd.get("manufacturer") is not None and _REF_COL_MFR < table_cols:
                _com_retry(lambda: table.SetText(row_idx, _REF_COL_MFR, str(rd["manufacturer"])), any_error=True)
            written += 1
        except Exception as ex:
            warnings.append(f"Ref docs: could not write supporting-document row {i + 1} to the "
                            f"table (AutoCAD kept rejecting the write). Detail: {ex}")

    _log(f"_fill_ref_docs_table: wrote {written} ref doc row(s)")

    # ── Deviations & Notes: same title-block table, col A (0) = number,
    # col B (1) = note text. The numbers are the ACTUAL deviation numbers the
    # conduit selected in ConduitIndex col K (Dev_Nums) — no renumbering, kept
    # in selection order. dev_rows is a list of (number, text) pairs.
    dwritten = 0
    for i, pair in enumerate(dev_rows):
        try:
            num, note = pair
        except Exception:
            continue
        row_idx = _DEV_DATA_START_ROW + i
        if row_idx >= table_rows:
            warnings.append(f"Deviations: table full after {dwritten} note(s)")
            break
        try:
            # Number column is a sequential 1..N based on top-down order — NOT the
            # catalog deviation number the conduit selected. The selection only decides
            # which notes appear (and their order); the drawing renumbers them 1,2,3,…
            if _DEV_COL_NUM < table_cols:
                _com_retry(lambda: table.SetText(row_idx, _DEV_COL_NUM, str(i + 1)), any_error=True)
            if _DEV_COL_TEXT < table_cols and note is not None:
                _com_retry(lambda: table.SetText(row_idx, _DEV_COL_TEXT, str(note)), any_error=True)
            dwritten += 1
        except Exception as ex:
            warnings.append(f"Deviations: could not write note {i + 1} to the table "
                            f"(AutoCAD kept rejecting the write). Detail: {ex}")
    _log(f"_fill_ref_docs_table: wrote {dwritten} deviation note(s)")
