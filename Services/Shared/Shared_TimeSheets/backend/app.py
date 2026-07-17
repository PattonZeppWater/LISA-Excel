import sys
import os
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'CommonTools', 'Python')))

from flask import Flask
from flask_cors import CORS
from routes.timesheets import timesheets_bp

app = Flask(__name__)
CORS(app)
app.register_blueprint(timesheets_bp)

@app.route("/api/health")
def health():
    return {"status": "ok", "service": "Shared_TimeSheets", "port": 5105}

if __name__ == "__main__":
    app.run(port=5105, debug=True)
