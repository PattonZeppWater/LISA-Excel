"""Level-3 smoke test (needs AutoCAD running).

Confirms render_plan faithfully executes build_layout_plan end to end:
  1. build the expected plan (pure) from a fixture conduit,
  2. generate the real DWG via AutoCAD,
  3. reopen the DWG and read back every block reference (EffectiveName + insertion
     point + attributes),
  4. assert each plan item is present at its planned (x, y) with its planned name,
     and spot-check key attrs (wire colour, instrument ISA).

The template keeps its catalog blocks in model space, so matching is by
(EffectiveName, x, y) -- our blocks sit at known coordinates, the catalog elsewhere.

Run standalone:   python tests/smoke_render_plan.py
Or via pytest:     pytest tests/test_smoke_level3.py   (skips if AutoCAD is closed)
"""
import os
import sys
import json
import tempfile

_HERE = os.path.dirname(__file__)
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services import parser
from services import autocad_bridge as ab

_FIXTURE = os.path.join(_HERE, "fixtures", "dup_instrument.json")
_TOL = 0.02


def autocad_running() -> bool:
    try:
        import win32com.client
        win32com.client.GetActiveObject("AutoCAD.Application")
        return True
    except Exception:
        return False


def _read_blocks(out_path):
    """Reopen the generated DWG and return [{name, x, y, attrs}] for every block ref."""
    import win32com.client
    acad = win32com.client.GetActiveObject("AutoCAD.Application")
    doc = acad.Documents.Open(out_path)
    ms = doc.ModelSpace
    blocks = []
    for i in range(ms.Count):
        e = ms.Item(i)
        try:
            if e.ObjectName != "AcDbBlockReference":
                continue
            try:
                name = str(e.EffectiveName)
            except Exception:
                name = str(e.Name)
            ip = e.InsertionPoint
            attrs = {}
            try:
                for a in e.GetAttributes():
                    attrs[str(a.TagString)] = str(a.TextString)
            except Exception:
                pass
            blocks.append({"name": name, "x": float(ip[0]), "y": float(ip[1]), "attrs": attrs})
        except Exception:
            pass
    doc.Close(False)
    return blocks


def _find(blocks, name, x, y):
    for b in blocks:
        if (b["name"].upper() == str(name).upper()
                and abs(b["x"] - x) < _TOL and abs(b["y"] - y) < _TOL):
            return b
    return None


def run_smoke(verbose=True):
    rep = []
    def log(m):
        rep.append(m)
        if verbose:
            print(m)

    if not autocad_running():
        log("SKIP: AutoCAD is not running.")
        return None, rep

    fx = json.load(open(_FIXTURE, encoding="utf-8"))
    conduit_row = next(r for r in fx["conduit_index"] if r.get("Cond_Tag") == "S01AIT003")
    conduit_data = parser.build_conduit_data(conduit_row)
    fill_rows = parser.get_fill_rows(fx["fill_index"], "S01AIT003")
    loop_list = parser.build_loop_list(fill_rows)
    bh = fx.get("block_heights")

    plan = ab.build_layout_plan(conduit_data, loop_list, bh)
    expected = [{"role": "conduit", **plan["conduit"]}] + plan["items"]
    log(f"plan: {len(expected)} expected blocks "
        f"({sum(1 for e in expected if e['role']=='instrument')} instrument, "
        f"{sum(1 for e in expected if e['role']=='wire')} wire)")

    out_path = os.path.join(tempfile.gettempdir(), "idp_smoke_render.dwg")
    if os.path.exists(out_path):
        try: os.remove(out_path)
        except Exception: pass

    result = ab.generate_dwg(conduit_data, loop_list, out_path, block_heights=bh)
    if not result.get("success"):
        log(f"FAIL: generation error: {result.get('error')}")
        return False, rep
    for w in (result.get("warnings") or []):
        log(f"  gen warning: {w}")

    blocks = _read_blocks(out_path)
    log(f"read back {len(blocks)} block refs from the DWG")

    missing, attr_fail = [], []
    for e in expected:
        b = _find(blocks, e["name"], e["x"], e["y"])
        if b is None:
            missing.append(e)
            continue
        # spot-check the attrs that define correctness for this role
        if e["role"] == "wire":
            want = e["attrs"].get("Src_Color")
            if want and b["attrs"].get("Src_Color") not in (want, None):
                attr_fail.append((e["role"], e["x"], e["y"], "Src_Color",
                                  want, b["attrs"].get("Src_Color")))
        if e["role"] == "instrument":
            for tag in ("ISATag_FunctIdent", "ISATag_ElementIdent", "ISATag_LoopNum"):
                want = e["attrs"].get(tag)
                if want and tag in b["attrs"] and b["attrs"][tag] != want:
                    attr_fail.append((e["role"], e["x"], e["y"], tag, want, b["attrs"][tag]))

    matched = len(expected) - len(missing)
    log(f"matched {matched}/{len(expected)} planned blocks at their planned positions")
    for e in missing:
        log(f"  MISSING: {e['role']} {e['name']!r} @ ({e['x']:.2f},{e['y']:.2f})")
    for f in attr_fail:
        log(f"  ATTR MISMATCH: {f[0]} @({f[1]:.2f},{f[2]:.2f}) {f[3]}: planned {f[4]!r} != drawn {f[5]!r}")

    ok = not missing and not attr_fail
    log("RESULT: PASS" if ok else "RESULT: FAIL")
    return ok, rep


if __name__ == "__main__":
    ok, _ = run_smoke()
    sys.exit(0 if ok else 1)
