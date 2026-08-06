"""
lisa_contract.py — validate/repair extractor output against LISA's input contract.

LISA generates a finished IDP only if each FillIndex row obeys the workbook's
dropdown universe (Handoff/lisa_symbols.json). This module answers, per row:
  - Is Type legal for this Wire Ct?           valid_types(ct)
  - Is the S/D symbol a legal dropdown value?  valid_symbols(type, ct, side)
  - How many Tag/Term slots does it activate?  slot_count(symbol)
and can snap a near-miss symbol to the closest legal one (snap_symbol).

Use check_records() before/after writing to report LISA-readiness and to drive
amber flagging of cells LISA would reject.
"""
from __future__ import annotations

import difflib
import json
import os
import sys

_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_JSON = os.path.join(_HERE, "Handoff", "lisa_symbols.json")


def load():
    with open(_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


CONTRACT = load()
SPECIAL = CONTRACT.get("special_cell_value", "Hide from Generation")


def key_of(type_name: str) -> str:
    """KEY(type) = type with dashes removed (Excel SUBSTITUTE(type,'-',''))."""
    return str(type_name or "").strip().replace("-", "")


def valid_types(wire_ct) -> list:
    return CONTRACT["type_by_wire_ct"].get(str(wire_ct or 1), [])


def valid_symbols(type_name, wire_ct, side) -> list:
    side = "L" if str(side).upper().startswith(("L", "S")) else "R"
    bucket = CONTRACT["symbols_by_key_ct_side"].get(key_of(type_name), {})
    return bucket.get(str(wire_ct or 1), {}).get(side, [])


def slot_count(symbol) -> int | None:
    return CONTRACT["blocktags_slot_count"].get(str(symbol or "").strip())


def snap_symbol(type_name, wire_ct, side, desired):
    """Return (best_valid_symbol, exact:bool). If `desired` is already legal,
    exact=True. Otherwise fuzzy-match to the closest legal symbol for this
    (type, ct, side); returns (None, False) if there are no candidates."""
    options = valid_symbols(type_name, wire_ct, side)
    if not options:
        return None, False
    d = str(desired or "").strip()
    if d in options:
        return d, True
    hit = difflib.get_close_matches(d, options, n=1, cutoff=0.6) if d else []
    return (hit[0] if hit else options[0]), False


def _row_ct(row):
    """Pipeline fill rows carry the connection count as 'count'; some carry
    'wire_ct'. Accept either."""
    return row.get("wire_ct") or row.get("count") or 1


def _check_side(row, side, issues, idx):
    sym_key = "s_symbol" if side == "L" else "d_symbol"
    sym = row.get(sym_key)
    typ = row.get("type")
    ct = _row_ct(row)
    if not sym:
        return
    legal = valid_symbols(typ, ct, side)
    if sym not in legal:
        best, _ = snap_symbol(typ, ct, side, sym)
        issues.append({
            "row": idx, "field": sym_key, "value": sym,
            "problem": f"symbol not in {key_of(typ)}_{ct}_{side} dropdown "
                       f"(LISA cannot map it)",
            "suggest": best,
        })
        return
    # symbol legal — check that filled tag/term slots don't exceed its capacity
    slots = slot_count(sym)
    if slots is not None:
        tags = row.get("s_tags" if side == "L" else "d_tags") or []
        filled = sum(1 for t in tags if t not in (None, "", SPECIAL))
        if filled > slots:
            issues.append({
                "row": idx, "field": sym_key, "value": sym,
                "problem": f"{filled} tags filled but symbol activates only "
                           f"{slots} slot(s); extras grey out in the workbook",
                "suggest": None,
            })


LEGAL_TYPES = {t for lst in CONTRACT.get("type_by_wire_ct", {}).values() for t in lst}

# off-dropdown type values seen in real workbooks -> legal dropdown values
TYPE_ALIASES = {
    "ETHERNET": "CAT-6", "CAT6": "CAT-6", "CAT 6": "CAT-6",
    "PULL ROPE": "PULL_ROPE", "PULLROPE": "PULL_ROPE", "PULLROPES": "PULL_ROPE",
    "MFR CABLE": "MFG_CABLE", "MFG CABLE": "MFG_CABLE", "MFGCABLE": "MFG_CABLE",
    "FIBRE": "FIBER",
}


def normalize_types(records) -> list:
    """Map off-dropdown Type values to the legal domain so LISA can read them:
      - fold GROUND/GND rows into the conduit's primary group (add GRN color),
      - alias ETHERNET->CAT-6, 'PULL ROPE'->PULL_ROPE, MFR CABLE->MFG_CABLE, ...
    Mutates records in place; returns a list of change summaries."""
    changes = []
    for rec in records or []:
        fill = rec.get("fill", []) or []
        # 1) fold ground rows into the primary (non-ground) group
        grounds = [g for g in fill if str(g.get("type") or "").strip().upper()
                   in ("GROUND", "GND")]
        if grounds:
            primary = next((g for g in fill if g not in grounds), None)
            for g in grounds:
                if primary is not None:
                    cols = primary.setdefault("colors", [])
                    if "GRN" not in cols:
                        cols.append("GRN")
                    primary["ground_folded"] = True
                    fill.remove(g)
                    changes.append({"conduit": rec.get("name"),
                                    "note": "folded GROUND row into primary group (added GRN)"})
                # if no primary, leave the ground row for the contract to flag
            rec["fill"] = fill
        # 2) alias remaining off-dropdown types to legal values
        for g in fill:
            t = g.get("type")
            if t and t not in LEGAL_TYPES:
                new = TYPE_ALIASES.get(str(t).strip().upper())
                if new:
                    g["type"] = new
                    g["type_normalized"] = f"{t} -> {new}"
                    changes.append({"conduit": rec.get("name"), "note": f"Type {t} -> {new}"})
    return changes


def cts_available(type_name) -> list:
    """Wire-Ct buckets that actually have symbols for this type, read from the
    contract. E.g. POWER -> [1,2,3,4] (POWER_6/POWER_8 dropdowns are empty);
    CONTROL -> [1,2,3,4,6,8]; MFG_CABLE -> [1]."""
    bucket = CONTRACT["symbols_by_key_ct_side"].get(key_of(type_name), {})
    out = [int(ct) for ct, sides in bucket.items() if sides.get("L") or sides.get("R")]
    return sorted(out)


def _remap_ct(type_name, count):
    """Map a raw conductor count to a legal *connection* count for this type.
    Returns (new_ct, note). new_ct is None to signal 'collapse to MFG_CABLE'."""
    avail = cts_available(type_name)
    if not avail or count in avail:
        return count, None
    if key_of(type_name) == "POWER":
        # phase-landing model: parallel conductor sets collapse to phase count
        for cand in (3, 4):
            if cand in avail and count % cand == 0:
                return cand, (f"{count}-conductor power feeder -> Wire Ct {cand} "
                              f"(parallel {cand}-phase sets collapse to phase landings)")
        # no clean phase grouping -> assume 3-phase + neutral (4); verify
        tgt = 4 if 4 in avail else max(avail)
        return tgt, (f"{count}-conductor power run -> Wire Ct {tgt} "
                     f"(assumed 3-phase; VERIFY landings vs the diagram)")
    # single-landing cable types (PULL_ROPE/FIBER/CAT-6/TSP/MFG_CABLE only exist
    # at Ct 1): `count` is a QUANTITY of cables, not a connection count.
    if max(avail) == 1:
        if count != 1:
            return 1, (f"{count}x {type_name} in one conduit -> Wire Ct 1 "
                       f"(split into one row per cable per the wiring diagram)")
        return 1, None
    # multi-conductor control cable beyond the dropdown range -> one MFG_CABLE
    if count > max(avail):
        return None, f"{count}-conductor {type_name} cable -> MFG_CABLE, Wire Ct 1"
    le = [c for c in avail if c <= count]
    tgt = max(le) if le else min(avail)
    return tgt, f"{count}-conductor {type_name} -> Wire Ct {tgt} (nearest legal)"


def normalize_connections(records) -> list:
    """Remodel each fill group so it obeys LISA's contract:
      - remap raw conductor counts to legal connection counts (per type),
      - collapse over-length non-power cables to MFG_CABLE (Wire Ct 1),
      - snap S/D symbols to a legal dropdown block for the (new type, ct, side),
        lowering symbol confidence so idp_write flags the change amber.
    Mutates the records in place. Returns a list of remodel summaries."""
    summary = []
    fidx = 0
    for rec in records or []:
        for grp in rec.get("fill", []) or []:
            fidx += 1
            typ = grp.get("type")
            count = grp.get("wire_ct") or grp.get("count") or 1
            new_ct, note = _remap_ct(typ, count)
            new_type = typ
            if new_ct is None:
                new_type, new_ct = "MFG_CABLE", 1
            ct_changed = (new_ct != count) or (new_type != typ)
            if ct_changed:
                grp["count"] = new_ct
                grp["wire_ct"] = new_ct
                grp["type"] = new_type
                grp["connection_remodel"] = note or f"remodeled to Wire Ct {new_ct}"

            sym_notes = []
            for side, skey, ckey in (("L", "s_symbol", "s_symbol_conf"),
                                     ("R", "d_symbol", "d_symbol_conf")):
                sym = grp.get(skey)
                if not sym:
                    continue
                best, exact = snap_symbol(new_type, new_ct, side, sym)
                if best is None:
                    grp[ckey] = 0.0
                    sym_notes.append(f"{skey}: {sym} -> NO LEGAL SYMBOL")
                elif not exact:
                    grp[skey] = best
                    grp[ckey] = 0.5   # < 0.6 => idp_write flags it amber
                    sym_notes.append(f"{skey}: {sym} -> {best}")

            if ct_changed or sym_notes:
                summary.append({
                    "row": fidx, "conduit": rec.get("name"),
                    "note": "; ".join(filter(None, [note if ct_changed else None, *sym_notes])),
                })
    return summary


def check_records(records) -> list:
    """Return a list of LISA-contract violations across all fill rows.
    Empty list == every row is LISA-generatable."""
    issues = []
    idx = 0
    for rec in records or []:
        for row in rec.get("fill", []) or []:
            idx += 1
            typ, ct = row.get("type"), _row_ct(row)
            if typ and typ not in valid_types(ct):
                issues.append({
                    "row": idx, "field": "type", "value": typ,
                    "problem": f"Type not in Type_{ct} domain",
                    "suggest": None,
                })
            _check_side(row, "L", issues, idx)
            _check_side(row, "R", issues, idx)
    return issues


if __name__ == "__main__":
    # smoke test against the contract itself
    print("Type_1:", valid_types(1))
    print("POWER/1/L symbols:", valid_symbols("POWER", 1, "L"))
    print("CAT-6 -> key:", key_of("CAT-6"), "| CAT-6/1/L:", valid_symbols("CAT-6", 1, "L"))
    print("slot_count('CB-TB_Square_L'):", slot_count("CB-TB_Square_L"))
    print("snap 'CB_TB_Square_L' ->", snap_symbol("POWER", 2, "L", "CB_TB_Square_L"))
    print("snap 'GND' (POWER/1/L) ->", snap_symbol("POWER", 1, "L", "GND"))
    demo = [{"fill": [
        {"type": "POWER", "wire_ct": 1, "s_symbol": "CB_L", "d_symbol": "TOTALLY_FAKE"},
        {"type": "POWER", "wire_ct": 9, "s_symbol": "CB_L"},
    ]}]
    from pprint import pprint
    print("check_records demo:")
    pprint(check_records(demo))
