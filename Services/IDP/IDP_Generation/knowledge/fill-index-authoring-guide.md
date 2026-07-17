# Fill Index — JSON Authoring Guide

## 1. Purpose

The fill index defines every individual loop (instrument connection) that passes through each conduit. Each row represents one loop — one pair or multi-conductor set connecting a source terminal block to a destination terminal block inside a specific conduit.

During DWG generation, fill_index rows drive:
- Which head-unit blocks are inserted (source and destination blocks)
- Which attribute values are written into those blocks (terminal numbers, wire tags, descriptions)
- How many Wire_IDP connectors are drawn

You produce a JSON array. Each element is one fill row. Paste the array into the IDP Generator UI via the **Paste JSON** button on the Fill Index tab.

---

## 2. JSON Structure

```json
[
  { "Fill_Ident": 1, "Cond_Tag": "P-01", "Src_TermBlockDesc": "TB-TB-TB-TB_L", ... },
  { "Fill_Ident": 2, "Cond_Tag": "P-01", "Src_TermBlockDesc": "TB-TB-TB-TB_L", ... }
]
```

---

## 3. Field Reference

### 3.1 Identity (Required)

| Field | Type | Description |
|---|---|---|
| `Fill_Ident` | integer | Unique numeric row ID across all fill rows. Sequential from 1. |
| `Cond_Tag` | string | **Foreign key** — must exactly match a `Cond_Tag` value in conduit_index. All fill rows with the same `Cond_Tag` are drawn together on that conduit's IDP sheet. |

### 3.2 Block Selection (Required)

These are the AutoCAD Electrical block names from `BlockLib_ACAD`. They must match exactly.

| Field | Type | Description |
|---|---|---|
| `Src_TermBlockDesc` | string | Block name for the **source** head unit. Example: `"TB-TB-TB-TB_L"`. The `_L` suffix indicates the left (source) variant. |
| `Dst_TermBlockDesc` | string | Block name for the **destination** head unit. Example: `"TB-TB-TB-TB_R"`. The `_R` suffix indicates the right (destination) variant. |
| `Src_TermBlockVisibilityState` | string or null | Visibility state to activate on the source block (for multi-state blocks). Omit or set to `null` if not applicable. |
| `Dst_TermBlockVisibilityState` | string or null | Visibility state to activate on the destination block. |

### 3.3 Loop Descriptions

| Field | Type | Description |
|---|---|---|
| `Loop_SrcDesc` | string or null | Source-side description. Also written to `ISATag_FunctIdent`, `Desc01`, and `Desc1` attributes. Example: `"Transformer Terminations"`. |
| `Loop_DstDesc` | string or null | Destination-side description. Written to `Desc02` / `Desc2`. |
| `Loop_Catagory` | string or null | Loop category label. Note: spelling is intentional — matches the Excel column header. Example: `"Power"`, `"Control"`, `"Instrument"`. |

### 3.4 Wire Summary

| Field | Type | Description |
|---|---|---|
| `Wire_Count` | integer | Number of conductors in this loop. Valid values: `1`, `2`, `3`, `4`. |
| `Wire_Type` | string or null | Wire insulation / cable type. Common values: `"THHN"`, `"TSP"`, `"SJT"`, `"PLTC"`. Used by **Populate Fills** to group conduit fill slots. |

### 3.5 Wire Detail (Wire1 – Wire4)

Repeat the following pattern for each conductor, replacing `1` with `2`, `3`, or `4` as needed. Include only wires up to `Wire_Count`.

| Field | Type | Description |
|---|---|---|
| `Wire1_Color` | string or null | Conductor insulation color. Examples: `"RED"`, `"BLK"`, `"WHT"`, `"GRN"`, `"ORG"`. |
| `Wire1_Size` | string or null | Conductor gauge (AWG or kcmil). Examples: `"12"`, `"14"`, `"16"`. |
| `Wire1_SrcTermBlk` | string or null | Source terminal block label for this wire. Mapped to the `Tag1` attribute on the source block. Example: `"Phase 1"`. |
| `Wire1_SrcTermNum` | string or null | Source terminal number for this wire. Mapped to the `Term1` attribute. Example: `"A"`, `"1"`, `"L1"`. |
| `Wire1_DstTermBlk` | string or null | Destination terminal block label. Mapped to `Tag1` on the destination block. |
| `Wire1_DstTermNum` | string or null | Destination terminal number. Mapped to `Term1` on the destination block. |
| `Wire1_SrcLabel` | string or null | Optional wire label at the source end. |
| `Wire1_DstLabel` | string or null | Optional wire label at the destination end. |

---

## 4. Block Naming Convention

Source blocks use the `_L` suffix; destination blocks use the `_R` suffix. The base block name encodes the terminal layout.

Examples:
| Block Name | Meaning |
|---|---|
| `TB-TB-TB-TB_L` | 4-terminal source block (TB family, 4 wire slots) |
| `TB-TB-TB-TB_R` | 4-terminal destination block |
| `TB-TB_L` | 2-terminal source block |
| `Inst_4W_L` | 4-wire instrument source block (Inst family) |
| `Inst_4W_R` | 4-wire instrument destination block |

