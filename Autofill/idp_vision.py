"""
idp_vision.py — read SCANNED conduit & wire routing schedules (no text layer)
=============================================================================
Real project plan sets carry the conduit schedule on a scanned/vector electrical
sheet (e.g. Luhdorff & Scalmanini "E-5 ELECTRICAL SITE PLAN & CONDUIT SCHEDULE").
There is no text to extract, so the text ladder (idp_extract) returns nothing —
this is what made Lennar 73.1142 come up empty. This module closes that gap
WITHOUT needing AIC's finished IDP package:

  find_scanned_pages(pdf)      -> page indices that are image/low-text (candidates)
  render_pages(pdf, pages, d)  -> PNG paths (for vision)
  schedule_to_records(data)    -> extractor records from a transcribed schedule
  transcribe_via_api(images)   -> records, if ANTHROPIC_API_KEY is set (Claude vision)
  build_vision_packet(images)  -> writes ASK_CLAUDE_VISION.md when there's no key,
                                  so an attached Claude Code chat can transcribe

The transcription intermediate (what a vision pass returns) mirrors the real
"CONDUIT & WIRE ROUTING SCHEDULE" columns:

  {"conduits": [
     {"tag":"A051", "from":"MOTOR CONTROL SECTION", "to":"LEVEL TRANSMITTER",
      "size":"3/4\\"", "type":"SPEC",
      "power_wire":  {"qty":2, "size":"#14"},
      "control_wire":{"qty":1, "size":"#18", "tsp":true},
      "ground_size":"", "notes":"LIT-051"},
     ...]}
"""
from __future__ import annotations

import json
import os
import re

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

_SCHED_HINT = ("CONDUIT", "SCHEDULE", "WIRE ROUTING", "ROUTING SCHEDULE")


def find_schedule_pages(pdf, max_pages=4000):
    """PINPOINT the conduit-schedule sheet(s) in a big plan set instead of
    rendering every drawing page. Recognizes:
      * a Bluebeam markup label naming the region (e.g. 'Facility_ConduitSchedule',
        'ConduitSchedule', 'Panel*Schedule') — the user tags these;
      * a page whose text has 'CONDUIT SCHEDULE' / 'CONDUIT & CABLE SCHEDULE' /
        'CONDUIT & WIRE ROUTING SCHEDULE' AND is graphics-heavy (a drawing, not a
        spec paragraph that merely mentions the words).
    Returns 0-based page indices (schedule pages first, then panelboard)."""
    if fitz is None:
        return []
    try:
        d = fitz.open(pdf)
    except Exception:
        return []
    sched, panel = [], []
    try:
        for i in range(min(len(d), max_pages)):
            # FAST: text only. Bluebeam markup labels have no spaces, so normalize.
            t = d[i].get_text("text")
            u = re.sub(r"[^A-Z0-9]", "", t.upper())
            if any(k in u for k in ("CONDUITSCHEDULE", "CONDUIT&CABLESCHEDULE",
                                    "CONDUITANDCABLESCHEDULE", "CONDUITWIREROUTING",
                                    "CONDUITCABLESCHEDULE",
                                    # title variance: RACEWAY* and CABLE-first orderings
                                    "RACEWAYSCHEDULE", "RACEWAYCABLESCHEDULE",
                                    "RACEWAYANDCABLESCHEDULE", "CABLEANDCONDUITSCHEDULE",
                                    "CABLE&CONDUITSCHEDULE", "CABLECONDUITSCHEDULE")):
                # exclude spec pages that only cite the schedule in prose: a real
                # schedule sheet is graphics-heavy / low-text (check only here)
                is_drawing = len(t.strip()) < 500
                if not is_drawing:
                    try:
                        is_drawing = len(d[i].get_drawings()) > 300
                    except Exception:
                        is_drawing = False
                if is_drawing:
                    sched.append(i)
            if any(k in u for k in ("PANELBOARDSCHEDULE", "PANELLSCHEDULE",
                                    "PANEL_LSCHEDULE", "FACILITYPANEL")):
                panel.append(i)
        seen, out = set(), []
        for i in sched + panel:
            if i not in seen:
                seen.add(i); out.append(i)
        return out
    finally:
        d.close()


