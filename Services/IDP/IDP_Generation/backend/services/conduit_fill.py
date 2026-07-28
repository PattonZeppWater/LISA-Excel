"""
conduit_fill.py — NEC Chapter 9 conduit-fill check (advisory).

Computes how full a conduit is straight from the published NEC Chapter 9 tables, and flags
it for the engineer only when it exceeds the allowable fill. Advisory: it surfaces the
number, never "approves" a conduit.

METHOD (NEC 2020 Chapter 9 — tables only, no manufacturer assumptions)
  fill% = (sum of each conductor's cross-sectional area, Table 5) / (conduit internal area,
  Table 4), compared to the maximum fill from Table 1:
      1 conductor -> 53%      exactly 2 -> 31%      3 or more -> 40%

  Every conductor is counted individually by its gauge from NEC Ch.9 Table 5 (THHN/THWN):
  a fill row contributes Wire_Count conductors of its gauge. A twisted pair therefore counts
  as its two conductors by gauge, a triad as three, etc.

  NOTE ON MULTICONDUCTOR CABLES: NEC Ch.9 Table 1, Note 9 permits treating a jacketed cable
  as ONE conductor sized by its overall OD, but that OD is manufacturer data (varies by part)
  and is not in any NEC table. By request, this check does NOT use assumed cable ODs — it
  counts conductors by their NEC Table 5 gauge only. For a genuinely jacketed cable this is a
  lower bound (it omits the jacket/shield), so confirm sizing against the real cable where a
  conduit is near the limit.

  Any item with no usable gauge (CAT/FIBER/pull rope/N-A) is skipped and reported, so a
  reported fill is a lower bound whenever something was skipped. A conduit whose type or
  trade size isn't in the NEC tables is not flagged (can't be evaluated) rather than guessed.

Sources: NEC 2020 Ch.9 Tables 1, 4, 5. #16 uses the TFFN fixture-wire area (THHN isn't
tabulated below 14 AWG). Values verified against the printed tables.
"""

# ── NEC Ch.9 Table 5 — approximate area (sq in) of one THHN/THWN conductor by gauge.
# (#16 = TFFN fixture wire, the standard small-signal conductor; THHN starts at #14.)
_WIRE_AREA_SQIN = {
    "16": 0.0072, "14": 0.0097, "12": 0.0133, "10": 0.0211, "8": 0.0366,
    "6": 0.0507, "4": 0.0824, "3": 0.0973, "2": 0.1158, "1": 0.1562,
    "1/0": 0.1855, "2/0": 0.2223, "3/0": 0.2679, "4/0": 0.3237,
}

# ── NEC Ch.9 Table 4 — total (100%) internal area (sq in) per conduit type + trade size.
_CONDUIT_AREA_SQIN = {
    "RMC": {  # Rigid Metal Conduit (also RGS — rigid galvanized steel)
        "1/2": 0.314, "3/4": 0.549, "1": 0.887, "1 1/4": 1.526, "1 1/2": 2.071,
        "2": 3.408, "2 1/2": 4.866, "3": 7.499, "3 1/2": 10.010, "4": 12.882,
    },
    "FMC": {  # Flexible Metal Conduit (FLEX)
        "3/8": 0.116, "1/2": 0.317, "3/4": 0.533, "1": 0.817, "1 1/4": 1.277,
        "1 1/2": 1.858, "2": 3.269, "2 1/2": 4.909, "3": 7.069, "3 1/2": 9.621, "4": 12.566,
    },
    "EMT": {  # Electrical Metallic Tubing
        "1/2": 0.304, "3/4": 0.533, "1": 0.864, "1 1/4": 1.496, "1 1/2": 2.036,
        "2": 3.356, "2 1/2": 5.858, "3": 8.846, "3 1/2": 11.545, "4": 14.753,
    },
    "IMC": {  # Intermediate Metal Conduit
        "1/2": 0.342, "3/4": 0.586, "1": 0.959, "1 1/4": 1.647, "1 1/2": 2.225,
        "2": 3.630, "2 1/2": 5.135, "3": 7.922, "3 1/2": 10.584, "4": 13.631,
    },
    "PVC": {  # Rigid PVC, Schedule 40 (most common)
        "1/2": 0.285, "3/4": 0.508, "1": 0.832, "1 1/4": 1.453, "1 1/2": 1.986,
        "2": 3.291, "2 1/2": 4.695, "3": 7.268, "3 1/2": 9.737, "4": 12.554,
    },
}


def _fill_pct(n_conductors: int) -> float:
    """NEC Ch.9 Table 1 allowable fill fraction by conductor count."""
    if n_conductors <= 1:
        return 0.53
    if n_conductors == 2:
        return 0.31
    return 0.40


def _norm_size(raw) -> str:
    """Workbook conduit size ('2 1/2\"', '3/4\"', '2-1/2') -> trade-size key ('2 1/2')."""
    s = str(raw or "").strip().replace('"', "").replace("''", "").replace("-", " ")
    return " ".join(s.split())


