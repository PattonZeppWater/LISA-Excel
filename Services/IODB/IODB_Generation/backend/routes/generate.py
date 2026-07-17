import io
import os
import zipfile
from flask import Blueprint, request, jsonify, send_file
from ..services.parser import parse_workbook
from ..services.io_layout import generate_io_layout
from ..services.exploded_view import generate_exploded_view
from ..services.iolist_generator import generate_io_list
from ..services.tagdb_generator import generate_tag_db, generate_tag_db_from_data
from ..services.downloader import build_workbook, compute_tagdb_addresses

gen_bp = Blueprint("generate", __name__)

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
TEMPLATE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', '_Templates')
)


# â”€â”€ Template â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _latest_template():
    candidates = [
        f for f in os.listdir(TEMPLATE_DIR)
        if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS
    ] if os.path.isdir(TEMPLATE_DIR) else []
    if not candidates:
        return None, None
    latest = max(candidates, key=lambda f: os.path.getmtime(os.path.join(TEMPLATE_DIR, f)))
    return os.path.join(TEMPLATE_DIR, latest), latest


@gen_bp.route("/template", methods=["GET"])
def download_template():
    path, filename = _latest_template()
    if not path:
        return jsonify({"error": "No template file found in the IODB _Templates folder."}), 404
    return send_file(
        path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@gen_bp.route("/parse-template", methods=["GET"])
def parse_template():
    path, filename = _latest_template()
    if not path:
        return jsonify({"error": "No template file found in the IODB _Templates folder."}), 404
    try:
        with open(path, "rb") as f:
            data = parse_workbook(f.read(), filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


# â”€â”€ Parse â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/parse", methods=["POST"])
def parse():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only .xlsx and .xls files are supported"}), 400
    try:
        data = parse_workbook(f.read(), f.filename)
    except zipfile.BadZipFile:
        return jsonify({"error": "Cannot read the file â€” it may be open in Excel. Close it and try again."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


# â”€â”€ Generate IOLayout â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/generate/iolayout", methods=["POST"])
def gen_iolayout():
    body = request.get_json(silent=True)
    if not body or "sheets" not in body:
        return jsonify({"error": "Missing sheets data"}), 400
    try:
        result = generate_io_layout(body["sheets"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


# â”€â”€ Generate ExplodedView â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/generate/explodedview", methods=["POST"])
def gen_explodedview():
    body = request.get_json(silent=True)
    if not body or "sheets" not in body:
        return jsonify({"error": "Missing sheets data"}), 400
    try:
        result = generate_exploded_view(body["sheets"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


# â”€â”€ Generate IOList â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/generate/iolist", methods=["POST"])
def gen_iolist():
    body = request.get_json(silent=True)
    if not body or "sheets" not in body:
        return jsonify({"error": "Missing sheets data"}), 400
    try:
        result = generate_io_list(body["sheets"], body.get("config_json"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


# â”€â”€ Generate TagDB â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/generate/tagdb", methods=["POST"])
def gen_tagdb():
    body = request.get_json(silent=True)
    if not body or "sheets" not in body:
        return jsonify({"error": "Missing sheets data"}), 400
    config_json = body.get("config_json")
    try:
        if config_json and "tagdb_data" in config_json:
            result = generate_tag_db_from_data(config_json)
        elif config_json and "tagdb_config" in config_json:
            result = generate_tag_db(body["sheets"], config_json)
        else:
            raise ValueError(
                "No TagDB config provided. "
                "Paste either a tagdb_config (settings) or tagdb_data (direct rows) JSON."
            )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


# â”€â”€ Compute TagDB addresses â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/generate/tagdb-addresses", methods=["POST"])
def gen_tagdb_addresses():
    body = request.get_json(silent=True)
    if not body or "sheets" not in body:
        return jsonify({"error": "Missing sheets data"}), 400
    try:
        updated_rows = compute_tagdb_addresses(body["sheets"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"rows": updated_rows})


# â”€â”€ Download â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@gen_bp.route("/download", methods=["POST"])
def download():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Missing request body"}), 400
    original_b64 = body.get("original_b64")
    sheets       = body.get("sheets", {})
    filename     = body.get("filename", "IODB.xlsx")
    if not original_b64:
        return jsonify({"error": "Missing original_b64"}), 400
    try:
        wb_bytes = build_workbook(original_b64, sheets, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return send_file(
        io.BytesIO(wb_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
