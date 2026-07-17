"""
exploded_view.py — TagDB → ExplodedView rows
"""

from collections import defaultdict

EV_COLUMNS = [
    "Tag", "Tag Description", "Parameter Description", "Base Tag",
    "Object Type", "Data Type", "PLC Address", "SCADA Address",
    "Default Value", "Min", "Max", "Unit", "Loop",
    "Drop", "Rack", "Slot", "Channel",
]

ELEMENTARY_TYPES = {"REAL", "DINT", "BOOL", "WORD", "INT", "UINT", "UDINT", "SINT", "USINT"}

_ALM_SP_PCT = {
    "HHAlmSp":  0.80, "HHAlmSpC": 0.75,
    "HAlmSp":   0.70, "HAlmSpC":  0.65,
    "LAlmSp":   0.30, "LAlmSpC":  0.35,
    "LLAlmSp":  0.20, "LLAlmSpC": 0.25,
}


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

def _is_elementary(type_str):
    t = type_str.strip().upper()
    return t in ELEMENTARY_TYPES or t.startswith("ARRAY")

def _get_tag_suffix(tag_s):
    for s in ("_R", "_W", "_C"):
        if tag_s.endswith(s): return s
    return None

def _strip_suffix(s):
    for suf in ("_R", "_W", "_C"):
        if s.endswith(suf): return s[:-len(suf)]
    return s

def _normalize_type(t):
    return "AIC_" + t[4:] if t.startswith("WML_") else t

def _is_analog_input(cls): return cls.startswith("AIC_IO_AI")
def _is_analog_output(cls): return cls.startswith("AIC_IO_AO")

def _safe_float(val):
    try: return float(val)
    except (TypeError, ValueError): return None

def _is_spare(tag_val, desc_val):
    tag_s  = str(tag_val).strip()  if tag_val  else ""
    desc_s = str(desc_val).strip() if desc_val else ""
    return tag_s.startswith("Spr_") or "spare" in desc_s.lower()


def _default_value(component, param, is_spare, cls_name="", min_val=None, max_val=None, tag_desc=""):
    if component == "ReadStructure": return "NA"
    if "AlmDly" in param: return 3
    if param.endswith("AlmDs"): return "TRUE" if is_spare else "FALSE"
    if param == "TestEn": return "FALSE"
    if param.endswith("AlmLtch"):
        d = tag_desc.lower() if tag_desc else ""
        return "TRUE" if ("fail to start" in d or "fail to stop" in d) else "FALSE"
    if _is_analog_input(cls_name):
        if param == "InMin":  return 4000
        if param == "InMax":  return 20000
        if param == "OutMin": return _safe_float(min_val)
        if param == "OutMax": return _safe_float(max_val)
    if _is_analog_output(cls_name):
        if param == "InMin":  return _safe_float(min_val)
        if param == "InMax":  return _safe_float(max_val)
        if param == "OutMin": return 4000
        if param == "OutMax": return 20000
    if param in _ALM_SP_PCT:
        mn = _safe_float(min_val)
        mx = _safe_float(max_val)
        if mn is not None and mx is not None:
            return round(mn + _ALM_SP_PCT[param] * (mx - mn), 4)
    if param == "DbDly":   return 1
    if param == "DbEn":    return "FALSE"
    if param == "NormClsd": return "FALSE"
    if param == "FiltSp":  return 1.0
    if param == "FiltEn":  return "FALSE"
    if param == "Oos":     return "FALSE"
    return None


def _load_master_params(sheets):
    mpl  = _get_sheet(sheets, "OBJ_MasterParameterList")
    h    = _make_h(mpl["headers"])
    master = {}
    for row in mpl["rows"]:
        cls    = str(_get(row, h, "CLASS")       or "").strip()
        comp   = str(_get(row, h, "COMPONENT")   or "").strip()
        param  = str(_get(row, h, "PARAMETER")   or "").strip()
        dtype  = str(_get(row, h, "TYPE")        or "").strip()
        desc   = str(_get(row, h, "DESCRIPTION") or "").strip()
        offset = _get(row, h, "Offset")
        if not cls or not param: continue
        master.setdefault(cls, []).append((comp, param, dtype, desc, offset))
    return master


def _row_elementary(raw, h):
    tag_val  = _get(raw, h, "TAG")
    type_val = _get(raw, h, "TYPE")
    return {
        "Tag":                   tag_val,
        "Tag Description":       _get(raw, h, "DESCRIPTION"),
        "Parameter Description": _get(raw, h, "DESCRIPTION"),
        "Base Tag":              tag_val,
        "Object Type":           type_val,
        "Data Type":             type_val,
        "PLC Address":           _get(raw, h, "PLC ADDRESS"),
        "SCADA Address":         _get(raw, h, "SCADA ADDRESS"),
        "Default Value":         None,
        "Loop":                  _get(raw, h, "LoopNumber") or _get(raw, h, "Loop"),
        "Min":                   _get(raw, h, "Min"),
        "Max":                   _get(raw, h, "Max"),
        "Unit":                  _get(raw, h, "Unit"),
        "Drop":                  _get(raw, h, "Drop"),
        "Rack":                  _get(raw, h, "Rack"),
        "Slot":                  _get(raw, h, "Slot"),
        "Channel":               _get(raw, h, "Channel"),
    }


