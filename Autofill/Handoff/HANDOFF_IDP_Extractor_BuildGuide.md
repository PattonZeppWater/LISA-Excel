# IDP Extractor — Build Guide & Landmine Map

**Companion to:** `HANDOFF_IDP_Extractor_Spec.md` (the feature spec)
**Machine-readable ground truth:** `template_dictionary.json`
**Purpose of this doc:** get anyone (human or AI) productive on the IDP Extractor without re-hitting the failures we already paid for.

---

## READ THIS FIRST (instructions for the AI assistant)

You are helping build/extend the AIC **IDP Extractor** — a Python toolchain that fills the IDP Workbook from electrical project sources, feeding LISA. Before you write code or scrape anything:

1. Read the spec (`HANDOFF_IDP_Extractor_Spec.md`) for the full feature set and success criteria. This file is the *build guidance and landmine map* on top of it.
2. Treat **"Ground truth (verified, non-negotiable)"** below and everything in `template_dictionary.json` as fact. Do **not** reconstruct the template schema, enums, or column positions from memory — load the JSON.
3. **Pick the extraction rung before you extract** (see "The extraction ladder"). The single biggest past mistake was scraping rendered PDFs when a structured DWG existed. Do not default to PDF text.
4. Re-read **"Things you (the AI) will get wrong"** before generating extraction or fill code. Those are the specific mistakes already made and corrected on this project.
5. Build/extend in the milestone order given. Prove the risky read (structured source) before polishing UI or labels.

---

## Ground truth (verified, non-negotiable)

### Runtime environment

