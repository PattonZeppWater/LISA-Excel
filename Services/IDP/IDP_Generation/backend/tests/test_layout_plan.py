"""Slice 2 — end-to-end layout assertions against build_layout_plan (no AutoCAD).

The plan is exactly what render_plan draws, so asserting it == asserting the DWG
output (block count, positions, colours, attrs, spacing) without screenshots.
"""
from services import autocad_bridge as ab
from services import parser


def _items(plan, role):
    return [it for it in plan["items"] if it["role"] == role]


def _on_half_grid(y):
    return abs(y / 0.5 - round(y / 0.5)) < 1e-9


# ── the bug that started Slice 2: duplicate instruments ──────────────────────

def test_duplicate_instruments_produce_two_instrument_blocks(dup_instrument):
    rows = parser.get_fill_rows(dup_instrument["fill_index"], "S01AIT003")
    loops = parser.build_loop_list(rows)
    plan = ab.build_layout_plan({"Cdt_Tag": "S01AIT003"}, loops,
                                dup_instrument.get("block_heights"))
    insts = _items(plan, "instrument")
    assert len(insts) == 2, f"expected 2 instrument blocks, got {len(insts)}"


def test_every_wire_has_a_color(dup_instrument):
    rows = parser.get_fill_rows(dup_instrument["fill_index"], "S01AIT003")
    loops = parser.build_loop_list(rows)
    plan = ab.build_layout_plan({}, loops, dup_instrument.get("block_heights"))
    wires = _items(plan, "wire")
    assert wires, "no wires in plan"
    missing = [w for w in wires if not w["attrs"].get("Src_Color")]
    assert not missing, f"{len(missing)} wire(s) have no colour"


def test_all_blocks_land_on_half_grid(dup_instrument):
    rows = parser.get_fill_rows(dup_instrument["fill_index"], "S01AIT003")
    loops = parser.build_loop_list(rows)
    plan = ab.build_layout_plan({}, loops, dup_instrument.get("block_heights"))
    off = [it for it in plan["items"] if not _on_half_grid(it["y"])]
    assert not off, f"{len(off)} block(s) off the 0.5 grid: {[(o['role'], o['y']) for o in off][:5]}"


# ── conduit + simple structure ───────────────────────────────────────────────

def test_conduit_present_with_attrs():
    plan = ab.build_layout_plan({"Cdt_Tag": "C1", "Cdt_Size": "2\""}, [], {})
    assert plan["conduit"]["name"] == "Conduit"
    assert plan["conduit"]["attrs"]["Cdt_Tag"] == "C1"
    assert plan["items"] == []


def test_simple_tb_loop_shape():
    loops = [{"src_block": "TB-TB_Square_L", "dst_block": "TB-TB_Square_R",
              "Wire_Count": 2, "Wire1_Color": "RED", "Wire2_Color": "BLK"}]
    plan = ab.build_layout_plan({}, loops, {})
    assert len(_items(plan, "wire")) == 2
    assert len(_items(plan, "src")) == 1
    assert len(_items(plan, "dst")) == 1
    colors = [w["attrs"]["Src_Color"] for w in _items(plan, "wire")]
    assert colors == ["RED", "BLK"]


# ── spacing on the 0.5 grid ──────────────────────────────────────────────────

def test_short_loops_spaced_one_step():
    """With no block heights, two single-wire loops sit 0.5 apart."""
    loops = [
        {"src_block": "TB_L", "dst_block": "TB_R", "Wire_Count": 1, "Cond_Tag": "C1"},
        {"src_block": "TB_L", "dst_block": "TB_R", "Wire_Count": 1, "Cond_Tag": "C2"},
    ]
    plan = ab.build_layout_plan({}, loops, {})
    wires = _items(plan, "wire")
    assert wires[0]["y"] == ab.START_Y
    assert wires[1]["y"] == ab.START_Y - 0.5


def test_tall_block_rounds_spacing_up():
    """A genuinely tall block (height 1.94) over a single wire row still rounds the
    spacing UP to the next 0.5 grid line: 1.94 (minus the small art margin) -> 2.0."""
    bh = {"BIG_L|NA": 1.94, "BIG_L": 1.94}
    loops = [
        {"src_block": "BIG_L", "src_block_visibility": "NA", "dst_block": "TB_R",
         "Wire_Count": 1, "Cond_Tag": "C1"},
        {"src_block": "TB_L", "dst_block": "TB_R", "Wire_Count": 1, "Cond_Tag": "C2"},
    ]
    plan = ab.build_layout_plan({}, loops, bh)
    second_wire = _items(plan, "wire")[1]
    assert second_wire["y"] == ab.START_Y - 2.0


