"""
workbook_mapper.py — Map dynamic workbook column names to IDP internal names.

The dynamic workbook uses human-readable headers (e.g. "S Symbol", "Wire Gauge").
The IDP backend expects internal names (e.g. "Src_TermBlockDesc", "Wire1_Size").

apply_workbook_mapping() is non-destructive: original workbook keys are preserved
alongside internal-name aliases, so write-back via _write_sheet still works.
"""

import re

# ── Column alias tables ───────────────────────────────────────────────────────
# Maps workbook header text → internal column name.
# Includes common spacing/typo variants so the mapper is tolerant.

CONDUIT_COL_ALIASES: dict[str, str] = {
    # Conduit name (workbook has a typo)
    "Condiuit Name":       "Cond_Tag",
    "Conduit Name":        "Cond_Tag",
    # Source junction-box names
    "Source  Name 1 ":     "Src_Name01",
    "Source Name 1 ":      "Src_Name01",
    "Source Name 1":       "Src_Name01",
    "Source Name 2":       "Src_Name02",
    "Source Name 3":       "Src_Name03",
    "Source Name 4":       "Src_Name04",
    # Destination junction-box names
    "Destination Name 1":  "Dst_Name01",
    "Destination Name 2":  "Dst_Name02",
    "Destination Name 3":  "Dst_Name03",
    "Destination Name 4":  "Dst_Name04",
    # Conduit properties
    "Conduit Size":        "Cond_Size",
    "Conduit Type":        "Cond_Type",
    "Ref Documents":       "Ref_DocNames",
    "Deviations Notes":    "Dev_Nums",
    "Deviation Notes":     "Dev_Nums",
}

FILL_COL_ALIASES: dict[str, str] = {
    # Link to conduit
    "Conduit":             "Cond_Tag",
    # Row identifier (Wire Ct / Wire Loop # is used as-is for Fill_Ident)
    "Wire Ct":             "Fill_Ident",
    "Wire Loop #":         "Fill_Ident",   # legacy header name
    # Wire type/gauge/color (single value, expanded to per-wire below)
    "Type":                "Wire_Type",
    "Wire Gauge":          "Wire_Size_Raw",
    "Wire Guage":          "Wire_Size_Raw",   # typo variant
    "Color":               "Wire_Color_Raw",   # legacy single-color column
    # Per-wire colors (Color 1-4 columns)
    "Color 1":             "Wire1_Color",
    "Color 2":             "Wire2_Color",
    "Color 3":             "Wire3_Color",
    "Color 4":             "Wire4_Color",
    # Source/destination AutoCAD block names
    "S Symbol":            "Src_TermBlockDesc",
    "D Symbol":            "Dst_TermBlockDesc",
    # Source/destination tag names — these populate the Tag1-4 block attributes
    # (NOT the symbol name).  One per conductor.
    "S Tag 1":             "Wire1_SrcTag",
    "S Tag 2":             "Wire2_SrcTag",
    "S Tag 3":             "Wire3_SrcTag",
    "S Tag 4":             "Wire4_SrcTag",
    "D Tag 1":             "Wire1_DstTag",
    "D Tag 2":             "Wire2_DstTag",
    "D Tag 3":             "Wire3_DstTag",
    "D Tag 4":             "Wire4_DstTag",
    # Per-wire label override mode (hidden helper columns) —
    #   "Default" = auto label, "Custom" = exact text typed, "Blank" = no label
    "WL1_Mode":            "Wire1_LabelMode",
    "WL2_Mode":            "Wire2_LabelMode",
    "WL3_Mode":            "Wire3_LabelMode",
    "WL4_Mode":            "Wire4_LabelMode",
    # Spare-block attributes — fill 'Type'/'Quantity' on Spare_L / Spare_R blocks;
    # the quantity also bumps the spare wire's quantity in the conduit fill table.
    "S Type":              "Src_SpareType",
    "D Type":              "Dst_SpareType",
    "S Quantity":          "Src_SpareQty",
    "D Quantity":          "Dst_SpareQty",
    # ISA tag fields used for loop labeling
    "S ISATag_FunctionID": "Loop_SrcDesc",
    "D ISATag_FunctionID": "Loop_DstDesc",
    # Instrument ISA tag components -> filled onto the instrument bubble
    "S ISATag_Loop#":      "Src_ISALoop",
    "D ISATag_Loop#":      "Dst_ISALoop",
    "S ISATag_ElementID":  "Src_ISAElem",
    "D ISATag_ElementID":  "Dst_ISAElem",
    "S ISATag_Element#":   "Src_ISAElemNum",
    "D ISATag_Element#":   "Dst_ISAElemNum",
    # Source-side descriptions (first occurrence of "S Description 1/2/3" in sheet)
    "S Description 1":     "Src_Desc1",
    "S Description 2":     "Src_Desc2",
    "S Description 3":     "Src_Desc3",
    # Destination-side descriptions — workbook mislabels these as "S Description" again,
    # so _build_col_map gives them the _2 suffix.  Also support "D Description" label.
    "S Description 1_2":   "Dst_Desc1",
    "S Description 2_2":   "Dst_Desc2",
    "S Description 3_2":   "Dst_Desc3",
    "D Description 1":     "Dst_Desc1",
    "D Description 2":     "Dst_Desc2",
    "D Description 3":     "Dst_Desc3",
    # Device ratings -> Rating attr on device blocks (Valve, Fuse, Switch, CB, …)
    "S Rating":            "Src_Rating",
    "S Rating_2":          "Dst_Rating",   # workbook may relabel D as "S Rating"
    "D Rating":            "Dst_Rating",
    # Per-term hide: hidden CSV column ("S1,D3" …) flags which terms to suppress
    "Hidden Terms":        "Hidden_Terms",
    # Wire labels (src-side; dst-side is not in this workbook layout)
    "Wire Label1":         "Wire1_SrcLabel",
    "Wire Label 1":        "Wire1_SrcLabel",
    "Wire Label 2":        "Wire2_SrcLabel",
    "Wire Label 3":        "Wire3_SrcLabel",
    "Wire Label 4":        "Wire4_SrcLabel",
}