| Thing | Value | Why it matters |
|-------|-------|----------------|
| Language | Python 3 | Library + Tkinter GUI + PyInstaller exe |
| Workbook I/O | **openpyxl with `keep_vba=True`** | The workbook is `.xlsm` with macros; anything else corrupts/strips them |
| Template | `IDP_Workbook_CurrentWIP_3.xlsm` (canonical sample `05_IDP_Workbook_CurrentWIP_3.xlsm`) | LISA reads it by header text — headers are sacred |
| KB / logic store location | **`%LOCALAPPDATA%\AIC_IDP_Extractor\`** | **NOT** the OneDrive project folder — OneDrive file locks cause "Access is denied" |
| AutoCAD DWG reads | **ObjectDBX (`AxDbDocument`), attach to a RUNNING AutoCAD** | Headless, fast (~156 blocks/dwg). Cannot cold-start COM from the sandbox ("Server execution failed") |
| PDF fast scan | PyMuPDF / `fitz` (text + page render + sub-PDF carve) | pdfplumber is table-only and fails on CAD-drawn schedules |
| Exe build | PyInstaller `.spec` (`IDP_ControlPanel.spec`), `--noconfirm` | hiddenimports must list every local module |
| Output policy | **Never overwrite** — `versioned_path()` writes `_vN` | User wants every prior extraction preserved |

### Template schema
See `template_dictionary.json`. Summary of the traps within it:
- `FillIndex` **`Type`** column = `POWER / CONTROL / TSP / MFG_CABLE` (+ `FIBER / CAT-6 / PULL_ROPE` at Wire Ct 1). It is **NOT** the PickList FillType (THHN/Cat5e/…).
- `FillIndex` **`Wire Ct`** = **connection count**, not raw conductor count. A 37-conductor cable landed whole = one `MFG_CABLE`, Wire Ct 1.
- `D Tag` columns are **non-contiguous** (AF, then AI–AK). `S Tag 1 ` / `D Tag 1 ` headers have a **trailing space**. Match by position, not exact string.
- **Wire Label** columns (BA–BD) are **TEXTJOIN-computed inside the workbook**. Do not populate them unless mode = `Custom`.

### LISA handoff rules
- Template is **read-only**; fill a copy.
- LISA matches columns by **header text** — never rename, reorder, or "clean up" headers.
- Wire labels are computed by the workbook's own TEXTJOIN formulas from the tag/term inputs; the tool fills the *inputs*, not the label.
- Wire-label grammar LISA prints: `SrcName:SrcTag:SrcTerm / DstName:DstTag:DstTerm` (empty segments collapse, name drops when it equals the tag; 3-phase terms use `%%CA/%%CB/%%CC`).

### LISA input contract — how LISA actually consumes the workbook
(From `How LISA Works.md` + the workbook's own data validations. Frozen in
`Handoff\lisa_symbols.json`; validated by `lisa_contract.py`.)

LISA reads the **FillIndex** sheet, matches each **symbol name to a real AutoCAD
block** in its library, and drives AutoCAD to draw one DWG per conduit. So the
output is only generatable if every row obeys the workbook's own dropdown logic:

- **Type** must be a member of named range `Type_<WireCt>` (Ct 1 → POWER/CONTROL/TSP/MFG_CABLE/FIBER/CAT-6/PULL_ROPE; Ct 2,3,4,6,8 → first four only).
- **S Symbol** ∈ named range `KEY(Type)_<WireCt>_L`; **D Symbol** ∈ `KEY(Type)_<WireCt>_R`, where **`KEY(Type) = Type with dashes removed`** (`CAT-6`→`CAT6`). The dropdown formula is literally `INDIRECT(SUBSTITUTE($C,"-","")&"_"&$B&"_L")`. **A symbol outside this set = LISA has no block to map = no drawing.** This is the #1 way output silently fails.
- Each symbol activates exactly **`BlockTags[symbol]`** Tag/Term slots; the workbook **greys out** the rest. Do not fill more Tag/Term columns than the symbol's slot count.
- Tag/Term cells accept the special value **`Hide from Generation`** to suppress an element.
- Wire labels have a **Max Wire Label Length** (cell right of the label columns); an over-length computed label turns red. Keep tag/term inputs short enough that the TEXTJOIN result fits.
- Generation is **per-conduit**. LISA can take a **conduit-list `.txt`** (names, one per line or comma-separated) to generate a subset — a cheap, useful extractor output.
- Symbol names are the `symbol_cascade` key format we already use: `<KEY>_<Ct>_<L|R>`. Our cascade *is* LISA's dropdown mechanism — keep them identical.

**Rule:** run `lisa_contract.check_records()` on the fill before declaring a
workbook done. Zero issues = LISA-generatable. Snap near-miss symbols with
`snap_symbol()`; flag anything that can't be snapped.

---

## The extraction ladder (pick the highest rung the source qualifies for)

PDF is a *presentation* format, not a data format. Reliability descends this ladder — always start at the top rung the source supports.

1. **Native source (DWG / DXF / source XLS).** Structured, exact, deterministic. Read with ObjectDBX. **This is the front door whenever a DWG exists.**
2. **PDF text layer** (`fitz` / pdfminer). Born-digital text. Good for notes, title blocks, and *some* schedules.
3. **Ruled-table geometry** (pdfplumber / camelot). Only when the table has real gridlines / clean columns.
4. **Vision read** (render page → read the image). Last resort for CAD-drawn schedules with no usable text layer.

Everything, regardless of rung, is normalized through the **knowledge base** (abbrev/symbol/token → template value). The KB is rung-agnostic and grows every run — that is where the "learns over time" value lives, **not** in a raw PDF-text firehose.

> Reality check from this project: on the 73.1188 conduit schedule, pdfplumber found **0 tables / 37 words** across ~60 conduits — the schedule was CAD-drawn vector graphics. That page is a rung-4 source. But its parent DWGs are rung-1. Read the DWG.

---

## Ordered build path (milestones with done-checks)

Do these in order. Do not jump to polish (labels, colors, UI theme) before the structured read works.

0. **Schema loads.** Tool loads `template_dictionary.json`; column positions match the live template's header rows. *Done-check:* dump the template headers, diff against the JSON, zero mismatches.
1. **Round-trip write.** Copy template → write one hardcoded ConduitIndex row → open in Excel, macros intact, header untouched. *Done-check:* file opens clean, LISA-relevant headers unchanged.
2. **Structured read (rung 1).** Attach to running AutoCAD, ObjectDBX-scan one DWG, list blocks + attributes. *Done-check:* block names + Tag/Term attributes printed for a known drawing.
3. **ConduitIndex from a real source.** Produce ConduitIndex rows for one project via the correct rung. *Done-check:* rows match the drafter's schedule on spot-check.
4. **FillIndex (connection-level).** Produce FillIndex rows keyed to those conduits, Wire Ct = connections, Type in the correct domain. *Done-check:* a power L+N pair = one row Ct 2 POWER; a multi-conductor cable = one MFG_CABLE flagged.
5. **Symbols + flagging.** Attach S/D symbols via cascade; flag every uncertain/defaulted cell amber. *Done-check:* defaults visible and flagged, none silent.
6. **LISA-readiness gate.** Run `lisa_contract.check_records()` on the fill; snap near-miss symbols, flag the rest. *Done-check:* zero unresolved contract violations — every symbol is in its `KEY(Type)_<Ct>_<L|R>` dropdown and no row over-fills its slot count. Only a workbook that passes this can generate finished IDPs.
7. **KB + remembered logic.** Every run reinforces the KB and applies user logic rules. *Done-check:* KB counts grow after a run; a user-added header alias takes effect.
8. **Polish.** Wire-legend colors, big-feeder Wire Ct splitting, conduit-list `.txt` output, control-panel UX. Only after 0–7 hold.

Ship a working step 4 before touching step 7. A correct headless ConduitIndex+FillIndex beats a pretty UI that mislabels wires.

---

## HARD PART 1: Reading CAD-drawn schedules

The schedule "table" in an AIC drawing is often **floating line segments + text strokes with no ruling relationships** — sometimes text stored as vector geometry. pdfplumber/camelot return nothing useful.

- **First, try to avoid it:** is there a DWG? Read it (Hard Part 2). The schedule you're squinting at is a rendered view of structured data.
- **If PDF-only:** locate the schedule page fast with `fitz` (search for "CONDUIT" / "SCHEDULE"), render that page to PNG, and read it visually. Transcribe into the record structure; flag anything ambiguous.
- **Do not** trust a pdfplumber table that came back suspiciously empty or ragged — that is the failure mode, not a valid parse.

## HARD PART 2: Reading DWGs headless

- Use **ObjectDBX (`AxDbDocument`)**, attaching to an **already-running** AutoCAD via `GetActiveObject`. You cannot cold-start AutoCAD COM from the sandbox — you get "Server execution failed".
- **Never kill a COM scan mid-document-open.** It wedged AutoCAD once and hung every subsequent attach. Let scans finish, or don't start them.
- Work on **copies** in the Claude Files tree; never open or alter the user's source DWGs.
- What the blocks mean (learned on the "test project for multiple drawings" set): a **Conduit** block → a ConduitIndex row + its `Fill0N` fills; **device** blocks carry `symbol` + `Tag1-4` + `Term1-4`; **WIRE_IDP** blocks carry labels. Pair `_L`/`_R` by Y-row, then first-L ↔ first-R by X.

## HARD PART 3: The FillIndex connection model

This is where the schema bites hardest.

- One FillIndex row = one **wire group / connection set**, *not* a cable. Power L+N → **one row, Wire Ct 2**. A 37-conductor control cable → usually **one `MFG_CABLE`, Wire Ct 1** (or split into individual CONTROL wires — a drawing-level call the cable schedule can't make; **default MFG_CABLE and flag it**).
- `Type` lives in the `Type_<n>` domain filtered by Wire Ct (see JSON). Do not pull from the THHN/Cat5e PickList.
- Per-wire tags/terms come from **wiring diagrams or the FILLWIRELABEL LISP**, not the cable schedule. The cable schedule alone yields a partial fill — say so and flag it.

---

## Things you (the AI) will get wrong

Pre-empting the specific hallucinations already hit on this project:

- **You will scrape the PDF by default.** For AIC drawings the structured DWG is the real source. Check for a DWG first (extraction ladder rung 1).
- **You will assume "convert PDF to text" solves extraction.** CAD-drawn schedules have no useful text layer (0 tables / 37 words on a 60-conduit page). Text conversion is a *fallback*, not the front door.
- **You will fill `FillIndex.Type` from the PickList (THHN/Cat5e).** Wrong. It is `POWER/CONTROL/TSP/MFG_CABLE` (+FIBER/CAT-6/PULL_ROPE at Ct 1).
- **You will set `Wire Ct` to the raw conductor count.** It is the *connection* count. A whole multi-conductor cable is Ct 1.
- **You will populate the Wire Label columns.** They are TEXTJOIN-computed in the workbook. Fill the tag/term inputs; leave labels blank unless mode=Custom.
- **You will rename or tidy the headers.** LISA matches by header text. `S Tag 1 ` has a trailing space; `D Tag` columns are non-contiguous. Leave them exactly as-is.
- **You will put the KB in the OneDrive project folder.** OneDrive locks cause "Access is denied". KB + logic store live in `%LOCALAPPDATA%\AIC_IDP_Extractor\`.
- **You will try to cold-start AutoCAD via COM.** "Server execution failed". Attach to a running instance with ObjectDBX; never kill a scan mid-open.
- **You will overwrite the last filled workbook.** Never. Use `versioned_path()` → `_vN`. The user wants every prior result preserved.
- **You will silently apply a default gauge/color/type.** Every default must be surfaced and the cell flagged amber.
- **You will fabricate a value to fill a blank.** Empty beats invented. Uncertain gets flagged, not guessed-and-hidden.
- **You will trust raw block-placement frequency as "how a project uses a symbol."** AIC's template stamps a full reference copy of nearly every block at fixed coordinates on every sheet. Filter by position (a coordinate recurring on most sheets = template palette, not usage) before counting — see `idp_project_symbols._template_positions`.
- **You will let a project's own DWG block names override the current template's dropdown.** A project's finished drawings can be from an OLDER template revision with different block names. Only accept a project-observed name if it's ALSO legal in the CURRENT `lisa_symbols.json` contract (`idp_project_symbols._legal_symbols`) — otherwise silently prefer the generic cascade.
- **You will shell out to a bundled `.py` script via `sys.executable` from inside a frozen exe.** Inside PyInstaller's exe, `sys.executable` is the exe itself, not a Python interpreter, and loose source files aren't on disk to hand to a subprocess. Import the module and call its function in-process instead (see `idp_dwg_scan.main()` being called directly, not via `subprocess.run`).
- **You will call `idp_dwg_scan.py` with just a project ROOT folder.** Its folder mode is NOT recursive (`os.listdir`, not `os.walk`) — a root containing only subfolders scans zero files silently. Gather the file list recursively yourself (`idp_project_symbols.find_project_dwgs`) and pass it explicitly with `--out <cache>`.
- **You will assume any string cell value is safe to write as-is.** openpyxl treats ANY string starting with `=` as a FORMULA the instant it's assigned — even plain extracted text. Confirmed as a real production bug: a CAD-symbol-font PDF misread a device name into garbled text that happened to start with `=` ('= - 27 - = -'); openpyxl silently stored it as a formula; Excel's parser choked on the invalid syntax and refused to open the file ("we found a problem with some content"). Every `write_workbook` call now runs `_sanitize_formula_leaks()` over everything it wrote (excluding the real TEXTJOIN label columns) before saving — but if you add a NEW code path that writes cells outside `write_workbook`, you must sanitize it too.
- **You will let a prior OUTPUT (`_FILLED`/`_NoGrey`) be reused as the TEMPLATE input.** A de-greyed or already-filled copy can carry a damaged/truncated `vbaProject.bin` (confirmed: one stray `_NoGrey` sample had a VBA project 86KB smaller than the real template's) — `write_workbook`/`degrey()` faithfully copy forward whatever VBA blob the template has, so garbage in is garbage out. `idp_write.check_template_sane()` blocks this (checked in `write_workbook` and at both template-selection points in the control panel) — never bypass it.
- **You will assume `S/D Type` and `S/D Quantity` (FillIndex cols AA/AB, AY/AZ) are general device-type/quantity fields.** They are NOT — per LISA's own parser (`IDP_Builder/resources/reference/LISA_workbook_mapper.py`), these are **Spare-block-only** attributes (`Src_SpareType`/`Dst_SpareType`, `Src_SpareQty`/`Dst_SpareQty` on `Spare_L`/`Spare_R` blocks). Writing them on a non-spare device is inert to LISA. `idp_write.py` gates these on `"spare" in s_symbol.lower()`.
- **You will assume `S/D ISATag_Loop#/ElementID/Element#/FunctionID` (cols N-Q, AL-AO) apply to every device.** They only matter for **instrument** blocks (symbol name contains `"inst"`) — LISA maps them onto the instrument's ISA bubble. `idp_write.py` gates these on `"inst" in s_symbol.lower()` / `d_symbol.lower()`.
- **You will forget `S/D Rating` and `S/D Description 1-3` (cols V/W, X-Z, AT/AU, AV-AX) exist and are safe to populate generally.** Unlike Type/Quantity/ISATag, these ARE general-purpose (any device) per LISA's mapper. A cable schedule's REMARKS column is exactly the kind of real data that used to be silently discarded — see `fill_56_1125.py`'s `d_desc` population, flagged via `d_desc_note` since source-vs-destination attribution of a remark is a judgment call.
- **You will emit free-text or plausible-looking symbol names.** LISA only accepts symbols in the `KEY(Type)_<Ct>_<L|R>` dropdown. A symbol that isn't in that exact named range produces no block and no drawing. Always validate with `lisa_contract.py` and snap/flag.
- **You will fill Tag/Term columns beyond the symbol's slot count.** The workbook greys those out; extra values are ignored or break generation. Respect `BlockTags[symbol]`.

