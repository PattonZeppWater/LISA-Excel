# Finished IDP Anatomy — what LISA's output actually looks like

Reverse-engineered from finished AIC IDP drawings (Stratford 73.1163 package +
the canonical `ExampleIDP.pdf`, 73.1142). This is the **target**: the extractor's
workbook is correct only if it would render into sheets shaped like this. Pairs
with `template_dictionary.json` (input schema) and `lisa_symbols.json` (symbols).

---

## 1. The drawing set

A finished IDP set is a multi-sheet DWG/PDF (one `.wdp` AutoCAD Electrical project ties them together):

- **G1 — Cover sheet:** project title, AIC title block, DWG NO `<proj>-G1`.
- **G2 — Sheet index / general** (drawing list).
- **G3 — Symbols legend:** three panels — **Abbreviation List**, **Instrument Identification Format** (ISA letter grid), **Electrical Symbols**. This sheet *is* the naming/symbol convention key.
- **`<proj>-NNE` — one Interconnection Diagram sheet per conduit** (the payload). Sheet is named/keyed by the conduit (e.g. `C061`, `P100`, `A051`).

## 2. Anatomy of one interconnection sheet

Landscape 17×11. Three vertical zones under a band of tables.

### Top tables (full width)
- **DEVIATIONS & NOTES** — numbered notes (the `#` is what a ConduitIndex `Deviations Notes` cell references). e.g. `1 = CONDUIT TYPE TO BE UPDATED IN REDLINES`.
- **SUPPORTING DOCUMENTS** (two blocks) — `DRAWING NUMBER | DESCRIPTION | MANUFACTURER`. Sourced from ConduitIndex `Ref Documents` → the Ref Documents sheet.
- **CHANGE ORDERS & ERRORS** — revision tracking.

### SOURCE (left) │ FIELD (center) │ DESTINATION (right)

**FIELD header (center, the conduit itself):**
- `NAME:` conduit name · `TYPE:` conduit type + a **hex location symbol** · `SIZE:` trade size.
- The conduit is drawn as a vertical raceway channel.
- **FILL TYPE summary table:** `FILL TYPE | SIZE | COLOR | QUANTITY` — the conduit's fill **aggregated by (type, size, color)**. Uses drawing abbreviations (see §4).

**SOURCE / DESTINATION zones (mirror images):**
- **Zone header = the device name, 2 lines** = `Name 1` / `Name 2` (e.g. `PUMP CONTROL PANEL (PCP)` / `MOTOR CONTROL SECTION (MCS)`; `WELL` / `DISCHARGE PRESSURE SWITCH`).
- **Device sub-label(s):** a sheet can have multiple source groups (e.g. `LIT-051 POWER` from a breaker AND `PID / LEVEL CONTROLLER` from terminals).
- **Device symbol** at the terminal end (see §5).
- **Per wire, reading outward:** terminal box (`TB 101`, `T1`, `LIT+`) → **wire label** → **color** → **gauge**. The GND wire ends in a small ground circle.

**Title block (bottom):** REV/DATE/NAME/CHANGES grid · DRAWN BY / ENGINEER / DATE / PROJECT NO · AIC disclaimer + logo · `INTERCONNECTION DIAGRAMS / <conduit> / <section> / <device desc>` · `DWG NO <proj>-NNE` · PAGE · DWG STATUS.

## 3. Wire-label grammar (confirmed from real sheets)

`SRC…:TERM  /  DST…:TERM` — the **same label** prints on both the source and destination side of the wire.

- **Source side** = `Panel:Section:TagType:Tag` — e.g. `PCP:MCS:TB:101`, `PCP:MCS:CB:CBD1`, `PCP:MCS:RVSS-1:T1`.
- **Destination side** = `Location:Device:Term` — e.g. `WELL:PSH-061:COM`, `WELL:P-01:ØA`, `WELL:LIT-051:PWR`.
- **Phase terms** render `ØA / ØB / ØC` (AutoCAD `%%CA/%%CB/%%CC`).
- **Term vocabulary seen:** `COM, NO, NC`, `+ , -`, `PWR, GND`, `T1/T2/T3`, `ØA/ØB/ØC`, neutral `12N`, shield `SHLD`.

