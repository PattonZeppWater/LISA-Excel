"""DocForge â€” folder structure builder.

Three endpoints, all consuming the same `tree` JSON shape. The first two are
plain JSON; the third is multipart so files can ride along.
"""

import io
import json
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from ..services.forge_service import (
    build_contents_zip,
    build_markdown,
    build_structure_zip,
)

forge_bp = Blueprint("forge", __name__)

_ZIP_MIME = "application/zip"
_MD_MIME  = "text/markdown"


@forge_bp.route("/markdown", methods=["POST"])
def markdown():
    body = request.get_json(silent=True) or {}
    tree = body.get("tree") or []
    data = build_markdown(tree)
    stamp = datetime.now().strftime("%Y%m%d")
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"DocForge_Structure_{stamp}.md",
        mimetype=_MD_MIME,
    )


@forge_bp.route("/zip-structure", methods=["POST"])
def zip_structure():
    body = request.get_json(silent=True) or {}
    tree = body.get("tree") or []
    data = build_structure_zip(tree)
    stamp = datetime.now().strftime("%Y%m%d")
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"DocForge_Structure_{stamp}.zip",
        mimetype=_ZIP_MIME,
    )


@forge_bp.route("/zip-contents", methods=["POST"])
def zip_contents():
    payload_raw = request.form.get("payload")
    if not payload_raw:
        return jsonify({"error": "Missing 'payload' form field"}), 400
    try:
        body = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Invalid payload JSON: {exc}"}), 400

    tree = body.get("tree") or []

    file_streams = {}
    for slot, storage in request.files.items():
        file_streams[slot] = storage.read()

    data = build_contents_zip(tree, file_streams)
    stamp = datetime.now().strftime("%Y%m%d")
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"DocForge_Contents_{stamp}.zip",
        mimetype=_ZIP_MIME,
    )
