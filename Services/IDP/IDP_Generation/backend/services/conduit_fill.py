"""
conduit_fill.py — NEC Chapter 9 conduit-fill check.

Flags a conduit that carries more wire than its size should, the way an electrical
engineer would size it: sum each conductor's cross-sectional area (NEC Ch.9 Table 5)
and compare against the conduit's allowable fill — a percentage of the conduit's own
internal area (NEC Ch.9 Table 4), where the percentage comes from NEC Ch.9 Table 1:

    1 conductor  -> 53%      2 conductors -> 31%      3 or more -> 40%

Assumptions (documented on purpose, since the workbook doesn't carry them):
  * Conductor insulation is THHN/THWN (the standard building-wire insulation for these
    gauges in conduit). #16 uses the TFFN fixture-wire area (THHN isn't listed below 14).
  * Multi-conductor cables (TSP / MFG_CABLE / FIBER / CAT-6) don't have a single building-
    wire gauge, so their conductors are counted by gauge as an approximation — the cable
    jacket is NOT added, so the estimate runs slightly LOW for cable-heavy conduits. Any
    conductor with no usable gauge (N/A, pullrope, unknown) is skipped and reported.
  * A conduit whose type or trade size isn't in the NEC tables below is NOT flagged
    (can't be evaluated) rather than guessed at.

All values are the published NEC Chapter 9 figures (2011 tables; unchanged in later
editions for these sizes), verified against the printed tables.
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


def evaluate(conduit_row: dict, fill_rows: list) -> dict | None:
    """Return an over-fill report dict for this conduit, or None if it's within fill
    (or can't be evaluated). Never raises.

    Report dict:
      { conduit, conduit_type, conduit_size, conductors, fill_area, allowed_area,
        conduit_area, fill_pct, allowed_pct, skipped_conductors, message }
    """
    try:
        ctype = _norm_type(conduit_row.get("Cond_Type"))
        csize = _norm_size(conduit_row.get("Cond_Size"))
        if not ctype or csize not in _CONDUIT_AREA_SQIN.get(ctype, {}):
            return None   # type/size not in NEC tables -> can't evaluate, don't flag
        conduit_area = _CONDUIT_AREA_SQIN[ctype][csize]

        conductors = 0
        skipped = 0
        fill_area = 0.0
        for r in fill_rows:
            try:
                n = int(r.get("Wire_Count") or 0)
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                continue
            g = _norm_gauge(r.get("Wire_Size_Raw"))
            if g is None:
                skipped += n
                continue
            conductors += n
            fill_area += n * _WIRE_AREA_SQIN[g]

        if conductors == 0:
            return None

        allowed_pct = _fill_pct(conductors)
        allowed_area = conduit_area * allowed_pct
        if fill_area <= allowed_area + 1e-9:
            return None

        tag = str(conduit_row.get("Cond_Tag") or "").strip()
        used_pct = (fill_area / conduit_area) * 100.0
        msg = (
            f"Conduit '{tag}' is over NEC fill: {conductors} conductor(s) fill "
            f"{used_pct:.0f}% of a {csize}\" {ctype} conduit "
            f"(NEC limit {allowed_pct*100:.0f}% for {conductors} conductors). "
            f"Use a larger conduit or fewer/smaller wires."
        )
        if skipped:
            msg += (f" Note: {skipped} conductor(s) had no usable gauge "
                    f"(cable/pullrope/N-A) and weren't counted, so actual fill is higher.")
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
            "skipped_conductors": skipped,
            "message": msg,
        }
    except Exception:
        return None


def annotate_conduits(conduit_index: list, fill_index: list) -> None:
    """Set conduit_row['Fill_Warning'] = message (str) on every over-fill conduit, else
    None. Mutates conduit_index in place. Never raises. Lets the frontend highlight
    over-fill conduits and warn before generation."""
    try:
        by_tag = {}
        for r in fill_index:
            t = str(r.get("Cond_Tag") or "").strip()
            if t:
                by_tag.setdefault(t, []).append(r)
        for c in conduit_index:
            tag = str(c.get("Cond_Tag") or "").strip()
            report = evaluate(c, by_tag.get(tag, [])) if tag else None
            c["Fill_Warning"] = report["message"] if report else None
    except Exception:
        pass
