# LISA-Excel — Session Handoff

> Give this to the next Claude Code conversation to get oriented fast. It captures the
> project, the working agreement, the machine/environment facts, what's been done, and the
> gotchas we hit. (Claude also keeps auto-loading memory files that echo the key points.)

---

## 0. Read-me-first working agreement (IMPORTANT)
- **Commit freely, but NEVER push without explicit permission — ask every single time**, even mid-task. Committing locally is fine; pushing is not until the user says so for that specific push.
- The user is **Owen** (owen.libatique@wateraic.com / Lyles Group). Teammate **Patton** also pushes to this repo.
- Be surgical; **don't break existing working behavior** — that's priority #1 for the user.

---

## 1. What this project is
Two halves that work together:
1. **The Excel workbook** (`Workbook/IDP_Workbook_CurrentWIP_4.xlsm`) — engineers fill in conduits + wire fills + project info. Macro‑enabled; VBA is heavily self‑healing.
2. **LISA** — a Python/Flask + React desktop app (in this same repo under `Services/`, `Frontend/`, `app.py`) that reads the workbook via `openpyxl` and drives **AutoCAD Electrical** (via COM) to generate one IDP drawing (`.dwg`) per conduit — and optionally a full ACADE **project** (`.wdp`/`.aepx`).

The VBA text source lives in `vba/*.cls` / `*.bas` (the readable, diffable source of truth for the macros).

---

## 2. Repo, branches, remote
- **Repo:** `C:\Users\owen.libatique\LISA-Excel`  → remote `github.com/PattonZeppWater/LISA-Excel`
- **Branches:** `main` and `bih-branch` are currently **content‑identical** and both pushed. Patton pushes to `main`; Owen has used `bih-branch`. Everything lately lands on `main`.
- Re-check at session start: `git status -sb`, `git log --oneline -8`, and `git diff --stat main bih-branch`.

---

