"""
idp_versioning.py — versioned snapshots of the whole LISA fused folder + a shared-folder
update channel, driven by the Autofill Training tab.

Model
-----
* Training makes a LEAN copy of the entire LISA fused folder into `archive/vN`, nests the
  previous version inside it (`archive/vN/_prev_version` = a copy of v(N-1), which itself
  contains its own `_prev_version` — a russian-doll history), bumps a local `version.json`,
  and (if a shared folder is configured) PUBLISHES `vN` to that shared folder so other
  computers can pull it.
* Other machines press Update; `apply_update` copies the latest published `vN` over their
  local files (source / data / dist only — NEVER `.venv` or `node_modules`, which are large
  and machine-specific), then the app is restarted to load it.
* "Up to date / Out of date" is a VERSION compare: local `version.json` vs the highest `vN`
  in the shared pull folder — so it works across computers.

Lean snapshot = everything EXCEPT `.venv`, `node_modules`, `__pycache__`, `.git`, `.vs`,
the `archive` store itself, and any `Version Control` folder. `Frontend/frontend/dist` IS
kept (tiny + required to run). This keeps each version small (a few MB) so the nesting does
not explode the drive.

Runs from source inside the fused venv. Python source and Flask-served static files are not
exclusively locked on Windows while the app runs, so an update can be copied in while the
exe is open on this and other machines; it takes effect on the next launch.
"""
import os
import json
import shutil

# Top-level (and nested) folder names never copied into a snapshot / never overwritten on
# update: huge + reproducible (.venv/node_modules), transient (caches/vcs), or self (archive).
_EXCLUDE = {".venv", "node_modules", "__pycache__", ".git", ".vs",
            "archive", "Version Control", "dist_staging", "build_cache",
            "build_staging", ".pytest_cache", ".mypy_cache"}
_NEST = "_prev_version"   # folder inside each vN holding the previous version (nested)