# Source / destination terminal slot column names in order (wire 1-4)
_SRC_TERM_COLS = ["S Term 1", "S Term 2", "S Term 3", "S Term 4"]
_DST_TERM_COLS = ["D Term 1", "D Term 2", "D Term 3", "D Term 4"]


# ── Public entry point ────────────────────────────────────────────────────────

def apply_workbook_mapping(parsed: dict) -> dict:
    """
    Enrich parse_workbook output from dynamic-workbook column names to internal names.

    Transforms parsed in-place and returns it.  Four steps:
      1. Auto-generate Cond_Ident (row position) when absent.
      2. Auto-generate Fill_Ident from Wire Loop # or row position.
      3. Expand single Color/Gauge/TermBlock to per-wire Wire{n}_* fields.
      4. Derive Fill slot columns on conduit rows from fill_index rows.
    """
    conduit_index: list = parsed.get("conduit_index", [])
    fill_index:    list = parsed.get("fill_index", [])

    # Step 1 – auto-generate Cond_Ident
    for i, row in enumerate(conduit_index, 1):
        if row.get("Cond_Ident") is None:
            row["Cond_Ident"] = i

    # Step 2 – auto-generate Fill_Ident (Wire Loop # already aliased by parser)
    for i, row in enumerate(fill_index, 1):
        if row.get("Fill_Ident") is None:
            row["Fill_Ident"] = i

    # Step 3 – expand per-wire fields in fill rows
    for row in fill_index:
        _expand_wire_fields(row)

    # Step 4 – derive fill slots on conduit rows
    for row in conduit_index:
        _derive_fill_slots(row, fill_index)

    return parsed


# ── Private helpers ───────────────────────────────────────────────────────────