def test_multiposition_block_spaces_by_its_rows_not_art_height():
    """Regression for the 'unnecessary gap' report (71.1214-02e): a 3-position breaker
    (1.52 tall) over a 3-wire row spans exactly 3 grid rows, so the next group (GND)
    sits 0.5 below the last wire -- at START_Y - 1.5 -- not pushed an extra step down."""
    bh = {"CB-CB-CB_L": 1.52, "DISC-DISC-DISC_R": 1.54, "GND_L": 0.43, "GND_R": 0.43}
    loops = [
        {"src_block": "CB-CB-CB_L", "dst_block": "DISC-DISC-DISC_R", "Wire_Count": 3,
         "Wire1_Color": "BRN", "Wire2_Color": "ORG", "Wire3_Color": "YEL", "Cond_Tag": "C1"},
        {"src_block": "GND_L", "dst_block": "GND_R", "Wire_Count": 1,
         "Wire1_Color": "GRN", "Cond_Tag": "C1"},
    ]
    plan = ab.build_layout_plan({}, loops, bh)
    gnd_wire = [w for w in _items(plan, "wire")][-1]   # the GND conductor
    assert gnd_wire["y"] == ab.START_Y - 1.5, f"GND at {gnd_wire['y']}, expected {ab.START_Y - 1.5}"


def test_instrument_groups_do_not_overlap(dup_instrument):
    """Regression for the overlap bug: consecutive instrument groups must be spaced
    so their (y - height .. y) intervals don't intersect in any column."""
    rows = parser.get_fill_rows(dup_instrument["fill_index"], "S01AIT003")
    loops = parser.build_loop_list(rows)
    plan = ab.build_layout_plan({}, loops, dup_instrument.get("block_heights"))
    # per (column, group) bounding interval
    boxes = {}
    for it in plan["items"]:
        if (it.get("height") or 0) <= 0:
            continue
        key = (round(it["x"], 2), it["group"])
        bot, top = it["y"] - it["height"], it["y"]
        b = boxes.get(key)
        boxes[key] = (min(b[0], bot), max(b[1], top)) if b else (bot, top)
    # no two DIFFERENT groups overlap in the same column
    for (x1, g1), (b1, t1) in boxes.items():
        for (x2, g2), (b2, t2) in boxes.items():
            if x1 == x2 and g1 < g2:
                overlap = min(t1, t2) - max(b1, b2)
                assert overlap <= 0.25, f"groups {g1}/{g2} overlap by {overlap:.2f} at x={x1}"


# ── pagination: split onto continuation sheets at the border ─────────────────

def _tall_loops(n):
    """n single-wire loops, each on a 1.5-tall source block (so the stack drops fast)."""
    bh = {"TALL_L|NA": 1.5, "TALL_L": 1.5}
    loops = [{"src_block": "TALL_L", "src_block_visibility": "NA", "dst_block": "TB_R",
              "Wire_Count": 1, "Cond_Tag": f"C{i}"} for i in range(n)]
    return loops, bh


def test_short_conduit_is_one_sheet():
    loops, bh = _tall_loops(2)
    assert ab.paginate_loops(loops, bh) == [(0, 2)]


def test_long_conduit_splits_into_sheets():
    """Enough tall groups to overrun the border bottom → more than one sheet."""
    loops, bh = _tall_loops(20)
    chunks = ab.paginate_loops(loops, bh)
    assert len(chunks) > 1
    # chunks are contiguous and cover every loop exactly once
    assert chunks[0][0] == 0
    assert chunks[-1][1] == len(loops)
    for (a, b), (c, d) in zip(chunks, chunks[1:]):
        assert b == c


def test_no_sheet_drops_below_the_page_floor():
    """Re-laying each sheet's slice from START_Y, no block bottom crosses PAGE_FLOOR."""
    loops, bh = _tall_loops(20)
    for a, b in ab.paginate_loops(loops, bh):
        plan = ab.build_layout_plan({}, loops[a:b], bh)
        for it in plan["items"]:
            bottom = it["y"] - (it.get("height") or 0.0)
            assert bottom >= ab.PAGE_FLOOR - 1e-9, f"block {it['name']} bottom {bottom} < floor"


def test_pagination_never_splits_an_instrument_group():
    """An anchor + its continuation rows always stay on the same sheet."""
    bh = {"INST_X|FIELD_4TERM": 2.0}
    loops = []
    for i in range(12):
        loops.append({"dst_block": "Inst_X", "dst_block_visibility": "Field_4Term",
                      "src_block": "TB_L", "Wire_Count": 2, "Cond_Tag": f"C{i}"})
        loops.append({"dst_block": "Inst_X", "src_block": "TB_L", "Wire_Count": 2,
                      "is_continuation": True, "Cond_Tag": f"C{i}"})
    for a, b in ab.paginate_loops(loops, bh):
        # every chunk boundary must fall on an anchor (a non-continuation row)
        assert not loops[a].get("is_continuation")
        if b < len(loops):
            assert not loops[b].get("is_continuation")


