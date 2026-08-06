"""
idp_vision_schedule.py — read a CONDUIT (AND CABLE) SCHEDULE that offline OCR can't, by
rendering the schedule page(s) and having Claude transcribe the table (Phase 1a fallback).

Offline OCR fails when the schedule is embedded in a busy PLAN SHEET (e.g. an E-sheet titled
"SITE PLAN, CONDUIT AND CABLE SCHEDULE"): the table-bbox detector locks onto the drawing, not
the little schedule table, and returns nothing. This module is the safety bridge — it renders
the candidate schedule pages and asks Claude to read the table into structured rows, which are
converted to conduit records. Opt-in: needs ANTHROPIC_API_KEY (no key ⇒ returns []). The
caller renders the pages for manual/skill transcription instead.
"""
import os
import json

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-5"

_PROMPT = (
    "You are reading an electrical plan sheet that contains a CONDUIT (AND CABLE) SCHEDULE "
    "table. Transcribe EVERY row of that schedule — ignore the drawing, the notes, and the "
    "title block. Return ONLY a JSON array, one object per conduit row, with keys: "
    '"conduit" (the conduit tag/number), "from" (source), "to" (destination), '
    '"size" (trade size, e.g. 1-1/2\\"), "type" (conduit material if shown, else \\"\\"), '
    '"cable" (the cable size & quantity / fill text verbatim, e.g. "3-#4, #8G"), '
    '"remarks" (the remarks cell). Use \\"\\" for any cell that is blank. Do not invent rows. '
    "If the sheet has no conduit schedule table, return []."
)


def _vision_rows(image_paths, timeout=180):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not image_paths:
        return None
    import base64, urllib.request
    content = [{"type": "text", "text": _PROMPT}]
    for p in image_paths[:12]:
        try:
            with open(p, "rb") as fh:
                content.append({"type": "image", "source": {"type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(fh.read()).decode()}})
        except OSError:
            continue
    body = json.dumps({"model": _MODEL, "max_tokens": 4000,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(_API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        txt = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
        s, e = txt.find("["), txt.rfind("]")
        return json.loads(txt[s:e + 1]) if s >= 0 and e > s else None
    except Exception:
        return None


def _norm_type(t):
    import re
    u = re.sub(r"[^A-Z0-9/]", "", str(t or "").upper())
    if not u:
        return "XXX"
    if u.startswith("PVC") and "GRS" in u:
        return "RGS"           # PVC-coated galvanized rigid steel
    if u.startswith("PVC"):
        return "PVC"
    if u in ("GRS", "GRC", "RIGID", "RGS"):
        return "RGS"
    if u == "RMC":
        return "RMC"
    if u == "FLEX":
        return "FLEX"
    return u if u in ("PVC", "RGS", "RMC", "FLEX", "PVC/RGS", "PCS", "RMC-PVC") else "XXX"


def _rows_to_records(rows):
    """Vision schedule rows -> conduit records (coarse fill parsed from the cable text)."""
    import re
    recs = []
    for r in rows or []:
        name = str(r.get("conduit") or "").strip()
        if not name:
            continue
        cable = str(r.get("cable") or "")
        fill = []
        # ground conductor: "#8G" / "#10 G" / "#14G"
        for gm in re.finditer(r"#\s*(\d+/?\d*)\s*G\b", cable, re.I):
            fill.append({"type": "GROUND", "wire_ct": 1, "count": 1,
                         "gauge": "#" + gm.group(1), "is_ground": True})
        # power/control conductors: "3-#4", "6-#10", "12-#14"
        for cm in re.finditer(r"(\d+)\s*[-x]\s*#\s*(\d+/?\d*)", cable, re.I):
            ct = int(cm.group(1)); ga = "#" + cm.group(2)
            typ = "POWER" if int(re.sub(r"\D", "", cm.group(2)) or 99) <= 8 else "CONTROL"
            fill.append({"type": typ, "wire_ct": ct, "count": ct, "gauge": ga, "is_ground": False})
        if not fill:
            # manufacturer cable / pull tape / unknown -> a single coarse group
            if re.search(r"PULL\s*TAPE|PULLTAPE|PULL\s*ROPE", cable, re.I):
                fill = [{"type": "PULL_ROPE", "wire_ct": None, "count": None, "gauge": "PULLTAPE", "is_ground": False}]
            else:
                fill = [{"type": "MFG_CABLE", "wire_ct": 1, "count": 1, "gauge": "MANU", "is_ground": False}]
        recs.append({"name": name,
                     "source": [str(r.get("from") or "").strip()],
                     "dest": [str(r.get("to") or "").strip()],
                     "size": str(r.get("size") or "").strip(),
                     "ctype": _norm_type(r.get("type")),
                     "fill": fill,
                     "deviations": str(r.get("remarks") or "").strip(),
                     "flags": ["from_vision_schedule"]})
    return recs


def find_schedule_pages(pdfs, log=lambda *a: None):
    """[(pdf, page_index), …] pages that carry a conduit/cable schedule, via the offline
    page finders (title/keyword based) — cheap, no OCR."""
    refs = []
    try:
        import idp_vision as _V
    except Exception:
        _V = None
    try:
        import idp_cable_schedule as _C
    except Exception:
        _C = None
    for p in pdfs:
        if not str(p).lower().endswith(".pdf"):
            continue
        pages = set()
        for finder in (getattr(_V, "find_schedule_pages", None),
                       getattr(_C, "find_cable_schedule_pages", None)):
            if finder:
                try:
                    pages |= set(finder(p) or [])
                except Exception:
                    pass
        for pi in sorted(pages):
            refs.append((p, pi))
    return refs


def render_schedule_pages(page_refs, out_dir, dpi=220, cap=10):
    import fitz
    os.makedirs(out_dir, exist_ok=True)
    by_pdf = {}
    for path, pi in page_refs:
        by_pdf.setdefault(path, []).append(pi)
    imgs = []
    for path, pis in by_pdf.items():
        try:
            d = fitz.open(path)
        except Exception:
            continue
        try:
            for pi in sorted(set(pis)):
                if len(imgs) >= cap:
                    break
                out = os.path.join(out_dir, f"sched_{os.path.basename(path)[:24]}_p{pi + 1}.png")
                try:
                    d[pi].get_pixmap(dpi=dpi).save(out)
                    imgs.append(out)
                except Exception:
                    continue
        finally:
            d.close()
    return imgs


def read_schedule_via_vision(pdfs, log=lambda *a: None, out_dir=None):
    """Render the candidate schedule page(s) and transcribe the conduit schedule via Claude.
    Returns conduit records (possibly []). No API key ⇒ [] (caller falls back to rendering
    the pages for a manual/skill read)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return []
    refs = find_schedule_pages(pdfs, log=log)
    if not refs:
        return []
    if out_dir is None:
        try:
            import idp_escalate
            out_dir = os.path.join(idp_escalate._localappdata_dir(), "_sched_vision")
        except Exception:
            out_dir = "_sched_vision"
    imgs = render_schedule_pages(refs, out_dir)
    if not imgs:
        return []
    log(f"Vision schedule-read: rendered {len(imgs)} candidate schedule sheet(s), asking "
        "Claude to transcribe the conduit schedule …")
    rows = _vision_rows(imgs)
    if not rows:
        log("Vision schedule-read: no rows returned.")
        return []
    recs = _rows_to_records(rows)
    log(f"Vision schedule-read: transcribed {len(recs)} conduit(s) from the plan-sheet schedule.")
    return recs
