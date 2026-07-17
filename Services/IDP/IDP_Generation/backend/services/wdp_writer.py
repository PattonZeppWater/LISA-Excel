"""
Write an AutoCAD Electrical project file (.wdp) alongside the generated IDP drawings.

The .wdp is named after the project number and lists every generated .dwg in the
output folder under the INTERCONNECTION DIAGRAMS subsection, so the batch of drawings
opens as one ACADE project. The settings header (libraries, wire-numbering, title-block
format, etc.) comes from idp_project_wdp.tmpl -- a real AIC IDP project header -- so the
project opens with the correct AIC configuration.

The project descriptor fields (*[1]..*[8]) are populated from the workbook's
"Project Description" sheet (Owner / Job Title / Content / Proj No. / Status / Date /
Engineer / Drafter). The project number also comes in directly from the UI and always
wins for *[4] (it names the file too).
"""
import os, glob, re

_TMPL_PATH = os.path.join(os.path.dirname(__file__), "idp_project_wdp.tmpl")
_SUB_LINE  = "=====SUB=INTERCONNECTION DIAGRAMS"

# drawing files we should NOT list in the project (AutoCAD side files)
_SKIP_SUFFIXES = ("_recover.dwg", "_recover000.dwg")

# a descriptor line in the .wdp header, e.g. "*[4]XX.XXXX"
_FIELD_LINE = re.compile(r"^\*\[(\d+)\]")

# Project Description sheet label (normalized) -> WDP descriptor line index.
# WDP *[N] fields: 1=end user, 2=project name, 3=content/discipline, 4=project number,
# 5=status, 6=date, 7=drawn by (drafter), 8=checked by (engineer).
_LABEL_TO_IDX = {
    "owner":           1,
    "job title":       2,
    "content":         3,
    "proj no.":        4,
    "proj no":         4,
    "project no.":     4,
    "project number":  4,
    "status":          5,
    "date":            6,
    "drafter":         7,   # -> "drawn by" line
    "engineer":        8,   # -> "checked by" line
}


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _field_map(project_number: str, project_info) -> dict:
    """Build {descriptor index -> value} from the Project Description sheet plus the
    UI project number (which always wins for *[4]).

    Preferred form: project_info == {"lines": [v1, v2, ...]} -- the k-th line maps
    POSITIONALLY to *[k] (matching ACADE's LINE1..LINE24 / "Update Title Block").
    Legacy form: a flat {label: value} dict is mapped by label via _LABEL_TO_IDX.
    """
    idx = {}
    if isinstance(project_info, dict):
        lines = project_info.get("lines")
        if isinstance(lines, (list, tuple)):
            for i, val in enumerate(lines, start=1):        # LINE k -> *[k]
                if str(val or "").strip():
                    idx[i] = str(val).strip()
        else:
            for label, val in project_info.items():         # legacy label-keyed dict
                n = _LABEL_TO_IDX.get(_norm(label))
                if n and str(val or "").strip():
                    idx[n] = str(val).strip()
    pn = (project_number or "").strip()
    if pn:
        idx[4] = pn
    return idx


def _load_header(field_map: dict) -> str:
    """Load the template and overwrite each descriptor line whose index we have a value
    for. Values we don't have keep the template default. Output uses CRLF and ends with
    exactly one newline so the drawing list starts on the next line."""
    with open(_TMPL_PATH, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in raw.split("\n"):
        mo = _FIELD_LINE.match(line)
        if mo:
            n = int(mo.group(1))
            if n in field_map:
                line = "*[%d]%s" % (n, field_map[n])
        out.append(line)
    header = "\n".join(out).rstrip("\n ")
    return header.replace("\n", "\r\n") + "\r\n"


def _project_dwgs(output_folder: str) -> list:
    """All real .dwg files in the output folder (sorted), excluding AutoCAD recover files."""
    names = []
    for p in glob.glob(os.path.join(output_folder, "*.dwg")):
        b = os.path.basename(p)
        if b.lower().endswith(_SKIP_SUFFIXES):
            continue
        names.append(b)
    return sorted(names, key=str.lower)


def write_project_wdp(output_folder: str, project_number: str, dwg_names=None,
                      project_info=None) -> str | None:
    """
    Write '<project_number>.wdp' into output_folder, listing every generated drawing and
    filling the project descriptor fields from project_info (the Project Description sheet).
    Returns the .wdp path, or None if it couldn't be written (never raises).
    """
    try:
        project_number = (project_number or "").strip()
        if not project_number or not output_folder or not os.path.isdir(output_folder):
            return None
        safe = "".join(c for c in project_number if c.isalnum() or c in "-_. ").strip() or "IDP_Project"

        if dwg_names is None:
            dwg_names = _project_dwgs(output_folder)
        # never reference the .wdp itself; keep it stable/sorted
        dwg_names = sorted({n for n in dwg_names if n.lower().endswith(".dwg")}, key=str.lower)

        body = _load_header(_field_map(project_number, project_info))
        for dwg in dwg_names:
            body += _SUB_LINE + "\r\n" + dwg + "\r\n"

        out_path = os.path.join(output_folder, safe + ".wdp")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(body)
        return out_path
    except Exception:
        return None
