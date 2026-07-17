import io
import json
import os
from flask import Blueprint, request, jsonify, send_file
from ..services.pdf_service import parse_markup_pdf
from ..services.docx_service import (
    compile_submittal,
    compile_submittal_pdf,
    load_submittal,
    TEMPLATE_PATH,
)

submittal_bp = Blueprint("submittal", __name__)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME  = "application/pdf"


@submittal_bp.route("/submittal/template", methods=["GET"])
def download_template():
    if not os.path.isfile(TEMPLATE_PATH):
        return jsonify({"error": "Template not found"}), 404
    return send_file(
        TEMPLATE_PATH,
        as_attachment=True,
        download_name="SubmittalCoverLetter_Template.docx",
        mimetype=_DOCX_MIME,
    )


@submittal_bp.route("/submittal/parse-pdf", methods=["POST"])
def parse_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (expected multipart field 'file')"}), 400
    try:
        result = parse_markup_pdf(request.files["file"].read())
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@submittal_bp.route("/submittal/compile", methods=["POST"])
def compile_doc():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Missing JSON body"}), 400
    try:
        buf = compile_submittal(body)
        filename = (body.get("fields", {}).get("filename") or "Submittal").strip()
        if not filename.lower().endswith(".docx"):
            filename += ".docx"
        return send_file(buf, as_attachment=True, download_name=filename, mimetype=_DOCX_MIME)
    except FileNotFoundError:
        return jsonify({"error": "Template file not found on server"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@submittal_bp.route("/submittal/compile-pdf", methods=["POST"])
def compile_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No PDF uploaded (expected multipart field 'file')"}), 400
    if "payload" not in request.form:
        return jsonify({"error": "Missing 'payload' form field"}), 400
    try:
        body = json.loads(request.form["payload"])
        pdf_bytes = compile_submittal_pdf(body, request.files["file"].read())

        filename = (body.get("fields", {}).get("filename") or "Submittal").strip()
        if filename.lower().endswith(".docx"):
            filename = filename[:-5]
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        return send_file(
            io.BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype=_PDF_MIME,
        )
    except FileNotFoundError:
        return jsonify({"error": "Template file not found on server"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@submittal_bp.route("/submittal/load", methods=["POST"])
def load_doc():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (expected multipart field 'file')"}), 400
    try:
        payload = load_submittal(request.files["file"].read())
        if payload is None:
            return jsonify({"error": "No LISA metadata found. Only documents compiled by LISA can be loaded."}), 422
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
