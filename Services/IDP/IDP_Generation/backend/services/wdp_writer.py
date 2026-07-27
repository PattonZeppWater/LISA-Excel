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
import os, glob, re, shutil, uuid, json

_TMPL_PATH = os.path.join(os.path.dirname(__file__), "idp_project_wdp.tmpl")
_SUB_LINE  = "=====SUB=INTERCONNECTION DIAGRAMS"

# Sidecar mapping {dwg filename -> {"desc1", "desc2", "desc3"}} for the .wdp's
# per-drawing "Drawing Properties" description lines (Conduit name / Source Name 1 /
# Destination Name 1). The .wdp is rebuilt from scratch from whatever .dwgs are in the
# folder on every /generate call, so a conduit generated in an earlier call needs its
# description remembered somewhere other than the .wdp itself.
_DESC_STORE = "_idp_dwg_descriptions.json"

# Bundled AIC project template (cleaned from the "For Claude" folder): the GENERAL sheets
# G1-G3 + title-block map (.wdl) + drawing template (.wdt). Used only for "make it a project".
_PROJECT_TMPL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "_Templates", "Project")
# GENERAL section sheets kept in the template: (filename suffix, ACADE subsection label)
_GENERAL_SHEETS = [("G1", "COVER SHEET"), ("G2", "DRAWING INDEX"), ("G3", "SYMBOLS LEGEND")]
_ICD_SECTION = "INTERCONNECTION DIAGRAMS"   # EQUIPMENT TYPE renamed; conduits live here

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


def _desc_store_path(output_folder: str) -> str:
    return os.path.join(output_folder, _DESC_STORE)


def record_dwg_descriptions(output_folder: str, dwg_names, desc1="", desc2="", desc3="",
                             cond_tag="", project_number="") -> None:
    """Remember a drawing's Description 1/2/3 (Conduit name / Source Name 1 / Destination
    Name 1), its owning Conduit tag, AND the project number it was generated under, in a
    small JSON sidecar next to the .dwgs. Call once per generated sheet, right after it's
    written.

    cond_tag alone isn't enough to scope a project's drawing list: output folders get
    reused across projects/runs (confirmed live -- a folder had 100+ drawings from unrelated
    past tests, PLUS a same-conduit-different-project-number leftover that cond_tag-only
    filtering let back in). project_number is what a later /generate call for a DIFFERENT
    project can use to exclude an earlier project's drawings even when they happen to share
    a conduit tag. Never raises."""
    try:
        if not output_folder or not os.path.isdir(output_folder):
            return
        path = _desc_store_path(output_folder)
        store = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    store = json.load(f)
            except Exception:
                store = {}
        entry = {k: str(v).strip() for k, v in
                 (("desc1", desc1), ("desc2", desc2), ("desc3", desc3),
                  ("cond_tag", cond_tag), ("project_number", project_number))
                 if str(v or "").strip()}
        for name in dwg_names:
            if entry:
                store[name] = entry
            else:
                store.pop(name, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f)
    except Exception:
        pass


def _load_dwg_descriptions(output_folder: str) -> dict:
    try:
        path = _desc_store_path(output_folder)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _desc_lines(desc: dict | None) -> str:
    """Up to three '===' Description-1/2/3 lines, in the order AutoCAD Electrical's .wdp
    format expects (the Nth '===' line IS Description N, positionally) -- trailing blanks
    are dropped, but a blank BETWEEN two filled slots is kept so a later description
    doesn't shift into an earlier slot."""
    desc = desc or {}
    vals = [str(desc.get(k) or "").strip() for k in ("desc1", "desc2", "desc3")]
    while vals and not vals[-1]:
        vals.pop()
    return "".join("===%s\r\n" % v for v in vals)


def _project_dwgs(output_folder: str) -> list:
    """All real .dwg files in the output folder (sorted), excluding AutoCAD recover files."""
    names = []
    for p in glob.glob(os.path.join(output_folder, "*.dwg")):
        b = os.path.basename(p)
        if b.lower().endswith(_SKIP_SUFFIXES):
            continue
        names.append(b)
    return sorted(names, key=str.lower)


def _norm_tag(s) -> str:
    return str(s or "").strip().lower()


