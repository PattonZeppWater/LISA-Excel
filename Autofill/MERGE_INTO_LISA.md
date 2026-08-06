# Merging IDP Extraction into LISA — seamless, no bugs

The IDP Extractor is now packaged as a **self-contained Flask blueprint** (`idp_web_panel.idp_bp`)
that mounts into LISA's existing Flask app with **no port, route, asset, or namespace conflict**.
This is the exact same tech stack LISA already uses (Flask + pywebview + a static web UI), so the
merge is additive — LISA keeps everything it has, and gains one "IDP Extraction" module.

> LISA source is **not** modified by the extractor. These are the (small) changes to make on the
> LISA side to absorb the module. Each is isolated and reversible.

## 1. Make the module importable
Copy the `IDP Extractor/` package next to LISA's `app.py` (or `pip install -e` it), so
`import idp_web_panel` works from LISA's environment. All extractor modules are namespaced `idp_*`
(plus `mapping_table`, `symbol_infer`, `logic_store`, `kb_expand`) — none collide with LISA's names.

## 2. Register the blueprint (one line in LISA's app.py)
```python
import idp_web_panel
idp_web_panel.register(app, prefix="/idp")     # mounts the whole module under /idp
```
This adds, all under `/idp` (nothing at LISA's existing routes):
- `GET  /idp/`                     → the IDP Extraction page (LISA-styled, self-contained)
- `GET  /idp/static/<asset>`       → its CSS/JS (relative asset links, so the prefix “just works”)
- `POST /idp/api/scan`             → start a scan (JSON body = options; see below)
- `GET  /idp/api/poll`             → `{log, running}` for live progress
- `GET/POST /idp/api/logic`        → remembered-logic rules
- `GET  /idp/api/provenance`       → per-cell provenance
- `POST /idp/api/route`            → project-folder routing report (preview)
- `POST /idp/api/suggest_output`   → `<Site>_FILLED.xlsm` path in the dictated folder

Verified: mounting on a host app that already owns `/` leaves the host's routes intact and every
`/idp/*` route (page, static, API) returns 200.

## 3. Add the sidebar item (LISA's React nav)
Add one entry to LISA's sidebar tree pointing the content area at `/idp/` (iframe or route):
```
{ label: "IDP Extraction", url: "/idp/" }
```
The page already renders in LISA's palette (it reuses LISA's `index-*.css`), so it looks native.

## 4. Driving the backend from LISA's React (HTTP, no pywebview needed)
React calls the JSON endpoints directly:
```js
await fetch("/idp/api/scan", {method:"POST", headers:{"Content-Type":"application/json"},
  body: JSON.stringify({files:[...abs paths...], template, output, mode:"auto",
                        hi_ocr:true, clear_dev:true, vision_assist:true})});
// then poll /idp/api/poll until running===false
```
File **paths** come from LISA's own file picker (or the shared pywebview `create_file_dialog`).
The standalone exe uses the pywebview `js_api` for native dialogs; merged, React supplies paths —
both feed the identical `_scan_core`, so behavior is identical.

## Why no bugs
- **No port clash** — merged, the module uses LISA's server (no second Flask). Standalone picks a
  free, Chromium-*safe* port (avoids `ERR_UNSAFE_PORT`).
- **No route clash** — everything is under the `/idp` prefix via the blueprint.
- **No asset clash** — assets are served by the blueprint (`/idp/static`), links are relative.
- **No import side effects** — importing `idp_web_panel` starts nothing; the server only runs in
  `launch()` / `__main__` (standalone). `register()` just adds routes.
- **Shared, single-source backend** — the same library powers the exe, `--classic`, and the merged
  module, so fixes stay in tandem.
- **Settings/state are user-scoped** — `%LOCALAPPDATA%\AIC_IDP_Extractor\` (settings, layouts,
  logic), independent of LISA's data.

## Standalone stays intact
`python idp_web_panel.py` (or the exe) still launches the pywebview window; `--classic` still opens
the full Tkinter UI. Merging changes none of that.