---

## Provenance (so these facts don't get "corrected" away)

- Template schema + column positions: dumped from `05_IDP_Workbook_CurrentWIP_3.xlsm` and encoded in `idp_write.py` (`CI_*` / `FI_*` constants) and `template_dictionary.json`.
- FillIndex Type domain, Wire Ct semantics, wire-label grammar, phase codes: reverse-engineered from LISA's `WIRE_IDP_PROJECT.xls` + a real generated IDP.
- DWG block meanings: ObjectDBX scan of the "test project for multiple drawings" set.
- The empty-table / DWG-is-the-source lesson: 73.1229 (Walnut Creek) and 73.1188 (Crows Landing) extractions.
- LISA input contract: `How LISA Works.md` (Patton Zepp) + the workbook's own data validations, frozen into `Handoff\lisa_symbols.json` by `export_lisa_contract.py` and enforced by `lisa_contract.py`.
- Finished-output anatomy + drawing conventions (colors, phase terms, fill-type abbreviations, archetypes): reverse-engineered from finished IDP drawings (Stratford 73.1163 + `ExampleIDP.pdf`), captured in `Handoff\finished-idp-anatomy.md` + `Handoff\idp_anatomy.json`.
- Project identity, per-cell provenance, project-DWG symbol scanning: built from a live ObjectDBX scan of Stratford's 45 finished-IDP DWGs. Key discovery: AIC's IDP drawing template stamps one copy of (nearly) every library block at a FIXED (x,y) on every sheet as a reference palette — real per-conduit usage is whatever ISN'T at those repeating positions (confirmed: 5889 raw block instances -> 697 real placements after filtering). Also discovered Stratford's DWGs use an OLDER block-naming convention (`Ground`/`CircuitBreaker_3Pole_Neut`/`Disconnect_3Pole_Neut`/`Inst_2W`) that does NOT match the CURRENT template's legal dropdown (`GND`/`CB`/`DISC`/`Inst_2W (Field_2Term)`) — project-symbol upgrades are gated to only ever suggest a name that's legal in the CURRENT contract, so a stale project naming convention safely falls back to the generic cascade instead of injecting an illegal symbol.

If any of the above conflicts with your training assumptions, the artifacts above win.
