"""
tagdb_generator.py — IOLayout + tagdb_config JSON → TagDB sheet rows
"""

import json
import os
import re
import jsonschema

_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "knowledge", "tagdb-config-schema.json"
)
_DATA_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "knowledge", "tagdb-data-schema.json"
)

TAGDB_HEADERS = [
    "TAG", "DESCRIPTION", "TYPE", "PLC ADDRESS", "SCADA ADDRESS",
    "Min", "Max", "Unit", "LoopNumber", "Drop", "Rack", "Slot", "Channel", "LOCATION",
]

DEFAULT_TYPE_MAP = {
    "DI_Physical":     "BOOL",
    "DO_Physical":     "BOOL",
    "AI_Physical":     "REAL",
    "AO_Physical":     "REAL",
    "DI_Communicated": "BOOL",
    "DO_Communicated": "BOOL",
    "AI_Communicated": "REAL",
    "AO_Communicated": "REAL",
}


def _load_schema():
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _validate_config(config):
    errors = []
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path)):
        path = " > ".join(str(p) for p in err.absolute_path) or "root"
        errors.append(f"{path}: {err.message}")
    return errors


def _get_sheet(sheets, name):
    for k in sheets:
        if k.lower().strip() == name.lower().strip():
            return sheets[k]
    raise ValueError(f"Sheet '{name}' not found. Available: {list(sheets.keys())}")


def _make_h(headers): return {h: i for i, h in enumerate(headers)}

def _get(row, h, col, default=None):
    idx = h.get(col)
    if idx is None: return default
    return row[idx] if idx < len(row) else default

def _safe_int(val, default=0):
    try: return int(val)
    except (TypeError, ValueError): return default


def _make_tag(equip_proc_loc, cfg):
    sep    = cfg.get("tag_separator", "_")
    prefix = cfg.get("tag_prefix", "")
    case   = cfg.get("tag_case", "upper")
    tag    = re.sub(r"[-\s]+", sep, str(equip_proc_loc).strip())
    if case == "upper":   tag = tag.upper()
    elif case == "lower": tag = tag.lower()
    return prefix + tag


def _make_plc_address(io_type, drop, rack, slot, channel, cfg):
    fmt           = cfg.get("plc_address_format", "none")
    remote_prefix = cfg.get("plc_remote_prefix", "").strip()

    if io_type.endswith("_Communicated") or fmt == "none":
        return ""

    if fmt == "AB_ControlLogix":
        rack_str = "Local" if (drop == 0 or not remote_prefix) else f"{remote_prefix}_{drop}"
        if io_type == "DI_Physical": return f"{rack_str}:{slot}:I.Data.{channel}"
        if io_type == "DO_Physical": return f"{rack_str}:{slot}:O.Data.{channel}"
        if io_type == "AI_Physical": return f"{rack_str}:{slot}:I.Ch{channel}Data"
        if io_type == "AO_Physical": return f"{rack_str}:{slot}:O.Ch{channel}Data"
        return ""

    if fmt == "AB_Micro800":
        em = "EM" if slot == 0 else f"P{slot}"
        if io_type == "DI_Physical": return f"_IO_{em}_DI_{channel}"
        if io_type == "DO_Physical": return f"_IO_{em}_DO_{channel}"
        if io_type == "AI_Physical": return f"_IO_{em}_AI_{channel}"
        if io_type == "AO_Physical": return f"_IO_{em}_AO_{channel}"
        return ""

    if fmt == "Siemens_S7300":
        if io_type == "DI_Physical": return f"I{channel // 8}.{channel % 8}"
        if io_type == "DO_Physical": return f"Q{channel // 8}.{channel % 8}"
        if io_type == "AI_Physical": return f"IW{channel * 2}"
        if io_type == "AO_Physical": return f"QW{channel * 2}"
        return ""

    if fmt == "Schneider_M340":
        if io_type == "DI_Physical": return f"%I0.{rack}.{slot}.{channel}"
        if io_type == "DO_Physical": return f"%Q0.{rack}.{slot}.{channel}"
        if io_type == "AI_Physical": return f"%IW0.{rack}.{slot}.{channel}"
        if io_type == "AO_Physical": return f"%QW0.{rack}.{slot}.{channel}"
        return ""

    return ""


