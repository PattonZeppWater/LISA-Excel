"""
idp_project.py — project identity + per-field provenance for the IDP Extractor.

detect_project_name(paths)  -> a "73.1163_Stratford"-style name derived from the
                                source files' folder path / filenames, so output
                                workbooks are named for the project they came from.
tag_source(records, path)   -> stamp every record + fill group with the file it
                                came from (additive; safe to call once per source).
build_provenance(records)   -> a flat list of {conduit, sheet, field, value,
                                source} rows — the exact file each cell's value
                                came from — for the control panel's Sources tab.
"""
from __future__ import annotations

import os
import re

_PROJECT_NUM_RE = re.compile(r"\b(\d{2}\.\d{3,4})\b")
_STRIP_RE = re.compile(r"[^A-Za-z0-9]+")

# generic project-type descriptors — the SITE name is the proper-noun words BEFORE these
_SITE_STOP = {
    "COMPOUND", "WATER", "WASTEWATER", "SYSTEM", "SYSTEMS", "FILTRATION", "ADDITION",
    "ADDITIONS", "STATION", "PLANT", "WWTP", "WTP", "WRF", "PUMP", "PUMPING", "BOOSTER",
    "WELL", "WELLS", "TANK", "RESERVOIR", "IMPROVEMENTS", "IMPROVEMENT", "UPGRADE",
    "UPGRADES", "PROJECT", "FACILITY", "FACILITIES", "TREATMENT", "SEWER", "LIFT",
    "STORAGE", "SITE", "DISTRIBUTION", "REHABILITATION", "REHAB", "EXPANSION",
    "REPLACEMENT", "PHASE", "CONTROL", "ELECTRICAL", "SCADA", "INTAKE", "OUTFALL",
}


def detect_site_name(paths):
    """The SITE name from the project folder — the proper-noun part before the generic
    project-type descriptors — e.g. '56.1059 - Moccasin Compound Water System Filtration
    Addition' -> 'Moccasin'; 'Crows Landing Sewer PS' -> 'Crows Landing'. Falls back to
    the full descriptive name, then to detect_project_name. Used to auto-name the output
    workbook '<Site>_FILLED.xlsm'."""
    paths = [p for p in (paths or []) if p]
    desc = ""
    for p in paths:
        for part in os.path.normpath(p).split(os.sep):
            m = _PROJECT_NUM_RE.search(part)
            if m:
                desc = re.sub(r"^[\s._+-]+", "", part[m.end():]).strip()
                break
        if desc:
            break
    if not desc:
        pn = detect_project_name(paths)
        desc = re.sub(r"^\d{2}\.\d{3,4}[_\s-]*", "", pn or "").replace("_", " ").strip()
    if not desc:
        return ""
    words = [w for w in re.split(r"[\s_]+", desc) if w]
    site = []
    for w in words:
        if re.sub(r"[^A-Za-z]", "", w).upper() in _SITE_STOP:
            break
        site.append(w)
    if not site:
        site = words[:1]
    return " ".join(site).strip() or desc


def detect_project_name(paths):
    """Best-effort project name from source file paths, e.g. '73.1163_Stratford'.
    Looks for a path component matching 'NN.NNNN <Name>' (AIC's job-number
    convention); falls back to the shared parent folder's name; '' if nothing."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return ""
    for p in paths:
        for part in os.path.normpath(p).split(os.sep):
            m = _PROJECT_NUM_RE.search(part)
            if m:
                num = m.group(1)
                rest = _STRIP_RE.sub("_", part[m.end():]).strip("_")
                return f"{num}_{rest}" if rest else num
    try:
        common = os.path.commonpath([os.path.abspath(p) for p in paths])
    except ValueError:
        common = os.path.dirname(os.path.abspath(paths[0]))
    base = os.path.basename(common) or os.path.basename(os.path.dirname(common))
    return _STRIP_RE.sub("_", base).strip("_")


def project_root(paths):
    """The shared ancestor directory of all source paths (used to scope a
    project-wide DWG search)."""
    paths = [os.path.abspath(p) for p in (paths or []) if p]
    if not paths:
        return None
    dirs = [p if os.path.isdir(p) else os.path.dirname(p) for p in paths]
    try:
        return os.path.commonpath(dirs)
    except ValueError:
        return dirs[0]


def tag_source(records, path):
    """Stamp every record + fill group with the source file it came from, and
    seed per-FIELD provenance (`_field_src`) so a value later backfilled from a
    different source (merge, wiring diagram, project DWG scan) can still report
    its own true origin instead of the record's original file."""
    for r in records or []:
        r.setdefault("_src_conduit", path)
        r.setdefault("_field_src", {})
        for key in ("name", "source", "dest", "size", "ctype", "deviations"):
            if r.get(key):
                r["_field_src"].setdefault(key, path)
        for g in r.get("fill", []) or []:
            g.setdefault("_src_fill", path)
            g.setdefault("_field_src", {})
            for key in ("type", "wire_ct", "count", "gauge", "s_symbol", "d_symbol", "colors"):
                if g.get(key):
                    g["_field_src"].setdefault(key, path)
    return records


