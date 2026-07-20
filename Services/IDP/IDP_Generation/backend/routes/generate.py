"""
generate.py â€” IDP Generation Flask routes.

Endpoints:
  GET  /api/health                 Flask health check (also on app.py)
  POST /api/parse                  Upload + parse Excel workbook
  GET  /api/parse-template         Parse the latest template from _Templates/
  GET  /api/download-template      Serve the latest IDP workbook template for download
  GET  /api/autocad-status         Check if AutoCAD is running on this machine
  GET  /api/browse-folder          Open a tkinter folder picker; return chosen path
  POST /api/generate               Generate one DWG for a given conduit_ident
  POST /api/wire-labels            Generate wire-labels Excel from fill_index
  POST /api/download               Re-export current workbook state to Excel
"""

import io
import os
import base64
import zipfile
import tkinter as tk
from tkinter import filedialog

from flask import Blueprint, request, jsonify, send_file

from ..services import parser as svc_parser
from ..services import autocad_bridge
from ..services import workbook_mapper as svc_mapper
from ..services import wdp_writer

idp_gen_bp = Blueprint("idp_gen", __name__)


# â”€â”€ Template directory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_TEMPLATE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "_Templates")
)


# â”€â”€ Parse â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/parse", methods=["POST"])
def parse():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm"):
        return jsonify({"error": "Only .xlsx and .xlsm files are supported"}), 400

    try:
        data = svc_parser.parse_workbook(f.read(), f.filename)
        return jsonify(data)
    except zipfile.BadZipFile:
        return jsonify({"error": "Cannot read the file â€” it may be open in Excel. Close it and try again."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# â”€â”€ Parse template â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/parse-template", methods=["GET"])
def parse_template():
    """Load and parse the most recently modified workbook template from _Templates/."""
    try:
        candidates = [
            f for f in os.listdir(_TEMPLATE_DIR)
            if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")
        ]
    except FileNotFoundError:
        return jsonify({"error": f"Template directory not found: {_TEMPLATE_DIR}"}), 404

    if not candidates:
        return jsonify({"error": "No workbook template files found in _Templates/"}), 404

    latest = max(
        candidates,
        key=lambda f: os.path.getmtime(os.path.join(_TEMPLATE_DIR, f))
    )
    template_path = os.path.join(_TEMPLATE_DIR, latest)

    try:
        with open(template_path, "rb") as fh:
            file_bytes = fh.read()
        data = svc_parser.parse_workbook(file_bytes, latest)
        return jsonify(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# â”€â”€ Download workbook template â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/download-template", methods=["GET"])
def download_template():
    """Serve the latest IDP workbook template from _Templates/ for download."""
    try:
        candidates = [
            f for f in os.listdir(_TEMPLATE_DIR)
            if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")
        ]
    except FileNotFoundError:
        return jsonify({"error": f"Template directory not found: {_TEMPLATE_DIR}"}), 404

    if not candidates:
        return jsonify({"error": "No workbook template files found in _Templates/"}), 404

    latest = max(
        candidates,
        key=lambda f: os.path.getmtime(os.path.join(_TEMPLATE_DIR, f))
    )
    template_path = os.path.join(_TEMPLATE_DIR, latest)
    mimetype = (
        "application/vnd.ms-excel.sheet.macroEnabled.12"
        if latest.lower().endswith(".xlsm")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return send_file(
        template_path,
        mimetype=mimetype,
        as_attachment=True,
        download_name=latest,
    )


# â”€â”€ AutoCAD status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/autocad-status", methods=["GET"])
def autocad_status():
    """Check whether a running AutoCAD instance is accessible on this machine."""
    try:
        import win32com.client
        acad = win32com.client.GetActiveObject("AutoCAD.Application")
        version = str(acad.Version)
        return jsonify({"running": True, "version": version})
    except Exception:
        return jsonify({"running": False, "version": None})


# â”€â”€ Browse folder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/browse-folder", methods=["GET"])
def browse_folder():
    """Open a tkinter folder picker on the server machine and return the chosen path."""
    try:
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Select Output Folder")
        root.destroy()
        return jsonify({"path": folder or None})
    except Exception as e:
        return jsonify({"path": None, "error": str(e)})


# â”€â”€ Generate one DWG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/generate", methods=["POST"])
def generate():
    """
    Generate a DWG for one conduit.

    Request body (JSON):
    {
        "conduit_ident":  int,
        "conduit_index":  [ {...row dicts...} ],
        "fill_index":     [ {...row dicts...} ],
        "output_folder":  "C:\\path\\to\\output"
    }

    Response (JSON):
    { "success": bool, "output_path": str, "warnings": [...], "error": str|null }
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    conduit_ident = body.get("conduit_ident")
    conduit_index = body.get("conduit_index", [])
    fill_index    = body.get("fill_index", [])
    output_folder = body.get("output_folder", "")
    ref_docs       = body.get("ref_docs", {})
    deviation_notes = body.get("deviation_notes", {})   # { "1": "note text", ... }
    project_desc   = body.get("project_desc", {}) or {}  # Project Description sheet -> .wdp fields
    project_number = str(body.get("project_number") or "").strip()
    seq_num        = body.get("seq_num")
    # File suffix appended after the sequence number (default "e", e.g. 56.1077-01e)
    file_suffix    = body.get("file_suffix")
    file_suffix    = "e" if file_suffix is None else str(file_suffix).strip()
    # "Make it a project": assemble a full AIC project (GENERAL sheets + a sectioned .wdp/.aepx)
    # instead of just listing the conduit drawings. Requires a project number (names the files).
    make_project   = bool(body.get("make_project"))

    if conduit_ident is None:
        return jsonify({"error": "'conduit_ident' is required"}), 400
    if not output_folder:
        return jsonify({"error": "'output_folder' is required"}), 400
    if make_project and not project_number:
        return jsonify({"error": "A project number is required to generate a project. "
                                 "Enter one in the Project number field or uncheck 'Make it a project'."}), 400

    conduit_row = svc_parser.get_conduit_row(conduit_index, int(conduit_ident))
    if conduit_row is None:
        return jsonify({"error": f"conduit_ident {conduit_ident} not found in conduit_index"}), 404

    cond_tag = conduit_row.get("Cond_Tag") or f"CONDUIT_{conduit_ident}"

    if project_number and seq_num is not None:
        safe_proj   = "".join(c for c in project_number if c.isalnum() or c in "-_.")
        safe_suffix = "".join(c for c in file_suffix if c.isalnum() or c in "-_.")
        output_filename = f"{safe_proj}-{int(seq_num):02d}{safe_suffix}.dwg"
    else:
        safe_tag = "".join(c for c in str(cond_tag) if c.isalnum() or c in "-_.")
        output_filename = f"{safe_tag}.dwg"

    output_path = os.path.join(output_folder, output_filename)

    fill_rows    = svc_parser.get_fill_rows(fill_index, cond_tag)
    loop_list    = svc_parser.build_loop_list(fill_rows)

    # Re-derive this conduit's Fill## slots from the RAW fill rows so the fill table
    # is always current, even if the frontend's cached conduit_index was parsed by an
    # older backend (stale Fill## values such as NONE/N/A). Raw fill rows are
    # backend-version-agnostic, so this self-heals without a re-parse. Strip any sent
    # Fill## keys first so _derive_fill_slots regenerates them.
    for _fk in [k for k in list(conduit_row.keys()) if str(k).startswith("Fill")]:
        del conduit_row[_fk]
    svc_mapper._derive_fill_slots(conduit_row, fill_rows)

    conduit_data = svc_parser.build_conduit_data(conduit_row)

    # Resolve ref doc rows for this conduit from ConduitIndex col L ("Ref Documents")
    ref_doc_names = [
        n.strip()
        for n in str(conduit_row.get("Ref_DocNames") or "").split(",")
        if n.strip()
    ]
    ref_doc_rows = [ref_docs[n] for n in ref_doc_names if n in ref_docs]

    # Resolve deviation notes for this conduit from ConduitIndex col K ("Deviations
    # Notes"): a comma-separated list of numbers. Each number maps to its note text
    # via the Ref Documents #→text lookup. Keep the ACTUAL number, in selection order,
    # no renumbering. dev_rows is a list of (number, text) pairs.
    def _norm_dev(n):
        try:
            return str(int(float(str(n).strip())))
        except (ValueError, TypeError):
            return str(n).strip()

    dev_nums = [
        n.strip()
        for n in str(conduit_row.get("Dev_Nums") or "").split(",")
        if n.strip()
    ]
    dev_rows = []
    for n in dev_nums:
        key = _norm_dev(n)
        if key in deviation_notes:
            dev_rows.append((key, deviation_notes[key]))
        elif n in deviation_notes:
            dev_rows.append((n, deviation_notes[n]))

    # Block heights (from BlockIndex) drive grid spacing; captured at parse time.
    block_heights = svc_parser.get_block_heights()

    # ── Paginate: a conduit whose stack would drop within 1" of the 1_BORDER bottom
    # is split across continuation sheets (start / middle / end). Pagination is pure
    # geometry (no AutoCAD), computed once over the whole conductor list.
    chunks = autocad_bridge.paginate_loops(loop_list, block_heights)

    base, ext = os.path.splitext(output_path)          # ext == ".dwg"
    sheet_paths = [output_path if k == 0 else f"{base}-{k + 1}{ext}"
                   for k in range(len(chunks))]
    n_sheets = len(chunks)

    out_paths, all_warnings, errors, validations = [], [], [], []
    for k, (a, b) in enumerate(chunks):
        loops_k = loop_list[a:b]

        if n_sheets == 1:
            cdata_k, state = conduit_data, None
        elif k == 0:
            # First sheet carries the full conduit schedule (all conductors).
            cdata_k, state = conduit_data, autocad_bridge.CONT_STATE_START
        else:
            # Continuation sheets show only their own conductors: strip the derived
            # Fill slots and re-derive them from just this sheet's fill rows.
            rows_k = fill_rows[a:b]
            cond_copy = {key: val for key, val in conduit_row.items()
                         if not str(key).startswith("Fill")}
            svc_mapper._derive_fill_slots(cond_copy, rows_k)
            cdata_k = svc_parser.build_conduit_data(cond_copy)
            state = (autocad_bridge.CONT_STATE_END if k == n_sheets - 1
                     else autocad_bridge.CONT_STATE_MIDDLE)

        prev_name = os.path.basename(sheet_paths[k - 1]) if k > 0 else ""
        next_name = os.path.basename(sheet_paths[k + 1]) if k < n_sheets - 1 else ""

        r = autocad_bridge.generate_dwg(
            cdata_k, loops_k, sheet_paths[k],
            ref_doc_rows=ref_doc_rows,          # supporting-docs table on every sheet
            dev_rows=dev_rows,                  # deviation notes (#→text) on every sheet
            block_heights=block_heights,
            cont_state=state, cont_prev=prev_name, cont_next=next_name)

        out_paths.append(sheet_paths[k])
        all_warnings += (r.get("warnings") or [])
        validations.append(r.get("validation"))
        if not r.get("success"):
            errors.append(f"sheet {k + 1}/{n_sheets}: {r.get('error')}")

    result = {
        "success":      not errors,
        "output_path":  out_paths[0] if out_paths else None,
        "output_paths": out_paths,
        "sheets":       n_sheets,
        "warnings":     all_warnings,
        "error":        "; ".join(errors) if errors else None,
        "validation":   validations if n_sheets > 1 else (validations[0] if validations else None),
    }

    # Remember this conduit's Drawing Properties Description 1/2/3 (Conduit name /
    # Source Name 1 / Destination Name 1) AND its Conduit tag for every sheet just written,
    # so the .wdp writer -- which rebuilds its drawing list from scratch from whatever .dwgs
    # are on disk -- can still label this conduit's drawing(s) on a LATER /generate call for
    # a different conduit, and can tell them apart from a stray .dwg some other project left
    # in the same output folder.
    if not errors and out_paths:
        wdp_writer.record_dwg_descriptions(
            output_folder,
            [os.path.basename(p) for p in out_paths],
            desc1=conduit_data.get("Cdt_Name"),
            desc2=conduit_data.get("Src_Name01"),
            desc3=conduit_data.get("Dst_Name01"),
            cond_tag=cond_tag,
        )

    # Every Cond_Tag currently in the loaded workbook's ConduitIndex -- passed to the .wdp/
    # .aepx writers so they only ever list drawings that belong to THIS workbook (plus the
    # GENERAL sheets), never a stray .dwg left in the output folder by a different project/
    # run or by a conduit since removed from this one.
    valid_cond_tags = [
        str(r.get("Cond_Tag")).strip()
        for r in conduit_index
        if str(r.get("Cond_Tag") or "").strip()
    ]

    # Write/refresh the AutoCAD Electrical project file(s) in the output folder, named after
    # the project number. Best-effort: never let a project-file problem fail the generation.
    if not errors and project_number:
        if make_project:
            # Full AIC project: copy the GENERAL template sheets (G1-G3) in, then write a
            # sectioned .wdp (GENERAL + INTERCONNECTION DIAGRAMS) and a matching .aepx.
            wdp_writer.ensure_project_sheets(output_folder, project_number)
            result["wdp_path"]  = wdp_writer.write_full_project_wdp(
                output_folder, project_number, project_info=project_desc,
                valid_cond_tags=valid_cond_tags)
            result["aepx_path"] = wdp_writer.write_project_aepx(
                output_folder, project_number, valid_cond_tags=valid_cond_tags)
        else:
            # Plain drawing list under INTERCONNECTION DIAGRAMS (unchanged behavior).
            result["wdp_path"] = wdp_writer.write_project_wdp(
                output_folder, project_number, project_info=project_desc,
                valid_cond_tags=valid_cond_tags)

    return jsonify(result)


# â”€â”€ Wire Labels â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/wire-labels", methods=["POST"])
def wire_labels():
    """
    Generate a wire-labels Excel file from fill_index.

    Request body (JSON):
    {
        "fill_index": [ {...row dicts...} ],
        "filename":   str   (optional, used to derive the download name)
    }
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    fill_index = body.get("fill_index", [])
    source_filename = body.get("filename", "IDP_Workbook.xlsx")
    stem = os.path.splitext(source_filename)[0]
    download_name = f"{stem}_WireLabels.xlsx"

    try:
        xl_bytes = svc_parser.build_wire_labels(fill_index)
        return send_file(
            io.BytesIO(xl_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# â”€â”€ Download (re-export Excel) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@idp_gen_bp.route("/download", methods=["POST"])
def download():
    """
    Re-export the current workbook state back to Excel.

    Request body (JSON):
    {
        "original_b64":  str,
        "conduit_index": [...],
        "fill_index":    [...],
        "filename":      str
    }
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    original_b64  = body.get("original_b64")
    conduit_index = body.get("conduit_index", [])
    fill_index    = body.get("fill_index", [])
    filename      = body.get("filename", "IDP_Workbook.xlsx")

    if not original_b64:
        return jsonify({"error": "'original_b64' is required"}), 400

    try:
        original_bytes = base64.b64decode(original_b64)
        wb_bytes = svc_parser.write_workbook(original_bytes, filename, conduit_index, fill_index)
        return send_file(
            io.BytesIO(wb_bytes),
            mimetype=(
                "application/vnd.ms-excel.sheet.macroEnabled.12"
                if filename.lower().endswith(".xlsm")
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
