"""
idp_project_symbols.py — infer FillIndex symbols from THIS project's own AutoCAD
drawings, not just the generic block library.

Different projects favor different blocks for the same device (e.g. one job's
"terminal block" landings are all CB-TB_Square, another's are plain TB_Square).
Scanning the project's own DWGs (finished IDPs, wiring diagrams, whatever's on
disk) tells us which blocks THIS project actually uses, so symbol inference can
prefer a project-confirmed block over the generic library guess.

find_project_dwgs(root)      -> list of .dwg paths under a project folder
load_or_scan(root, dwgs)      -> block list, via a cached _dwg_scan.json if
                                 present, else a live ObjectDBX scan (requires
                                 AutoCAD already running; degrades to [] if not)
build_block_library(scan)     -> {base_name_no_side: {"L": n, "R": n}} usage
                                 counts, keyed on the block name with any
                                 trailing _L/_R stripped
apply_project_symbols(records, library) -> re-scores S/D Symbol against blocks
                                 actually seen in this project; upgrades
                                 confidence + notes when a project-confirmed
                                 block replaces a generic guess
"""
from __future__ import annotations

import json
import os
import re

try:
    import idp_dwg_scan
except Exception:
    idp_dwg_scan = None

SIDE_RE = re.compile(r"_(L|R)$", re.IGNORECASE)
_SCAN_CACHE_NAME = "_dwg_scan.json"

# ── SYMBOL LIBRARY from the BLOCK-LIBRARY FOLDER ─────────────────────────────
# The symbol blocks don't change, so instead of scanning project AutoCAD drawings every run
# we read the authoritative BLOCK LIBRARY folder (the "dummy tool library" in the Claude
# Files directory) — one .dwg per block, the FILENAME is the block name (e.g. CB-CB_L,
# DISC-DISC-DISC-TB_Round_R). Listing the folder gives us "what the blocks look like"; we
# infer symbols from that. No AutoCAD reading, no per-run scan — just the folder listing.
# "Symbol Library for IDPS" is FIRST so a bundled copy sitting next to the app wins over any
# external "dummy tool library" — this is what makes the library travel with a transferred
# install (no external folder or per-machine setting required).
_BLOCK_LIB_FOLDER_NAMES = ("Symbol Library for IDPS", "dummy tool library", "tool library",
                           "block library", "symbol library")


def _block_library_dir():
    """Locate the block-library folder: an explicit setting first, else search up from this
    module AND the running exe for a known-named folder that contains *_L.dwg / *_R.dwg."""
    try:
        import idp_settings
        d = (idp_settings.get_block_library_dir() or "").strip()
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    import sys
    seeds = [os.path.dirname(os.path.abspath(__file__))]
    try:
        seeds.append(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass
    roots = []
    for s in seeds:
        d = s
        for _ in range(6):                      # walk up toward the Claude Files dir
            if d not in roots:
                roots.append(d)
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    for r in roots:
        for name in _BLOCK_LIB_FOLDER_NAMES:
            cand = os.path.join(r, name)
            if os.path.isdir(cand):
                return cand
    return None


_BLOCK_LIB = None


def load_symbol_library(refresh=False):
    """The block library READ FROM the block-library folder — {base_name: {'L':1,'R':1}} for
    every block .dwg present — so symbol matching infers 'what the blocks look like' with NO
    AutoCAD scan. Backups (…_bak_… / names not ending in _L/_R) are ignored. Cached; pass
    refresh=True to re-read after the folder changes. Empty only if the folder isn't found."""
    global _BLOCK_LIB
    if _BLOCK_LIB is not None and not refresh:
        return _BLOCK_LIB
    lib = {}
    d = _block_library_dir()
    if d:
        try:
            for fn in os.listdir(d):
                if not fn.lower().endswith(".dwg") or "_bak_" in fn.lower():
                    continue
                stem = fn[:-4]
                m = SIDE_RE.search(stem)
                base = _strip_side(stem)
                if m and base:
                    lib.setdefault(base, {"L": 0, "R": 0})[m.group(1).upper()] = 1
        except Exception:
            lib = {}
    _BLOCK_LIB = lib
    return lib


def block_library_source():
    """The folder the block library was read from (for logging), or '' if not found."""
    return _block_library_dir() or ""


def symbol_count(refresh=False):
    """Number of actual SYMBOLS in the library — each side (`_L`/`_R`) counts as one block
    (so `CB-TB_Square` contributes 2), NOT the number of distinct bases. Timestamped `_bak_`
    duplicates are already excluded by load_symbol_library."""
    lib = load_symbol_library(refresh=refresh)
    return sum((1 if v.get("L") else 0) + (1 if v.get("R") else 0) for v in lib.values())


def has_memory():
    return bool(load_symbol_library())


def _strip_side(name):
    return SIDE_RE.sub("", str(name or ""))


def find_project_dwgs(root, limit=200):
    """Walk `root` for .dwg files (bounded, so a huge project tree can't hang)."""
    if not root or not os.path.isdir(root):
        return []
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".dwg"):
                out.append(os.path.join(dirpath, f))
                if len(out) >= limit:
                    return out
    return out