def _expand_wire_fields(row: dict) -> None:
    """
    Expand single Color/Gauge/Symbol columns into per-wire Wire{n}_* fields.

    The dynamic workbook stores one color, one gauge, and one src/dst symbol
    per fill row.  The IDP backend expects up to four independent wire records
    per row (Wire1_*, Wire2_*, …).  This function fans out the shared values
    and also maps S Term/D Term slots to the Wire{n}_SrcTermNum/DstTermNum fields.

    Wire_Count is derived from the number of non-empty S Term slots when not
    already present on the row.
    """
    raw_color = row.get("Wire_Color_Raw")
    raw_size  = _hash_gauge(row.get("Wire_Size_Raw"))
    row["Wire_Size_Raw"] = raw_size          # carry the '#' to the fill table too
    src_block = row.get("Src_TermBlockDesc")
    dst_block = row.get("Dst_TermBlockDesc")

    # A multi-wire symbol may list one color per conductor, comma-separated
    # (e.g. "BRN, ORG, YEL").  Split positionally; a single value fans to all.
    color_list = (
        [c.strip() for c in str(raw_color).split(",")]
        if raw_color is not None and "," in str(raw_color)
        else None
    )

    # Conductor count = the user's "Wire Ct" (column B) when it's a valid 1-4.
    # This is authoritative, so stray/blacked-out data in unused wire slots (e.g. a
    # leftover S Term on a 1-wire loop) does NOT inflate the count. Falls back to
    # counting populated S Term slots only when Wire Ct is blank/invalid.
    # (Wire Ct 6 & 8 are instrument-only and auto-expand into separate rows, so
    # each such row falls back to its own S Term count.)
    if row.get("Wire_Count") is None:
        is_inst = ("inst" in str(src_block or "").lower()) \
            or ("inst" in str(dst_block or "").lower())
        if is_inst:
            # Each instrument row is one shielded pair = 2 conductors, regardless of
            # the Wire Ct shown (which is the instrument's total terminal count).
            row["Wire_Count"] = 2
        else:
            wire_loop_n = None
            try:
                _wc = row.get("Wire Ct")
                if _wc is None:
                    _wc = row.get("Wire Loop #")
                iv = int(_wc)
                if 1 <= iv <= 4:
                    wire_loop_n = iv
            except (TypeError, ValueError):
                pass

            if wire_loop_n is not None:
                row["Wire_Count"] = wire_loop_n
            else:
                wire_count = 0
                for n, col in enumerate(_SRC_TERM_COLS, 1):
                    if row.get(col) is not None:
                        wire_count = n
                row["Wire_Count"] = wire_count if wire_count > 0 else None

    # Conduit_Fill_Qty = how many of this item are in the conduit (always >= 1)
    row["Conduit_Fill_Qty"] = row.get("Wire_Count") or 1

    # Map S Term 1-4 → Wire{n}_SrcTermNum  /  D Term 1-4 → Wire{n}_DstTermNum
    for n, col in enumerate(_SRC_TERM_COLS, 1):
        key = f"Wire{n}_SrcTermNum"
        if key not in row:
            row[key] = row.get(col)

    for n, col in enumerate(_DST_TERM_COLS, 1):
        key = f"Wire{n}_DstTermNum"
        if key not in row:
            row[key] = row.get(col)

    # Fan out shared color / gauge / block to each wire slot (don't overwrite
    # any per-wire values that already exist on the row).  When the color column
    # holds a comma-separated list, assign each conductor its own color.
    for n in range(1, 5):
        if f"Wire{n}_Color" not in row:
            if color_list is not None:
                row[f"Wire{n}_Color"] = color_list[n - 1] if n - 1 < len(color_list) else None
            else:
                row[f"Wire{n}_Color"] = raw_color
        if f"Wire{n}_Size" not in row:
            row[f"Wire{n}_Size"] = raw_size
        if f"Wire{n}_SrcTermBlk" not in row:
            row[f"Wire{n}_SrcTermBlk"] = src_block
        if f"Wire{n}_DstTermBlk" not in row:
            row[f"Wire{n}_DstTermBlk"] = dst_block


