# IDP backend test harness — Slice 1 (pure logic)

Fast, AutoCAD/Excel-free regression tests for the generation logic. Every test maps
to a real bug we fixed, so a green run means those regressions can't sneak back in.

## Run it

```
cd "...\IDP_Generation\backend"
"<LISA venv>\Scripts\python.exe" -m pytest
```
(The LISA venv has `pywin32`, `openpyxl`, and `pytest`. No AutoCAD or Excel app needs
to be open — these tests never touch COM.)

## What's covered

| File | Module | Key cases (bug it guards) |
|------|--------|---------------------------|
| `test_parser.py` | `parser` | duplicate instruments split into 2 groups (missing-instrument + lost colors); FunctionID→FunctIdent / ElementID→ElementIdent; hidden term → sentinel; hidden tag → blanked |
| `test_workbook_mapper.py` | `workbook_mapper` | TSP pairs combine with quantity (RED/BLK ×2, not RED/BLK/RED/BLK); GND drain breaks out; no fill row without colour/gauge; `#10`→`#10AWG` |
| `test_autocad_bridge_helpers.py` | `autocad_bridge` (pure helpers only) | 0.5 grid rounding; BlockIndex height lookup; hidden-term blanking keeps slots; unused tags blanked (clears block default); instrument ISA carried to attrs; tag collapse |
| `test_layout_plan.py` | `autocad_bridge.build_layout_plan` | **Slice 2 — full layout as data:** duplicate instruments → 2 instrument blocks; every wire has a colour; all blocks land on the 0.5 grid; conduit present; simple-loop shape; short-loop spacing; tall-block rounds spacing up; instrument grouping (1 block, per-row src) |

## Level 3 — real-AutoCAD smoke test (`smoke_render_plan.py` + `test_smoke_level3.py`)
Generates a fixture conduit's DWG, reopens it, reads back every block reference and
asserts each planned block is present at its planned (x, y) with matching name + key
attrs. This is the one check that needs AutoCAD; it **auto-skips when AutoCAD is
closed**, so the normal pure run is unaffected. Run it with AutoCAD open:
```
pytest tests/test_smoke_level3.py        # or: python tests/smoke_render_plan.py
```

## Slice 2 architecture (end-to-end without AutoCAD)
`generate_dwg` now splits into two stages:
- **`build_layout_plan(conduit_data, loop_list, block_heights)`** — PURE. Returns the
  whole drawing as data: `{conduit, items:[{role,name,x,y,visibility,attrs}], warnings}`.
- **`render_plan(model, plan, warnings)`** — the only COM-coupled part; inserts each
  block and sets its attrs/visibility.

Because the generator *renders the plan* (single source of truth), asserting the plan
== asserting the DWG. `test_layout_plan.py` checks block counts, positions, colours,
attrs and spacing with no AutoCAD. The remaining COM-only step (does `InsertBlock`
actually place it) is thin and deterministic — confirm it occasionally with a real
generation (an optional Level-3 smoke test on a machine with AutoCAD).

## Fixtures
`fixtures/dup_instrument.json` — parsed JSON of a real workbook (conduit `S01AIT003`
with two pasted identical 4-term loops). Regenerate from a workbook with:
`parser.parse_workbook(bytes, name)` → dump `conduit_index`/`fill_index`.

## Adding a case
When a new bug is found and fixed: add a fixture (or hand-author a row via
`conftest.fill_row(**overrides)`) and assert the corrected behavior. This is Slice 1
of the harness; Slice 2 (a `build_layout_plan` that exposes the whole drawing as data
for end-to-end assertions) is the planned follow-up.