def _cache_path(root):
    return os.path.join(root, _SCAN_CACHE_NAME)


def _find_cached_scans(root):
    """Any _dwg_scan.json already on disk under root (recursive) — e.g. from a
    prior scan of a subfolder. Reusing these avoids a slow live re-scan."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        if _SCAN_CACHE_NAME in files:
            found.append(os.path.join(dirpath, _SCAN_CACHE_NAME))
    return found


def load_or_scan(root, dwgs=None, rescan=False):
    """Return {filename: [block dicts]} for a project's DWGs. Prefers a cached
    scan (at `root` or anywhere in its subtree); runs a live ObjectDBX scan (via
    idp_dwg_scan.py, a separate process so a COM hiccup can't take down the
    caller) only if AutoCAD is reachable. Returns {} (never raises) if nothing
    is available — callers must degrade."""
    if not root:
        return {}
    cache = _cache_path(root)
    if not rescan and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    if not rescan:
        merged = {}
        for cp in _find_cached_scans(root):
            try:
                with open(cp, "r", encoding="utf-8") as fh:
                    merged.update(json.load(fh))
            except Exception:
                continue
        if merged:
            return merged
    dwgs = dwgs if dwgs is not None else find_project_dwgs(root)
    if not dwgs or idp_dwg_scan is None:
        return {}
    try:
        # Run IN-PROCESS (not via subprocess/sys.executable) — inside a frozen
        # exe, sys.executable is the exe itself and the loose idp_dwg_scan.py
        # source wouldn't exist to hand to a subprocess. Pass the explicit
        # (recursively-gathered) file list — idp_dwg_scan's own folder mode
        # does NOT recurse, so a bare root would silently scan nothing whenever
        # the .dwg files live in subfolders (as they do here).
        idp_dwg_scan.main(["--out", cache] + dwgs)
    except Exception:
        return {}
    try:
        with open(cache, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _template_positions(scan, threshold=0.8):
    """AIC's IDP drawing template stamps one copy of (nearly) every library block
    at a FIXED (x, y) on every sheet — a reference palette, not real wiring.
    Real per-conduit placements vary position sheet to sheet. Detect the
    template grid as any position recurring on >= `threshold` of all sheets,
    regardless of which block occupies it, and treat everything else as real
    usage. (Confirmed on Stratford: 45 sheets, 5889 raw block instances, 124
    template positions -> 697 real placements after filtering.)"""
    scan = scan or {}
    n = len(scan)
    if n < 2:
        return set()
    from collections import defaultdict
    pos_sheets = defaultdict(set)
    for sheet, blocks in scan.items():
        seen = set()
        for b in blocks:
            xy = (round(b.get("x", 0), 1), round(b.get("y", 0), 1))
            if xy not in seen:
                pos_sheets[xy].add(sheet)
                seen.add(xy)
    return {xy for xy, sheets in pos_sheets.items() if len(sheets) >= threshold * n}


def build_block_library(scan):
    """{scan filename -> [blocks]} -> {base_name: {"L": count, "R": count}} of
    REAL per-conduit placements only (template/legend copies excluded)."""
    template_xy = _template_positions(scan)
    lib = {}
    for blocks in (scan or {}).values():
        for b in blocks:
            xy = (round(b.get("x", 0), 1), round(b.get("y", 0), 1))
            if xy in template_xy:
                continue   # legend/reference placement, not real usage
            name = b.get("name", "")
            m = SIDE_RE.search(name)
            side = m.group(1).upper() if m else None
            base = _strip_side(name)
            if not base or side not in ("L", "R"):
                continue
            lib.setdefault(base, {"L": 0, "R": 0})[side] += 1
    return lib


_LEGAL_SYMS = None


def _legal_symbols(refresh=False):
    """Every symbol legal in the workbook's S/D Symbol dropdowns — the CURRENT template's
    dropdown universe (lisa_contract's frozen contract, mirrored by the workbook PickList).
    A symbol assigned by the pipeline is snapped to a value FROM this set at write time
    (see idp_write), so we always PICK a real dropdown option and never write an off-list
    value; anything not here falls back to the closest valid pick."""
    global _LEGAL_SYMS
    if _LEGAL_SYMS is None or refresh:
        s = set()
        try:
            import lisa_contract
            for ctd in lisa_contract.CONTRACT.get("symbols_by_key_ct_side", {}).values():
                for sides in ctd.values():
                    s.update(sides.get("L", []))
                    s.update(sides.get("R", []))
        except Exception:
            pass
        _LEGAL_SYMS = s
    return _LEGAL_SYMS


def project_symbol(token, side, library):
    """Return the project-confirmed block name for a device token + side, if
    THIS project's drawings actually place a block matching that token AND that
    exact name is legal in the current template (see _legal_symbols)."""
    if not library or not token:
        return None
    legal = _legal_symbols()
    tnorm = re.sub(r"[^A-Z0-9]", "", str(token).upper())
    best, best_n = None, 0
    for base, counts in library.items():
        if counts.get(side, 0) <= 0:
            continue
        cand = f"{base}_{side}"
        if legal and cand not in legal:
            continue   # project's own naming has drifted from the current template
        bnorm = re.sub(r"[^A-Z0-9]", "", base.upper())
        if tnorm and tnorm in bnorm and counts[side] > best_n:
            best, best_n = base, counts[side]
    return f"{best}_{side}" if best else None


_TAG_ATTR_PREFIXES = ("TAG", "DESC", "NAME")
_PLACEHOLDER_VALUES = {"XXXX", "XX", "SOURCE", "DEST", "NA", "TBD", "-", "N/A", ""}


_NAV_LABEL_RE = re.compile(r"^(TO|FROM|SEE)\s+(DWG|SHEET|DRAWING)\b", re.I)


def _real_tag_text(attrs):
    """The first non-placeholder tag/description attribute value on a block
    instance — a real device name straight from the finished drawing, not a
    template placeholder like 'XXXX'/'SOURCE'/'DEST', and not a drawing
    cross-reference label like 'TO DWG 73.1128-72E' (a sheet-continuation
    note, not a device — harmless as a keyword since it's too specific to
    ever match elsewhere, but pure clutter in the Remembered Logic table)."""
    for k, v in (attrs or {}).items():
        if not any(k.upper().startswith(p) for p in _TAG_ATTR_PREFIXES):
            continue
        vs = str(v or "").strip()
        if len(vs) < 3 or vs.upper() in _PLACEHOLDER_VALUES:
            continue
        if all(c == "X" for c in vs.upper()):
            continue
        if vs.replace(".", "").isdigit():
            continue
        if _NAV_LABEL_RE.match(vs):
            continue
        return vs
    return None


def extract_learned_rules(scan):
    """Harvest genuine (device text -> symbol token) pairs from a finished IDP
    DWG scan — every REAL (non-template-palette) block instance that carries an
    actual tag/description attribute is a ground-truth training example: this
    exact device name was really landed on this exact block, in a drawing that
    already shipped. Only symbols legal in the CURRENT template are kept (see
    _legal_symbols) so a project on an older naming convention can't leak a
    now-illegal symbol into global logic. Returns a list of Remembered-Logic-
    ready dicts: {type: 'symbol_keyword', match, result, note}."""
    template_xy = _template_positions(scan)
    legal = _legal_symbols()
    seen, rules = set(), []
    for fname, blocks in (scan or {}).items():
        for b in blocks:
            xy = (round(b.get("x", 0), 1), round(b.get("y", 0), 1))
            if xy in template_xy:
                continue   # template/legend palette copy, not real usage
            name = b.get("name", "")
            m = SIDE_RE.search(name)
            side = m.group(1).upper() if m else None
            base = _strip_side(name)
            if not base or side not in ("L", "R"):
                continue
            if legal and f"{base}_{side}" not in legal:
                continue   # stale/older project naming -- don't teach it globally
            text = _real_tag_text(b.get("attrs"))
            if not text:
                continue
            key = text.strip().upper()
            if key in seen:
                continue
            seen.add(key)
            rules.append({
                "type": "symbol_keyword", "match": text, "result": base, "context": "",
                "note": f"learned from a finished IDP drawing ({os.path.basename(fname)}, "
                        f"block {base}_{side})",
            })
    return rules


def apply_project_symbols(records, library, source_label=None):
    """Upgrade S/D Symbol to a project-confirmed block where one exists for the
    already-recognized device token. `source_label` (e.g. the project's CAD
    folder) is recorded as the field's provenance. Returns count upgraded."""
    if not library:
        return 0
    upgraded = 0
    for rec in records or []:
        for g in rec.get("fill", []) or []:
            for side_key, sym_key, conf_key in (("L", "s_symbol", "s_symbol_conf"),
                                                 ("R", "d_symbol", "d_symbol_conf")):
                token = g.get(f"{'s' if side_key == 'L' else 'd'}_symbol_token")
                cur = g.get(sym_key)
                if not cur:
                    continue
                cand = project_symbol(token or _strip_side(cur), side_key, library)
                if cand and cand != cur:
                    g[sym_key] = cand
                    g[conf_key] = max(float(g.get(conf_key) or 0), 0.9)
                    g[f"{sym_key}_note"] = (
                        f"{cur} -> {cand} (confirmed against this project's own DWG blocks)")
                    if source_label:
                        g[f"{sym_key}_src"] = source_label
                    upgraded += 1
    return upgraded


if __name__ == "__main__":
    import sys as _sys
    root = _sys.argv[1] if len(_sys.argv) > 1 else "."
    dwgs = find_project_dwgs(root)
    print(f"{len(dwgs)} DWGs under {root}")
    scan = load_or_scan(root, dwgs)
    lib = build_block_library(scan)
    print(f"{len(lib)} distinct block base-names in project library")
    for base, counts in sorted(lib.items(), key=lambda kv: -(kv[1]["L"] + kv[1]["R"]))[:20]:
        print(f"  {base:<30} L={counts['L']:<4} R={counts['R']}")