# ── pull boxes: one per conductor ────────────────────────────────────────────

def test_pullbox_generates_one_per_wire():
    """A pull/junction box is placed once per conductor (wire count), not once per
    row: a 2-wire loop -> 2 boxes, a 1-wire loop -> 1 box, each aligned to its wire."""
    bh = {"CB-CB_L": 1.0, "GND_L": 0.43, "PullBox_R": 0.45}
    loops = [
        {"src_block": "CB-CB_L", "dst_block": "PullBox_R", "Wire_Count": 2,
         "Wire1_Color": "BLK", "Wire2_Color": "RED", "Cond_Tag": "C1"},
        {"src_block": "GND_L", "dst_block": "PullBox_R", "Wire_Count": 1,
         "Wire1_Color": "GRN", "Cond_Tag": "C1"},
    ]
    plan = ab.build_layout_plan({}, loops, bh)
    boxes = [it for it in plan["items"] if it["name"] == "PullBox_R"]
    assert len(boxes) == 3, f"expected 3 boxes (2+1 wires), got {len(boxes)}"
    wire_ys = sorted({w["y"] for w in _items(plan, "wire")}, reverse=True)
    box_ys = sorted({b["y"] for b in boxes}, reverse=True)
    assert box_ys == wire_ys, "every wire should have a box at its y"
    assert all(b["x"] == ab.RIGHT_X for b in boxes)


# ── power-to-instrument connector ────────────────────────────────────────────

def test_power_instrument_adds_pwr_wire_connector():
    """A Power loop landing on an instrument also drops the Inst_Pwr_Wire connector
    at the instrument; a non-power (signal) instrument loop does not."""
    loops_pwr = [
        {"dst_block": "Inst_4W_R", "dst_block_visibility": "NA", "src_block": "TB_L",
         "Wire_Count": 2, "Wire_Type": "POWER",
         "Wire1_Color": "BLK", "Wire2_Color": "WHT",
         "Wire1_DstTermNum": "L", "Wire2_DstTermNum": "N", "Cond_Tag": "C1"},
    ]
    plan = ab.build_layout_plan({}, loops_pwr, {})
    pw = [it for it in plan["items"] if it["name"] == "Inst_Pwr_Wire_R"]
    assert len(pw) == 1, "power instrument should get one Inst_Pwr_Wire_R"
    # the connector sits AT the wire rows (entries on the wires); the instrument is
    # dropped INST_PWR_DROP so its L/N meet the connector's downward exit.
    inst = _items(plan, "instrument")[0]
    assert inst["y"] == ab.START_Y - ab.INST_PWR_DROP
    assert pw[0]["x"] == ab.RIGHT_X and pw[0]["y"] == ab.START_Y
    # Term attrs on the instrument are preserved (not moved to Line/Neutral)
    assert inst["attrs"].get("Term1") == "L" and inst["attrs"].get("Term2") == "N"

    loops_sig = [dict(loops_pwr[0], Wire_Type="TSP")]
    plan2 = ab.build_layout_plan({}, loops_sig, {})
    assert not [it for it in plan2["items"] if it["name"].startswith("Inst_Pwr_Wire")]


# ── instrument grouping in the plan ──────────────────────────────────────────

def test_generation_report_is_schema_shaped():
    """build_generation_report emits cad_ai_harness-shaped blockref objects."""
    placed = [
        {"id": "conduit-0", "role": "conduit", "block": "Conduit", "x": 0.0, "y": 0.0, "layer": "0"},
        {"id": "wire-1", "role": "wire", "block": "Wire_IDP", "x": 12.0, "y": 14.0, "layer": "WIRE"},
    ]
    rpt = ab.build_generation_report(placed, "job_x")
    assert rpt["job_id"] == "job_x"
    assert rpt["units"] == "inches"
    for o in rpt["objects"]:
        assert set(["id", "type", "layer", "block", "position"]) <= set(o)
        assert o["type"] == "blockref"
        assert isinstance(o["position"], list) and len(o["position"]) == 2


def test_instrument_group_inserts_one_instrument_keeps_per_row_src():
    """A 4-term instrument (anchor + continuation) → ONE instrument block, but the
    per-row source TBs stay one-per-row."""
    loops = [
        {"dst_block": "Inst_X", "dst_block_visibility": "Field_4Term",
         "src_block": "TB_L", "Wire_Count": 2, "Wire1_Color": "RED", "Wire2_Color": "BLK",
         "Cond_Tag": "C1"},
        {"dst_block": "Inst_X", "src_block": "TB_L", "Wire_Count": 2,
         "is_continuation": True, "Cond_Tag": "C1"},
    ]
    plan = ab.build_layout_plan({}, loops, {})
    assert len(_items(plan, "instrument")) == 1
    assert len(_items(plan, "src")) == 2      # one per row
    assert len(_items(plan, "wire")) == 4     # 2 rows x 2 wires