## 3. Machine / environment facts (Windows 11, corporate/managed)
- **VBA object model access WORKS now.** The user enabled Trust Center → Macro Settings → **"Trust access to the VBA project object model."** So Claude can edit the `.xlsm`'s VBA programmatically via Excel COM. (Setting the `AccessVBOM` registry alone did NOT work — the UI checkbox is what flipped it. Verify at session start: open a workbook via COM, check `VBProject.Name` is non‑empty.)
- **Base Python 3.12:** `C:\Users\owen.libatique\AppData\Local\Programs\Python\Python312\python.exe`
- **Node (for frontend builds):** installed at `C:\node20\node-v20.18.1-win-x64\` (use `npm.cmd` there). Node is NOT on PATH.
- **Trusted location:** `C:\trusted` is a policy trusted location. Downloaded/zipped files get "mark of the web" → Excel opens them in **Protected View** (Ctrl+V/Z/typing disabled) and **blocks macros** (`blockcontentexecutionfrominternet` policy). Fix: put the file in `C:\trusted`, or right‑click the zip → Properties → Unblock, or Enable Editing/Content.
- Shell here is Git Bash + PowerShell. Excel COM work is done via PowerShell scripts (kill stray `EXCEL.EXE` first; set `EnableEvents=$false`, `AutomationSecurity=1`).

---

## 4. How to make changes (the mechanics)
### VBA logic → edit `vba/*.cls`, then import into the `.xlsm`
1. Edit the `vba/*.cls` file.
2. Import into `Workbook/IDP_Workbook_CurrentWIP_4.xlsm` via COM. Sheet modules (`Sheet1`–`Sheet10`) and `ThisWorkbook` are **document modules** — you can't `Import` them; replace code in place via `component.CodeModule` → `DeleteLines(1, CountOfLines)` then `AddFromString(body)` where `body` is the file **from the `Option Explicit` line down** (skip the `VERSION`/`BEGIN`/`Attribute` header). Standard modules (`modUndo`, `modZP2`) can be imported or replaced the same way.
3. **Compile-check**: add a temp standard module with a no‑op `Sub`, `Application.Run` it (forces a full project compile), remove it, then `Save`.
4. Working scripts from this session are in the scratchpad (`import_pd.ps1`, `import_s10.ps1`, `verify_compile.ps1`) — reusable patterns.

### Layout / cell / comment changes → edit the `.xlsm` directly via COM
Set values, number formats, cell comments, etc. directly (e.g. `ws.Range("A3:H100").NumberFormat = "@"`). Save as macro‑enabled (keeps `vbaProject.bin`).

### LISA Python → edit under `Services/IDP/IDP_Generation/backend/`
Then sanity‑check with `py_compile`. There's no live test harness without AutoCAD; unit-style checks import a module and exercise pure functions (e.g. `_with_awg`, `_gauge_label`).

### Frontend (React) → edit `Frontend/frontend/src/…`, then rebuild
`dist/` is **gitignored** and is what `app.py` serves — so a source change only shows after a rebuild:
```
cd Frontend/frontend
"C:\node20\node-v20.18.1-win-x64\npm.cmd" install    # once
"C:\node20\node-v20.18.1-win-x64\npm.cmd" run build  # -> Frontend/frontend/dist
```
Requires `CommonTools/Frontend/` (now committed — `useFileInput`, `downloadBlob`).

---

## 5. What's been done (features shipped to `main`)
**Excel / VBA (in `vba/` + the `.xlsm`):**
- Self‑heal fixes: **Project Description (Sheet10)** restores header text + formatting; **Ref Docs (Sheet8)** restores the row‑1 white‑text title bands; **FillIndex (Sheet2)** restores the header box border (was being stripped on Ctrl+A/rebuild).
- **Wire label**: destination side now dedupes `Name==Tag` like the source side (`xfmr-100:xfmr-100:%%CA` → `xfmr-100:%%CA`).
- **"Hide from Generation"** is now a **right‑click** toggle (`Worksheet_BeforeRightClick`), replacing the single‑item dropdown whose 365 autocomplete turned typed letters (f/g/h) into the hide option.
- **Rating** header comments tell users to include units.
- **Project Description** sheet: data area kept as **Text** (`@`) + **columns auto‑fit**.
- (Patton) FillIndex paste fixes, wire‑label auto‑widen, **Max Wire Label Length = 500**, title‑block auto‑populate.

**LISA (Python):**
- **`3/0` (aught) sizes**: table shows `#3/0` (no `AWG`), matching the wire label. Other AWG unchanged (`#12AWG`). — `workbook_mapper._with_awg`.
- **MCM stays MCM** (not auto‑converted to KCMIL); `250 mcm` → `250MCM`. — `_split_gauge` in `workbook_mapper.py` AND `autocad_bridge.py` (`_gauge_label`).
- **"Make it a project" checkbox** (main priority feature): when checked (project number required), a generate run assembles a full ACADE project in the output folder — copies the GENERAL sheets **G1–G3** (renamed to the project number), writes a sectioned **`.wdp`** (GENERAL + INTERCONNECTION DIAGRAMS) and a matching **`.aepx`**, with title‑block descriptions from the Project Description sheet. Unchecked = old behavior. — `wdp_writer.ensure_project_sheets / write_full_project_wdp / write_project_aepx`; template bundled at `Services/IDP/_Templates/Project/` (G1–G3 `.dwg`, `.wdl`, `.wdt`). Frontend checkbox in `IdpGeneration.jsx`.
- (Patton) per‑drawing Description 1/2/3 in the `.wdp` (`record_dwg_descriptions`, JSON sidecar), title‑block auto‑populate, template‑pick fix, Rev04, project‑scoped drawing lists.
- **Launcher/Setup** (`LAUNCH LISA.bat` / `SETUP - Run First.bat`): hardened (verify the venv actually runs, not just exists; rebuild `.venv` cleanly on setup) AND **layout‑aware** — they `cd` into `LISA\` only if it exists (bundle), else stay next to the `.bat` (repo). So they work from the repo *and* a bundle.

**Project Description ↔ `.wdp` mapping (1:1, already wired):** Owner→`*[1]`, Job Title→`*[2]`, Content→`*[3]`, Proj No.→`*[4]`, Status→`*[5]`, Date→`*[6]`, Engineer→`*[7]`, Drafter→`*[8]` (matches the template's `.wdl` LINE names).

---

## 6. LISA architecture cheat‑sheet
- **Unified app**: `app.py` (repo root) — one Flask process on port 5000, serves the React build from `Frontend/frontend/dist`, blueprint mounted at `/api/idp-gen`. Packaged/desktop via webview.
- **Generate route**: `Services/IDP/IDP_Generation/backend/routes/generate.py` → `/generate` (one conduit per POST; frontend loops conduits). Reads `project_desc`, `project_number`, `make_project`, `output_folder`, etc. Writes DWG(s) via `autocad_bridge`, then the `.wdp`/`.aepx`.
- **Services** (`backend/services/`): `parser.py` (reads workbook incl. the "Project Description" sheet → `project_desc`), `workbook_mapper.py` (column aliases, fill slots, `_with_awg`/`_split_gauge`), `autocad_bridge.py` (COM to a running AutoCAD; copy template DWG → open → render → SaveAs), `wdp_writer.py` (project files).
- **Templates**: per‑conduit DWG template in `Services/IDP/_Templates/*.dwg`; project template in `Services/IDP/_Templates/Project/`.
- **Frontend**: `Frontend/frontend/src/pages/IdpGeneration.jsx` (React + Vite). State persisted to `localStorage`. Payload → `/api/idp-gen/generate`.

---

## 7. Repo vs bundle (the recurring confusion — read this)
- **The repo** (`LISA-Excel`) is the **source of truth** and is what git tracks. App is at the **root** (no `LISA/` subfolder).
- **A "bundle"** (`Desktop\LISA_Versions\LISA_IDP_Testing_v1.0.xx\`) is a **separate, runnable packaged copy** — has a `LISA/` subfolder + built `dist/` + its own `.venv/`. **Git never touches a bundle.** Pulling/merging the repo does NOT update a bundle, and editing a bundle is not tracked.
- **Rule:** do all edits + git in the repo; treat bundles as disposable build outputs (regenerate from the repo when needed).
- The user asked about a repeatable **`build-bundle` script** (not built yet) — a one‑command way to produce a fresh bundle from the repo. Offer it if bundle drift comes up again.

### How to run/test LISA
- **From the repo** (now works thanks to layout‑aware bats): open AutoCAD, then `LAUNCH LISA.bat` in `LISA-Excel\`. Needs the repo `.venv` (rebuilt this session — complete) and `dist/` (built this session). If deps/venv look off, run `SETUP - Run First.bat` and **let it finish** ("Setup complete!").
- **From a bundle**: `Desktop\LISA_Versions\LISA_IDP_Testing_v1.0.{23,28,29,30}`. v29/v28 venvs are good; **v30's venv is currently broken** (an interrupted SETUP left it half‑installed) — re‑run its SETUP to completion or rebuild, or just run from the repo.

---

## 8. Known gotchas & open items
- **Binary `.xlsm` merge conflicts**: if two people edit the workbook, git can't auto‑merge it. Resolve by taking one side's `.xlsm` and re‑importing the merged `vba/` (which DOES merge cleanly) — that's how the last two merges were fixed. Coordinate workbook edits when possible.
- **`SETUP` must run to completion** — if interrupted, the `.venv` is left half‑built (missing `flask` etc.) and LISA won't start. Symptom seen this session. Fix: re‑run SETUP fully (it now deletes and rebuilds `.venv` cleanly).
- **Frontend `dist` is gitignored** → rebuild (`npm run build`) after any frontend change or the app shows the old UI.
- **NOT YET VERIFIED IN ACADE**: the "Make it a project" `.wdp`/`.aepx` output was built to mirror the AIC template exactly, but hasn't been confirmed to open correctly in actual AutoCAD Electrical. **This is the top thing to verify next.** If it needs tweaks, likely spots: conduit `===` subsection labels, file naming, the `.aepx` FileID/ProjectPath, or section structure in `wdp_writer.write_full_project_wdp`.
- **Project template source**: the untouched original is the user's `Desktop\For Claude\` folder. The cleaned copy in the repo is `Services/IDP/_Templates/Project/`.
- A `LISA_IDP_User_Guide.md` (end‑user how‑to) was drafted earlier but is not committed (got cleaned out) — re‑create if wanted.

---

## 9. Key paths
| Thing | Path |
|---|---|
| Repo | `C:\Users\owen.libatique\LISA-Excel` |
| Workbook | `Workbook/IDP_Workbook_CurrentWIP_4.xlsm` |
| VBA source | `vba/Sheet1..10.cls`, `ThisWorkbook.cls`, `modUndo.bas`, `modZP2.bas` |
| LISA backend | `Services/IDP/IDP_Generation/backend/{routes,services}/` |
| Frontend page | `Frontend/frontend/src/pages/IdpGeneration.jsx` |
| Project template | `Services/IDP/_Templates/Project/` |
| Conduit DWG templates | `Services/IDP/_Templates/*.dwg` |
| Launcher / setup | `LAUNCH LISA.bat`, `SETUP - Run First.bat` (repo root) |
| Test bundles | `…\Desktop\LISA_Versions\LISA_IDP_Testing_v1.0.*` |
| Original project template | `…\Desktop\For Claude\` |
| Base Python 3.12 | `…\AppData\Local\Programs\Python\Python312\python.exe` |
| Node 20 | `C:\node20\node-v20.18.1-win-x64\` |

---
*Handoff written 2026‑07‑21. Verify git state + VBOM access at the start of the next session before relying on the above.*
