"""Sanitize the user's PDF to a focused appendix, convert the LISA DOCX to
PDF via Word COM (docx2pdf), merge the two, and rewrite our marker-URI
hyperlinks on the front pages into internal /GoTo links pointing at the
matching appendix page.
"""

import io
import os
import tempfile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    RectangleObject,
)


# Must match the prefix used in docx_service._make_location_para_with_link.
URI_HYPERLINK_PREFIX = "https://lisa.invalid/page/"


# ── Appendix construction ─────────────────────────────────────────────────────

def sanitize_pdf(
    pdf_bytes: bytes,
    keep_subject_prefix: str = "PSM_",
    neighbor_radius: int = 1,
) -> tuple[bytes, dict[int, int]]:
    """Build the appendix PDF.
    Keeps PSM_* pages plus `neighbor_radius` neighbors before/after each, in
    original order. On every kept page, strips annotations whose /Subj does not
    start with `keep_subject_prefix`, except the IRT-paired callout that holds
    the comment text. Each kept page's MediaBox/CropBox is then expanded to
    encompass every retained annotation's /Rect plus a small margin so the
    whole markup is visible when the merged PDF is printed.

    Returns (sanitized_bytes, {original_page_idx: appendix_page_idx}).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    psm_pages: set[int] = set()
    for pg_idx, page in enumerate(reader.pages):
        for a in (page.get("/Annots") or []):
            try:
                subj = a.get_object().get("/Subj")
            except Exception:
                continue
            if subj and str(subj).startswith(keep_subject_prefix):
                psm_pages.add(pg_idx)
                break

    keep_pages: set[int] = set()
    for p in psm_pages:
        for d in range(-neighbor_radius, neighbor_radius + 1):
            q = p + d
            if 0 <= q < len(reader.pages):
                keep_pages.add(q)

    sorted_pages = sorted(keep_pages)
    page_map: dict[int, int] = {orig: i for i, orig in enumerate(sorted_pages)}

    writer = PdfWriter()
    for orig_idx in sorted_pages:
        new_page = writer.add_page(reader.pages[orig_idx])
        annots = new_page.get("/Annots")
        if not annots:
            continue

        kept_refs = []
        psm_irts = []
        for a in annots:
            try:
                obj = a.get_object()
                subj = obj.get("/Subj")
            except Exception:
                continue
            if subj and str(subj).startswith(keep_subject_prefix):
                kept_refs.append(a)
                irt = obj.get("/IRT")
                if irt is not None:
                    psm_irts.append(irt)

        for a in annots:
            if a in kept_refs:
                continue
            if a in psm_irts:
                kept_refs.append(a)

        if kept_refs:
            new_page[NameObject("/Annots")] = ArrayObject(kept_refs)
            _expand_page_for_annots(new_page, kept_refs)
        else:
            del new_page[NameObject("/Annots")]

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), page_map


def _expand_page_for_annots(page, annots, margin: float = 18.0) -> None:
    """Grow the page's MediaBox/CropBox so every kept annotation /Rect fits."""
    mb = page.mediabox
    x0, y0 = float(mb.left),  float(mb.bottom)
    x1, y1 = float(mb.right), float(mb.top)

    for a in annots:
        try:
            obj = a.get_object() if hasattr(a, "get_object") else a
            rect = obj.get("/Rect")
            if rect is None or len(rect) < 4:
                continue
            rx0 = min(float(rect[0]), float(rect[2]))
            ry0 = min(float(rect[1]), float(rect[3]))
            rx1 = max(float(rect[0]), float(rect[2]))
            ry1 = max(float(rect[1]), float(rect[3]))
        except Exception:
            continue
        x0 = min(x0, rx0)
        y0 = min(y0, ry0)
        x1 = max(x1, rx1)
        y1 = max(y1, ry1)

    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin

    new_box = RectangleObject([
        FloatObject(x0), FloatObject(y0),
        FloatObject(x1), FloatObject(y1),
    ])
    had_crop = NameObject("/CropBox") in page
    page[NameObject("/MediaBox")] = new_box
    if had_crop:
        page[NameObject("/CropBox")] = new_box


# ── DOCX → PDF ────────────────────────────────────────────────────────────────

def docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert .docx to .pdf via Microsoft Word (docx2pdf / COM).
    Flask serves each request on a worker thread; COM requires CoInitialize
    on every thread that uses it, so we set up and tear down per call.
    """
    import pythoncom
    from docx2pdf import convert as docx2pdf_convert

    pythoncom.CoInitialize()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            in_path  = os.path.join(tmp, "in.docx")
            out_path = os.path.join(tmp, "in.pdf")
            with open(in_path, "wb") as f:
                f.write(docx_bytes)
            docx2pdf_convert(in_path, out_path)
            with open(out_path, "rb") as f:
                return f.read()
    finally:
        pythoncom.CoUninitialize()


# ── Merge + hyperlink rewrite ─────────────────────────────────────────────────

def merge_and_hyperlink(
    front_pdf_bytes: bytes,
    appendix_pdf_bytes: bytes,
    location_targets: list[tuple[str, int]],
) -> bytes:
    """Concatenate front + appendix and rewrite our marker-URI link annotations
    on the front pages into internal /GoTo links pointing at the matching
    appendix page. Marker links whose label has no appendix target are dropped
    (the rendered hyperlink-styled text remains).
    """
    from urllib.parse import unquote

    front_reader    = PdfReader(io.BytesIO(front_pdf_bytes))
    appendix_reader = PdfReader(io.BytesIO(appendix_pdf_bytes))
    front_count     = len(front_reader.pages)

    writer = PdfWriter()
    writer.append(front_reader)
    writer.append(appendix_reader)

    target_index = {label: idx for label, idx in location_targets if label}

    for fp_idx in range(front_count):
        page = writer.pages[fp_idx]
        annots = page.get("/Annots")
        if not annots:
            continue

        keep = []
        for a in annots:
            try:
                obj = a.get_object() if hasattr(a, "get_object") else a
                if obj.get("/Subtype") != "/Link":
                    keep.append(a)
                    continue
                action_ref = obj.get("/A")
                if action_ref is None:
                    keep.append(a)
                    continue
                action = action_ref.get_object() if hasattr(action_ref, "get_object") else action_ref
                if action.get("/S") != "/URI":
                    keep.append(a)
                    continue
                uri = str(action.get("/URI") or "")
                if not uri.startswith(URI_HYPERLINK_PREFIX):
                    keep.append(a)
                    continue

                label = unquote(uri[len(URI_HYPERLINK_PREFIX):])
                appendix_idx = target_index.get(label)
                if appendix_idx is None:
                    # No matching appendix page — drop the link annotation
                    continue

                target_page = writer.pages[front_count + appendix_idx]
                if NameObject("/URI") in action:
                    del action[NameObject("/URI")]
                action[NameObject("/S")] = NameObject("/GoTo")
                action[NameObject("/D")] = ArrayObject([
                    target_page.indirect_reference,
                    NameObject("/Fit"),
                ])
                keep.append(a)
            except Exception:
                keep.append(a)

        page[NameObject("/Annots")] = ArrayObject(keep)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# Unused re-exports kept for backwards reference
__all__ = ["sanitize_pdf", "docx_to_pdf", "merge_and_hyperlink", "URI_HYPERLINK_PREFIX"]
