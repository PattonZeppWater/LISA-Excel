"""
idp_dwg_scan.py — read block attributes from IDP .dwg files via AutoCAD.

Fast path: ObjectDBX (AxDbDocument) — loads each DWG IN MEMORY without opening
the editor UI (much faster, no window churn, never saves). Falls back to opening
in the editor if ObjectDBX isn't available.

Requires an already-running AutoCAD (COM can't cold-start it from a sandbox).

Dumps every block reference's effective name, insertion X/Y (for source/dest
side), and attributes (tag -> value) to <folder>/_dwg_scan.json.

Usage (AutoCAD open):
    python idp_dwg_scan.py "C:\\path\\to\\folder"           # all .dwg DIRECTLY in folder
    python idp_dwg_scan.py "C:\\path\\to\\file.dwg"          # one drawing
    python idp_dwg_scan.py --out OUT.json file1.dwg file2.dwg ...   # explicit file list
                                                              # (needed for a project tree
                                                              # scanned recursively elsewhere —
                                                              # folder mode does NOT recurse)
"""
import json
import os
import sys

import win32com.client
import pythoncom


def _acad():
    try:
        return win32com.client.GetActiveObject("AutoCAD.Application")
    except Exception:
        return win32com.client.Dispatch("AutoCAD.Application")


def _open_dbx(acad):
    """Return a reusable AxDbDocument (ObjectDBX) or None if unavailable."""
    ver = ""
    try:
        ver = str(acad.Version).split(".")[0]   # e.g. "26"
    except Exception:
        pass
    progids = [f"ObjectDBX.AxDbDocument.{ver}"] if ver else []
    progids += ["ObjectDBX.AxDbDocument.26", "ObjectDBX.AxDbDocument.25",
                "ObjectDBX.AxDbDocument.24", "ObjectDBX.AxDbDocument"]
    for pid in progids:
        try:
            return acad.GetInterfaceObject(pid)
        except Exception:
            continue
    return None


def catalog_symbol_library(lib_dir, out_json=None):
    """Open every symbol/block .dwg in the LISA symbol library (via ObjectDBX on
    a RUNNING AutoCAD) and learn each block's schema: baked-in device tag
    (Tag1 default), terminal capacity (# of Term* attribute defs), tag/desc
    slots, and a geometry summary. Writes a catalog JSON and returns the dict.
    Returns {} if AutoCAD/ObjectDBX isn't reachable — never raises."""
    import glob
    import re
    try:
        acad = _acad(); dbx = _open_dbx(acad)
        if dbx is None:
            return {}
    except Exception:
        return {}
    files = [f for f in sorted(glob.glob(os.path.join(lib_dir, "*.dwg")))
             if "_bak_" not in os.path.basename(f)]
    cat = {}
    for p in files:
        name = os.path.splitext(os.path.basename(p))[0]
        try:
            dbx.Open(p); ms = dbx.ModelSpace
            tags, geom, dev = [], {}, ""
            for i in range(ms.Count):
                e = ms.Item(i); on = e.ObjectName; geom[on] = geom.get(on, 0) + 1
                if on == "AcDbAttributeDefinition":
                    t = e.TagString; tags.append(t)
                    if t == "Tag1":
                        dev = e.TextString
            cat[name] = {
                "device_default": dev,
                "terminals": len([t for t in tags if re.match(r"Term\d", t)]),
                "tag_slots": len([t for t in tags if re.match(r"Tag\d", t)]),
                "attr_tags": sorted(set(tags)),
                "geom": {k.replace("AcDb", ""): v for k, v in geom.items()
                         if k != "AcDbAttributeDefinition"},
            }
        except Exception as ex:
            cat[name] = {"error": str(ex)[:80]}
    if out_json:
        try:
            with open(out_json, "w", encoding="utf-8") as fh:
                json.dump(cat, fh, indent=1, ensure_ascii=False)
        except OSError:
            pass
    return cat


def _scan_modelspace(ms):
    blocks = []
    for ent in ms:
        try:
            if ent.ObjectName != "AcDbBlockReference":
                continue
            try:
                name = ent.EffectiveName
            except Exception:
                name = ent.Name
            ins = ent.InsertionPoint
            rec = {"name": name, "x": round(ins[0], 3), "y": round(ins[1], 3), "attrs": {}}
            try:
                if ent.HasAttributes:
                    for a in ent.GetAttributes():
                        rec["attrs"][a.TagString] = a.TextString
            except Exception:
                pass
            blocks.append(rec)
        except Exception:
            continue
    return blocks


def _resolve_args(args):
    """Accept either a single folder/file (legacy), or '--out PATH file1 file2 ...'
    (explicit list — required for files gathered recursively by a caller, since
    folder mode below only looks directly inside the given directory)."""
    out_path = None
    files, it = [], iter(args)
    for a in it:
        if a == "--out":
            out_path = next(it)
        elif os.path.isdir(a):
            files += [os.path.join(a, f) for f in sorted(os.listdir(a))
                      if f.lower().endswith(".dwg")]
            out_path = out_path or os.path.join(a, "_dwg_scan.json")
        else:
            files.append(a)
    out_path = out_path or os.path.join(
        os.path.dirname(files[0]) if files else ".", "_dwg_scan.json")
    return files, out_path


def main(args):
    files, out = _resolve_args(args if isinstance(args, list) else [args])
    pythoncom.CoInitialize()
    acad = _acad()
    print("attached:", acad.Name, acad.Version, flush=True)
    dbx = _open_dbx(acad)
    print("mode:", "ObjectDBX (headless)" if dbx is not None else "editor open", flush=True)

    result = {}
    for i, f in enumerate(files, 1):
        base = os.path.basename(f)
        key = os.path.abspath(f)   # unique even across subfolders with same-named files
        try:
            if dbx is not None:
                dbx.Open(os.path.abspath(f))
                blocks = _scan_modelspace(dbx.ModelSpace)
            else:
                doc = acad.Documents.Open(os.path.abspath(f))
                blocks = _scan_modelspace(doc.ModelSpace)
                doc.Close(False)
            result[key] = blocks
            wa = sum(1 for b in blocks if b["attrs"])
            print(f"[{i}/{len(files)}] {base}: {len(blocks)} blocks, {wa} w/ attrs", flush=True)
        except Exception as e:
            print(f"[{i}/{len(files)}] {base}: ERROR {e}", flush=True)

    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] if len(sys.argv) > 1 else ["."])