**Inst-family blocks** use `ISATag_FunctIdent`, `Term01`–`Term04`, `Desc01`–`Desc03` attributes.  
**TB-family blocks** use `Tag1`–`Tag4`, `Term1`–`Term4`, `Desc1`–`Desc3` attributes.  
Both are populated automatically — you do not need to choose; the generator fills whichever attributes exist on the block.

---

## 5. Rules

1. `Fill_Ident` must be unique integers across all fill rows.
2. `Cond_Tag` must match an existing conduit_index row exactly (case-sensitive).
3. `Wire_Count` controls how many `Wire_IDP` connectors are drawn. Only include `Wire{n}_*` fields up to `Wire_Count`.
4. Rows are drawn in array order — the first fill row for a conduit appears nearest the top of the sheet.
5. Source blocks must use `_L` suffix; destination blocks must use `_R` suffix.
6. Block names must exactly match entries in `BlockLib_ACAD` in the workbook.

---

## 6. Worked Example

```json
[
  {
    "Fill_Ident": 1,
    "Cond_Tag": "P-01",
    "Src_TermBlockDesc": "TB-TB-TB-TB_L",
    "Dst_TermBlockDesc": "TB-TB-TB-TB_R",
    "Src_TermBlockVisibilityState": null,
    "Dst_TermBlockVisibilityState": null,
    "Loop_SrcDesc": "Transformer Terminations",
    "Loop_DstDesc": "Panel LP-A",
    "Loop_Catagory": "Power",
    "Wire_Count": 4,
    "Wire_Type": "THHN",
    "Wire1_Color": "BLK",
    "Wire1_Size": "12",
    "Wire1_SrcTermBlk": "Phase 1",
    "Wire1_SrcTermNum": "A",
    "Wire1_DstTermBlk": "L1",
    "Wire1_DstTermNum": "1",
    "Wire1_SrcLabel": null,
    "Wire1_DstLabel": null,
    "Wire2_Color": "RED",
    "Wire2_Size": "12",
    "Wire2_SrcTermBlk": "Phase 2",
    "Wire2_SrcTermNum": "B",
    "Wire2_DstTermBlk": "L2",
    "Wire2_DstTermNum": "2",
    "Wire2_SrcLabel": null,
    "Wire2_DstLabel": null,
    "Wire3_Color": "BLU",
    "Wire3_Size": "12",
    "Wire3_SrcTermBlk": "Phase 3",
    "Wire3_SrcTermNum": "C",
    "Wire3_DstTermBlk": "L3",
    "Wire3_DstTermNum": "3",
    "Wire3_SrcLabel": null,
    "Wire3_DstLabel": null,
    "Wire4_Color": "GRN",
    "Wire4_Size": "12",
    "Wire4_SrcTermBlk": "Ground",
    "Wire4_SrcTermNum": "GND",
    "Wire4_DstTermBlk": "GND",
    "Wire4_DstTermNum": "GND",
    "Wire4_SrcLabel": null,
    "Wire4_DstLabel": null
  },
  {
    "Fill_Ident": 2,
    "Cond_Tag": "P-01",
    "Src_TermBlockDesc": "TB-TB_L",
    "Dst_TermBlockDesc": "TB-TB_R",
    "Src_TermBlockVisibilityState": null,
    "Dst_TermBlockVisibilityState": null,
    "Loop_SrcDesc": "Transformer Neutral",
    "Loop_DstDesc": "Panel LP-A",
    "Loop_Catagory": "Power",
    "Wire_Count": 2,
    "Wire_Type": "THHN",
    "Wire1_Color": "WHT",
    "Wire1_Size": "12",
    "Wire1_SrcTermBlk": "Neutral",
    "Wire1_SrcTermNum": "N",
    "Wire1_DstTermBlk": "N",
    "Wire1_DstTermNum": "N1",
    "Wire1_SrcLabel": null,
    "Wire1_DstLabel": null,
    "Wire2_Color": "GRN",
    "Wire2_Size": "12",
    "Wire2_SrcTermBlk": "Ground",
    "Wire2_SrcTermNum": "GND",
    "Wire2_DstTermBlk": "GND",
    "Wire2_DstTermNum": "G1",
    "Wire2_SrcLabel": null,
    "Wire2_DstLabel": null
  }
]
```

---

## 7. Workflow Notes for Agents

- The agent receives the current fill_index as JSON (copied from the UI).
- The agent adds, edits, removes, or reorders rows and returns the complete updated array.
- To add a new loop to conduit P-01, add a new object with `"Cond_Tag": "P-01"` and a unique `Fill_Ident`.
- Block names must come from the available block library — the agent should reference the BlockLib_ACAD data if provided, or ask the user to confirm block names.
- After applying the updated fill_index, the user clicks **Populate Fills** to recompute the conduit fill slot summaries.
