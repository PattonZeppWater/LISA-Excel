"""
idp_panelboard.py — parse a PANELBOARD SCHEDULE (offline, text-layer).

Returns {ckt_number: {"breaker": "20/1", "desc": "SITE POLE LIGHTS", "load": "L"}}
so idp_terms.apply_panelboard_circuits can attach breaker ratings and cross-check
circuit descriptions against a conduit's destination.

Text-layer panelboards only — a vector-drawn schedule (text is curves) yields {}
and the pipeline falls back to circuit-number-only terms (still correct), or the
EDC/vision escalation supplies the transcription.

find_and_parse(paths) scans the given PDFs, returns the merged CKT map.
"""
from __future__ import annotations

import os
import re

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None

_HDR = re.compile(r"PANELBOARD\s+SCHEDULE|PANEL\s*:", re.I)
# a data line like: "1  SITE POLE LIGHTS  20/1  L  920 ..." — CKT then desc then breaker
_ROW = re.compile(r"^\s*(\d{1,2})\b(.*?)(\d{1,3}/[123])\b", re.S)
_BRK = re.compile(r"\b(\d{1,3}/[123])\b")


def parse_text(page_text):
    """Parse one page's text into {ckt:{breaker,desc}}. Best-effort — panelboard
    layouts vary, so we anchor on 'CKT number ... breaker (NN/P)' patterns."""
    out = {}
    if not page_text or not _HDR.search(page_text):
        return out
    for ln in page_text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"^(\d{1,2})\s+(.+)", ln)
        if not m:
            continue
        ckt = int(m.group(1))
        if ckt < 1 or ckt > 84:
            continue
        rest = m.group(2)
        brk = _BRK.search(rest)
        breaker = brk.group(1) if brk else ""
        # description = text before the breaker token (strip trailing load letters/VA)
        desc = rest[:brk.start()].strip() if brk else rest.strip()
        desc = re.sub(r"\s{2,}.*$", "", desc).strip(" .-")
        if desc or breaker:
            out[ckt] = {"breaker": breaker, "desc": desc}
    return out


def find_and_parse(paths, max_pages=40):
    """Scan PDFs in `paths` for a text-layer panelboard schedule; merge results."""
    if fitz is None:
        return {}
    merged = {}
    for p in paths or []:
        if not str(p).lower().endswith(".pdf") or not os.path.exists(p):
            continue
        try:
            d = fitz.open(p)
        except Exception:
            continue
        try:
            for i in range(min(len(d), max_pages if len(d) < 60 else len(d))):
                t = d[i].get_text("text")
                if _HDR.search(t):
                    for k, v in parse_text(t).items():
                        merged.setdefault(k, v)
        finally:
            d.close()
    return merged


if __name__ == "__main__":
    import sys, json
    print(json.dumps(find_and_parse(sys.argv[1:]), indent=2))
