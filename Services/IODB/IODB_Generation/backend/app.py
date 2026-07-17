import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'CommonTools', 'Python'))

from flask import Flask
from flask_cors import CORS
from routes.generate import gen_bp
from routes.io_list import io_list_bp

app = Flask(__name__)
CORS(app)

app.register_blueprint(gen_bp)
app.register_blueprint(io_list_bp)


@app.route("/api/health")
def health():
    return {"status": "ok", "service": "IODB_Generation", "port": 5113}


if __name__ == "__main__":
    app.run(port=5113, debug=True)
