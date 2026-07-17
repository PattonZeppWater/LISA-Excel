import io
import json
import zipfile


def inject_zip_payload(docx_bytes: bytes, entry_name: str, payload: dict) -> bytes:
    """Embed a JSON payload as a named entry inside a DOCX (ZIP) file.

    The entry is invisible to Word and survives normal editing.
    Use load_zip_payload to retrieve it later.
    """
    buf = io.BytesIO(docx_bytes)
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr(entry_name, json.dumps(payload, ensure_ascii=False))
    return buf.getvalue()


def load_zip_payload(docx_bytes: bytes, entry_name: str) -> dict | None:
    """Extract a JSON payload previously embedded by inject_zip_payload.

    Returns None if the entry does not exist.
    """
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        if entry_name not in z.namelist():
            return None
        return json.loads(z.read(entry_name))