def _tag_is_gnd(*vals) -> bool:
    """True if any provided tag value reads 'GND'."""
    return any(v not in (None, "") and str(v).strip().upper() == "GND" for v in vals)


def _hash_gauge(v):
    """Prefix a numeric wire gauge with '#': 14 -> #14.  Leaves blanks, text,
    already-#'d, and N/A values alone."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.startswith("#") or s.upper() in ("N\\A", "NA"):
        return v
    return "#" + s if s[0].isdigit() else v


def _split_gauge(v):
    """Classify a wire-gauge value into (kind, core).

    kind is one of 'KCMIL', 'MCM', 'AWG', 'TEXT', 'NA', 'EMPTY'.  core is the bare size
    with any leading '#', trailing 'AWG'/'KCMIL'/'MCM', and internal spaces removed:
        '10 AWG'   -> ('AWG',   '10')
        '4AWG'     -> ('AWG',   '4')
        '#12'      -> ('AWG',   '12')
        '3/0'      -> ('AWG',   '3/0')
        '300KCMIL' -> ('KCMIL', '300')
        '300 KCMIL'-> ('KCMIL', '300')
        '250MCM'   -> ('MCM',   '250')   (MCM kept as MCM, NOT converted to KCMIL)
        '250 mcm'  -> ('MCM',   '250')
        'FIBER'    -> ('TEXT',  'FIBER')   (non-numeric text gauge, left as typed)
    """
    if v is None:
        return ("EMPTY", "")
    s = str(v).strip()
    if not s:
        return ("EMPTY", "")
    if s.upper() in ("N/A", "NA", "N\\A"):
        return ("NA", s)
    core = s[1:].strip() if s.startswith("#") else s      # drop a leading '#'
    up = core.upper()
    if "KCMIL" in up:                                     # kcmil -> keep the number only
        num = up.replace("KCMIL", "").replace(" ", "").strip()
        return ("KCMIL", num)
    if "MCM" in up:                                       # mcm -> keep as MCM (do NOT convert to kcmil)
        num = up.replace("MCM", "").replace(" ", "").strip()
        return ("MCM", num)
    if up.endswith("AWG"):                                # strip a trailing AWG
        up = up[:-3]
    up = up.replace(" ", "").strip()
    if up[:1].isdigit():
        return ("AWG", up)
    return ("TEXT", s)                                    # e.g. FIBER -> leave as typed


def _with_awg(size) -> str | None:
    """Format a wire gauge for the conduit fill TABLE:
       AWG sizes   -> '#<size>AWG'   ('10' -> '#10AWG')
       AWG aught   -> '#<size>'      ('3/0' -> '#3/0', '1/0' -> '#1/0') -- no 'AWG' suffix
       KCMIL sizes -> '<size>KCMIL'  (no '#', no 'AWG'; '300 KCMIL' -> '300KCMIL')
       MCM sizes   -> '<size>MCM'    (kept as MCM, not converted; '250 mcm' -> '250MCM')
       text gauges (FIBER) / N/A / blanks are left exactly as typed."""
    kind, core = _split_gauge(size)
    if kind == "KCMIL":
        return f"{core}KCMIL"
    if kind == "MCM":
        return f"{core}MCM"
    if kind == "AWG":
        # Aught sizes (1/0, 2/0, 3/0, 4/0) are written WITHOUT the 'AWG' suffix so the
        # table matches the wire label -- e.g. '3/0' -> '#3/0' in both places.
        if "/" in core:
            return f"#{core}"
        return f"#{core}AWG"
    return size


def _is_tsp(wire_type) -> bool:
    """A Twisted Shielded Pair loop -- its conductors are combined into a single
    fill row with a slash-joined colour combo (e.g. RED + WHT -> 'RED/WHT')."""
    return str(wire_type or "").strip().upper() == "TSP"


def _combine_colors(colors) -> str | None:
    """Join conductor colours into a 'RED/WHT' combo, dropping blanks/dupes-in-a-row
    while preserving order."""
    out = []
    for c in colors:
        s = str(c).strip() if c not in (None, "") else ""
        if s:
            out.append(s)
    return "/".join(out) if out else None


def _tsp_pair_entries(colors):
    """Split a TSP bundle's colours into shielded PAIRS (2 conductors each) and
    return one combined colour per pair: [RED,BLK,RED,BLK] -> ['RED/BLK','RED/BLK'].
    Each pair becomes its own fill entry, so identical pairs collapse to one row
    whose quantity counts them (RED/BLK x2) instead of 'RED/BLK/RED/BLK' x1."""
    out = []
    for k in range(0, len(colors), 2):
        combo = _combine_colors(colors[k:k + 2])
        if combo is not None:
            out.append(combo)
    return out


def _derive_fill_slots(conduit_row: dict, fill_index: list) -> None:
    """
    Add Fill##_Type/Color/Size/Quantity to a conduit row from its fill_index rows.

    The dynamic workbook does not have Fill slot columns on ConduitIndex; they
    are implied by the FillIndex rows that share the same Cond_Tag.  This step
    synthesises them so build_conduit_data() can populate the conduit block in
    AutoCAD.
    """
    cond_tag = conduit_row.get("Cond_Tag")
    if not cond_tag:
        return

    # Skip if explicit Fill slots already came from the workbook
    if "Fill01_Type" in conduit_row:
        return

    matching = [r for r in fill_index if str(r.get("Cond_Tag", "")) == str(cond_tag)]

    # Build one entry per conductor, then GROUP conductors that share the same
    # Type/Size/Color into a single table row with Quantity = how many there are.
    # (e.g. four identical CONTROL #14AWG BLU wires -> one row, qty 4.)
    # A conductor whose tag is GND reads "GROUND"; gauges get "AWG" appended.
    order = []   # preserves first-seen order: [type, size, color, qty]
    seen  = {}   # (type, size, color) -> index into order

    def _add(ftype, size, color, inc):
        # A conductor with neither a gauge nor a colour still gets a placeholder
        # row WHEN it has a type -- e.g. a symbol placed with its Type set but the
        # wire's size/colour not filled in yet -> <TYPE> / XXX / XXX / XXX.
        # Identical type-only conductors collapse to that single placeholder row.
        # With no type at all there is nothing to show, so it is skipped and the
        # empty-conduit NONE / N/A fallback (below) covers that case.
        if size in (None, "") and color in (None, ""):
            if ftype in (None, ""):
                return
            key = (ftype, "XXX", "XXX")
            if key not in seen:
                seen[key] = len(order)
                order.append([ftype, "XXX", "XXX", "XXX"])
            return
        # Colour + Type entered but the gauge hasn't been picked yet -> show the
        # gauge as "TBD" (rather than blank) so it reads as a deliberate placeholder.
        if size in (None, "") and ftype not in (None, "") and color not in (None, ""):
            size = "TBD"
        key = (ftype, size, color)
        if key in seen:
            order[seen[key]][3] += inc
        else:
            seen[key] = len(order)
            order.append([ftype, size, color, inc])

    group_colors = []     # remaining anchor colors for the current instrument group
    in_tsp_group = False  # True while inside a coloured TSP anchor's group (its
                          # continuation rows are already counted by the anchor's pairs)
    for fill_row in matching:
        src = str(fill_row.get("Src_TermBlockDesc") or "")
        dst = str(fill_row.get("Dst_TermBlockDesc") or "")
        is_inst  = ("inst"  in src.lower()) or ("inst"  in dst.lower())
        is_spare = ("spare" in src.lower()) or ("spare" in dst.lower())
        wire_count = int(fill_row.get("Wire_Count") or 1)

        # A spare conductor counts as the quantity entered for it, not 1
        inc = 1
        if is_spare:
            raw = fill_row.get("Src_SpareQty") or fill_row.get("Dst_SpareQty")
            m = re.search(r"\d+", str(raw)) if raw is not None else None  # 'X4' or '4' -> 4
            inc = int(m.group()) if m else 1
            if inc < 1:
                inc = 1

        if is_inst:
            # Each instrument row is one shielded pair (wire_count conductors). The
            # bundle's colors live on the group's anchor row (the one carrying
            # Color 1-4); distribute them across the group's rows in order so the
            # whole instrument reads as one fill entry (e.g. TSP, qty 4).
            row_cols = [fill_row.get(f"Wire{w}_Color") for w in range(1, 5)]
            if any(c not in (None, "") for c in row_cols):
                group_colors = list(row_cols)        # anchor: (re)load the queue
            ftype = fill_row.get("Wire_Type")
            size  = _with_awg(fill_row.get("Wire_Size_Raw"))
            if _is_tsp(ftype):
                # TSP instrument: the pair colours all sit on the group's anchor
                # row (Color 1-4), while the group spans several 1-conductor rows.
                # Emit one entry per shielded PAIR on the anchor (identical pairs
                # collapse and the quantity counts them), then skip the rest.
                if any(c not in (None, "") for c in row_cols):   # coloured anchor
                    for combo in _tsp_pair_entries(group_colors):
                        _add(ftype, size, combo, inc)
                    in_tsp_group = True            # following continuation rows are covered
                elif not in_tsp_group:
                    # Standalone TSP instrument row with no colours (not a continuation
                    # of a coloured anchor): still count its conductors so the fill table
                    # reflects them instead of dropping the whole row.
                    for _w in range(wire_count):
                        _add(ftype, size, None, inc)
                group_colors = []
            else:
                in_tsp_group = False
                for _w in range(wire_count):
                    color = group_colors.pop(0) if group_colors else None
                    _add(ftype, size, color, inc)
            continue

        in_tsp_group = False   # left the instrument rows

        # TSP: combine each shielded PAIR into one RED/WHT row (identical pairs
        # collapse, quantity counts them). A GND conductor (bare drain) still
        # breaks out as its own GROUND row.
        if _is_tsp(fill_row.get("Wire_Type")):
            combo = []
            for w in range(1, wire_count + 1):
                if _tag_is_gnd(fill_row.get(f"Wire{w}_SrcTag"), fill_row.get(f"Wire{w}_DstTag")):
                    _add("GROUND", _with_awg(fill_row.get(f"Wire{w}_Size")),
                         fill_row.get(f"Wire{w}_Color"), inc)
                else:
                    combo.append(fill_row.get(f"Wire{w}_Color"))
            size = _with_awg(fill_row.get("Wire1_Size") or fill_row.get("Wire_Size_Raw"))
            for color in _tsp_pair_entries(combo):
                _add(fill_row.get("Wire_Type"), size, color, inc)
            continue

        # non-instrument: one conductor per wire slot
        for w in range(1, wire_count + 1):
            if _tag_is_gnd(fill_row.get(f"Wire{w}_SrcTag"), fill_row.get(f"Wire{w}_DstTag")):
                ftype = "GROUND"
            else:
                ftype = fill_row.get("Wire_Type")
            size  = _with_awg(fill_row.get(f"Wire{w}_Size"))
            color = fill_row.get(f"Wire{w}_Color")
            _add(ftype, size, color, inc)

    # Nothing in the fill index for this conduit -> show a single NONE / N/A row
    if not order:
        order.append(["NONE", "N/A", "N/A", "N/A"])

    for i, (ftype, size, color, qty) in enumerate(order, 1):
        if i > 30:
            break
        slot = f"{i:02d}"
        conduit_row[f"Fill{slot}_Type"]     = ftype
        conduit_row[f"Fill{slot}_Color"]    = color
        conduit_row[f"Fill{slot}_Size"]     = size
        conduit_row[f"Fill{slot}_Quantity"] = qty
