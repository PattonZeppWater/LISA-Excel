from flask import jsonify


def bad_request(msg: str):
    return jsonify({"error": msg}), 400


def not_found(msg: str):
    return jsonify({"error": msg}), 404


def unprocessable(msg: str):
    return jsonify({"error": msg}), 422


def server_error(msg: str):
    return jsonify({"error": msg}), 500
