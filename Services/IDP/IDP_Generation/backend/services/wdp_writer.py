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


def ensure_project_sheets(output_folder: str, project_number: str, conduit_index=None) -> list:
    """Copy the GENERAL template sheets into output_folder named per IC.EDC.S011
    (<project>-G.NN.dwg: cover = G.01, drawing-index page(s) = G.02.., legend = last), plus the
    title-block map (.wdl -> <project>_wdtitle.wdl) and the drawing template (.wdt).
    Copy-if-missing. Returns the sheet plan (see project_sheet_plan) so the caller can fill each
    general sheet's title block. Never raises."""
    safe = _safe(project_number)
    plan, _pages = project_sheet_plan(project_number, conduit_index)
    try:
        if not output_folder or not os.path.isdir(output_folder):
            return plan
        for g in plan:
            src = _find_template_file("*-%s.dwg" % g["template_suffix"])
            dst = os.path.join(output_folder, g["name"])
            if src and not os.path.exists(dst):
                shutil.copyfile(src, dst)
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
    return plan


# Sidecar recording which project info the GENERAL sheet title blocks were last filled
# with, so we fill them ONCE (not on every conduit during Generate All) yet still re-fill
# when the project number / description changes.
_GENERAL_TB_MARK = "_idp_general_titleblocks.json"
# Bump when the fill LOGIC changes so old markers invalidate and the general sheets get
# re-filled once with the new logic (v2: fill the big center cover text, not just the
# small title block -- _set_title_block_attrs now sets every attribute of a repeated tag).
_GENERAL_TB_FILL_VERSION = 2