def _workbook_dwgs(output_folder: str, valid_cond_tags, project_number=None) -> list:
    """Every .dwg in the output folder that belongs to a conduit in the CURRENT workbook AND
    was generated under THIS project number -- matched against what was recorded (via
    record_dwg_descriptions) when that drawing was generated. Excludes any stray .dwg left
    in the output folder by an unrelated run, by a conduit since removed from this workbook,
    or (confirmed live: an output folder shared across projects) by an EARLIER project that
    happened to reuse the same conduit tag -- cond_tag alone doesn't tell those apart, only
    project_number does.

    valid_cond_tags=None means "no workbook context" -- falls back to every .dwg on disk
    (the old, unfiltered behavior), so a caller that doesn't have a conduit list handy still
    gets something rather than an empty project. project_number=None/blank skips that half
    of the filter (matches any project, or none recorded)."""
    if valid_cond_tags is None:
        return _project_dwgs(output_folder)
    valid = {_norm_tag(t) for t in valid_cond_tags}
    proj = _norm_tag(project_number)
    descriptions = _load_dwg_descriptions(output_folder)
    out = []
    for dwg in _project_dwgs(output_folder):
        entry = descriptions.get(dwg) or {}
        if _norm_tag(entry.get("cond_tag")) not in valid:
            continue
        if proj and _norm_tag(entry.get("project_number")) != proj:
            continue
        out.append(dwg)
    return out


def write_project_wdp(output_folder: str, project_number: str, dwg_names=None,
                      project_info=None, valid_cond_tags=None) -> str | None:
    """
    Write '<project_number>.wdp' into output_folder, listing every drawing that belongs to
    the current workbook (see _workbook_dwgs) and filling the project descriptor fields
    from project_info (the Project Description sheet). Pass valid_cond_tags (every Cond_Tag
    currently in ConduitIndex) so a stray .dwg from a different project/run never gets
    listed. Returns the .wdp path, or None if it couldn't be written (never raises).
    """
    try:
        project_number = (project_number or "").strip()
        if not project_number or not output_folder or not os.path.isdir(output_folder):
            return None
        safe = "".join(c for c in project_number if c.isalnum() or c in "-_. ").strip() or "IDP_Project"

        if dwg_names is None:
            dwg_names = _workbook_dwgs(output_folder, valid_cond_tags, project_number)
        # never reference the .wdp itself; keep it stable/sorted
        dwg_names = sorted({n for n in dwg_names if n.lower().endswith(".dwg")}, key=str.lower)

        descriptions = _load_dwg_descriptions(output_folder)
        body = _load_header(_field_map(project_number, project_info))
        for dwg in dwg_names:
            body += _SUB_LINE + "\r\n" + _desc_lines(descriptions.get(dwg)) + dwg + "\r\n"

        out_path = os.path.join(output_folder, safe + ".wdp")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(body)
        return out_path
    except Exception:
        return None


# ============================================================================
# "Make it a project" -- assemble a full AIC project (GENERAL sheets + conduits)
# ============================================================================

def _safe(project_number: str) -> str:
    """Filesystem-safe project-number stem (matches the naming used for conduit DWGs)."""
    return "".join(c for c in (project_number or "") if c.isalnum() or c in "-_. ").strip() or "IDP_Project"


def _block(section: str, dwg: str, desc: dict = None) -> str:
    """One drawing entry in a .wdp: =SECTION / =====SUB=SECTION / up to three ===Description
    lines (Description 1/2/3, in order) / the filename."""
    return "=%s\r\n=====SUB=%s\r\n%s%s\r\n" % (section, section, _desc_lines(desc), dwg)


def _find_template_file(pattern: str):
    """First file in the bundled Project template dir matching a glob, or None."""
    hits = sorted(glob.glob(os.path.join(_PROJECT_TMPL_DIR, pattern)))
    return hits[0] if hits else None


# Number of GENERAL sheets that lead the deliverable (cover=1, index=2, legend=3). The
# conduit drawings' sheet numbers continue after these, so a project offsets by this much.
GENERAL_SHEET_COUNT = len(_GENERAL_SHEETS)


