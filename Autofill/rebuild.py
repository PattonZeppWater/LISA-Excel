"""
rebuild.py — one-command rebuild of IDP_ControlPanel.exe.

Runs PyInstaller into a STAGING folder, then copies the fresh exe to a destination path
(so a running exe is never half-overwritten). The destination is configurable and REMEMBERED:

    python rebuild.py                         -> rebuild to the last-used (or default) location
    python rebuild.py "D:\\Tools"             -> rebuild into that folder, and remember it
    python rebuild.py "D:\\Tools\\IDP.exe"    -> rebuild to that exact file, and remember it

Default location = the exe's current home: <this folder>/dist/IDP_ControlPanel.exe.
The chosen path is saved to rebuild_target.txt so future runs reuse it until you pass a new one.
"""
import os
import sys
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET_CFG = os.path.join(HERE, "rebuild_target.txt")
SPEC = os.path.join(HERE, "IDP_ControlPanel.spec")
DEFAULT_DEST = os.path.join(HERE, "dist", "IDP_ControlPanel.exe")
# Build INTERMEDIATES go to a non-OneDrive temp dir. Building inside the OneDrive-synced
# folder makes OneDrive/antivirus lock files mid-build (PermissionError on build/localpycs),
# which randomly fails the build — keeping work/dist out of OneDrive avoids that entirely.
BUILD_WORK = os.path.join(tempfile.gettempdir(), "aic_idp_build")
BUILD_DIST = os.path.join(tempfile.gettempdir(), "aic_idp_dist")
STAGED = os.path.join(BUILD_DIST, "IDP_ControlPanel.exe")


def resolve_target(arg):
    """Destination exe path: explicit arg (remembered) > remembered file > default dist/."""
    dest = (arg or "").strip().strip('"')
    if dest:
        try:
            with open(TARGET_CFG, "w", encoding="utf-8") as fh:
                fh.write(dest)
        except OSError:
            pass
    elif os.path.isfile(TARGET_CFG):
        try:
            dest = open(TARGET_CFG, encoding="utf-8").read().strip()
        except OSError:
            dest = ""
    if not dest:
        dest = DEFAULT_DEST
    # a folder (or anything not ending in .exe) → append the exe name
    if not dest.lower().endswith(".exe"):
        dest = os.path.join(dest, "IDP_ControlPanel.exe")
    return os.path.abspath(dest)


def main():
    dest = resolve_target(sys.argv[1] if len(sys.argv) > 1 else "")
    print("=" * 64)
    print("Rebuilding IDP_ControlPanel.exe")
    print("  target:", dest)
    print("=" * 64)
    os.chdir(HERE)   # spec's datas resolve relative to the spec dir
    for d in (BUILD_WORK, BUILD_DIST):
        shutil.rmtree(d, ignore_errors=True)
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "--workpath", BUILD_WORK, "--distpath", BUILD_DIST, SPEC])
    if r.returncode != 0 or not os.path.isfile(STAGED):
        print("\n*** BUILD FAILED — the existing exe was NOT touched. ***")
        return 1
    destdir = os.path.dirname(dest)
    if destdir and not os.path.isdir(destdir):
        try:
            os.makedirs(destdir, exist_ok=True)
        except OSError as e:
            print(f"\n*** Could not create '{destdir}': {e} ***")
            return 1
    try:
        shutil.copyfile(STAGED, dest)
    except PermissionError:
        print(f"\n*** COPY FAILED — '{dest}' is in use. Close the running exe and re-run. ***")
        return 1
    except OSError as e:
        print(f"\n*** COPY FAILED — {e} ***")
        return 1
    size_mb = os.path.getsize(dest) / 1e6
    print(f"\n=== DONE. Rebuilt exe ({size_mb:.0f} MB) at:\n    {dest} ===")
    print(f"(destination remembered in {os.path.basename(TARGET_CFG)} — pass a new path to change it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
