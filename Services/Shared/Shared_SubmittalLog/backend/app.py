import sys
import os
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'CommonTools', 'Python')))

from flask import Flask
from flask_cors import CORS
from routes.submittal import submittal_bp

app = Flask(__name__)
CORS(app)

app.register_blueprint(submittal_bp)


@app.route("/api/health")
def health():
    return {"status": "ok", "service": "Shared_SubmittalLog", "port": 5106}


if __name__ == "__main__":
    app.run(port=5106, debug=True)
