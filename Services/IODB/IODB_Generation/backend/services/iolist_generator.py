"""
iolist_generator.py â€” io_list JSON config â†’ IOList sheet rows
"""

from .validator import validate

IO_LIST_HEADERS = [
    "ProcIdent", "EquipProcLoc", "Descriptor", "LoopNumber",
    "IOType", "Comment", "RangeMin", "Range Max", "Units",
]

KEY_TO_COL = {
    "proc_ident":     "ProcIdent",
    "equip_proc_loc": "EquipProcLoc",
    "descriptor":     "Descriptor",
    "loop_number":    "LoopNumber",
    "io_type":        "IOType",
    "comment":        "Comment",
    "range_min":      "RangeMin",
    "range_max":      "Range Max",
    "units":          "Units",
}


def generate_io_list(sheets: dict, config_json: dict) -> dict:
    if not config_json:
        raise ValueError(
            "No IO list config provided. Paste a JSON config file before generating."
        )

    errors = validate(config_json)
    if errors:
        raise ValueError("IO list validation failed:\n" + "\n".join(errors))

    points    = config_json.get("io_list", [])
    col_indices = {col: i for i, col in enumerate(IO_LIST_HEADERS)}
    by_type: dict = {}
    rows = []

    for point in points:
        row = [None] * len(IO_LIST_HEADERS)
        for json_key, col_name in KEY_TO_COL.items():
            idx = col_indices.get(col_name)
            if idx is not None:
                row[idx] = point.get(json_key)
        rows.append(row)
        io_type = str(point.get("io_type") or "").strip()
        by_type[io_type] = by_type.get(io_type, 0) + 1

    return {
        "sheet":   "IOList",
        "headers": IO_LIST_HEADERS,
        "rows":    rows,
        "stats":   {"total": len(rows), "by_type": by_type, "available": True},
    }
