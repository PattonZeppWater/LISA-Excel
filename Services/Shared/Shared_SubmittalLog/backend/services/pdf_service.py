"""Parse PSM_* markups out of a Bluebeam-marked PDF.

Replaces the prior XML-export workflow. The data we need lives directly in
PDF annotations:
  - /Subj         = Bluebeam "Subject" (PSM_Deviation, PSM_Exclusion, ...)
  - /PageLabels   = Bluebeam page label ("Specs 453", "Plans_WWTP 38")
  - /IRT          = points to the paired Cloud+ FreeTextCallout whose /Contents
                    is the human comment text ("This comment is a deviation")
"""

import io

from pypdf import PdfReader

_SUBJECT_BUCKET = {
    "psm_deviation":          "deviations",
    "psm_clarification":      "rfi",
    "psm_equal/substitution": "equal_substitutions",
    "psm_exclusion":          "exclusions",
}


def parse_markup_pdf(raw: bytes) -> dict:
    """Return {rfi, equal_substitutions, deviations, exclusions, ignored}."""
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc

    try:
        labels = reader.page_labels
    except Exception:
        labels = [str(i + 1) for i in range(len(reader.pages))]

    result = {
        "rfi":                 [],
        "equal_substitutions": [],
        "deviations":          [],
        "exclusions":          [],
        "ignored":             [],
    }

    items_with_page = []  # (page_idx, item, bucket)

    for pg_idx, page in enumerate(reader.pages):
        for a in (page.get("/Annots") or []):
            try:
                obj = a.get_object()
            except Exception:
                continue
            subj = obj.get("/Subj")
            if subj is None:
                continue
            subj_str = str(subj)
            bucket = _SUBJECT_BUCKET.get(subj_str.lower())

            comment = ""
            irt = obj.get("/IRT")
            if irt is not None:
                try:
                    comment = str(irt.get_object().get("/Contents") or "")
                except Exception:
                    comment = ""
            if not comment:
                comment = str(obj.get("/Contents") or "")

            item = {
                "subject":  subj_str,
                "page":     labels[pg_idx] if pg_idx < len(labels) else str(pg_idx + 1),
                "page_idx": pg_idx,
                "comment":  comment,
                "author":   str(obj.get("/T") or ""),
                "date":     str(obj.get("/CreationDate") or ""),
            }
            if bucket:
                items_with_page.append((pg_idx, item, bucket))
            else:
                result["ignored"].append(item)

    items_with_page.sort(key=lambda t: (t[0], t[1]["date"]))
    for _, item, bucket in items_with_page:
        result[bucket].append(item)

    return result
