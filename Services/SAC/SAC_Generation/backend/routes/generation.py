from flask import Blueprint, jsonify

generation_bp = Blueprint('generation', __name__)


@generation_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'SAC_Generation'})
