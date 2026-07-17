"""parser.py regression tests (continuation grouping, ISA mapping, hide tokens)."""
from services import parser
from conftest import fill_row


def _count_groups(loops):
    """Mimic the generator's grouping: an anchor plus its following
    is_continuation rows form one group (= one instrument inserted)."""
    n, i, groups = len(loops), 0, []
    while i < n:
        j = i + 1
        while j < n and loops[j].get("is_continuation"):
            j += 1
        groups.append(loops[i:j])
        i = j
    return groups


def test_duplicate_instruments_split_into_two_groups(dup_instrument):
    """Two pasted identical 4-term loops must NOT merge into one group, or the
    2nd instrument never inserts and its wires lose colour (the bug we fixed)."""
    rows = parser.get_fill_rows(dup_instrument["fill_index"], "S01AIT003")
    loops = parser.build_loop_list(rows)
    groups = _count_groups(loops)
    assert len(groups) == 2, f"expected 2 instrument groups, got {len(groups)}"
    # each group is anchor + 1 continuation, and each anchor carries its colours
    for g in groups:
        assert len(g) == 2
        assert [g[0].get(f"Wire{n}_Color") for n in range(1, 5)] == ["RED", "BLK", "RED", "BLK"]
        assert g[1].get("is_continuation") is True


def test_continuation_row_without_colors_is_grouped():
    """A same-instrument row with no colours of its own IS a continuation."""
    rows = [
        fill_row(Dst_TermBlockDesc="Inst_X (Field_4Term)", Wire_Count=2,
                 Wire1_Color="RED", Wire2_Color="BLK"),
        fill_row(Dst_TermBlockDesc="Inst_X (Field_4Term)", Wire_Count=2),
    ]
    loops = parser.build_loop_list(rows)
    assert loops[1].get("is_continuation") is True
    assert len(_count_groups(loops)) == 1


def test_instrument_isa_functident_is_functionid():
    """FunctionID fills the FUNCTION bubble, ElementID the ELEMENT bubble
    (both were wrongly set to ElementID before)."""
    rows = [fill_row(
        Dst_TermBlockDesc="Inst_Sensor (Field_4Term)",
        Src_ISAElem=None, Loop_DstDesc="AE",         # FunctionID arrives as Loop_DstDesc
        Dst_ISAElem="AIT", Dst_ISALoop="119", Dst_ISAElemNum="003",
    )]
    L = parser.build_loop_list(rows)[0]
    assert L["ISATag_FunctIdent"] == "AE"
    assert L["ISATag_ElementIdent"] == "AIT"
    assert L["ISATag_ElementNum"] == "003"
    assert L["ISATag_LoopNum"] == "119"


def test_hidden_term_gets_sentinel():
    rows = [fill_row(Wire1_DstTermNum="5", Hidden_Terms="D1")]
    L = parser.build_loop_list(rows)[0]
    assert L["Wire1_DstTermNum"] == "##HIDETERM##"


def test_hidden_tag_is_blanked():
    rows = [fill_row(Wire1_DstTag="VFD-2", Hidden_Terms="DG1")]
    L = parser.build_loop_list(rows)[0]
    assert L["Wire1_DstTag"] == ""


def test_non_hidden_term_untouched():
    rows = [fill_row(Wire1_DstTermNum="5")]
    L = parser.build_loop_list(rows)[0]
    assert L["Wire1_DstTermNum"] == "5"
