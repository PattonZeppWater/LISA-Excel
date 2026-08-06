"""note_refs.py — cross-conduit REF.DWG detection (pure, no AutoCAD)."""
from services import note_refs as nr
from services import autocad_bridge as ab


def _row(cond, side, sym, wtype, elem, loop, elnum, func):
    r = {"Cond_Tag": cond, "Wire_Type": wtype}
    pfx = "Src_" if side == "S" else "Dst_"
    r[pfx + "TermBlockDesc"] = sym
    if side == "S":
        r.update({"Src_ISAElem": elem, "Src_ISALoop": loop, "Src_ISAElemNum": elnum, "Loop_SrcDesc": func})
    else:
        r.update({"Dst_ISAElem": elem, "Dst_ISALoop": loop, "Dst_ISAElemNum": elnum, "Loop_DstDesc": func})
    return r


_CI = [{"Cond_Tag": "P100", "Cond_Ident": 1}, {"Cond_Tag": "P200", "Cond_Ident": 2}]


def _inst_loop(dst_sym, wtype):
    return {"src_block": None, "dst_block": dst_sym, "Wire_Type": wtype,
            "ISATag_ElementIdent": "AIT", "ISATag_LoopNum": "003",
            "ISATag_ElementNum": "003", "ISATag_FunctIdent": "AE"}


def test_power_drawing_points_to_tsp_counterpart():
    fill = [_row("P100", "D", "Inst_4W_R (Field_4Term)", "POWER", "AIT", "003", "003", "AE"),
            _row("P200", "D", "Inst_4W_R (Field_4Term)", "TSP",   "AIT", "003", "003", "AE")]
    loop = _inst_loop("Inst_4W_R", "POWER")
    nr.annotate_instrument_refs([loop], fill, _CI, "73.1111", "s", "P100")
    assert loop.get("ref_dwg") == "73.1111-D.02"   # counterpart P200 = index 1 -> seq 2 -> D.02


def test_tsp_drawing_points_to_power_counterpart():
    fill = [_row("P100", "D", "Inst_4W_R", "POWER", "AIT", "003", "003", "AE"),
            _row("P200", "D", "Inst_4W_R", "TSP",   "AIT", "003", "003", "AE")]
    loop = _inst_loop("Inst_4W_R", "TSP")
    nr.annotate_instrument_refs([loop], fill, _CI, "73.1111", "s", "P200")
    assert loop.get("ref_dwg") == "73.1111-D.01"   # counterpart P100 = index 0 -> seq 1 -> D.01


def test_counterpart_number_uses_seq_start_when_present():
    # Continuation sheets consume consecutive numbers, so a later conduit's real drawing
    # number (Seq_Start) is higher than its plain position. The REF must follow Seq_Start.
    ci = [{"Cond_Tag": "P100", "Cond_Ident": 1, "Seq_Start": 1, "Sheet_Count": 2},
          {"Cond_Tag": "P200", "Cond_Ident": 2, "Seq_Start": 3, "Sheet_Count": 1}]
    fill = [_row("P100", "D", "Inst_4W_R (Field_4Term)", "POWER", "AIT", "003", "003", "AE"),
            _row("P200", "D", "Inst_4W_R (Field_4Term)", "TSP",   "AIT", "003", "003", "AE")]
    loop = _inst_loop("Inst_4W_R", "POWER")
    nr.annotate_instrument_refs([loop], fill, ci, "73.1111", "s", "P100")
    assert loop.get("ref_dwg") == "73.1111-D.03"   # counterpart P200 Seq_Start=3 -> D.03, not position 2
    # falls back to 1-based position when Seq_Start is absent (older backend)
    assert nr.counterpart_dwg_number(_CI, "P200", "73.1111", "s") == "73.1111-D.02"


def test_non_4w_instrument_gets_no_ref():
    fill = [_row("P100", "D", "CB_R", "POWER", "", "", "", ""),
            _row("P200", "D", "CB_R", "TSP",   "", "", "", "")]
    loop = {"dst_block": "CB_R", "Wire_Type": "POWER"}
    nr.annotate_instrument_refs([loop], fill, _CI, "73.1111", "s", "P100")
    assert "ref_dwg" not in loop


def test_single_conduit_no_counterpart():
    fill = [_row("P100", "D", "Inst_4W_R", "POWER", "AIT", "003", "003", "AE")]
    loop = _inst_loop("Inst_4W_R", "POWER")
    nr.annotate_instrument_refs([loop], fill, [{"Cond_Tag": "P100", "Cond_Ident": 1}], "73.1111", "s", "P100")
    assert "ref_dwg" not in loop


def test_fallback_tag_name_when_no_project_number():
    fill = [_row("P100", "D", "Inst_4W_R", "POWER", "AIT", "003", "003", "AE"),
            _row("P200", "D", "Inst_4W_R", "TSP",   "AIT", "003", "003", "AE")]
    loop = _inst_loop("Inst_4W_R", "POWER")
    nr.annotate_instrument_refs([loop], fill, _CI, "", "e", "P100")
    assert loop.get("ref_dwg") == "P200"   # no project number -> tag-based name


