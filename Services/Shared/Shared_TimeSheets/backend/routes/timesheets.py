"""
timesheets.py â€” All Shared_TimeSheets routes.

Endpoints:
  GET  /api/health                      health check (also on app.py)
  GET  /api/timesheets/download         Fetch remaining units from Viewpoint â†’ download Excel
  POST /api/timesheets/preview          Upload timesheet xlsx â†’ fetch Viewpoint data â†’ preview rows + highlights
  POST /api/timesheets/process          Upload timesheet xlsx â†’ fetch Viewpoint data â†’ download highlighted xlsx
  POST /api/timesheets/reporting        Upload zip of timesheets â†’ download Vista-import zip
"""

import io
import os
import base64
import zipfile
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file

from ..services.auth_service import get_token
from ..services.data_service import fetch_all_units
from ..services.excel_service import build_excel
from ..services.processor_service import (
    detect_config,
    build_lookup_from_rows,
    get_preview_with_highlights,
    process_timesheets,
)
from ..services.reporting_service import process_reporting_zip

timesheets_bp = Blueprint("timesheets", __name__)


# â”€â”€ Get Data (download remaining units Excel) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@timesheets_bp.route("/download", methods=["GET"])
def download():
    try:
        token = get_token()
        rows = fetch_all_units(token)
        filename, file_b64 = build_excel(rows)
        return jsonify({"filename": filename, "file_b64": file_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# â”€â”€ Preview â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@timesheets_bp.route("/preview", methods=["POST"])
def preview():
    """
    Upload a timesheet xlsx. Fetches remaining units from Viewpoint automatically.
    Returns preview rows, highlights, config info, and stats.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext != ".xlsx":
        return jsonify({"error": "Only .xlsx files are supported"}), 400

    ts_bytes = f.read()

    try:
        cfg = detect_config(ts_bytes)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        token      = get_token()
        units_rows = fetch_all_units(token)
        lookup, known_jobs = build_lookup_from_rows(units_rows)
    except Exception as e:
        return jsonify({"error": f"Viewpoint API error: {e}"}), 500

    try:
        rows, highlights, stats = get_preview_with_highlights(ts_bytes, lookup, known_jobs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "rows":        rows,
        "highlights":  highlights,
        "config_type": cfg.type_name,
        "stats":       stats,
    })


# â”€â”€ Process & Download â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@timesheets_bp.route("/process", methods=["POST"])
def process():
    """
    Upload a timesheet xlsx. Fetches remaining units from Viewpoint automatically.
    Returns highlighted xlsx as base64 JSON along with stats.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext != ".xlsx":
        return jsonify({"error": "Only .xlsx files are supported"}), 400

    ts_bytes = f.read()
    orig_filename = f.filename

    try:
        cfg = detect_config(ts_bytes)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        token      = get_token()
        units_rows = fetch_all_units(token)
        lookup, known_jobs = build_lookup_from_rows(units_rows)
    except Exception as e:
        return jsonify({"error": f"Viewpoint API error: {e}"}), 500

    try:
        output_buf, stats = process_timesheets(ts_bytes, lookup, known_jobs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    stem     = os.path.splitext(orig_filename)[0]
    filename = f"{stem}_reviewed.xlsx"
    file_b64 = base64.b64encode(output_buf.read()).decode("utf-8")

    return jsonify({"filename": filename, "file_b64": file_b64, "stats": stats})


# â”€â”€ Reporting (zip â†’ Vista CSV zip) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@timesheets_bp.route("/reporting", methods=["POST"])
def reporting():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip files are supported"}), 400

    try:
        csv_bytes, xlsx_files, per_file_csvs, per_file_pdfs = process_reporting_zip(f.read())
    except zipfile.BadZipFile:
        return jsonify({"error": "Cannot read the zip â€” the file may be corrupted."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    today    = datetime.now().strftime("%Y%m%d")
    csv_name = f"AIC_Timesheets_{today}.csv"
    zip_name = f"AIC_Timesheets_{today}.zip"

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, csv_bytes)
        for fname, fbytes in xlsx_files.items():
            zf.writestr(f"timesheets/{fname}", fbytes)
        for csv_fname, csv_fbytes in per_file_csvs.items():
            zf.writestr(f"Timesheets_CSV/{csv_fname}", csv_fbytes)
        for pdf_fname, pdf_fbytes in per_file_pdfs.items():
            zf.writestr(f"Timesheets_PDF/{pdf_fname}", pdf_fbytes)
    out.seek(0)

    return send_file(
        out,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )
