"""
conduit_fill.py — NEC Chapter 9 conduit-fill check (advisory).

Computes how full a conduit is the way NEC Chapter 9 does, and flags it for the engineer
only when it exceeds the allowable fill. This is an ADVISORY check that shows its work —
it never "approves" a conduit; it surfaces a number + the assumptions behind it so a human
can confirm against the real cable part numbers before sizing.

METHOD (NEC 2020 Chapter 9)
  fill% = (sum of each item's cross-sectional area) / (conduit internal area, Table 4),
  compared to the maximum fill from Table 1:
      1 conductor/cable -> 53%      exactly 2 -> 31%      3 or more -> 40%
  (The Table 1 Note 4 "nipple" 60% exception for sleeves <=24" is intentionally NOT applied
  — omitting it is the conservative choice.)

TWO KINDS OF ITEM
  * Individual insulated conductors (POWER / CONTROL / GROUND): area from Table 5 by gauge;
    each counts as one conductor (Wire_Count per row). The equipment grounding conductor
    counts toward fill like any other conductor.
  * Multi-conductor CABLES (TSP / CAT-x / FIBER): per NEC Ch.9 Table 1, Note 9, a cable of
    two or more conductors is treated as a SINGLE conductor whose area is a circle of the
    cable's OVERALL outside diameter, area = pi*(OD/2)^2 (for an elliptical jacket, Note 9
    uses the MAJOR diameter). So each cable counts as ONE item at its jacket OD — never its
    inner conductors, and never OD + inner conductors.

CABLE DIAMETERS — the one real assumption
  Note 9 says cable OD comes from the MANUFACTURER's spec, not an NEC table, because jacket
  dimensions vary by product. The workbook carries no part number, so the values below are
  documented typical single-cable ODs; a real design should confirm them against the actual
  part (override the table, or read them if the workbook ever carries an OD/part column):
      - TSP single shielded pair (nominal OD): #18 = 0.222" (Belden 8760),
        #16 = 0.313" (Belden 8719), #14 = 0.355" (Belden 8720).
      - CAT-5/5e/6/6A: 0.335"  (Southwire Cat6A 23 AWG / 4 pr).
      - FIBER: 0.30" representative (indoor/outdoor distribution; construction-dependent).
  IMPORTANT: multiple SEPARATE single-pair rows are counted as that many separate cables.
  If the design actually runs one MULTI-PAIR cable (all pairs under one jacket), it should
  be entered as one row — that is a much smaller single OD, so model it that way.

OTHER RULES
  * Individual-conductor areas are THHN/THWN (Table 5). #16 uses the TFFN fixture-wire area
    (THHN isn't tabulated below 14 AWG) — the standard small-signal conductor.
  * Any item with no usable gauge/OD (pull rope, N/A, unknown) is skipped and reported, so
    a reported fill is a lower bound when something was skipped.
  * A conduit whose type or trade size isn't in the NEC tables is NOT flagged (can't be
    evaluated) rather than guessed at.

NEC values below are the published Chapter 9 figures, verified against the printed tables.
Sources: NEC 2020 Ch.9 Tables 1 (+Note 9), 4, 5.
"""
import math

# ── NEC Ch.9 Table 5 — approximate area (sq in) of one THHN/THWN conductor by gauge.
# (#16 = TFFN fixture wire, the standard small-signal conductor; THHN starts at #14.)
_WIRE_AREA_SQIN = {
    "16": 0.0072, "14": 0.0097, "12": 0.0133, "10": 0.0211, "8": 0.0366,
    "6": 0.0507, "4": 0.0824, "3": 0.0973, "2": 0.1158, "1": 0.1562,
    "1/0": 0.1855, "2/0": 0.2223, "3/0": 0.2679, "4/0": 0.3237,
}

# ── Cable overall outside diameters (inches). A cable fills the conduit by its OD area
# and counts as ONE item (NEC Ch.9). Edit these to match the products actually used.
_CAT_OD_IN = 0.335    # CAT-5/5e/6/6A (Southwire Cat6A 23AWG/4pr; user-confirmed)
_FIBER_OD_IN = 0.30   # representative fiber distribution cable (construction-dependent)
# TSP (single shielded pair) OD by conductor gauge; falls back to _TSP_OD_DEFAULT.
_TSP_OD_BY_GAUGE_IN = {"18": 0.222, "16": 0.313, "14": 0.355}
_TSP_OD_DEFAULT_IN = 0.30


def _circle_area(od_in: float) -> float:
    return math.pi * (od_in / 2.0) ** 2

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


