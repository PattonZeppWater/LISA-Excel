# IDP Extractor — Feature Spec

**Companion to:** `HANDOFF_IDP_Extractor_BuildGuide.md` (the build guidance + landmine map)
**Machine-readable ground truth:** `template_dictionary.json`
**Purpose of this doc:** define *what the tool is and must do*. The build guide sits on top of it and tells you how to build it without stalling.

---

## What the tool is (one paragraph)

A Python toolchain (library + control-panel GUI/exe + two Claude skills) that reads AIC electrical project sources — conduit/cable schedules, wiring diagrams, and AutoCAD drawings — and fills the **AIC IDP Workbook** (`ConduitIndex` + `FillIndex` sheets), which then feeds **LISA** (the drawing-generation tool). The workbook is the interface: get the conduits and their wire fill into it, correctly and in LISA's exact schema, without a human transcribing schedules by hand.

---

## The pipeline (two phases)

| Phase | Skill / module | Input | Output |
|-------|----------------|-------|--------|
| **1a — Conduits** | `idp-conduit-extractor` | Conduit schedules, site schematics, DWGs | `ConduitIndex` rows (one per conduit run) |
| **1b — Fill** | `idp-fillindex` | Wiring diagrams, cut sheets, cable schedules, DWGs | `FillIndex` rows (one per wire group / connection set) |

Phase 1b consumes Phase 1a's `ConduitIndex` (the `Conduit` column must match a `Conduit Name`).

---

## Feature set (what "done" looks like)

1. **Source-routed extraction.** Every source is read at the *highest reliability rung available* (see build guide "Extraction ladder"): DWG (structured) → Excel (IDP workbook / conduit list) → PDF text layer → ruled-table geometry → vision read. `idp_ingest.extract_source` routes by file type (`idp_excel` for `.xls*`, `idp_extract` for `.pdf`) and `merge_records` dedups across sources. The tool picks the rung; it does not force PDF scraping when structured data exists.
2. **Schema-faithful output.** Output matches `template_dictionary.json` exactly — headers, column positions, enums, and the connection-level `FillIndex` model. LISA reads by header text, so headers are sacred.
3. **Self-growing knowledge base.** Every project processed reinforces a persistent KB of naming conventions, abbreviation→value mappings, and symbol→block mappings, so repeat conventions stop needing to be re-solved.
4. **Editable remembered-logic.** The user can add/edit extraction rules (header aliases, value rules, symbol keywords, notes) at any time, persisted and applied on every run.
5. **Symbol inference.** S/D symbols are inferred from the symbol library by how blocks look / their name tokens, via the `symbol_cascade`.
6. **Confidence flagging.** Any uncertain or defaulted cell is flagged amber with a REVIEW comment. Defaults are never applied silently.
7. **Non-destructive.** The template is read-only (fill a copy). Prior filled workbooks are never overwritten — new runs write `_vN` versions, auto-named after the detected project (e.g. `73.1163_Stratford_IDP_FILLED.xlsm`).
8. **Control-panel GUI/exe.** A LISA-themed (dark navy) app exposes every scanning option and the remembered-logic editor; built to a standalone `.exe`.
9. **Per-cell provenance.** Every ConduitIndex/FillIndex value traces back to the exact source file it came from (or how it was derived), viewable/filterable/exportable in the control panel's Sources tab.
10. **Project-scoped symbol confirmation.** When the project's own AutoCAD drawings are reachable, S/D Symbol is upgraded to whatever block that project's finished drawings actually place — gated so a project's own (possibly outdated) naming can never inject a symbol illegal in the current template.
11. **Drawing-fidelity checks.** Beyond LISA-legality, output is checked against finished-drawing conventions (separate ground presence, symbol side consistency, fill-row coverage) so a generated IDP is more likely to match a real finished sheet, not just pass the dropdown gate.

---

## Success criteria

- A project's conduit schedule produces a `ConduitIndex` that a drafter would accept with only spot edits.
- The `FillIndex` reflects **connections** (terminals that land), not raw conductor counts, and its wire-label inputs produce LISA's `Src:Tag:Term / Dst:Tag:Term` labels correctly.
- Nothing the tool is unsure about is presented as certain — it is flagged.
- Re-running a project never destroys the previous result.
- The KB is measurably larger after each project (types, fill-types, symbols, colors recorded).

---

## Non-goals

- Not an AutoCAD plug-in and not a LISA replacement — it feeds LISA's existing workbook.
- Does not edit or "improve" source PDFs or DWGs. Read-only on all sources.
- Does not invent data. Empty beats fabricated; uncertain gets flagged, not guessed-and-hidden.
