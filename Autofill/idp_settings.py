"""
idp_settings.py — small persisted settings shared by both front-ends (web + classic).

Holds the DICTATED output folder for filled workbooks, so results always land in one
designated place instead of being scattered into each project folder. The folder is
remembered whenever the user picks a Save folder; until then it defaults to a dedicated
'IDP Filled Workbooks' folder on the Desktop.

Also remembers a DESIGNATED template workbook path (independent of any project folder,
changeable at any time) and a short list of recently-used project folders, so the UI can
default to what the user actually works with instead of starting cold every launch.
"""
import os
import json

_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                    or os.path.expanduser("~"), "AIC_IDP_Extractor")
_PATH = os.path.join(_DIR, "settings.json")
_DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Desktop", "IDP Filled Workbooks")
_RECENT_MAX = 12


def load():
    try:
        with open(_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save(data):
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except Exception:
        pass


def get_output_dir():
    """The dictated output folder (persisted, else the default), created if missing."""
    d = (load().get("output_dir") or "").strip() or _DEFAULT_OUT
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return d


def set_output_dir(path):
    """Remember the folder the user chose as the dictated output folder."""
    if not path:
        return
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if d:
        s = load()
        s["output_dir"] = d
        save(s)


def _bundled_template():
    """A template that SHIPS inside the app (Workbook/IDP_Workbook_CurrentWIP_*.xlsm), so a
    fresh machine can run a scan before the user has picked one. '' if none is bundled."""
    import glob
    root = _fused_root()
    for pat in ("Workbook/IDP_Workbook_CurrentWIP_*.xlsm", "Workbook/*.xlsm"):
        hits = sorted(glob.glob(os.path.join(root, pat.replace("/", os.sep))))
        if hits:
            return hits[-1]     # newest by name (…_4 beats …_3)
    return ""


def get_template_path():
    """The DESIGNATED template workbook (persisted). Falls back to the template that SHIPS with
    the app, so a transferred machine can scan immediately; '' only if neither exists."""
    p = (load().get("template_path") or "").strip()
    if p and os.path.isfile(p):
        return p
    return _bundled_template()


def set_template_path(path):
    """Remember the template workbook the user chose as the designated default."""
    if not path or not os.path.isfile(path):
        return
    s = load()
    s["template_path"] = os.path.abspath(path)
    save(s)


def get_block_library_dir():
    """The designated BLOCK-LIBRARY folder (one .dwg per symbol block, filename = block
    name) the extractor reads to know 'what the blocks look like' — so it infers symbols
    from the library instead of scanning project AutoCAD drawings. '' if not set (then it's
    auto-located near the app)."""
    return (load().get("block_library_dir") or "").strip()


def set_block_library_dir(path):
    """Remember the block-library folder (changeable if it ever moves)."""
    if not path:
        return
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if d:
        s = load()
        s["block_library_dir"] = d
        save(s)


def get_api_key():
    """The Anthropic API key the user entered so the exe can learn through Claude
    (Training tab). Environment variable wins; else the persisted key; else ''."""
    return (os.environ.get("ANTHROPIC_API_KEY") or (load().get("api_key") or "").strip())


def set_api_key(key):
    """Remember the user's Anthropic API key (stored locally in settings.json).
    Pass '' to clear it."""
    s = load()
    key = (key or "").strip()
    if key:
        s["api_key"] = key
    else:
        s.pop("api_key", None)
    save(s)


def has_api_key():
    return bool(get_api_key())


def get_recent_projects():
    """Recently-used project folders, most-recent first, pruned to those that still exist."""
    out = []
    for p in (load().get("recent_projects") or []):
        try:
            if p and os.path.isdir(p) and p not in out:
                out.append(p)
        except Exception:
            pass
    return out[:_RECENT_MAX]


def add_recent_project(path):
    """Push a project folder onto the recent list (dedup, most-recent first, capped)."""
    if not path:
        return
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if not d or not os.path.isdir(d):
        return
    d = os.path.abspath(d)
    s = load()
    lst = [x for x in (s.get("recent_projects") or []) if x and os.path.abspath(x) != d]
    lst.insert(0, d)
    s["recent_projects"] = lst[:_RECENT_MAX]
    save(s)


def _fused_root():
    """The LISA fused folder root (this file lives in <root>/Autofill/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_version_dir():
    """Default version-control folder, resolved in this order:
      1) a shipped 'version_source.txt' in the app root — its first non-comment line is the
         SHARED/SERVER 'Version Control' path (a UNC like \\\\SERVER\\share\\...\\Version Control).
         Because this ships INSIDE the folder, a COPIED install automatically points back at
         the server and its Update button pulls from there with NO per-user setup. Set the
         server path once by editing that one file.
      2) else a 'Version Control' folder NEXT TO the LISA fused folder (local fallback).
    An in-app user setting (version_pull_dir) still overrides both."""
    try:
        p = os.path.join(_fused_root(), "version_source.txt")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip().strip('"')
                    if s and not s.startswith("#"):
                        return s
    except Exception:
        pass
    # carried 'Version Control' folder INSIDE the app (ships with it) — so a self-contained
    # copy still works standalone even before the server path is set in version_source.txt.
    carried = os.path.join(_fused_root(), "Version Control")
    if os.path.isdir(carried):
        return carried
    return os.path.join(os.path.dirname(_fused_root()), "Version Control")


def set_version_source_path(path):
    """Write the SHARED version-control path into 'version_source.txt' (which ships INSIDE the
    folder), so setting it once in the app travels to EVERY copied machine — no per-user setup.
    Preserves the file's comment header and replaces its single active (first non-comment) line.
    Empty path clears the active line (falls back to the carried Version Control)."""
    p = (path or "").strip().strip('"')
    vs = os.path.join(_fused_root(), "version_source.txt")
    header = []
    try:
        if os.path.isfile(vs):
            with open(vs, "r", encoding="utf-8") as fh:
                for line in fh:
                    if (not line.strip()) or line.strip().startswith("#"):
                        header.append(line.rstrip("\n"))
                    else:
                        break                    # stop at the active line — we replace it
    except Exception:
        pass
    if not header:
        header = ["# LISA Autofill version-update source — the line below is the SHARED",
                  "# Version Control path every copy pulls updates from / publishes to.",
                  "# Use a full UNC path (\\\\server\\share\\...), not a mapped drive letter.",
                  "# >>> set in-app (Scan page update-folder field) or edit this line:"]
    lines = header + ([p] if p else [])
    try:
        with open(vs, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return True
    except Exception:
        return False


def get_version_pull_dir():
    """Shared folder the Update button PULLS published versions from (persisted, else the
    default next to LISA fused)."""
    return (load().get("version_pull_dir") or "").strip() or _default_version_dir()


def set_version_pull_dir(path):
    if not path:
        return
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if d:
        s = load()
        s["version_pull_dir"] = d
        save(s)


def get_version_push_dir():
    """Folder Training PUBLISHES new versions to. Defaults to the pull folder (same place),
    but can be set independently in the Training tab."""
    return (load().get("version_push_dir") or "").strip() or get_version_pull_dir()


def set_version_push_dir(path):
    if not path:
        return
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if d:
        s = load()
        s["version_push_dir"] = d
        save(s)


def _inside_any(d, sources):
    """True if directory `d` is inside — or contains — any source file's folder
    (i.e. it lives in a project/source tree)."""
    if not d:
        return True
    try:
        d = os.path.abspath(d)
    except Exception:
        return True
    for s in sources or []:
        try:
            sd = os.path.abspath(s if os.path.isdir(s) else os.path.dirname(s))
            cp = os.path.commonpath([d, sd])
            if cp == d or cp == sd:
                return True
        except Exception:
            pass
    return False


def _safe_name(s):
    """Filesystem-safe folder/file name."""
    import re
    return re.sub(r'[<>:"/\\|?*]+', " ", str(s or "")).strip(" .") or "IDP"


def resolve_output_path(site, requested, sources):
    """The FINAL filled-workbook path:  <base>/<Project>/<Site>_FILLED.xlsm — i.e. each
    project's workbook lands in its OWN subfolder named after the project. `base` is the
    dictated output folder, UNLESS the user explicitly chose a real, absolute folder
    OUTSIDE every source/project tree. Never the project's own source folder."""
    site = _safe_name(site or "IDP")
    fname = f"{site}_FILLED.xlsm"
    req = (requested or "").strip()
    cand = ""
    if req:
        cand = req if os.path.isdir(req) else os.path.dirname(req)
    if (not cand or not os.path.isabs(cand) or not os.path.isdir(cand)
            or _inside_any(cand, sources)):
        base = get_output_dir()
    else:
        base = cand
    # per-PROJECT subfolder named after the project (skip if base already is it)
    proj_dir = base if os.path.basename(os.path.normpath(base)).lower() == site.lower() \
        else os.path.join(base, site)
    try:
        os.makedirs(proj_dir, exist_ok=True)
    except Exception:
        proj_dir = base
    return os.path.join(proj_dir, fname)
