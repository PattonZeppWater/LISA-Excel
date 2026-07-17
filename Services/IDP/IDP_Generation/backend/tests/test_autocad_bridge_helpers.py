"""autocad_bridge.py PURE-helper tests (no AutoCAD): spacing, heights, attrs."""
from services import autocad_bridge as ab


# ── grid spacing math ────────────────────────────────────────────────────────

def test_round_up_step_increments():
    assert ab._round_up_step(0.3) == 0.5    # short -> one step
    assert ab._round_up_step(0.5) == 0.5
    assert ab._round_up_step(0.6) == 1.0    # tall -> next 0.5
    assert ab._round_up_step(1.0) == 1.0
    assert ab._round_up_step(1.9382) == 2.0
    assert ab._round_up_step(3.7769) == 4.0


# ── BlockIndex height lookup ─────────────────────────────────────────────────

def test_bi_height_by_name_and_vis():
    bh = {"ANT_L|NA": 0.6, "ANT_L": 0.6, "INST_X|FIELD_4TERM": 3.5, "INST_X": 3.5}
    assert ab._bi_height(bh, "ANT_L", "NA") == 0.6
    assert ab._bi_height(bh, "Inst_X", "Field_4Term") == 3.5
    assert ab._bi_height(bh, "Inst_X", None) == 3.5      # name-only fallback
    assert ab._bi_height(bh, "Unknown", "NA") == 0.0     # missing -> 0
    assert ab._bi_height(None, "ANT_L", "NA") == 0.0     # no map -> 0


# ── hidden-term blanking ─────────────────────────────────────────────────────

def test_maybe_hide_terms_blanks_sentinel_preserves_slots():
    attrs = {"Term01": "5", "Term02": ab._HIDE_TERM_SENTINEL, "Term03": "9", "Term04": ""}
    out = ab._maybe_hide_terms(dict(attrs), {})
    assert out["Term01"] == "5"
    assert out["Term02"] == ""        # hidden -> blank, slot kept
    assert out["Term03"] == "9"


# ── tag attrs blank unused slots (clears block default placeholder) ──────────

def test_build_dst_attrs_blanks_unused_tags():
    a = ab._build_dst_attrs({"Wire1_DstTag": "VFD-2"})
    assert a["Tag1"] == "VFD-2"
    assert a["Tag2"] == "" and a["Tag3"] == "" and a["Tag4"] == ""


# ── tag collapse / blank ─────────────────────────────────────────────────────

def test_collapse_blanks_unused_slots():
    # only Tag1 filled -> rest must be "" (blank) to overwrite the block default
    assert ab._collapse_tags(["VFD-2", None, None, None]) == ["VFD-2", "", "", ""]

def test_collapse_identical_tags():
    assert ab._collapse_tags(["DISC-5", "DISC-5", "DISC-5", None]) == ["DISC-5", "", "", ""]

def test_collapse_distinct_tags_preserved():
    assert ab._collapse_tags(["A", "B", None, None]) == ["A", "B", "", ""]


# ── instrument ISA carried onto block attrs ─────────────────────────────────

def test_build_src_attrs_carries_isa():
    a = ab._build_src_attrs({
        "ISATag_FunctIdent": "AE", "ISATag_ElementIdent": "AIT",
        "ISATag_ElementNum": "003", "ISATag_LoopNum": "119",
    })
    assert a["ISATag_FunctIdent"] == "AE"
    assert a["ISATag_ElementIdent"] == "AIT"
    assert a["ISATag_ElementNum"] == "003"
    assert a["ISATag_LoopNum"] == "119"
