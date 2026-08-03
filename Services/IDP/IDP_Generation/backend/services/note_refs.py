"""
note_refs.py — cross-conduit "REF. DWG" note detection (PURE, no AutoCAD).

A 4-wire instrument (symbol contains "4W", e.g. Inst_4W_L/R) usually has its landing
wires split across TWO conduits: one carries the POWER pair, the other the TSP/signal
pair. On each of those drawings we want a NOTES flag + a "REF. DWG / <number>" callout
that points at the OTHER conduit's drawing, so a reader can find the counterpart wire.

"Same instrument" is identified by the ISA-tag tuple (ElementID, Loop#, Element#,
FunctionID) read from whichever side (S/D) carries the Inst_* symbol — the fields the
user confirmed match between the two conduits. This module is pure: it annotates each
qualifying loop dict with loop["ref_dwg"] = the counterpart drawing number (a string like
"73.1111-D.05"); placement/COM lives in autocad_bridge. Nothing here touches the workbook.
"""

from . import wdp_writer


def _norm(s) -> str:
    return str(s or "").strip().upper()


def _row_inst_side(row) -> str | None:
    """'S' or 'D' — whichever side of a raw fill row holds an Inst_* symbol, else None."""
    if "inst" in str(row.get("Src_TermBlockDesc") or "").lower():
        return "S"
    if "inst" in str(row.get("Dst_TermBlockDesc") or "").lower():
        return "D"
    return None


def _row_isa_key(row):
    """ISA identity tuple for the instrument on a raw fill row, or None if not an
    instrument / no ISA data. (ElementID, Loop#, Element#, FunctionID)."""
    side = _row_inst_side(row)
    if not side:
        return None
    if side == "S":
        key = (_norm(row.get("Src_ISAElem")), _norm(row.get("Src_ISALoop")),
               _norm(row.get("Src_ISAElemNum")), _norm(row.get("Loop_SrcDesc")))
    else:
        key = (_norm(row.get("Dst_ISAElem")), _norm(row.get("Dst_ISALoop")),
               _norm(row.get("Dst_ISAElemNum")), _norm(row.get("Loop_DstDesc")))
    return key if any(key) else None


def _loop_isa_key(loop):
    """Same ISA tuple, read from a build_loop_list loop dict (it copies the instrument-
    side ISA into ISATag_* keys)."""
    key = (_norm(loop.get("ISATag_ElementIdent")), _norm(loop.get("ISATag_LoopNum")),
           _norm(loop.get("ISATag_ElementNum")), _norm(loop.get("ISATag_FunctIdent")))
    return key if any(key) else None


def _loop_is_4w_instrument(loop) -> bool:
    """True when the loop's instrument symbol contains '4w' (Inst_4W_*, Inst_Sensor_4W_*)."""
    for k in ("src_block", "dst_block"):
        s = str(loop.get(k) or "").lower()
        if "inst" in s and "4w" in s:
            return True
    return False


def counterpart_dwg_number(conduit_index, cond_tag, project_number, file_suffix=None) -> str:
    """The drawing NAME the counterpart conduit's FIRST sheet is actually saved as — i.e. the
    real sheet name the REF. DWG callout should show.

    It asks wdp_writer.conduit_drawing_no for that name — the SAME single source of truth
    generate.py uses to NAME the .dwg files — rather than re-deriving the number with its own
    format. That is what keeps the reference correct across a numbering-scheme change: the
    callout is always literally the counterpart sheet's name (currently IC.EDC.S011
    "<project>-D.NN"), never a separately-formatted guess that can drift out of sync.

    `seq` is the conduit's project-sequential Seq_Start (continuation sheets consume consecutive
    numbers, so a multi-sheet conduit at D.15/D.16 pushes the next conduit to D.17). Falls back
    to 1-based position if an older backend didn't stamp Seq_Start, and to the sanitized Cond_Tag
    for a non-project generate (where generate.py names the file "<Cond_Tag>.dwg"). `file_suffix`
    is kept for call-site compatibility but the S011 drawing name does not use it."""
    row, idx = None, None
    for j, r in enumerate(conduit_index or []):
        if str(r.get("Cond_Tag") or "").strip() == str(cond_tag).strip():
            row, idx = r, j
            break
    project_number = str(project_number or "").strip()
    if project_number and row is not None:
        try:
            seq = int(row.get("Seq_Start"))
        except (TypeError, ValueError):
            seq = idx + 1
        return wdp_writer.conduit_drawing_no(project_number, seq)
    return "".join(c for c in str(cond_tag) if c.isalnum() or c in "-_.")


def annotate_instrument_refs(loop_list, fill_index, conduit_index,
                             project_number, file_suffix, current_cond_tag):
    """Set loop['ref_dwg'] on each 4-wire-instrument loop in loop_list that has a
    power/TSP counterpart in ANOTHER conduit. Mutates and returns loop_list. Purely
    additive: loops that don't qualify are left untouched (no 'ref_dwg' key), so drawings
    without a split 4W instrument are completely unaffected."""
    current_cond_tag = str(current_cond_tag or "").strip()

    # Scan the WHOLE fill_index (all conduits) once:
    #   occ:     isa_key -> { cond_tag: is_power }
    #   four_w:  set of isa_keys whose instrument is a 4-wire type — determined by the
    #            INSTRUMENT (any occurrence with a "4w" symbol), NOT per drawing. The two
    #            conduits of a 4W instrument can use different symbols (e.g. Inst_4W on the
    #            power side, a 2-wire symbol on the signal side), so gating per-loop on "4w"
    #            would miss the signal drawing. Keying off the instrument catches both.
    occ = {}
    four_w = set()
    for row in (fill_index or []):
        key = _row_isa_key(row)
        if not key:
            continue
        ct = str(row.get("Cond_Tag") or "").strip()
        if not ct:
            continue
        is_pw = "power" in str(row.get("Wire_Type") or "").lower()
        d = occ.setdefault(key, {})
        d[ct] = d.get(ct, False) or is_pw          # any power row on that conduit => power
        side = _row_inst_side(row)
        sym = str(row.get("Src_TermBlockDesc" if side == "S" else "Dst_TermBlockDesc") or "").lower()
        if "4w" in sym:
            four_w.add(key)

    for loop in (loop_list or []):
        key = _loop_isa_key(loop)
        if not key or key not in four_w:
            continue
        conduits = occ.get(key)
        if not conduits:
            continue
        here_pw = conduits.get(current_cond_tag)
        if here_pw is None:
            here_pw = "power" in str(loop.get("Wire_Type") or "").lower()

        # Prefer a counterpart conduit of the OPPOSITE power-ness (the power<->TSP split);
        # fall back to any other conduit carrying the same instrument.
        counterpart = None
        for ct, pw in conduits.items():
            if ct != current_cond_tag and pw != here_pw:
                counterpart = ct
                break
        if counterpart is None:
            for ct in conduits:
                if ct != current_cond_tag:
                    counterpart = ct
                    break
        if counterpart is None:
            continue

        loop["ref_dwg"] = counterpart_dwg_number(
            conduit_index, counterpart, project_number, file_suffix)
    return loop_list