def _cable_od(wire_type: str, gauge_key):
    """Overall OD (inches) for a multi-conductor CABLE wire type, or None if this wire
    type isn't a cable (i.e. it's individual conductors sized by gauge)."""
    wt = str(wire_type or "").strip().upper()
    if wt.startswith("CAT"):          # CAT-5 / CAT-6 / CAT6A ...
        return _CAT_OD_IN
    if "FIBER" in wt or "FO" == wt:
        return _FIBER_OD_IN
    if "TSP" in wt or ("SHIELD" in wt and "PAIR" in wt):
        return _TSP_OD_BY_GAUGE_IN.get(gauge_key, _TSP_OD_DEFAULT_IN)
    return None


def _item_area(row):
    """What this fill row adds to the conduit, the NEC way.
    Returns (area_sqin, count, skipped, kind, spec):
      - a CABLE (TSP/CAT/FIBER) -> (OD-circle area, 1, 0, 'cable', od_in): one conductor at
        its jacket OD (NEC Ch.9 Table 1 Note 9).
      - individual conductors    -> (n * gauge area, n, 0, 'conductor', gauge).
      - unusable (no gauge/OD)    -> (0, 0, n, None, None): skipped, reported separately."""
    try:
        n = int(row.get("Wire_Count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 0.0, 0, 0, None, None
    gauge = _norm_gauge(row.get("Wire_Size_Raw"))
    od = _cable_od(row.get("Wire_Type"), gauge)
    if od is not None:
        return _circle_area(od), 1, 0, "cable", od       # a cable = one item at its OD
    if gauge is not None:
        return n * _WIRE_AREA_SQIN[gauge], n, 0, "conductor", gauge
    return 0.0, 0, n, None, None                          # not a known cable, no gauge -> skip


def evaluate(conduit_row: dict, fill_rows: list) -> dict | None:
    """Return the NEC fill report for this conduit, or None only if it CAN'T be evaluated
    (conduit type/size not in the NEC tables, or nothing to count). Never raises.

    Report dict (always returned when evaluable, over-fill or not):
      { conduit, conduit_type, conduit_size, items, cables, conductors, fill_area,
        allowed_area, conduit_area, fill_pct, allowed_pct, over (bool), skipped,
        message (str|None -- set iff over) }
    where `items` = NEC conductor count for the Table 1 rule (each cable counts as 1),
    `cables` = multi-conductor cables counted by jacket OD, `conductors` = individual
    conductors counted by gauge.
    """
    try:
        ctype = _norm_type(conduit_row.get("Cond_Type"))
        csize = _norm_size(conduit_row.get("Cond_Size"))
        if not ctype or csize not in _CONDUIT_AREA_SQIN.get(ctype, {}):
            return None   # type/size not in NEC tables -> can't evaluate
        conduit_area = _CONDUIT_AREA_SQIN[ctype][csize]

        items = 0        # NEC "number of conductors" for the fill-% rule (a cable counts 1)
        cables = 0       # multi-conductor cables (counted by jacket OD, Table 1 Note 9)
        conductors = 0   # individual conductors (counted by gauge, Table 5)
        skipped = 0
        fill_area = 0.0
        for r in fill_rows:
            area, cnt, skip, kind, _spec = _item_area(r)
            fill_area += area
            items += cnt
            skipped += skip
            if kind == "cable":
                cables += 1
            elif kind == "conductor":
                conductors += cnt

        if items == 0:
            return None   # nothing countable

        allowed_pct = _fill_pct(items)
        allowed_area = conduit_area * allowed_pct
        used_pct = (fill_area / conduit_area) * 100.0
        over = fill_area > allowed_area + 1e-9

        # Human-readable item breakdown, so the number isn't a black box.
        parts = []
        if cables:
            parts.append(f"{cables} cable(s) by jacket OD")
        if conductors:
            parts.append(f"{conductors} conductor(s) by gauge")
        item_desc = " + ".join(parts) if parts else f"{items} conductor(s)"

        tag = str(conduit_row.get("Cond_Tag") or "").strip()
        msg = None
        if over:
            msg = (
                f"Conduit '{tag}' over NEC fill: {used_pct:.0f}% of a {csize}\" {ctype} "
                f"({item_desc} = {items} conductors; NEC Ch.9 limit {allowed_pct*100:.0f}%). "
                f"Use a larger conduit or fewer/smaller cables."
            )
            if cables:
                msg += (" Each TSP/cable is counted as one conductor at its jacket OD (NEC "
                        "Table 1 Note 9); ODs are typical manufacturer values - confirm the "
                        "actual part number, and if the pairs share one multi-pair jacket "
                        "enter them as a single cable.")
            if skipped:
                msg += (f" {skipped} item(s) had no usable gauge/OD (pull rope/N-A) and "
                        f"weren't counted, so actual fill is higher.")
        return {
            "conduit": tag,
            "conduit_type": ctype,
            "conduit_size": csize,
            "items": items,
            "cables": cables,
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
