import sys
import os
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'CommonTools', 'Python')))

from flask import Flask
from flask_cors import CORS
from routes.forge import forge_bp

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB — contents zip can be large

app.register_blueprint(forge_bp)


@app.route("/api/health")
def health():
    return {"status": "ok", "service": "Shared_DocForge", "port": 5116}


if __name__ == "__main__":
    app.run(debug=True, port=5116)