def find_scanned_pages(pdf, max_text=250):
    """Pages with (almost) no extractable text but heavy graphical content — the
    signature of an electrical sheet a vision pass must read. Catches BOTH raster
    scans (big embedded image) AND vector CAD exports (text drawn as curves, so
    the page has hundreds/thousands of vector paths but little/no text). Returns
    0-based page indices."""
    if fitz is None:
        return []
    try:
        d = fitz.open(pdf)
    except Exception:
        return []
    out = []
    try:
        for i in range(len(d)):
            page = d[i]
            if len(page.get_text("text").strip()) > max_text:
                continue                      # has a real text layer — not scanned
            big_raster = any((im[2] or 0) * (im[3] or 0) > 500_000
                             for im in page.get_images(full=True))
            try:
                vector_dense = len(page.get_drawings()) > 200
            except Exception:
                vector_dense = False
            if big_raster or vector_dense:
                out.append(i)
        return out
    finally:
        d.close()


def render_pages(pdf, pages, out_dir, dpi=170, prefix="sched"):
    """Render the given pages to PNGs for a vision pass. Returns paths."""
    if fitz is None:
        return []
    os.makedirs(out_dir, exist_ok=True)
    d = fitz.open(pdf)
    paths = []
    try:
        stem = os.path.splitext(os.path.basename(pdf))[0].replace(" ", "_")[:40]
        for i in pages:
            pix = d[i].get_pixmap(dpi=dpi)
            p = os.path.join(out_dir, f"{prefix}_{stem}_p{i + 1:02}.png")
            pix.save(p)
            paths.append(p)
        return paths
    finally:
        d.close()


# ── turn a transcribed schedule into records ────────────────────────────────
def _gauge(s):
    s = str(s or "").strip()
    return s if s else ""


def schedule_to_records(data):
    """Convert a transcribed CONDUIT & WIRE ROUTING SCHEDULE into extractor
    records. Colors/symbols are left to the write pipeline (apply_conventions
    fills phase/TSP/ground colors by convention). Wire-fill semantics:
      power_wire  qty/size  -> one POWER group, Wire Ct = qty
      control_wire qty/size -> TSP group (Ct1) if tsp, else CONTROL group Ct=qty
      ground_size           -> a GROUND group (Ct1); the pipeline encodes GND
      NOTES 'PULL ROPE'/'SPARE' with no wires -> a PULL_ROPE group
    """
    recs = []
    for c in (data or {}).get("conduits", []):
        tag = str(c.get("tag", "")).strip()
        if not tag:
            continue
        rec = {"name": tag,
               "ctype": str(c.get("type", "")).strip() or "PER SPEC",
               "size": str(c.get("size", "")).strip(),
               "source": [str(c.get("from", "")).strip()] if c.get("from") else [],
               "dest": [str(c.get("to", "")).strip()] if c.get("to") else [],
               "fill": [],
               # the schedule's POWER/CONTROL/GROUND columns are the designed
               # conductor set — authoritative. Don't let ensure_ground add a
               # ground the schedule leaves blank (e.g. A051/A071), nor let
               # merge_analog_pairs re-type a control pair (the schedule already
               # marks TSP explicitly). apply_conventions still fills colors.
               "fill_authoritative": True}
        note = str(c.get("notes", "")).upper()
        pw = c.get("power_wire") or {}
        cw = c.get("control_wire") or {}
        gs = _gauge(c.get("ground_size"))

        pw_qty = int(pw.get("qty") or 0)
        cw_qty = int(cw.get("qty") or 0)

        if pw_qty:
            rec["fill"].append({"type": "POWER", "count": pw_qty, "wire_ct": pw_qty,
                                "gauge": _gauge(pw.get("size")), "colors": []})
        if cw_qty:
            if cw.get("tsp"):
                rec["fill"].append({"type": "TSP", "count": 1, "wire_ct": 1,
                                    "gauge": _gauge(cw.get("size")) or "#18",
                                    "colors": ["RED/BLK"]})
            else:
                rec["fill"].append({"type": "CONTROL", "count": cw_qty, "wire_ct": cw_qty,
                                    "gauge": _gauge(cw.get("size")), "colors": []})
        if gs:
            rec["fill"].append({"type": "GROUND", "count": 1, "wire_ct": 1,
                                "gauge": gs, "colors": ["GRN"]})
        # a spare/pull-rope conduit with no conductors
        if not rec["fill"] and ("PULL ROPE" in note or "SPARE" in note or tag[:1] == "X"):
            rec["fill"].append({"type": "PULL_ROPE", "count": 1, "wire_ct": 1,
                                "gauge": "", "colors": ["N/A"]})
        # a utility-service power conduit with no conductors listed ("per utility")
        # still renders as a POWER sheet — flag the undetermined fill for verify
        elif not rec["fill"] and tag[:1] == "P" and "UTILITY" in note:
            rec["fill"].append({"type": "POWER", "count": 1, "wire_ct": 1,
                                "gauge": "", "colors": []})
            rec.setdefault("flags", []).append(
                f"{tag}: fill per utility — conductor count/size/color not in the "
                f"schedule; verify with the utility.")
        if c.get("notes"):
            rec["deviations"] = str(c.get("notes")).strip()
        recs.append(rec)
    return recs


# ── Claude vision (optional, when a key exists) ─────────────────────────────
_MODEL = "claude-opus-4-8"
_API_URL = "https://api.anthropic.com/v1/messages"

VISION_PROMPT = (
    "You are reading a scanned electrical CONDUIT & WIRE ROUTING SCHEDULE from a "
    "project plan set. Transcribe EVERY row of the schedule table into JSON with "
    "this exact shape — do not infer or add conduits that are not printed:\n"
    '{"conduits":[{"tag":"","from":"","to":"","size":"","type":"",'
    '"power_wire":{"qty":0,"size":""},"control_wire":{"qty":0,"size":"","tsp":false},'
    '"ground_size":"","notes":""}]}\n'
    "Rules: qty is the integer conductor count in that column (0 if the cell is "
    "blank/dashes). size keeps the gauge exactly as printed (e.g. #14, #1, 3/0). "
    "Set control_wire.tsp=true when the control-wire cell says TSP. Put device "
    "tags (LIT-051, PSH-061), 'PER UTILITY', 'PULL ROPE', etc. in notes. Output "
    "ONLY the JSON."
)


def transcribe_via_api(image_paths, model=_MODEL, timeout=120):
    """If ANTHROPIC_API_KEY is set, ask Claude vision to transcribe the rendered
    schedule page(s) and return records. Returns None if no key / any failure."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not image_paths:
        return None
    import base64
    import urllib.request
    content = [{"type": "text", "text": VISION_PROMPT}]
    for p in image_paths:
        try:
            with open(p, "rb") as fh:
                b64 = base64.standard_b64encode(fh.read()).decode()
        except OSError:
            continue
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": b64}})
    body = json.dumps({"model": model, "max_tokens": 4000,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(_API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        txt = "".join(p.get("text", "") for p in data.get("content", [])
                      if p.get("type") == "text")
        s, e = txt.find("{"), txt.rfind("}")
        if s < 0 or e < 0:
            return None
        return schedule_to_records(json.loads(txt[s:e + 1]))
    except Exception:
        return None


def build_vision_packet(image_paths, out_dir, project=""):
    """No API key → write ASK_CLAUDE_VISION.md pointing at the rendered schedule
    images so an attached Claude Code chat can transcribe them and hand the JSON
    back. Returns the packet path (or '')."""
    if not image_paths:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "ASK_CLAUDE_VISION.md")
    lines = [
        f"# IDP Extractor — scanned schedule needs transcription"
        f"{(' (' + project + ')') if project else ''}",
        "",
        "The plan set's conduit schedule is on a SCANNED sheet (no text layer), so "
        "the text extractor found nothing. Open the image(s) below, then reply with "
        "the schedule as JSON in the shape the extractor expects (see "
        "`idp_vision.VISION_PROMPT`). The rendered page image(s):",
        "",
    ]
    for p in image_paths:
        lines.append(f"- `{p}`")
    lines += ["", VISION_PROMPT]
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path
    except OSError:
        return ""


if __name__ == "__main__":
    import sys
    for pdf in sys.argv[1:]:
        pages = find_scanned_pages(pdf)
        print(f"{pdf}\n  scanned/low-text pages: {[p + 1 for p in pages]}")