This is the workbook's TEXTJOIN output — the extractor fills the **tag/term inputs**, the workbook composes this string.

## 4. FILL TYPE abbreviations (drawing) ↔ workbook Type

| Drawing (FILL TYPE col) | Workbook `Type` |
|---|---|
| `PWR`  | `POWER` |
| `CTRL` | `CONTROL` |
| `TSP`  | `TSP` |
| `GND`  | ground wire (its own summary row) |
| `MFG`  | `MFG_CABLE` |

The FILL TYPE table aggregates identical `(type, size, color)` wires into one row with a QUANTITY.

## 5. Device-symbol conventions (destination clues)

- **ISA bubble (circle):** instruments/switches — `PSH-061` (pressure switch), `LE/LIT 051` (combined element/transmitter — two-line). Maps to the switch/instrument S/D symbols.
- **Motor:** circle annotated with **HP + FLA** (`P-01 / 60HP / 77FLA`).
- **Terminal block:** small rectangle (`TB 101`, `TB LIT+`).
- **Circuit breaker:** CB symbol with rating (`CBD1 3A`).
- **Ground:** small open circle at the wire end.

## 6. Archetypes (validate the fill against these)

- **Discrete control** (C061): `CONTROL`, per-contact terms (`COM/NO/NC`), `#14`, `RED`; dest = switch ISA bubble. + `GND` `GRN`.
- **3-phase power** (P100): `POWER` **3 phase landings** `ØA/ØB/ØC`, `#1`, colors **BRN/ORG/YEL** (480 V); dest = motor bubble w/ HP+FLA. Ground **separate**, `GRN #6`. → confirms: a multi-conductor / parallel feeder renders as **Wire Ct 3**, not the raw conductor count.
- **Analog instrument** (A051): `POWER` pair (`BLU`+`BLK`, `#14`) **plus** `TSP` signal pair (`RED/BLK`, `#18`, Ct 1) **plus** `SHLD`; dest = `LE/LIT` bubble with `GND/PWR/+/-`.

## 7. Color conventions (from finished drawings)

- **480 V 3-phase power:** ØA=`BRN`, ØB=`ORG`, ØC=`YEL`; ground=`GRN` (`#6`).
- **Discrete control:** color is **project-specific** — Stratford (73.1163) uses `BLU`, the 73.1142 example uses `RED`. Do **not** hard-default; the extractor learns each project's control color from its own data (`idp_anatomy._project_control_color`). `#14`; ground `GRN`.
- **Instrument power:** `BLU` (power) + `BLK` (neutral/return) (`#14`).
- **Analog signal pair (TSP):** `RED`(+)/`BLK`(−), written `RED/BLK` (`#18`); `SHLD` handled separately.

## 8. Input → output mapping (the generation logic)

| Finished-sheet element | Comes from |
|---|---|
| FIELD `NAME/TYPE/SIZE` | ConduitIndex `Conduit Name / Conduit Type / Conduit Size` |
| SOURCE header (2 lines) | ConduitIndex `Source Name 1 / 2` |
| DESTINATION header (2 lines) | ConduitIndex `Destination Name 1 / 2` |
| DEVIATIONS & NOTES | ConduitIndex `Deviations Notes` → Ref Documents sheet |
| SUPPORTING DOCUMENTS | ConduitIndex `Ref Documents` → Ref Documents sheet |
| Each wire (term/label/color/gauge) | one FillIndex connection (S/D Term, Color, Wire Gauge; label = TEXTJOIN) |
| SOURCE / DEST device symbol | FillIndex `S Symbol` / `D Symbol` |
| FILL TYPE summary table | FillIndex rows aggregated by (Type, Wire Gauge, Color) |

**Corollary for the extractor:** every conduit that will get a sheet needs ≥1 FillIndex row; the fill must match the archetype for its kind (power→phase landings + colors; analog→TSP+power+shield; discrete→per-contact CONTROL). These are the checks in `idp_anatomy.json`.