def ensure_project_sheets(output_folder: str, project_number: str) -> list:
    """Copy the GENERAL template sheets (G1-G3) into output_folder renamed to the project
    number, plus the title-block map (.wdl -> <project>_wdtitle.wdl) and drawing template
    (.wdt). Copy-if-missing, so it's safe to call once per conduit.

    Returns a list of (dst_name, sheet_number, drawing_no, newly_copied) for each GENERAL
    sheet now present, in cover->index->legend order (sheet_number 1,2,3; drawing_no is the
    filename stem, e.g. '73.1159-G1'). `newly_copied` is True only on the call that first
    created the file, so the caller can fill its title block exactly once. Never raises."""
    safe = _safe(project_number)
    general = []
    try:
        if not output_folder or not os.path.isdir(output_folder):
            return general
        for i, (suf, _label) in enumerate(_GENERAL_SHEETS, start=1):
            src = _find_template_file("*-%s.dwg" % suf)
            dst_name = "%s-%s.dwg" % (safe, suf)
            dst = os.path.join(output_folder, dst_name)
            newly = False
            if src and not os.path.exists(dst):
                shutil.copyfile(src, dst)
                newly = True
            if os.path.exists(dst):
                general.append((dst_name, i, os.path.splitext(dst_name)[0], newly))
        wdl = _find_template_file("*_wdtitle.wdl") or _find_template_file("*.wdl")
        if wdl:
            dst = os.path.join(output_folder, "%s_wdtitle.wdl" % safe)
            if not os.path.exists(dst):
                shutil.copyfile(wdl, dst)
        wdt = _find_template_file("*.wdt")
        if wdt:
            dst = os.path.join(output_folder, os.path.basename(wdt))
            if not os.path.exists(dst):
                shutil.copyfile(wdt, dst)
    except Exception:
        pass
    return general


def write_full_project_wdp(output_folder: str, project_number: str, project_info=None,
                            valid_cond_tags=None) -> str | None:
    """Write '<project_number>.wdp' as a full sectioned AIC project: the GENERAL sheets
    (G1-G3 with COVER SHEET / DRAWING INDEX / SYMBOLS LEGEND labels) followed by every
    CURRENT-WORKBOOK conduit drawing under INTERCONNECTION DIAGRAMS -- see _workbook_dwgs;
    pass valid_cond_tags (every Cond_Tag currently in ConduitIndex) so a stray .dwg left in
    the output folder by a different project/run, or by a conduit since removed from this
    workbook, is never listed. Descriptor fields come from the Project Description sheet.
    Returns the path or None (never raises)."""
    try:
        if not output_folder or not os.path.isdir(output_folder):
            return None
        safe = _safe(project_number)
        body = _load_header(_field_map(project_number, project_info))

        general_names = set()
        for suf, label in _GENERAL_SHEETS:
            name = "%s-%s.dwg" % (safe, suf)
            general_names.add(name.lower())
            if os.path.exists(os.path.join(output_folder, name)):
                body += _block("GENERAL", name, {"desc1": label})

        # INTERCONNECTION DIAGRAMS = every current-workbook conduit dwg, labeled with each
        # conduit's own Description 1/2/3 (Conduit name / Source Name 1 / Destination Name
        # 1), recorded when it was generated.
        descriptions = _load_dwg_descriptions(output_folder)
        for dwg in _workbook_dwgs(output_folder, valid_cond_tags, project_number):
            if dwg.lower() in general_names:
                continue
            body += _block(_ICD_SECTION, dwg, descriptions.get(dwg))

        out_path = os.path.join(output_folder, safe + ".wdp")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(body)
        return out_path
    except Exception:
        return None


def write_project_aepx(output_folder: str, project_number: str, valid_cond_tags=None) -> str | None:
    """Write '<project_number>.aepx' (ACADE project XML) listing the GENERAL sheets plus
    every current-workbook conduit drawing (see _workbook_dwgs) with sequential FileIDs, so
    AutoCAD Electrical opens the assembled project. Pass valid_cond_tags (every Cond_Tag
    currently in ConduitIndex) so a stray .dwg from a different project/run is never listed.
    Never raises."""
    try:
        if not output_folder or not os.path.isdir(output_folder):
            return None
        safe = _safe(project_number)
        general = {
            "%s-%s.dwg" % (safe, suf) for suf, _label in _GENERAL_SHEETS
            if os.path.exists(os.path.join(output_folder, "%s-%s.dwg" % (safe, suf)))
        }
        conduits = _workbook_dwgs(output_folder, valid_cond_tags, project_number)
        dwgs = sorted(general | {d for d in conduits if d not in general}, key=str.lower)
        entries = "".join(
            '<Drawing FilePath="%s" FileID="%d"/>' % (os.path.splitext(d)[0] + ".DWG", i + 1)
            for i, d in enumerate(dwgs)
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\r\n'
            '<ProjectConfiguration Version="1.0">'
            '<Project ProjectPath="%s" LocalGUID="%s" EmxFilePath="" EmxGUID=""/>'
            '<Drawings NextID="%d">%s</Drawings>'
            '</ProjectConfiguration>\r\n'
            % (os.path.abspath(output_folder), uuid.uuid4(), len(dwgs) + 1, entries)
        )
        out_path = os.path.join(output_folder, safe + ".aepx")
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            f.write(xml)
        return out_path
    except Exception:
        return None
