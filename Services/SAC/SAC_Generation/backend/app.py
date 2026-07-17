import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'CommonTools', 'Python'))

from flask import Flask
from flask_cors import CORS
from routes.generation import generation_bp

app = Flask(__name__)
CORS(app)

app.register_blueprint(generation_bp)

if __name__ == '__main__':
    app.run(port=5130, debug=False)
