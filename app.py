"""
app.py — LISA unified entry point.

Single Flask process serving all services and the React frontend.
Run directly for development: python app.py
Packaged by PyInstaller for distribution: lisa.spec
"""

import sys
import os

# When frozen by PyInstaller, data files live under sys._MEIPASS.
# When running from source, they live next to this file.
def _base():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

BASE = _base()

# CommonTools shared Python utilities
sys.path.insert(0, os.path.join(BASE, "CommonTools", "Python"))

import pythoncom
from flask import Flask, send_from_directory
from flask_cors import CORS

# ── Blueprint imports ──────────────────────────────────────────────────────────
from Services.IDP.IDP_Generation.backend.routes.generate import idp_gen_bp
from Services.IODB.IODB_Generation.backend.routes.generate import gen_bp
from Services.IODB.IODB_Generation.backend.routes.io_list import io_list_bp
from Services.SAC.SAC_Generation.backend.routes.generation import generation_bp
from Services.Shared.Shared_DocForge.backend.routes.forge import forge_bp
from Services.Shared.Shared_SubmittalLog.backend.routes.submittal import submittal_bp
from Services.Shared.Shared_TimeSheets.backend.routes.timesheets import timesheets_bp

# ── App factory ────────────────────────────────────────────────────────────────
_DIST = os.path.join(BASE, "Frontend", "frontend", "dist")
app = Flask(__name__, static_folder=_DIST, static_url_path="")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB

# ── Register blueprints with namespaced prefixes ───────────────────────────────
# Prefixes match the Vite proxy namespaces so the frontend needs zero changes.
app.register_blueprint(idp_gen_bp,    url_prefix="/api/idp-gen")
app.register_blueprint(gen_bp,        url_prefix="/api/iodb-gen")
app.register_blueprint(io_list_bp,    url_prefix="/api/iodb-gen/io-list")
app.register_blueprint(generation_bp, url_prefix="/api/sac-gen")
app.register_blueprint(forge_bp,      url_prefix="/api/docforge")
app.register_blueprint(submittal_bp,  url_prefix="/api/submittal-log")
app.register_blueprint(timesheets_bp, url_prefix="/api/timesheets")

# ── Health ─────────────────────────────────────────────────────────────────────
@app.route("/api/health")
def health():
    return {"status": "ok", "service": "LISA", "version": _read_version()}

def _read_version():
    try:
        return open(os.path.join(BASE, "Build", "version.txt")).read().strip()
    except Exception:
        return "dev"

# ── React SPA fallback ─────────────────────────────────────────────────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    full = os.path.join(app.static_folder, path)
    if path and os.path.exists(full):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

# ── Launch ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import threading, time, webview

    def _run_flask():
        """Run Flask on a background thread. CoInitialize here for COM/AutoCAD."""
        pythoncom.CoInitialize()
        app.run(port=5000, debug=False, threaded=False, use_reloader=False)

    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()
    time.sleep(1.5)  # let Flask fully start before opening the window

    window = webview.create_window(
        title="LISA — Advanced Integration & Controls",
        url="http://localhost:5000",
        width=1400,
        height=900,
        min_size=(900, 600),
        text_select=True,
    )
    webview.start()
