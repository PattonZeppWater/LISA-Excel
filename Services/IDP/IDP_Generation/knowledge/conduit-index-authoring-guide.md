# Conduit Index — JSON Authoring Guide

## 1. Purpose

The conduit index is the master list of conduits in a project. Each row represents one physical conduit run. It drives the Conduit block placed at the center of each IDP drawing and controls the fill summary (how many conductors of each type are inside the conduit).

You produce a JSON array. Each element is one conduit row. Paste the array into the IDP Generator UI via the **Paste JSON** button on the Conduit Index tab.

---

## 2. JSON Structure

The top-level value is a plain JSON array — no wrapper object.

```json
[
  { "Cond_Ident": 1, "Cond_Tag": "P-01", ... },
  { "Cond_Ident": 2, "Cond_Tag": "P-02", ... }
]
```

---

## 3. Field Reference

### 3.1 Identity (Required)

| Field | Type | Description |
|---|---|---|
| `Cond_Ident` | integer | Unique numeric row ID. Sequential from 1. Must be unique across all conduit rows. |
| `Cond_Tag` | string | Conduit tag. Used as the DWG filename (`<Cond_Tag>.dwg`) and as the link key to fill_index rows. Must be unique. Examples: `"P-01"`, `"C-14"`, `"HS-003"`. |

### 3.2 Conduit Properties

| Field | Type | Description |
|---|---|---|
| `Cond_Type` | string or null | Conduit material. Common values: `"EMT"`, `"PVC"`, `"SS-FLEX"`, `"LT"`. |
| `Cond_Size` | string or null | Nominal trade size. Examples: `"3/4\""`, `"1\""`, `"1-1/2\""`, `"2\""`. |
| `Src_Jbox` | string or null | Source junction box tag. Example: `"JB-101"`. |
| `Dst_Jbox` | string or null | Destination junction box tag. Example: `"JB-205"`. |

### 3.3 Name Lines (displayed on the Conduit block)

| Field | Type | Description |
|---|---|---|
| `Src_Name01` | string or null | Source name line 1 (e.g. equipment tag or location). |
| `Src_Name02` | string or null | Source name line 2 (e.g. panel or terminal strip). |
| `Src_Name03` | string or null | Source name line 3 (optional detail). |
| `Dst_Name01` | string or null | Destination name line 1. |
| `Dst_Name02` | string or null | Destination name line 2. |
| `Dst_Name03` | string or null | Destination name line 3. |

### 3.4 Fill Slots

Up to 30 fill slots summarize the conduit contents. Each slot is one grouped fill type (e.g. all 12AWG RED THHN conductors as one line). The **Populate Fills** button in the UI computes these automatically from the fill_index — you only need to author them directly if bypassing that workflow.

**CRITICAL**: Fill slots must be contiguous starting at `Fill01`. The generator stops at the first slot where `Fill##_Type` is null or empty. Do not leave gaps.

| Field | Type | Description |
|---|---|---|
| `Fill01_Type` | string or null | Fill material type. Examples: `"THHN"`, `"TSP"`, `"Pull Rope"`. |
| `Fill01_Color` | string or null | Color of this fill group. Examples: `"RED"`, `"BLK"`, `"WHT"`, `"GRN"`. |
| `Fill01_Size` | string or null | Gauge or size. Examples: `"12"`, `"14"`, `"16"`, `"1/4\""`. |
| `Fill01_Quantity` | integer or null | Count of conductors/items in this group. |
| `Fill02_Type` … `Fill30_Type` | same | Repeat pattern for additional fill groups. |

---

## 4. Rules

1. `Cond_Tag` must be unique — it is the filename and the foreign key that fill_index rows reference.
2. `Cond_Ident` must be unique integers. Use sequential numbering starting at 1.
3. Fill slot numbering uses zero-padded two-digit suffixes: `Fill01`, `Fill02`, …, `Fill30`.
4. Null fields should be JSON `null`, not empty string, for consistency.
5. Omit fill slots entirely if using Populate Fills (the UI will compute them).

---

## 5. Worked Example

```json
[
  {
    "Cond_Ident": 1,
    "Cond_Tag": "P-01",
    "Cond_Type": "EMT",
    "Cond_Size": "3/4\"",
    "Src_Jbox": null,
    "Dst_Jbox": null,
    "Src_Name01": "MCC-1",
    "Src_Name02": "Bucket 4A",
    "Src_Name03": null,
    "Dst_Name01": "Panel LP-B",
    "Dst_Name02": "TB-101",
    "Dst_Name03": null
  },
  {
    "Cond_Ident": 2,
    "Cond_Tag": "P-02",
    "Cond_Type": "PVC",
    "Cond_Size": "1\"",
    "Src_Jbox": "JB-14",
    "Dst_Jbox": null,
    "Src_Name01": "Transformer T-1",
    "Src_Name02": "Secondary Terminals",
    "Src_Name03": null,
    "Dst_Name01": "Panel LP-A",
    "Dst_Name02": "Main Lugs",
    "Dst_Name03": null,
    "Fill01_Type": "THHN",
    "Fill01_Color": "BLK",
    "Fill01_Size": "12",
    "Fill01_Quantity": 2,
    "Fill02_Type": "THHN",
    "Fill02_Color": "WHT",
    "Fill02_Size": "12",
    "Fill02_Quantity": 1,
    "Fill03_Type": "THHN",
    "Fill03_Color": "GRN",
    "Fill03_Size": "12",
    "Fill03_Quantity": 1
  }
]
```

---

## 6. Workflow Notes for Agents

- The agent receives the current conduit_index as JSON (copied from the UI).
- The agent adds, edits, or reorganizes rows and returns the complete updated array.
- The user pastes the returned array back and clicks **Apply**.
- After applying, the user clicks **Populate Fills** to recompute fill slots from fill_index.
- If the agent is authoring fill slots directly (not using Populate Fills), ensure slots are contiguous from `Fill01`.