def _general_tb_signature(project_number: str, project_desc, general) -> str:
    import hashlib
    lines = (project_desc or {}).get("lines") or []
    payload = json.dumps(
        {"v": _GENERAL_TB_FILL_VERSION, "pn": project_number or "",
         "lines": [str(x) for x in lines],
         "sheets": [(n, s, d) for (n, s, d, _w) in (general or [])]},
        sort_keys=True,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def general_titleblocks_to_fill(output_folder, project_number, project_desc, general) -> list:
    """Return [(dwg_path, drawing_no, sheet_number), ...] for the GENERAL sheets whose title
    blocks still need filling. Returns [] when a marker shows they were already filled with
    this exact project number + description -- so a Generate-All fills them once, a fresh
    folder (or a folder whose general sheets predate this feature) fills them now regardless
    of whether they were copied THIS run, and changing the project info re-fills. Never raises."""
    try:
        present = [(os.path.join(output_folder, name), dno, sn)
                   for (name, sn, dno, _newly) in (general or [])
                   if os.path.exists(os.path.join(output_folder, name))]
        if not present:
            return []
        sig = _general_tb_signature(project_number, project_desc, general)
        mark = os.path.join(output_folder, _GENERAL_TB_MARK)
        if os.path.exists(mark):
            try:
                with open(mark, "r", encoding="utf-8") as f:
                    if json.load(f).get("sig") == sig:
                        return []
            except Exception:
                pass
        return present
    except Exception:
        return []


def mark_general_titleblocks_filled(output_folder, project_number, project_desc, general) -> None:
    """Record that the GENERAL sheet title blocks are now filled for this exact project
    info, so later conduits in the same run skip the (COM-costly) re-open. Never raises."""
    try:
        sig = _general_tb_signature(project_number, project_desc, general)
        with open(os.path.join(output_folder, _GENERAL_TB_MARK), "w", encoding="utf-8") as f:
            json.dump({"sig": sig}, f)
    except Exception:
        pass


# ============================================================================
# Drawing INDEX (G2) -- row list + multi-page (continuation) numbering
# ============================================================================

# How many DATA rows (drawings) fit under the header on ONE index sheet before the table
# would run past the index-sheet border. The G2 table starts high on the sheet (top row at
# Y~7.42) with 0.2533"-tall rows; 22 data rows keep the bottom of the table safely above the
# border. Kept conservative on purpose -- overflow just spills onto a continuation index
# sheet, so a slightly-low value only means an extra index page, never a clipped table.
INDEX_ROW_CAPACITY = int(os.getenv("IDP_INDEX_ROW_CAPACITY", "22"))


def _seq_start(row) -> int | None:
    """This conduit's project-sequential start sheet (1-based across conduits), or None."""
    try:
        v = row.get("Seq_Start")
        return int(v) if v is not None and str(v).strip() != "" else None
    except (ValueError, TypeError):
        return None


def _sheet_count(row) -> int:
    """How many sheets this conduit occupies (1 + continuations). Defaults to 1."""
    try:
        return max(1, int(row.get("Sheet_Count") or 1))
    except (ValueError, TypeError):
        return 1


def _total_conduit_sheets(conduit_index) -> int:
    """Total conduit sheets across the whole workbook (each conduit's continuations
    included)."""
    return sum(_sheet_count(r) for r in (conduit_index or []))


def index_page_count(total_conduit_sheets: int) -> int:
    """How many index (G2) sheets the drawing index needs.

    The index lists EVERY sheet in the deliverable -- cover (1) + the index pages themselves
    + legend (1) + every conduit sheet -- so the page count is mildly self-referential (more
    index pages means more rows to list). Solve the fixed point: rows = 2 + pages + conduits;
    pages = ceil(rows / capacity). Converges in a couple of iterations. Always >= 1."""
    cap = max(1, INDEX_ROW_CAPACITY)
    c = max(0, int(total_conduit_sheets or 0))
    pages = 1
    for _ in range(8):
        rows = 2 + pages + c                      # cover + index pages + legend + conduits
        nxt = max(1, -(-rows // cap))             # ceil division
        if nxt == pages:
            break
        pages = nxt
    return pages


def project_general_offset(conduit_index) -> int:
    """Number of GENERAL sheets that lead the deliverable (cover + index page(s) + legend),
    i.e. how much conduit sheet numbers are offset. Reduces to 3 (today's cover/index/legend)
    when the index fits on one page. Both /generate and the finalize pass call this so
    conduit numbering and the index agree."""
    return 2 + index_page_count(_total_conduit_sheets(conduit_index))


# Sheet-category letters from IC.EDC.S011 Table 1. General and Interconnect-Diagram (IDP)
# sheets take NO scope-of-work number, so their drawing numbers are <project>-<CAT>.<NN>.
_CAT_GENERAL = "G"
_CAT_IDP     = "D"   # Interconnect Diagram = the IDP sheets this tool produces


def _general_drawing_no(safe: str, n: int) -> str:
    """IC.EDC.S011 General-sheet drawing number: <project>-G.NN (2-digit, no SOW). Because the
    General sheets lead the deliverable, a General sheet's category index equals its running
    sheet number (cover = G.01, drawing index = G.02, ...)."""
    return "%s-%s.%02d" % (safe, _CAT_GENERAL, int(n))


def conduit_drawing_no(project_number: str, index: int) -> str:
    """IC.EDC.S011 IDP (Interconnect Diagram) drawing number: <project>-D.NN (2-digit, no SOW).
    `index` is the drawing's position among the project's conduit sheets (1-based, continuation
    sheets included) -- the Interconnect-Diagram category keeps its OWN index starting at 01,
    independent of how many General sheets lead the set."""
    return "%s-%s.%02d" % (_safe(project_number), _CAT_IDP, int(index))


def project_sheet_plan(project_number: str, conduit_index) -> tuple[list, int]:
    """Ordered GENERAL-sheet plan for a project, numbered per IC.EDC.S011 (<project>-G.NN) and
    accounting for however many index (continuation) pages the drawing index needs.

    Returns (plan, index_pages) where plan is a list of dicts:
        {template_suffix, name, drawing_no, sheet_number, label, is_index, index_page}
    in deliverable order: cover (G.01) -> index page(s) (G.02, G.03, ...) -> legend. `name` is
    the OUTPUT filename (<project>-G.NN.dwg); `template_suffix` (G1/G2/G3) selects which bundled
    template the sheet is copied from. Conduit sheets follow with their own D.NN numbering."""
    safe = _safe(project_number)
    pages = index_page_count(_total_conduit_sheets(conduit_index))
    plan = []
    n = 1

    def _entry(template_suffix, label, is_index, index_page):
        nonlocal n
        dno = _general_drawing_no(safe, n)
        e = {"template_suffix": template_suffix, "name": "%s.dwg" % dno, "drawing_no": dno,
             "sheet_number": n, "label": label, "is_index": is_index, "index_page": index_page}
        n += 1
        return e

    plan.append(_entry("G1", "COVER SHEET", False, None))
    for p in range(pages):
        plan.append(_entry("G2", "DRAWING INDEX", True, p))
    plan.append(_entry("G3", "SYMBOLS LEGEND", False, None))
    return plan, pages


def _existing_general_sheets(output_folder: str, safe: str) -> list:
    """General-category sheet filenames (<safe>-G.NN.dwg) present in the output folder, sorted
    by their NN index."""
    def _idx(nm):
        try:
            return int(os.path.splitext(nm)[0].rsplit(".", 1)[-1])
        except (ValueError, IndexError):
            return 0
    names = [os.path.basename(p) for p in glob.glob(os.path.join(output_folder, "%s-G.*.dwg" % safe))]
    return sorted(names, key=_idx)


def _conduit_desc_for(row, dwg_name, descriptions) -> str:
    """Drawing-index description for a conduit sheet: the drawing's Drawing Properties
    Description 1/2/3 (Conduit name / Source Name 1 / Destination Name 1), the same lines the
    .wdp records for the drawing, joined with a space. Blank lines are dropped so a drawing
    with only Description 1 reads cleanly. Values come from the recorded descriptions or, if
    those weren't stored, the conduit row's own fields."""
    entry = descriptions.get(dwg_name) or {}
    d1 = str(entry.get("desc1") or row.get("Cdt_Name") or row.get("Cond_Tag") or "").strip()
    d2 = str(entry.get("desc2") or row.get("Src_Name01") or "").strip()
    d3 = str(entry.get("desc3") or row.get("Dst_Name01") or "").strip()
    return " ".join(p for p in (d1, d2, d3) if p)


def build_index_rows(output_folder: str, project_number: str, conduit_index,
                     file_suffix: str = "e") -> tuple[list, int]:
    """Build the full ordered drawing-index row list for the project, matching exactly the
    numbering /generate assigns. Returns (rows, index_pages), where each row is a dict:
        {sheet_no, drawing_no, section, description}
    Rows are: cover / index page(s) / legend (GENERAL, from the sheet plan) followed by every
    conduit sheet (INTERCONNECTION DIAGRAMS), in Seq_Start order, with continuation sheets of
    a conduit taking the next consecutive drawing numbers -- the same formula generate.py uses
    for filenames and sheet numbers. Never raises (returns ([], 1) on trouble)."""
    try:
        plan, pages = project_sheet_plan(project_number, conduit_index)
        offset = 2 + pages
        descriptions = _load_dwg_descriptions(output_folder)
        rows = []
        for g in plan:
            rows.append({"sheet_no": g["sheet_number"], "drawing_no": g["drawing_no"],
                         "section": "GENERAL", "description": g["label"]})
        conduits = [r for r in (conduit_index or []) if _seq_start(r) is not None]
        conduits.sort(key=lambda r: _seq_start(r))
        for r in conduits:
            seq = _seq_start(r)
            for k in range(_sheet_count(r)):
                idx = seq + k                       # 1-based conduit-sheet index -> D.NN
                dno = conduit_drawing_no(project_number, idx)
                name = dno + ".dwg"
                desc = _conduit_desc_for(r, name, descriptions)
                if k > 0:
                    desc = (desc + " (CONT.)").strip()
                rows.append({"sheet_no": offset + idx, "drawing_no": dno,
                             "section": _ICD_SECTION, "description": desc})
        return rows, pages
    except Exception:
        return [], 1


# Marker text that identifies a drawing as the DRAWING INDEX sheet (its Drawing Properties
# Description 1). Used when rebuilding the index straight from a .wdp.
INDEX_DESC_MARKER = "DRAWING INDEX"


def parse_wdp(wdp_path: str) -> list:
    """Parse an AutoCAD Electrical .wdp project file into an ordered list of its drawings --
    every drawing the project contains, whether LISA generated it or it was added manually in
    ACADE. Returns a list of dicts, in project order:
        {filename, drawing_no, section, description}
    where `section` is the drawing's subsection (=====SUB=, e.g. GENERAL / INTERCONNECTION
    DIAGRAMS) and `description` is its Drawing Properties Description 1/2/3 (the '===' lines)
    joined with a space. Reads the .wdp as it exists on disk, so it reflects manual additions.
    Never raises (returns [] on trouble)."""
    try:
        with open(wdp_path, "r", encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()
    except Exception:
        return []
    out, section, sub, descs = [], "", "", []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("====="):                    # =====SUB=NAME (subsection marker)
            if "SUB=" in s:
                sub = s.split("SUB=", 1)[1].strip()
            continue
        if s.startswith("==="):                       # ===Description N line
            descs.append(s[3:].strip())
            continue
        if s.startswith("="):                         # =SECTION (resets subsection + pending descs)
            section, sub, descs = s[1:].strip(), "", []
            continue
        if s[0] in "*+?#@":                            # header / settings markers -- not a drawing
            descs = []
            continue
        # Anything else is a drawing entry (a filename on its own line).
        fname = os.path.basename(s)
        if fname.lower().endswith(".dwg"):
            out.append({
                "filename": fname,
                "drawing_no": os.path.splitext(fname)[0],
                "section": sub or section or "",
                "description": " ".join(d for d in descs if d),
            })
        descs = []
    return out


def write_full_project_wdp(output_folder: str, project_number: str, project_info=None,
                            valid_cond_tags=None, conduit_index=None) -> str | None:
    """Write '<project_number>.wdp' as a full sectioned AIC project: the GENERAL sheets
    (<project>-G.NN with COVER SHEET / DRAWING INDEX / SYMBOLS LEGEND labels, including any
    drawing-index continuation pages) followed by every CURRENT-WORKBOOK conduit drawing under
    INTERCONNECTION DIAGRAMS -- see _workbook_dwgs; pass valid_cond_tags (every Cond_Tag
    currently in ConduitIndex) so a stray .dwg left in the output folder by a different
    project/run, or by a conduit since removed from this workbook, is never listed. Pass
    conduit_index so the GENERAL section lists the right number of index pages in order.
    Descriptor fields come from the Project Description sheet. Returns the path or None."""
    try:
        if not output_folder or not os.path.isdir(output_folder):
            return None
        safe = _safe(project_number)
        body = _load_header(_field_map(project_number, project_info))

        general_names = set()
        plan, _pages = project_sheet_plan(project_number, conduit_index)
        for g in plan:
            general_names.add(g["name"].lower())
            if os.path.exists(os.path.join(output_folder, g["name"])):
                body += _block("GENERAL", g["name"], {"desc1": g["label"]})

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
        general = set(_existing_general_sheets(output_folder, safe))   # <proj>-G.NN.dwg
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
