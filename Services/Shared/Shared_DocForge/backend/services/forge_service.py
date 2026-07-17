"""Build folder-structure outputs (markdown summary, empty-structure zip,
contents zip) from a nested tree definition.

Tree shape:
    [
      {
        "name": "Folder Name",
        "files": [                       # for markdown / structure: ["a.pdf", ...]
          {"slot": "f0", "name": "a.pdf"},   # for contents zip
        ],
        "children": [ /* same shape, max depth 3 */ ]
      },
      ...
    ]
"""

import io
import re
import zipfile

MAX_DEPTH = 3
_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ── Public entry points ───────────────────────────────────────────────────────

def build_markdown(tree: list) -> bytes:
    """Render the tree as a Markdown bullet list and return UTF-8 bytes."""
    lines = ["# Structure", ""]
    for node in tree or []:
        _walk_markdown(node, lines, depth=0)
    if len(lines) <= 2:
        lines.append("_(empty)_")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_structure_zip(tree: list) -> bytes:
    """Build a zip containing only the folder hierarchy. No file entries.
    Returns the zip as bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for node in tree or []:
            _walk_structure(node, zf, prefix="", depth=0)
    return buf.getvalue()


def build_contents_zip(tree: list, file_streams: dict) -> bytes:
    """Build a zip containing the folder structure AND the user-uploaded files.
    `file_streams` is a dict of slot id -> bytes (read from the multipart upload).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for node in tree or []:
            _walk_contents(node, zf, prefix="", file_streams=file_streams, depth=0)
    return buf.getvalue()


# ── Tree walkers ──────────────────────────────────────────────────────────────

def _walk_markdown(node: dict, lines: list, depth: int) -> None:
    if depth >= MAX_DEPTH:
        return
    name = _safe_folder_name(node.get("name"), depth, len(lines))
    indent = "  " * depth
    lines.append(f"{indent}- **{name}**")
    for f in node.get("files") or []:
        fname = _file_name(f)
        if fname:
            lines.append(f"{indent}  - {fname}")
    for child in node.get("children") or []:
        _walk_markdown(child, lines, depth + 1)


def _walk_structure(node: dict, zf: zipfile.ZipFile, prefix: str, depth: int) -> None:
    if depth >= MAX_DEPTH:
        return
    name = _safe_folder_name(node.get("name"), depth, 0)
    folder = f"{prefix}{name}/"
    zf.writestr(folder, b"")
    for child in node.get("children") or []:
        _walk_structure(child, zf, folder, depth + 1)


def _walk_contents(node: dict, zf: zipfile.ZipFile, prefix: str,
                   file_streams: dict, depth: int) -> None:
    if depth >= MAX_DEPTH:
        return
    name = _safe_folder_name(node.get("name"), depth, 0)
    folder = f"{prefix}{name}/"
    zf.writestr(folder, b"")
    for f in node.get("files") or []:
        fname = _file_name(f)
        slot  = f.get("slot") if isinstance(f, dict) else None
        if not fname or slot not in file_streams:
            continue
        zf.writestr(folder + fname, file_streams[slot])
    for child in node.get("children") or []:
        _walk_contents(child, zf, folder, file_streams, depth + 1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_folder_name(raw, depth: int, fallback_seed: int) -> str:
    name = (raw or "").strip()
    if not name:
        name = f"Tier {depth + 1}"
    name = _INVALID_PATH_CHARS.sub("_", name).strip(" .")
    return name or f"Tier {depth + 1}"


def _file_name(item) -> str:
    if isinstance(item, dict):
        return _INVALID_PATH_CHARS.sub("_", (item.get("name") or "").strip()).strip(" .")
    if isinstance(item, str):
        return _INVALID_PATH_CHARS.sub("_", item.strip()).strip(" .")
    return ""
