"""
idp_dwg_extract.py — build ConduitIndex + FillIndex records from a DWG scan.

Consumes the _dwg_scan.json produced by idp_dwg_scan.py (block name, insertion
X/Y, attributes) — no AutoCAD needed here.

Mapping (learned from the block attributes):
  * `Conduit` block  -> one ConduitIndex row:
        Cdt_Name -> name, Cdt_Size/Cdt_Type -> size/ctype,
        Src_Name01-03 -> source, Dst_Name01-03 -> destination,
        Fill0N_Type/_Size/_Color/_Quantity -> per-fill Type/Gauge/Color/Wire Ct
  * device blocks (name ends `_L` source / `_R` destination):
        the block name IS the symbol; Tag1-4 / Term1-4 are the tags/terms.
  * pairing: within a row (same Y), sort each side by X and pair 1st-L ↔ 1st-R.
  * wire label is NOT copied from WIRE_IDP; it's rebuilt from tags+terms downstream.
"""
import collections
import json
import re


def _g(attrs, key):
    return (attrs.get(key) or "").strip()


def _conduit_rec(cblk):
    a = cblk["attrs"]
    rec = {
        "name": _g(a, "Cdt_Name"),
        "source": [_g(a, "Src_Name01"), _g(a, "Src_Name02"), _g(a, "Src_Name03")],
        "dest": [_g(a, "Dst_Name01"), _g(a, "Dst_Name02"), _g(a, "Dst_Name03")],
        "size": _g(a, "Cdt_Size"),
        "ctype": _g(a, "Cdt_Type") or "XXX",
        "docs": [], "wires": [], "fill": [], "flags": [],
    }
    cfills = []
    for n in range(1, 31):
        p = f"Fill{n:02d}_"
        t = _g(a, p + "Type")
        if t:
            cfills.append({"type": t, "gauge": _g(a, p + "Size"),
                           "color": _g(a, p + "Color"), "qty": _g(a, p + "Quantity")})
    return rec, cfills


def _side(blk):
    a = blk["attrs"]
    tags = [_g(a, f"Tag{i}") for i in range(1, 5)]
    terms = [_g(a, f"Term{i}") for i in range(1, 5)]
    # some blocks number terminals Term01..Term08
    if not any(terms):
        terms = [_g(a, f"Term0{i}") for i in range(1, 5)]
    return blk["name"], tags, terms


def _pairs(devs):
    """Group device blocks by row (Y); within a row pair 1st-L ↔ 1st-R by X."""
    byY = collections.defaultdict(lambda: {"L": [], "R": []})
    for b in devs:
        if b["name"].endswith("_L"):
            byY[round(b["y"], 1)]["L"].append(b)
        elif b["name"].endswith("_R"):
            byY[round(b["y"], 1)]["R"].append(b)
    pairs = []
    for y in sorted(byY, reverse=True):
        Ls = sorted(byY[y]["L"], key=lambda b: b["x"])
        Rs = sorted(byY[y]["R"], key=lambda b: b["x"])
        for L, R in zip(Ls, Rs):
            pairs.append((L, R))
    return pairs


def extract_from_scan(scan_path, dedup=True):
    data = json.load(open(scan_path, encoding="utf-8"))
    return extract_from_data(data, dedup=dedup)


def extract_from_data(data, dedup=True):
    records = []
    seen = set()
    for fn, blocks in data.items():
        cblk = next((b for b in blocks if b["name"] == "Conduit"), None)
        if not cblk:
            continue
        rec, cfills = _conduit_rec(cblk)
        if not rec["name"]:
            continue
        if dedup and rec["name"] in seen:
            continue
        seen.add(rec["name"])
        rec["flags"].append("from_dwg")

        devs = [b for b in blocks if b["name"].endswith("_L") or b["name"].endswith("_R")]
        pairs = _pairs(devs)
        src0 = rec["source"][0] or rec["name"]
        dst0 = rec["dest"][0] or ""
        for idx, (L, R) in enumerate(pairs):
            lname, ltags, lterms = _side(L)
            rname, rtags, rterms = _side(R)
            nterm = min(max([i + 1 for i, t in enumerate(lterms) if t]
                            + [i + 1 for i, t in enumerate(rterms) if t] + [1]), 4)
            cf = cfills[idx] if idx < len(cfills) else {}
            try:
                cnt = int(re.sub(r"[^0-9]", "", cf.get("qty", "")) or nterm)
            except ValueError:
                cnt = nterm
            rec["fill"].append({
                "type": cf.get("type", ""), "gauge": cf.get("gauge", ""),
                "colors": ([cf["color"]] if cf.get("color") and cf["color"] != "N/A" else [""]) * 1,
                "count": cnt, "slots": nterm,
                "s_symbol": lname, "d_symbol": rname,
            })
            for i in range(nterm):
                rec["wires"].append({
                    "src": (src0, ltags[i] or ltags[0], lterms[i]),
                    "dst": (dst0, rtags[i] or rtags[0], rterms[i]),
                })
        # if the conduit had fills but no device pairs, still emit the fills
        if not pairs and cfills:
            for cf in cfills:
                rec["fill"].append({"type": cf["type"], "gauge": cf["gauge"],
                                    "colors": [cf["color"]] if cf["color"] != "N/A" else [""],
                                    "count": cf["qty"] or 1})
        records.append(rec)
    return records


if __name__ == "__main__":
    import sys
    recs = extract_from_scan(sys.argv[1])
    print(f"{len(recs)} conduits from DWG scan")
    for r in recs[:8]:
        print(f"  {r['name']}: {len(r['fill'])} fills, {len(r['wires'])} wires, "
              f"src={r['source'][0]!r} dst={r['dest'][0]!r} type={r['ctype']}")
