# LISA

**LISA** is an internal AIC desktop app (Python/Flask backend, driven from a small React UI in a pywebview window) that automates a handful of engineering document-generation tasks, plus the companion Excel workbook that drives its main feature.

- **IDP Generation** — reads the `Workbook/IDP_Workbook_CurrentWIP_3.xlsm` workbook and drives AutoCAD (via COM automation) to generate electrical Interconnection Diagram (IDP) drawings, plus the matching AutoCAD Electrical project file (`.wdp`).
- **IODB Generation** — I/O list and I/O database generation.
- **SAC Generation** — SAC-related document generation.
- **Shared services** — DocForge (Word document generation), Submittal Log, and TimeSheets integration, shared across the above tools.

## Layout

```
app.py                     Flask app entry point (pywebview host)
requirements.txt           top-level Python deps
CommonTools/Python/        shared helpers (docx, HTML, file validation, error helpers)
Services/
  IDP/IDP_Generation/       IDP backend: parser, workbook_mapper, autocad_bridge, wdp_writer, routes
  IDP/_Templates/           AutoCAD IDP template drawings (block libraries)
  IODB/IODB_Generation/     IODB backend
  SAC/SAC_Generation/       SAC backend
  Shared/Shared_DocForge/   Word doc generation service
  Shared/Shared_SubmittalLog/  Submittal log service
  Shared/Shared_TimeSheets/    TimeSheets integration (needs a real .env - see below)
Frontend/frontend/         React + Vite frontend source (src/, package.json, vite.config.js)
Workbook/                  the IDP Excel workbook (see Workbook/README.md)
vba/                       the workbook's VBA source, exported as plain text (see Workbook/README.md)
LAUNCH LISA.bat             end-user launcher (after setup)
SETUP - Run First.bat       one-time setup (installs Python 3.12 + deps if needed)
```

## Setup (development)

1. **Backend:** `python -m venv .venv`, activate it, `pip install -r requirements.txt` (each `Services/*/*/requirements.txt` may add tool-specific deps).
2. **Frontend:** from `Frontend/frontend`, `npm install` then `npm run dev` (proxies `/api` to `localhost:5000`) or `npm run build` to produce `dist/` for the packaged app.
3. **Secrets:** `Services/Shared/Shared_TimeSheets/.env.example` is a blank template — copy it to `.env` in the same folder and fill in real `API_KEY`/`API_SECRET` values locally. **Never commit `.env`.**
4. **AutoCAD:** IDP generation drives AutoCAD via COM automation — a full AutoCAD (Electrical) install must be open on the machine running generation.
5. **Workbook:** see `Workbook/README.md` for the VBA module map and what the workbook's macros do.

## Deployment

The end-user package is a zip containing this app plus the workbook, `LAUNCH LISA.bat` / `SETUP - Run First.bat`, and a built `Frontend/frontend/dist`. `SETUP - Run First.bat` auto-installs Python 3.12 if needed (no admin required) and builds the venv; `LAUNCH LISA.bat` starts it thereafter.
