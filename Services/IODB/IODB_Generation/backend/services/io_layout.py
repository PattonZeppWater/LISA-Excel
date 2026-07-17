"""
io_layout.py — IOList + PLCEquipment → IOLayout rows
"""

from collections import defaultdict

IO_LAYOUT_HEADERS = [
    "ProcIdent", "EquipProcLoc", "Descriptor", "LoopNumber",
    "IOType", "Comment", "Drop", "Rack", "Slot", "Channel",
    "RangeMin", "RangeMax", "Unit",
]


def _get_sheet(sheets: dict, name: str) -> dict:
    for k in sheets:
        if k.lower().strip() == name.lower().strip():
            return sheets[k]
    raise ValueError(f"Sheet '{name}' not found. Available: {list(sheets.keys())}")


def _make_h(headers: list) -> dict:
    return {h: i for i, h in enumerate(headers)}


def _get(row, h, col, default=None):
    idx = h.get(col)
    if idx is None:
        return default
    return row[idx] if idx < len(row) else default


def _safe_int_iosize(val) -> int:
    if val is None:
        return 0
    try:
        v = int(val)
        return v if v > 0 else 0
    except (ValueError, TypeError):
        return 0


def _iotype_for_card(part_type: str):
    if part_type and part_type.endswith("Card"):
        return f"{part_type[:-4]}_Physical"
    return None


def generate_io_layout(sheets: dict) -> dict:
    eq   = _get_sheet(sheets, "PLCEquipment")
    eq_h = _make_h(eq["headers"])
    il   = _get_sheet(sheets, "IOList")
    il_h = _make_h(il["headers"])

    cards = []
    for row in eq["rows"]:
        part_type = str(_get(row, eq_h, "PartType") or "").strip()
        io_size   = _safe_int_iosize(_get(row, eq_h, "IOSize"))
        if io_size == 0:
            continue
        iotype = _iotype_for_card(part_type)
        if iotype is None:
            continue
        cards.append({
            "iotype":  iotype,
            "drop":    _get(row, eq_h, "Drop"),
            "rack":    _get(row, eq_h, "Rack"),
            "slot":    _get(row, eq_h, "Slot"),
            "io_size": io_size,
        })

    def _n(v):
        try: return int(v)
        except (TypeError, ValueError): return 0

    cards.sort(key=lambda c: (_n(c["drop"]), _n(c["rack"]), _n(c["slot"])))

    available_counts = defaultdict(int)
    for card in cards:
        available_counts[card["iotype"]] += card["io_size"]

    queues = defaultdict(list)
    for row in il["rows"]:
        iotype = str(_get(row, il_h, "IOType") or "").strip()
        if not iotype or iotype.endswith("_Communicated"):
            continue
        queues[iotype].append(row)

    output_rows = []
    assigned_counts = defaultdict(int)

    for card in cards:
        iotype  = card["iotype"]
        drop    = card["drop"]
        rack    = card["rack"]
        slot    = card["slot"]
        io_size = card["io_size"]
        prefix  = iotype.split("_")[0]
        queue   = queues[iotype]

        for ch in range(io_size):
            ch_str = f"{ch:02d}"
            if queue:
                rec = queue.pop(0)
                row_dict = {
                    "ProcIdent":    _get(rec, il_h, "ProcIdent"),
                    "EquipProcLoc": _get(rec, il_h, "EquipProcLoc"),
                    "Descriptor":   _get(rec, il_h, "Descriptor"),
                    "LoopNumber":   _get(rec, il_h, "LoopNumber"),
                    "IOType":       _get(rec, il_h, "IOType"),
                    "Comment":      _get(rec, il_h, "Comment"),
                    "Drop":         drop,
                    "Rack":         rack,
                    "Slot":         slot,
                    "Channel":      ch_str,
                    "RangeMin":     _get(rec, il_h, "RangeMin"),
                    "RangeMax":     _get(rec, il_h, "Range Max") or _get(rec, il_h, "RangeMax"),
                    "Unit":         _get(rec, il_h, "Units") or _get(rec, il_h, "Unit"),
                }
                assigned_counts[iotype] += 1
            else:
                row_dict = {
                    "ProcIdent":    "SPR",
                    "EquipProcLoc": f"SPARE {prefix}",
                    "Descriptor":   f"CH-{drop}-{rack}-{slot}-{ch_str}",
                    "LoopNumber":   "NA",
                    "IOType":       iotype,
                    "Comment":      None,
                    "Drop":         drop,
                    "Rack":         rack,
                    "Slot":         slot,
                    "Channel":      ch_str,
                    "RangeMin":     None,
                    "RangeMax":     None,
                    "Unit":         None,
                }
            output_rows.append(row_dict)

    spare_counts = {
        iotype: available_counts[iotype] - assigned_counts[iotype]
        for iotype in available_counts
    }

    rows_as_arrays = [[r.get(col) for col in IO_LAYOUT_HEADERS] for r in output_rows]

    return {
        "sheet":   "IOLayout",
        "headers": IO_LAYOUT_HEADERS,
        "rows":    rows_as_arrays,
        "stats":   {
            "total_rows":      len(output_rows),
            "cards_processed": len(cards),
            "available":       dict(available_counts),
            "assigned":        dict(assigned_counts),
            "spares":          spare_counts,
        },
    }