CONDUIT_FIELDS = [
    ("conduit_name", "name", lambda r: r.get("name")),
    ("source_name_1", "source", lambda r: (r.get("source") or [""])[0]),
    ("destination_name_1", "dest", lambda r: (r.get("dest") or [""])[0]),
    ("conduit_size", "size", lambda r: r.get("size")),
    ("conduit_type", "ctype", lambda r: r.get("ctype")),
]
FILL_FIELDS = [
    ("type", "type", lambda g: g.get("type")),
    ("wire_ct", "wire_ct", lambda g: g.get("wire_ct") or g.get("count")),
    ("wire_gauge", "gauge", lambda g: g.get("gauge")),
    ("s_symbol", "s_symbol", lambda g: g.get("s_symbol")),
    ("d_symbol", "d_symbol", lambda g: g.get("d_symbol")),
    ("colors", "colors", lambda g: g.get("colors")),
    ("s_rating", "s_rating", lambda g: g.get("s_rating")),
    ("d_rating", "d_rating", lambda g: g.get("d_rating")),
    ("s_description", "s_desc", lambda g: "; ".join(filter(None, g.get("s_desc") or []))),
    ("d_description", "d_desc", lambda g: "; ".join(filter(None, g.get("d_desc") or []))),
]
# note-field -> (display label, fixed source | None => look up "<key>_src" on the group)
NOTE_META = {
    "connection_remodel": ("Wire Ct remodel", "derived — lisa_contract connection-count gate"),
    "type_normalized": ("Type normalization", "derived — lisa_contract type gate"),
    "color_note": ("Wire color", "derived — idp_anatomy drawing convention"),
    "gauge_note": ("Wire gauge", "derived — idp_anatomy drawing convention"),
    "s_symbol_note": ("S Symbol upgrade", None),
    "d_symbol_note": ("D Symbol upgrade", None),
    "wiring_note": ("S Tag / S Term", None),
    "s_desc_note": ("S Description", None),
    "d_desc_note": ("D Description", None),
}


def build_provenance(records):
    """Flatten records into one row per cell-level field — the exact file each
    value was read from (or how it was derived). Returns a list of dicts:
    {conduit, sheet, field, value, source}."""
    rows = []
    for r in records or []:
        cname = r.get("name", "")
        csrc = r.get("_src_conduit") or "(inferred / no direct source)"
        fsrc = r.get("_field_src") or {}
        for label, key, getter in CONDUIT_FIELDS:
            val = getter(r)
            if val not in (None, "", []):
                rows.append({"conduit": cname, "sheet": "ConduitIndex", "field": label,
                            "value": str(val), "source": fsrc.get(key, csrc)})
        for i, g in enumerate(r.get("fill", []) or []):
            gsrc = g.get("_src_fill") or csrc
            gfsrc = g.get("_field_src") or {}
            row_label = f"Fill[{i}]"
            for label, key, getter in FILL_FIELDS:
                val = getter(g)
                if val not in (None, "", []):
                    rows.append({"conduit": cname, "sheet": f"FillIndex:{row_label}",
                                "field": label, "value": str(val),
                                "source": gfsrc.get(key, gsrc)})
            for note_key, (label, fixed_src) in NOTE_META.items():
                if g.get(note_key):
                    src = fixed_src or g.get(note_key.replace("_note", "_src")) or gsrc
                    rows.append({"conduit": cname, "sheet": f"FillIndex:{row_label}",
                                "field": label, "value": str(g[note_key]), "source": src})
    return rows


if __name__ == "__main__":
    tests = [
        [r"C:\Projects\73.1163 Stratford\IDP\conduit list.xlsx"],
        [r"C:\Projects\Some Random Folder\a.pdf", r"C:\Projects\Some Random Folder\b.pdf"],
    ]
    for t in tests:
        print(t, "->", detect_project_name(t))
