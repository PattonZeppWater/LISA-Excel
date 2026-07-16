# LISA Excel — IDP Workbook

The Excel side of **LISA**: the macro-enabled workbook (`IDP_Workbook_CurrentWIP_3.xlsm`) that engineers fill out to describe conduits and wire fills, which LISA's Python backend reads to generate AutoCAD electrical IDP drawings.

## Contents

- **`Workbook/IDP_Workbook_CurrentWIP_3.xlsm`** — the actual workbook (binary, macro-enabled).
- **`vba/`** — the workbook's VBA source, exported as plain text so it's diffable and reviewable in git. This is the same code embedded in the `.xlsm`'s `vbaProject.bin` — the exported files here are the readable source of truth; **edit them by re-exporting from the workbook after making changes in Excel's VBA editor** (or by importing them back in), not by hand-editing and expecting the `.xlsm` to pick them up automatically.

## VBA module map

VBA sheet modules are identified by a fixed **codename** (`Sheet1`, `Sheet2`, ...) that doesn't change even if the sheet tab is renamed. Mapping at time of export:

| File | Codename | Sheet tab name |
|---|---|---|
| `Sheet1.cls` | Sheet1 | ConduitIndex |
| `Sheet2.cls` | Sheet2 | FillIndex |
| `Sheet3.cls` | Sheet3 | PickList |
| `Sheet4.cls` | Sheet4 | Document Manager |
| `Sheet5.cls` | Sheet5 | BlockIndex |
| `Sheet6.cls` | Sheet6 | BlockTags |
| `Sheet7.cls` | Sheet7 | BlockLib_ACAD |
| `Sheet8.cls` | Sheet8 | Ref Documents & Deviations |
| `Sheet9.cls` | Sheet9 | SymLR |
| `Sheet10.cls` | Sheet10 | Project Description |
| `ThisWorkbook.cls` | ThisWorkbook | (workbook-level events) |
| `modUndo.bas` | modUndo | standard module — custom Ctrl+Z/Ctrl+Y undo/redo |
| `modZP2.bas` | modZP2 | standard module |

## What the VBA does (high level)

- **Self-healing headers & validation** — every data sheet restores its own header row and dropdown validations if a user accidentally clears them (`*_EnsureHeaders` / `*_EnsureValidations` subs, wired into each sheet's `Worksheet_Change`).
- **FillIndex (Sheet2)** — the largest module. Drives symbol-based cell greying (attributes that don't apply to the picked block get greyed and cleared), auto-generated wire-label formulas, per-row rebuild logic, and the wire-label-length warning.
- **ConduitIndex (Sheet1)** — the Ref Documents / Deviations Notes multi-select "checkmark" dropdowns, plus the Conduit Type dropdown (dynamically sized off Document Manager's list, so it never goes stale as types are added).
- **modUndo** — binds/unbinds Ctrl+Z / Ctrl+Y to a custom multi-level undo stack while ConduitIndex or FillIndex is the active sheet (native Excel undo gets wiped by the rebuild logic, so this restores expected undo behavior).

## Related

This workbook is consumed by the LISA backend (Python/Flask), which parses it via `openpyxl` and drives AutoCAD via COM to generate drawings. That code lives in a separate part of the LISA project, not in this repo.
