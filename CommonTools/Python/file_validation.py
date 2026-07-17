def validate_extension(filename: str, allowed: set) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def require_file(request_files, allowed: set):
    """Return (file, None) on success or (None, (error_dict, status_code)) on failure."""
    if "file" not in request_files:
        return None, ({"error": "No file uploaded"}, 400)
    f = request_files["file"]
    if not f.filename or not validate_extension(f.filename, allowed):
        return None, ({"error": f"File must be one of: {', '.join(sorted(allowed))}"}, 400)
    return f, None