def _expand_group(base, r_row, w_row, c_row, cls_name, params, h):
    def g(row, col): return _get(row, h, col) if row is not None else None

    r_desc   = g(r_row, "DESCRIPTION")
    _src     = r_row or w_row or c_row
    loop_val = g(_src, "LoopNumber") or g(_src, "Loop")
    min_val  = g(r_row, "Min")
    max_val  = g(r_row, "Max")
    unit_val = g(r_row, "Unit")
    drop_val = g(r_row, "Drop")
    rack_val = g(r_row, "Rack")
    slot_val = g(r_row, "Slot")
    chan_val  = g(r_row, "Channel")
    spare    = _is_spare(g(r_row, "TAG"), r_desc)

    comp_row = {"ReadStructure": r_row, "WriteStructure": w_row, "ConfigStructure": c_row}
    rows = []
    for comp, param, dtype, param_desc, offset in params:
        src = comp_row.get(comp)
        if src is None: continue
        tag_val    = g(src, "TAG")
        type_val   = g(src, "TYPE")
        base_scada = g(src, "SCADA ADDRESS")
        try:
            offset_f   = float(offset or 0)
            word_off   = int(offset_f)
            scada_addr = int(base_scada) + word_off
            if str(dtype).upper() == "BOOL":
                bit_num  = round((offset_f - word_off) * 100)
                plc_addr = f"%MW{(scada_addr - 400001):04d}.{bit_num:02d}"
            else:
                plc_addr = f"%MW{(scada_addr - 400001):04d}"
        except (TypeError, ValueError):
            scada_addr = None
            plc_addr   = None
        obj_type = _strip_suffix(_normalize_type(str(type_val).strip())) if type_val else cls_name
        default  = _default_value(
            comp, param, spare, cls_name=cls_name,
            min_val=min_val, max_val=max_val,
            tag_desc=str(r_desc) if r_desc else "",
        )
        rows.append({
            "Tag":                   f"{tag_val}.{param}" if tag_val else param,
            "Tag Description":       r_desc,
            "Parameter Description": param_desc,
            "Base Tag":              base,
            "Object Type":           obj_type,
            "Data Type":             dtype,
            "PLC Address":           plc_addr,
            "SCADA Address":         scada_addr,
            "Default Value":         default,
            "Loop":                  loop_val,
            "Min":                   min_val,
            "Max":                   max_val,
            "Unit":                  unit_val,
            "Drop":                  drop_val,
            "Rack":                  rack_val,
            "Slot":                  slot_val,
            "Channel":               chan_val,
        })
    return rows


def generate_exploded_view(sheets: dict) -> dict:
    master_params = _load_master_params(sheets)

    tagdb = _get_sheet(sheets, "TagDB")
    h     = _make_h(tagdb["headers"])

    required = {"TAG", "DESCRIPTION", "TYPE", "PLC ADDRESS", "SCADA ADDRESS"}
    missing  = required - set(h.keys())
    if missing:
        raise ValueError(f"TagDB is missing required columns: {sorted(missing)}")

    data_rows = [
        r for r in tagdb["rows"]
        if any(v is not None for v in r) and _get(r, h, "TAG") is not None
    ]

    groups    = {}
    seen      = set()
    row_order = []

    for raw in data_rows:
        tag_s  = str(_get(raw, h, "TAG")  or "").strip()
        type_s = str(_get(raw, h, "TYPE") or "").strip()
        if not tag_s: continue

        if _is_elementary(type_s):
            row_order.append(("elem", raw))
            continue

        suffix = _get_tag_suffix(tag_s)
        if suffix is None:
            if type_s.upper() in ("START", "END", ""):
                continue
            row_order.append(("elem", raw))
            continue

        base = tag_s[:-len(suffix)]
        if base not in groups:
            groups[base] = {}
        groups[base][suffix] = raw
        if base not in seen:
            seen.add(base)
            row_order.append(("group", base))

    output              = []
    unidentified_classes = set()

    for kind, payload in row_order:
        if kind == "elem":
            output.append(_row_elementary(payload, h))
        else:
            grp      = groups[payload]
            r_row    = grp.get("_R")
            w_row    = grp.get("_W")
            c_row    = grp.get("_C")
            r_type   = str(_get(r_row, h, "TYPE") or "").strip() if r_row else ""
            cls_name = _strip_suffix(_normalize_type(r_type))
            params   = master_params.get(cls_name)
            if not params:
                unidentified_classes.add(cls_name)
                for row in (r_row, w_row, c_row):
                    if row is not None:
                        output.append(_row_elementary(row, h))
            else:
                output.extend(_expand_group(payload, r_row, w_row, c_row, cls_name, params, h))

    rows_as_arrays = [[r.get(col) for col in EV_COLUMNS] for r in output]

    return {
        "sheet":    "ExplodedView",
        "headers":  EV_COLUMNS,
        "rows":     rows_as_arrays,
        "stats":    {
            "rows_written": len(output),
            "classes_used": len({r["Object Type"] for r in output if r.get("Object Type")}),
        },
        "warnings": sorted(unidentified_classes),
    }
