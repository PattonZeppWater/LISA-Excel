"""workbook_mapper.py regression tests (fill derivation, AWG, tag collapse)."""
from services import workbook_mapper as wm


def _fill(rows):
    """Run _derive_fill_slots and return the [(type,size,color,qty), ...] table."""
    cr = {"Cond_Tag": "C1"}
    for r in rows:
        r.setdefault("Cond_Tag", "C1")
    wm._derive_fill_slots(cr, rows)
    out, i = [], 1
    while f"Fill{i:02d}_Type" in cr:
        out.append((cr[f"Fill{i:02d}_Type"], cr[f"Fill{i:02d}_Size"],
                    cr[f"Fill{i:02d}_Color"], cr[f"Fill{i:02d}_Quantity"]))
        i += 1
    return out


# ── _with_awg / _hash_gauge ──────────────────────────────────────────────────

def test_with_awg_numeric():
    assert wm._with_awg("10") == "#10AWG"

def test_with_awg_already_hashed():
    # the '#' is prepended upstream; AWG must still be appended (a past bug)
    assert wm._with_awg("#10") == "#10AWG"

def test_with_awg_already_awg_unchanged():
    assert wm._with_awg("#10AWG") == "#10AWG"

def test_with_awg_aught_no_suffix():
    # Aught sizes (1/0..4/0) drop the 'AWG' suffix so the table matches the wire label.
    assert wm._with_awg("3/0") == "#3/0"
    assert wm._with_awg("1/0") == "#1/0"
    assert wm._with_awg("#4/0") == "#4/0"

def test_with_awg_mcm_kept():
    # MCM stays MCM (must NOT auto-convert to KCMIL); spacing/case normalized.
    assert wm._with_awg("250MCM") == "250MCM"
    assert wm._with_awg("250 mcm") == "250MCM"

def test_with_awg_kcmil_unchanged():
    assert wm._with_awg("300KCMIL") == "300KCMIL"
    assert wm._with_awg("300 kcmil") == "300KCMIL"

def test_with_awg_text_gauge_unchanged():
    assert wm._with_awg("FIBER") == "FIBER"

def test_with_awg_na_and_none():
    assert wm._with_awg("N/A") == "N/A"
    assert wm._with_awg(None) is None

def test_hash_gauge():
    assert wm._hash_gauge("14") == "#14"
    assert wm._hash_gauge("#14") == "#14"
    assert wm._hash_gauge("FIBER") == "FIBER"


# ── TSP fill derivation ──────────────────────────────────────────────────────

def test_tsp_instrument_two_pairs_quantity():
    """4 conductors = 2 identical pairs -> one RED/BLK row, qty 2 (not RED/BLK/RED/BLK)."""
    rows = [
        {"Dst_TermBlockDesc": "Inst_4T", "Wire_Count": 1, "Wire_Type": "TSP",
         "Wire_Size_Raw": "18", "Wire1_Color": "RED", "Wire2_Color": "BLK",
         "Wire3_Color": "RED", "Wire4_Color": "BLK"},
        {"Dst_TermBlockDesc": "Inst_4T", "Wire_Count": 1, "Wire_Type": "TSP", "Wire_Size_Raw": "18"},
        {"Dst_TermBlockDesc": "Inst_4T", "Wire_Count": 1, "Wire_Type": "TSP", "Wire_Size_Raw": "18"},
        {"Dst_TermBlockDesc": "Inst_4T", "Wire_Count": 1, "Wire_Type": "TSP", "Wire_Size_Raw": "18"},
    ]
    assert _fill(rows) == [("TSP", "#18AWG", "RED/BLK", 2)]


def test_tsp_single_pair():
    rows = [
        {"Dst_TermBlockDesc": "Inst_2T", "Wire_Count": 1, "Wire_Type": "TSP",
         "Wire_Size_Raw": "18", "Wire1_Color": "RED", "Wire2_Color": "WHT"},
        {"Dst_TermBlockDesc": "Inst_2T", "Wire_Count": 1, "Wire_Type": "TSP", "Wire_Size_Raw": "18"},
    ]
    assert _fill(rows) == [("TSP", "#18AWG", "RED/WHT", 1)]


def test_tsp_standalone_instrument_row_counts_conductors():
    """A TSP instrument row with no colours that is NOT a continuation of a coloured
    anchor still contributes its conductors (regression: it used to vanish, so a
    POWER+TSP pair of rows read 4 conductors instead of 6)."""
    rows = [
        {"Dst_TermBlockDesc": "Inst_2T", "Wire_Count": 2, "Wire_Type": "POWER", "Wire_Size_Raw": "12"},
        {"Dst_TermBlockDesc": "Inst_2T", "Wire_Count": 2, "Wire_Type": "TSP", "Wire_Size_Raw": "12"},
    ]
    table = _fill(rows)
    assert any(t[0] == "TSP" and t[3] == 2 for t in table), table
    assert sum(t[3] for t in table) == 4   # POWER 2 + TSP 2 conductors


def test_tsp_non_instrument_single_row():
    rows = [{"Src_TermBlockDesc": "TB", "Dst_TermBlockDesc": "TB", "Wire_Count": 2,
             "Wire_Type": "TSP", "Wire1_Size": "18", "Wire1_Color": "RED", "Wire2_Color": "BLK"}]
    assert _fill(rows) == [("TSP", "#18AWG", "RED/BLK", 1)]


def test_tsp_gnd_drain_breaks_out():
    rows = [{"Src_TermBlockDesc": "TB", "Dst_TermBlockDesc": "TB", "Wire_Count": 3,
             "Wire_Type": "TSP", "Wire1_Size": "18",
             "Wire1_Color": "RED", "Wire1_SrcTag": "X",
             "Wire2_Color": "BLK", "Wire2_SrcTag": "Y",
             "Wire3_Color": "GRN", "Wire3_Size": "18", "Wire3_SrcTag": "GND"}]
    table = _fill(rows)
    assert ("GROUND", "#18AWG", "GRN", 1) in table
    assert ("TSP", "#18AWG", "RED/BLK", 1) in table


# ── no-entry-without-colour-or-gauge ─────────────────────────────────────────

def test_no_entry_when_no_color_or_gauge():
    """A symbol on both sides but no colour/gauge must NOT make a fill row
    (only the empty-conduit NONE/N/A fallback remains)."""
    rows = [{"Src_TermBlockDesc": "TB", "Dst_TermBlockDesc": "TB",
             "Wire_Count": 1, "Wire_Type": "POWER"}]
    assert _fill(rows) == [("NONE", "N/A", "N/A", "N/A")]


def test_entry_appears_with_gauge_and_color():
    rows = [{"Src_TermBlockDesc": "TB", "Dst_TermBlockDesc": "TB", "Wire_Count": 1,
             "Wire_Type": "POWER", "Wire1_Size": "10", "Wire1_Color": "BLU"}]
    assert _fill(rows) == [("POWER", "#10AWG", "BLU", 1)]