def _norm_type(raw):
    """Workbook conduit type -> NEC table key, or None if not determinable.
    A combined spec (e.g. 'PVC/RGS', 'RMC-PVC'), 'PER SPEC', 'PCS', 'XXX' or blank is
    left unknown so the conduit simply isn't flagged rather than mis-sized."""
    s = str(raw or "").strip().upper()
    if not s:
        return None
    # Combined / spec-dependent types: can't pick one internal area -> don't evaluate.
    if "/" in s or ("PVC" in s and ("RGS" in s or "RMC" in s)):
        return None
    if "FLEX" in s or "FMC" in s:
        return "FMC"
    if "EMT" in s:
        return "EMT"
    if "IMC" in s:
        return "IMC"
    if "RMC" in s or "RGS" in s:
        return "RMC"
    if "PVC" in s:
        return "PVC"
    return None


def _norm_gauge(raw):
    """'#16', '3/0', '#3/0 ' -> key into _WIRE_AREA_SQIN, or None (N/A / unknown)."""
    s = str(raw or "").strip().upper().lstrip("#").strip()
    if not s or s in ("N/A", "NA", "NONE"):
        return None
    return s if s in _WIRE_AREA_SQIN else None


def _item_area(row):
    """What this fill row adds to the conduit: (area_sqin, conductors, skipped).
    Every conductor is counted by its NEC Table 5 gauge area (Wire_Count of them per row).
    A row with no usable gauge (CAT/FIBER/pull rope/N-A) is skipped and reported."""
    try:
        n = int(row.get("Wire_Count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 0.0, 0, 0
    gauge = _norm_gauge(row.get("Wire_Size_Raw"))
    if gauge is not None:
        return n * _WIRE_AREA_SQIN[gauge], n, 0
    return 0.0, 0, n                          # no usable gauge -> skip (reported)


def evaluate(conduit_row: dict, fill_rows: list) -> dict | None:
    """Return the NEC fill report for this conduit, or None only if it CAN'T be evaluated
    (conduit type/size not in the NEC tables, or nothing to count). Never raises.

    Report dict (always returned when evaluable, over-fill or not):
      { conduit, conduit_type, conduit_size, conductors, fill_area, allowed_area,
        conduit_area, fill_pct, allowed_pct, over (bool), skipped, message (str|None) }
    """
    try:
        ctype = _norm_type(conduit_row.get("Cond_Type"))
        csize = _norm_size(conduit_row.get("Cond_Size"))
        if not ctype or csize not in _CONDUIT_AREA_SQIN.get(ctype, {}):
            return None   # type/size not in NEC tables -> can't evaluate
        conduit_area = _CONDUIT_AREA_SQIN[ctype][csize]

        conductors = 0   # every conductor counted by its NEC Table 5 gauge
        skipped = 0
        fill_area = 0.0
        for r in fill_rows:
            area, cnt, skip = _item_area(r)
            fill_area += area
            conductors += cnt
            skipped += skip

        if conductors == 0:
            return None   # nothing countable

        allowed_pct = _fill_pct(conductors)
        allowed_area = conduit_area * allowed_pct
        used_pct = (fill_area / conduit_area) * 100.0
        over = fill_area > allowed_area + 1e-9

        tag = str(conduit_row.get("Cond_Tag") or "").strip()
        msg = None
        if over:
            msg = (
                f"Conduit '{tag}' over NEC fill: {used_pct:.0f}% of a {csize}\" {ctype} "
                f"({conductors} conductors by NEC Table 5 gauge; NEC Ch.9 limit "
                f"{allowed_pct*100:.0f}%). Use a larger conduit or fewer/smaller conductors."
            )
            if skipped:
                msg += (f" {skipped} item(s) had no usable gauge (CAT/FIBER/pull rope/N-A) "
                        f"and weren't counted, so actual fill is higher.")
        return {
            "conduit": tag,
            "conduit_type": ctype,
            "conduit_size": csize,
            "conductors": conductors,
            "conduit_area": round(conduit_area, 4),
            "fill_area": round(fill_area, 4),
            "allowed_area": round(allowed_area, 4),
            "fill_pct": round(used_pct, 1),
            "allowed_pct": round(allowed_pct * 100, 1),
            "over": over,
            "skipped": skipped,
            "message": msg,
        }
    except Exception:
        return None


def annotate_conduits(conduit_index: list, fill_index: list) -> None:
    """Annotate each conduit row with the fill result, in place. Never raises.
      Fill_Pct     -> number (percent of conduit area filled) or None if not evaluable
      Fill_Over    -> True when over the NEC limit (the 'dangerous' case), else False
      Fill_Warning -> the over-fill message (str) or None
    Lets the frontend show the % next to Generate for every conduit, and highlight/alert
    only the over-fill ones."""
    try:
        by_tag = {}
        for r in fill_index:
            t = str(r.get("Cond_Tag") or "").strip()
            if t:
                by_tag.setdefault(t, []).append(r)
        for c in conduit_index:
            tag = str(c.get("Cond_Tag") or "").strip()
            report = evaluate(c, by_tag.get(tag, [])) if tag else None
            if report:
                c["Fill_Pct"] = report["fill_pct"]
                c["Fill_Over"] = report["over"]
                c["Fill_Warning"] = report["message"]
            else:
                c["Fill_Pct"] = None
                c["Fill_Over"] = False
                c["Fill_Warning"] = None
    except Exception:
        pass