def test_signal_side_non4w_symbol_still_gets_ref():
    # Same instrument: POWER conduit uses Inst_4W (a 4w symbol), SIGNAL conduit uses a
    # 2-wire instrument symbol (no '4w'). BOTH drawings must still get a note — the 4W
    # decision is per-instrument, not per-drawing-symbol. (Guards the real Inst_Shi bug.)
    fill = [_row("P01", "D", "Inst_4W_R (Field_4Term)",        "POWER", "AIT", "001", "001", "AE"),
            _row("S01", "D", "Inst_Sensor_2W_R (Field_2Term)", "TSP",   "AIT", "001", "001", "AE")]
    ci = [{"Cond_Tag": "P01", "Cond_Ident": 1}, {"Cond_Tag": "S01", "Cond_Ident": 2}]
    lp = {"dst_block": "Inst_4W_R", "Wire_Type": "POWER",
          "ISATag_ElementIdent": "AIT", "ISATag_LoopNum": "001", "ISATag_ElementNum": "001", "ISATag_FunctIdent": "AE"}
    nr.annotate_instrument_refs([lp], fill, ci, "73.1415", "e", "P01")
    assert lp.get("ref_dwg") == "73.1415-D.02"
    ls = {"dst_block": "Inst_Sensor_2W_R", "Wire_Type": "TSP",
          "ISATag_ElementIdent": "AIT", "ISATag_LoopNum": "001", "ISATag_ElementNum": "001", "ISATag_FunctIdent": "AE"}
    nr.annotate_instrument_refs([ls], fill, ci, "73.1415", "e", "S01")
    assert ls.get("ref_dwg") == "73.1415-D.01"   # signal side points back at the power drawing


def test_build_note_items_shapes():
    assert ab._build_note_items("R", True, 26.5, 12.0, "") == []      # no ref -> nothing
    # POWER drawing, R side: bracket beside signal terminals, rot 270
    items = ab._build_note_items("R", True, 26.5, 12.0, "73.1111-02s")
    assert len(items) == 2
    bracket, text = items
    assert bracket["name"] == ab.NOTE_BRACKET and bracket["kind"] == "block"
    assert bracket["rotation_deg"] == 270.0
    assert bracket["dyn_props"]["Flip state1"] == 0
    assert bracket["x"] == 26.0 and bracket["y"] == 11.75          # (26.5-0.5, 12.0-0.25)
    assert text["kind"] == "mtext" and text["text"] == "REF. DWG\\P73.1111-02s"   # 2-line
    assert text["attach"] == 6                                     # POWER -> middle-right
    assert bracket["note_group"] == text["note_group"]            # grouped together
    # left-side POWER instrument gets the flipped bracket + middle-left text
    lb, lt = ab._build_note_items("L", True, 6.5, 12.0, "73.1111-02s")
    assert lb["dyn_props"]["Flip state1"] == 1 and lt["attach"] == 4
    # TSP drawing, R side: bracket above power terminals, rot 180, text bottom-center
    b2, t2 = ab._build_note_items("R", False, 26.5, 12.0, "73.1111-01s")
    assert b2["rotation_deg"] == 180.0 and b2["y"] == 13.25 and t2["attach"] == 8


def test_build_note_items_4term_power_centers_and_widens():
    # A 4-terminal POWER instrument: bracket + text drop to the centre of the 4 stacked
    # signal terminals (inst_y - 0.25*(4-1) = -0.75) and the span widens to 2.5 to enclose
    # the taller stack. 2-term stays at -0.25 / 2.0 (the reference geometry).
    b2, t2 = ab._build_note_items("R", True, 26.5, 12.0, "73.1111-02s", n_terms=2)
    assert b2["y"] == 11.75 and t2["y"] == 11.75            # inst_y - 0.25
    assert b2["dyn_props"]["Distance1"] == 2.0
    b4, t4 = ab._build_note_items("R", True, 26.5, 12.0, "73.1111-02s", n_terms=4)
    assert b4["y"] == 11.25 and t4["y"] == 11.25            # inst_y - 0.75 (centre of 4 terms)
    assert b4["dyn_props"]["Distance1"] == 2.5              # 0.5*4 + 0.5
    assert b4["x"] == 26.0                                  # horizontal offset unchanged
    # left side keeps its flip + compensated x-offset, just re-centred vertically
    lb4, lt4 = ab._build_note_items("L", True, 6.5, 12.0, "73.1111-02s", n_terms=4)
    assert lb4["dyn_props"]["Flip state1"] == 1 and lb4["y"] == 11.25
    assert lb4["dyn_props"]["Distance1"] == 2.5
    # the TSP-side note (rot 180) is a fixed 2-terminal bracket regardless of n_terms
    bt, _ = ab._build_note_items("R", False, 26.5, 12.0, "73.1111-01s", n_terms=4)
    assert bt["y"] == 13.25 and bt["dyn_props"]["Distance1"] == 2.0
