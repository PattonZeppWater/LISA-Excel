import sys
import os
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'CommonTools', 'Python')))

import pythoncom
from flask import Flask
from flask_cors import CORS
from routes.generate import idp_gen_bp

app = Flask(__name__)
CORS(app)
app.register_blueprint(idp_gen_bp)

@app.route("/api/health")
def health():
    return {"status": "ok", "service": "IDP_Generation", "port": 5125}

if __name__ == "__main__":
    pythoncom.CoInitialize()
    app.run(port=5125, debug=True, threaded=False)