def generate_tag_db(sheets: dict, config_json: dict) -> dict:
    if not config_json:
        raise ValueError("No TagDB config provided.")

    errors = _validate_config(config_json)
    if errors:
        raise ValueError("TagDB config validation failed:\n" + "\n".join(errors))

    cfg                  = config_json["tagdb_config"]
    include_spares       = cfg.get("include_spares", False)
    include_communicated = cfg.get("include_communicated", True)
    location             = cfg.get("location", "")
    scada_prefix         = cfg.get("scada_address_prefix", "")
    type_map             = {**DEFAULT_TYPE_MAP, **cfg.get("type_map", {})}

    iolayout = _get_sheet(sheets, "IOLayout")
    il_h     = _make_h(iolayout["headers"])

    if not iolayout.get("rows"):
        raise ValueError("IOLayout sheet is empty. Generate IOLayout before generating TagDB.")

    col_idx  = {col: i for i, col in enumerate(TAGDB_HEADERS)}
    by_type: dict = {}
    rows = []

    for raw in iolayout["rows"]:
        proc_ident = str(_get(raw, il_h, "ProcIdent") or "").strip()
        io_type    = str(_get(raw, il_h, "IOType")    or "").strip()

        if not include_spares and proc_ident == "SPR":
            continue
        if not include_communicated and io_type.endswith("_Communicated"):
            continue

        equip = str(_get(raw, il_h, "EquipProcLoc") or "").strip()
        desc  = _get(raw, il_h, "Descriptor")
        drop  = _safe_int(_get(raw, il_h, "Drop"))
        rack  = _safe_int(_get(raw, il_h, "Rack"))
        slot  = _safe_int(_get(raw, il_h, "Slot"))
        chan  = _safe_int(_get(raw, il_h, "Channel"))
        rmin  = _get(raw, il_h, "RangeMin")
        rmax  = _get(raw, il_h, "RangeMax")
        unit  = _get(raw, il_h, "Unit")

        tag        = _make_tag(equip, cfg)
        plc_addr   = _make_plc_address(io_type, drop, rack, slot, chan, cfg)
        scada_addr = scada_prefix + tag
        plc_type   = type_map.get(io_type, "BOOL")

        row = [None] * len(TAGDB_HEADERS)
        row[col_idx["TAG"]]           = tag
        row[col_idx["DESCRIPTION"]]   = desc
        row[col_idx["TYPE"]]          = plc_type
        row[col_idx["PLC ADDRESS"]]   = plc_addr
        row[col_idx["SCADA ADDRESS"]] = scada_addr
        row[col_idx["Min"]]           = rmin
        row[col_idx["Max"]]           = rmax
        row[col_idx["Unit"]]          = unit
        row[col_idx["LoopNumber"]]    = _get(raw, il_h, "LoopNumber")
        row[col_idx["Drop"]]          = _get(raw, il_h, "Drop")
        row[col_idx["Rack"]]          = _get(raw, il_h, "Rack")
        row[col_idx["Slot"]]          = _get(raw, il_h, "Slot")
        row[col_idx["Channel"]]       = _get(raw, il_h, "Channel")
        row[col_idx["LOCATION"]]      = location

        rows.append(row)
        by_type[io_type] = by_type.get(io_type, 0) + 1

    return {
        "sheet":   "TagDB",
        "headers": TAGDB_HEADERS,
        "rows":    rows,
        "stats":   {"total": len(rows), "by_type": by_type, "available": True},
    }


def generate_tag_db_from_data(config_json: dict) -> dict:
    with open(_DATA_SCHEMA_PATH, encoding="utf-8") as f:
        data_schema = json.load(f)
    validator = jsonschema.Draft7Validator(data_schema)
    errors = sorted(validator.iter_errors(config_json), key=lambda e: list(e.absolute_path))
    if errors:
        msgs = [f"{' > '.join(str(p) for p in e.absolute_path) or 'root'}: {e.message}" for e in errors]
        raise ValueError("TagDB data validation failed:\n" + "\n".join(msgs))

    tagdb_data  = config_json["tagdb_data"]
    src_headers = tagdb_data["headers"]
    src_rows    = tagdb_data["rows"]

    dest_idx    = {col: i for i, col in enumerate(TAGDB_HEADERS)}
    src_to_dest: dict = {}
    for si, hdr in enumerate(src_headers):
        if hdr is not None and hdr in dest_idx:
            src_to_dest[si] = dest_idx[hdr]

    out_rows = []
    for src_row in src_rows:
        row = [None] * len(TAGDB_HEADERS)
        for si, di in src_to_dest.items():
            row[di] = src_row[si] if si < len(src_row) else None
        out_rows.append(row)

    return {
        "sheet":   "TagDB",
        "headers": TAGDB_HEADERS,
        "rows":    out_rows,
        "stats":   {"total": len(out_rows), "available": True},
    }
