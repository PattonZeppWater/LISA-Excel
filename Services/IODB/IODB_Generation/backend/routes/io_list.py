from datetime import datetime
from flask import Blueprint, jsonify, request, send_file
from ..services.validator import validate
from ..services.exporter import export

io_list_bp = Blueprint("io_list", __name__, url_prefix="/api/io-list")


@io_list_bp.route("/validate", methods=["POST"])
def validate_route():
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return jsonify({"valid": False, "errors": ["Request body is not valid JSON"]}), 400
    errors = validate(payload)
    return jsonify({"valid": len(errors) == 0, "errors": errors})


@io_list_bp.route("/export", methods=["POST"])
def export_route():
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return jsonify({"error": "Request body is not valid JSON"}), 400
    errors = validate(payload)
    if errors:
        return jsonify({"valid": False, "errors": errors}), 422
    try:
        buf = export(payload)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 500
    filename = f"IOList_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