def fused_root():
    """The LISA fused folder root (this module lives in <root>/Autofill/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def archive_dir():
    return os.path.join(fused_root(), "archive")


def _version_file():
    return os.path.join(fused_root(), "version.json")


def current_version():
    try:
        with open(_version_file(), encoding="utf-8") as fh:
            return int(json.load(fh).get("version", 0))
    except Exception:
        return 0


def _set_current_version(n):
    try:
        with open(_version_file(), "w", encoding="utf-8") as fh:
            json.dump({"version": int(n)}, fh, indent=1)
    except Exception:
        pass


def _ignore(_src, names):
    """shutil.copytree ignore callback — drop excluded names at every level."""
    return [n for n in names if n in _EXCLUDE]


def _max_version_in(folder):
    """Highest N among `vN` subfolders of `folder` (0 if none / unreachable)."""
    best = 0
    try:
        for n in os.listdir(folder):
            if (n[:1].lower() == "v" and n[1:].isdigit()
                    and os.path.isdir(os.path.join(folder, n))):
                best = max(best, int(n[1:]))
    except Exception:
        return 0
    return best


def snapshot_to(dst):
    """Lean copy of the fused folder → `dst` (excludes .venv/node_modules/archive/etc.)."""
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(fused_root(), dst, ignore=_ignore, symlinks=False)


def create_version(push_dir=None, log=lambda *a: None):
    """Snapshot the fused folder → archive/v(N+1), nest the previous version inside it, bump
    the local version, and publish to `push_dir` if given. Returns {version, archive, published}."""
    arch = archive_dir()
    os.makedirs(arch, exist_ok=True)
    pd = (push_dir or "").strip().strip('"')
    shared_max = _max_version_in(pd) if pd and os.path.isdir(pd) else 0
    n = max(_max_version_in(arch), current_version(), shared_max) + 1
    vdir = os.path.join(arch, "v%d" % n)

    log("Archiving the current LISA fused state -> v%d ..." % n)
    snapshot_to(vdir)

    # nest the previous version inside this one (russian-doll history)
    prev = os.path.join(arch, "v%d" % (n - 1))
    if os.path.isdir(prev):
        try:
            # The nested history OMITS the big travel-once symbol library — it already sits at
            # EACH version's top level (from snapshot_to), so re-embedding it at every history
            # level would balloon the archive quadratically.
            _nest_skip = lambda _s, names: [x for x in names if x == "Symbol Library for IDPS"]
            shutil.copytree(prev, os.path.join(vdir, _NEST), symlinks=False, ignore=_nest_skip)
        except Exception as e:
            log("  (could not nest previous version: %s)" % e)

    _set_current_version(n)

    # Never publish INTO the app folder — the shared copy would then be re-snapshotted on the
    # next training, compounding the nesting into a runaway. Force the user to pick a folder
    # OUTSIDE the LISA fused folder.
    try:
        # normcase both sides — Windows is case-insensitive, so a case difference (drive
        # letter, OneDrive vs onedrive) must NOT defeat this guard.
        _common = os.path.normcase(os.path.commonpath([os.path.abspath(pd), fused_root()])) if pd else ""
        # A CARRIED "Version Control" folder is the one exception: it's in _EXCLUDE, so it is
        # never snapshotted and can't cause runaway nesting — publishing to it (the server's
        # own copy, so copies auto-update) is safe even though it sits inside the app folder.
        _carried_vc = os.path.basename(os.path.normpath(pd)).lower() == "version control" if pd else False
        if pd and _common == os.path.normcase(fused_root()) and not _carried_vc:
            log("  (publish folder is INSIDE the app — skipped to avoid runaway nesting; set "
                "the version folder to a location OUTSIDE the LISA fused folder)")
            pd = ""
    except Exception:
        pass

    published = ""
    if pd:
        try:
            os.makedirs(pd, exist_ok=True)
            dest = os.path.join(pd, "v%d" % n)
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(vdir, dest, symlinks=False)
            try:
                with open(os.path.join(pd, "latest.json"), "w", encoding="utf-8") as fh:
                    json.dump({"version": n}, fh, indent=1)
            except Exception:
                pass
            published = dest
            log("Published v%d -> %s" % (n, pd))
        except Exception as e:
            log("  (publish skipped: %s)" % e)
    else:
        log("  (no version folder set — archived locally only, not published)")
    return {"version": n, "archive": vdir, "published": published}


def status(pull_dir=None):
    """Version compare against the shared pull folder. Returns
    {current, latest, up_to_date, out_of_date, reachable, path}."""
    cur = current_version()
    pd = (pull_dir or "").strip().strip('"')
    reachable = bool(pd) and os.path.isdir(pd)
    latest = _max_version_in(pd) if reachable else cur
    # If the shared folder is unreachable we cannot know — report up-to-date (no false red).
    up = (latest <= cur) if reachable else True
    return {"current": cur, "latest": latest, "up_to_date": up,
            "out_of_date": (not up), "reachable": reachable, "path": pd}


def _copy_over(src, dst, log):
    """Copy every file under `src` onto `dst` (same relative path), skipping excluded dirs
    and the nested-previous history. Locked/undeletable files are logged and skipped."""
    skip = _EXCLUDE | {_NEST}
    count = 0
    for droot, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip]
        rel = os.path.relpath(droot, src)
        for f in files:
            s = os.path.join(droot, f)
            r = os.path.join(dst, f) if rel == "." else os.path.join(dst, rel, f)
            try:
                os.makedirs(os.path.dirname(r), exist_ok=True)
                shutil.copy2(s, r)
                count += 1
            except Exception as e:
                log("  (skipped %s: %s)" % (f, e))
    return count


def apply_update(pull_dir, log=lambda *a: None):
    """Copy the latest published version's files from `pull_dir` over the local fused folder
    (source/data/dist only). Applied fully on restart. Returns a result dict."""
    pd = (pull_dir or "").strip().strip('"')
    if not pd or not os.path.isdir(pd):
        return {"ok": False, "error": "update folder not reachable: %r" % pd}
    latest = _max_version_in(pd)
    if latest <= current_version():
        return {"ok": True, "updated": False, "version": current_version(),
                "note": "Already up to date (v%d)." % current_version()}
    src = os.path.join(pd, "v%d" % latest)
    if not os.path.isdir(src):
        return {"ok": False, "error": "v%d missing in update folder" % latest}
    log("Updating v%d -> v%d from %s ..." % (current_version(), latest, pd))
    n = _copy_over(src, fused_root(), log)
    _set_current_version(latest)
    log("Applied %d file(s). Restart LISA to load v%d." % (n, latest))
    return {"ok": True, "updated": True, "version": latest, "files": n}
