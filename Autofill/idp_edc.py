"""
idp_edc.py — pull terminal landings off AIC EDC drawing sheets into the FillIndex.

The EDC package's terminal data lives on VECTOR drawing sheets (three-line,
terminal, PLC I/O, analog-input) — there is no clean text table, so it must be
READ (vision). This module:
  find_edc_sheets(pdf)          -> the AIC drawing pages worth reading
  render_sheets(pdf, pages, d)  -> PNGs
  transcribe_via_api(images)    -> {conduit: term-record}, if ANTHROPIC_API_KEY set
  build_edc_packet(images, d)   -> ASK_CLAUDE_EDC.md when there is no key
  apply_edc_terms(records, map) -> write the transcribed S/D Tag+Term onto records

Transcription shape (what a vision pass returns), keyed by conduit tag:
  {"C001": {"s_tag":"EG1","s_terms":["YA-10","YH-10","YR-10","YF-10"],
            "d_tag":"PLC1:DI","d_terms":["0.2.01.05","0.2.01.06","0.2.01.07","0.2.01.08"],
            "fill_type":"CONTROL"},
   "H005": {"s_tag":"EG1","s_terms":["ØA","ØB","ØC","N"],
            "d_tag":"ATS1","d_terms":["ØA","ØB","ØC","N"],"fill_type":"POWER"}}
Phases are kept ØA/ØB/ØC — never relabel to terminal codes.
"""
from __future__ import annotations

import json
import os
import re

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None

_KINDS = ("THREE-LINE", "TERMINAL", "WIRING DIAGRAM", "I/O", "ANALOG INPUT",
          "ANALOG OUTPUT", "ONE-LINE", "ELEMENTARY", "INTERCONNECTION DIAGRAM")
_DWG = re.compile(r"\b\d{2}\.\d{4}-\d+[A-Z]?\b")   # AIC dwg number e.g. 73.1188-51
_AIC = re.compile(r"ADVANCED INTEGRATION|REIMAGINING", re.I)


def _key(t):
    return str(t or "").replace("-", "").upper()


def find_edc_sheets(pdf, max_pages=1200):
    """AIC drawing pages carrying terminal-bearing content (a DWG number in the
    title block + a diagram-kind keyword, low-ish text = a drawing not a spec)."""
    if fitz is None:
        return []
    try:
        d = fitz.open(pdf)
    except Exception:
        return []
    out = []
    try:
        for i in range(min(len(d), max_pages)):
            t = d[i].get_text("text")
            u = t.upper()
            if not _DWG.search(t):
                continue
            if not (_AIC.search(t) or any(k in u for k in _KINDS)):
                continue
            if not any(k in u for k in _KINDS):
                continue
            if len(t) > 8000:        # dense spec/cut-sheet text, not a drawing sheet
                continue
            out.append(i)
        return out
    finally:
        d.close()


def render_sheets(pdf, pages, out_dir, dpi=140):
    if fitz is None:
        return []
    os.makedirs(out_dir, exist_ok=True)
    d = fitz.open(pdf)
    paths = []
    try:
        stem = os.path.splitext(os.path.basename(pdf))[0].replace(" ", "_")[:40]
        for i in pages:
            p = os.path.join(out_dir, f"edc_{stem}_p{i + 1:04}.png")
            d[i].get_pixmap(dpi=dpi).save(p)
            paths.append(p)
        return paths
    finally:
        d.close()


_CHAN = re.compile(r"^0\.\d\.\d\d\.\d\d[+\-]?$")      # PLC channel (analog has +/-)
_TAG = re.compile(r"^[A-Z]{1,4}-?\d{2,3}[A-Z]?$")     # ISA-ish tag (YA-10, FOP-04, F01-01)


def parse_io_sheets(pdf, max_pages=1200):
    """OFFLINE (no vision): read AIC PLC-I/O sheets by their TEXT layer +
    coordinates. Each I/O row aligns a PLC channel (x≈mid) with its ISA tag and
    description (to the right) on one y-band. Returns list of dicts
    {channel, tag, desc, module, sheet}."""
    if fitz is None:
        return []
    try:
        d = fitz.open(pdf)
    except Exception:
        return []
    pts = []
    try:
        for i in range(min(len(d), max_pages)):
            t = d[i].get_text("text")
            u = t.upper()
            if not re.search(r"0\.\d\.\d\d\.\d\d", t):
                continue
            if "PLC I/O" not in u and "I/O DIGITAL" not in u and "I/O ANALOG" not in u:
                continue
            module = "AI" if "ANALOG" in u else ("DI" if "DIGITAL" in u else "")
            words = d[i].get_text("words")   # x0,y0,x1,y1,word,...
            chans = [w for w in words if _CHAN.match(w[4])]
            seen = set()
            for c in chans:
                cy, cx1, addr = c[1], c[2], c[4].rstrip("+-")   # base channel
                if (i, addr, round(cy / 30)) in seen:           # dedup analog +/- pair
                    continue
                row = sorted((w for w in words if abs(w[1] - cy) < 7 and w[0] > cx1 - 4),
                             key=lambda w: w[0])
                tag = next((w[4] for w in row if _TAG.match(w[4]) and w[4].rstrip("+-") != addr), "")
                tagx = next((w[0] for w in row if w[4] == tag), cx1) if tag else cx1
                desc = " ".join(w[4] for w in row if w[0] > tagx
                                and not _TAG.match(w[4]) and not _CHAN.match(w[4]))
                if not tag:   # some rows carry the loop tag inside "Channel NN - <TAG>"
                    dm = re.search(r"Channel\s+\d+\s*-\s*([A-Z]{1,4}-?\d{1,4}(?:-\d{1,4})?)", desc)
                    if dm:
                        tag = dm.group(1)
                seen.add((i, addr, round(cy / 30)))
                pts.append({"channel": addr, "tag": tag, "desc": desc.strip(),
                            "module": module, "sheet": i + 1})
        return pts
    finally:
        d.close()


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def match_io_to_conduits(records, points, log=lambda *a: None):
    """OFFLINE matcher: tie parsed PLC-I/O points to conduits by device tag /
    description, and write S/D Term (PLC channel) + Tag (ISA point). Groups
    multiple points onto one conduit (e.g. all 4 generator status bits → the
    generator conduit). Returns count of conduits termed."""
    if not points:
        return 0
    _SIGNAL = ("CONTROL", "TSP", "MFGCABLE", "MFG_CABLE", "CAT6", "CAT-6")
    _INSTR = re.compile(r"\b(FE|LE|FIT|LIT|PIT|AIT)\s*[- ]?([A-Z]?-?\d[\d-]*)")
    n = 0
    for rec in records or []:
        if rec.get("wires"):                     # already termed (power phases etc.)
            continue
        # ONLY field signal conduits — never a POWER feeder/branch. This is what
        # kept generator DIs off H005/H006/L002 (they carry POWER, not signal).
        grp = next((g for g in rec.get("fill", [])
                    if _key(g.get("type")) in _SIGNAL), None)
        if grp is None:
            continue
        dst = " ".join(rec.get("dest") or []).upper()
        src = " ".join(rec.get("source") or []).upper()
        note = str(rec.get("deviations") or "").upper()
        blob = f"{dst} {src} {note}"
        matched = []
        if re.search(r"\bEG\d*\b", dst) or re.search(r"\bEG\d*\b", src):
            # generator status bits → the PLC<->EG signal conduit (C001)
            matched = [p for p in points if "GENERATOR" in p["desc"].upper()
                       and p["module"] == "DI"]
        else:
            m = _INSTR.search(blob)
            if m:
                cd = _digits(m.group(2))
                # instrument family: FE/FIT→F (flow), LE/LIT→L (level),
                # PE/PIT→P (pressure), TE/TIT→T, AE/AIT→A — the AI tag must share it
                fam = {"FE": "F", "FIT": "F", "LE": "L", "LIT": "L", "PIT": "P",
                       "PE": "P", "TE": "T", "AIT": "A"}.get(m.group(1).upper(), "")
                for p in points:
                    if p["module"] != "AI":
                        continue
                    tg = p["tag"].upper()
                    if not tg or re.match(r"^(IR|OR|CR|X)\d", tg):   # relay/aux, not instrument
                        continue
                    if fam and not tg.startswith(fam):               # wrong instrument family
                        continue
                    if cd and _digits(tg) == cd:                     # EXACT loop-number match
                        matched.append(p)
        if not matched:
            continue
        field_desc = (rec.get("dest") or [""])[0] or (rec.get("source") or [""])[0]
        # S (PLC) side: term = channel, tag blank, desc = the PLC module.
        # D (field) side: tag = the real ISA point tag, desc = the field equipment.
        wires = [{"src": ("", "", p["channel"]), "dst": ("", p["tag"], "")}
                 for p in matched[:4]]
        rec["wires"] = wires
        grp["slots"] = len(wires)
        grp["s_desc"] = ["PLC1 " + (matched[0]["module"] or "I/O")]
        if field_desc:
            grp["d_desc"] = [field_desc]
        rec.setdefault("flags", []).append("edc_io_terms(verify)")
        n += 1
    if n:
        log(f"EDC PLC-I/O (text layer): {len(points)} points parsed → {n} conduit(s) term-backfilled.")
    return n


# ── ladder-style EDC I/O sheets (AIC AutoCAD-Electrical export) ──────────────
# Many AIC EDC I/O sheets are LADDER DRAWINGS: the terminal-block tag (TBDI-0.02,
# TBAI-0.07, TBDO-0.05) is text, and each channel's device DESCRIPTION is text, but
# the channel NUMBER is graphical/positional (rotated vector), so it can't be read
# reliably from the text layer. What CAN be read reliably is (a) the TB tag and
# (b) which field devices land on that TB — enough to assign the correct terminal-
# block landing; the exact channel is left blank + flagged for a quick human/vision
# check. This complements parse_io_sheets (which needs explicit 0.r.ss.cc addresses).
_TB_RE = re.compile(r"\bTB([AD])([IO])-(\d\.\d\d)\b")
_IO_BOILER = {"SPARE", "DIGITAL", "ANALOG", "INPUT", "OUTPUT", "PLC", "FIELD", "RACK",
              "REAR", "FRONT", "PANEL", "THE", "AND", "OR", "OF", "TO", "IS", "CO",
              "WITHIN", "THIS", "DOCUMENT", "PROPERTY", "MAY", "NOT", "BE", "USED",
              "FOR", "ANY", "PURPOSE", "WITHOUT", "EXPRESS", "WRITTEN", "CONSENT",
              "ALTERED", "SHARED", "CONTAINED", "INFORMATION", "REV", "DATE", "NAME",
              "CHANGES", "PROJECT", "ENGINEER", "DRAWN", "BY", "NO", "ELECTRICAL",
              "DISTRIBUTION", "DRAWINGS", "I/O", "LYLES", "W.M."}


def _tokens(s):
    """Significant alphabetic word stems (≥3 letters), boilerplate dropped. Alpha-only
    so 'DOOR-1' → 'DOOR' matches the sheet's 'MAN DOOR 1', and numbers (which are the
    graphical channel #, not device identity) never drive the match."""
    return [w for w in re.findall(r"[A-Z]{3,}", str(s or "").upper())
            if w not in _IO_BOILER]


def parse_io_ladder(pdf, max_pages=1200):
    """Read ladder-style AIC PLC-I/O sheets. Returns per terminal-block:
    {tb, kind ('AI'/'DI'/'AO'/'DO'), module ('AI'/'DI'), blob (sheet device text),
    tokens (set), sheet}. One entry per TB tag found on an I/O sheet."""
    if fitz is None:
        return []
    try:
        d = fitz.open(pdf)
    except Exception:
        return []
    out = []
    try:
        for i in range(min(len(d), max_pages)):
            t = d[i].get_text("text")
            u = t.upper()
            if not (("PLC I/O" in u) or ("DIGITAL INPUT" in u) or ("ANALOG INPUT" in u)
                    or ("DIGITAL OUTPUT" in u) or ("ANALOG OUTPUT" in u)):
                continue
            tbs = _TB_RE.findall(u)
            if not tbs:
                continue
            toks = set(_tokens(t))
            for a, io, num in set(tbs):
                kind = a + io                    # AI / DI / AO / DO
                out.append({"tb": f"TB{kind}-{num}", "kind": kind,
                            "module": "AI" if a == "A" else "DI",
                            "blob": u, "tokens": toks, "sheet": i + 1})
        return out
    finally:
        d.close()


_TITLE_WORDS = {"DWG", "DRAWN", "ENGINEER", "PAGE", "REV", "PROJECT", "INFORMATION",
                "COMMISSION", "DIVISION", "CONTRACTOR", "LYLES", "CONSENT", "ALTERED",
                "PERFORMANCE", "PROPERTY", "DOCUMENT", "SHARED", "PURPOSE", "WRITTEN",
                "EXPRESS", "CHANGES", "CONTAINED", "STATUS", "SUBMITTAL", "STATION"}


def parse_io_positional(pdf, max_pages=1200):
    """OFFLINE, NO-OCR channel reader for AIC ladder-style PLC-I/O sheets. The channel
    NUMBER is graphical, but the device DESCRIPTIONS are real text and the channels run
    sequentially (INPUT 00,01,…). So: read the description blocks, put them in the sheet's
    READING order (via the page rotation matrix — these sheets are rotated 270°), drop the
    TB-tag + title-block blocks, and the position IS the channel. Returns per terminal
    block a list of {tb, module, channel, desc, tokens, sheet}."""
    if fitz is None:
        return []
    try:
        d = fitz.open(pdf)
    except Exception:
        return []
    out = []
    try:
        for i in range(min(len(d), max_pages)):
            pg = d[i]
            t = pg.get_text("text")
            u = t.upper()
            if not (("PLC I/O" in u) or ("DIGITAL INPUT" in u) or ("ANALOG INPUT" in u)
                    or ("DIGITAL OUTPUT" in u) or ("ANALOG OUTPUT" in u)):
                continue
            tbm = _TB_RE.search(u)
            if not tbm:
                continue
            tb = f"TB{tbm.group(1)}{tbm.group(2)}-{tbm.group(3)}"
            module = "AI" if tbm.group(1) == "A" else "DI"
            n_ch = 8 if ("ANALOG" in u) else 16          # BMXAMI0800=8, BMXDDI1602=16
            M = pg.rotation_matrix
            items = []
            for b in pg.get_text("blocks"):
                up = b[4].upper()
                if _TB_RE.search(up):                     # the TB-tag block itself
                    continue
                if any(w in _TITLE_WORDS for w in re.findall(r"[A-Z]+", up)):
                    continue
                if ("MODICON" in up or "MODULE" in up or "RACK" in up or "SLOT" in up
                        or re.search(r"\bBMX[A-Z0-9]+", up)):
                    continue                              # module / rack-slot header blocks
                # device words + short digits (keep '1'/'2' so VFD 1 vs VFD 2 disambiguate)
                toks = [w for w in re.findall(r"[A-Za-z0-9\-]+", up)
                        if w not in _IO_BOILER and w not in _TITLE_WORDS]
                words = [w for w in toks if re.search(r"[A-Z]", w) or (w.isdigit() and len(w) <= 2)]
                if any(re.search(r"[A-Z]{3,}", w) for w in words):   # a real device word
                    desc = " ".join(words)
                elif "SPARE" in up:
                    desc = "SPARE"                        # keep spare rungs — they hold a channel slot
                else:
                    continue
                c = fitz.Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) * M
                items.append((round(c.y, 1), round(c.x, 1), desc))
            items.sort()
            for idx, (_y, _x, desc) in enumerate(items[:n_ch]):
                out.append({"tb": tb, "module": module, "channel": f"{idx:02d}",
                            "desc": desc,
                            "tokens": set(re.findall(r"[A-Z]{2,}|[0-9]", desc)),  # keep 1/2
                            "sheet": i + 1})
        return out
    finally:
        d.close()


def match_io_positional(records, channels, log=lambda *a: None):
    """Land each field-signal conduit on its EXACT PLC channel (TB tag : channel #) by
    matching the conduit's field device to the channel description read positionally.
    Offline, no vision. Returns count termed."""
    if not channels:
        return 0
    _SIGNAL = ("CONTROL", "TSP", "MFGCABLE", "MFG_CABLE", "CAT6", "CAT-6")
    n = 0
    for rec in records or []:
        if rec.get("wires") and any((w.get("src") or ("", "", ""))[2]
                                    or (w.get("dst") or ("", "", ""))[2] for w in rec["wires"]):
            continue                                       # already has a real term
        grp = next((g for g in rec.get("fill", []) if _key(g.get("type")) in _SIGNAL), None)
        if grp is None:
            continue
        src = " ".join(rec.get("source") or [])
        dst = " ".join(rec.get("dest") or [])
        s_plc = bool(re.search(r"\b(MASTER\s+)?PLC\b|PLC CABINET|NW SWITCH", src.upper()))
        field = (dst if s_plc else src) + " " + " ".join(
            x for g in rec.get("fill", []) for x in ((g.get("s_desc") or []) + (g.get("d_desc") or [])))
        ftoks = set(re.findall(r"[A-Z]{2,}|[0-9]",
                    re.sub(r"MASTER|PLC|CABINET|SWITCH", " ", field.upper())))
        if not ftoks:
            continue
        # analog signal (TSP) → analog (A) modules; discrete control → digital (D) modules.
        # Hard filter so a discrete device never lands on an analog channel or vice-versa.
        want = "AI" if _key(grp.get("type")) == "TSP" else "DI"
        best, best_score = None, 0
        for ch in channels:
            if ch["module"] != want or "SPARE" in ch["tokens"]:
                continue
            score = len(ftoks & ch["tokens"])
            if score > best_score:                        # ties keep the lower channel (first)
                best, best_score = ch, score
        if not best or best_score < 2:                    # need ≥2 shared tokens to commit
            continue
        term = f"{best['tb']}:{best['channel']}"
        plc_end, field_end = ("", best["tb"], best["channel"]), ("", "", "")
        k = max(1, int(grp.get("wire_ct") or grp.get("count") or 1))
        rec["wires"] = [({"src": plc_end, "dst": field_end} if s_plc
                         else {"src": field_end, "dst": plc_end}) for _ in range(min(k, 4))]
        grp["slots"] = len(rec["wires"])
        rec.setdefault("flags", []).append(f"edc_io_channel={term}")
        n += 1
    if n:
        log(f"EDC PLC-I/O (offline positional): {len(channels)} channels read → "
            f"{n} conduit(s) landed on their EXACT channel (no OCR/vision).")
    return n


def match_io_ladder(records, sheets, log=lambda *a: None):
    """Offline: assign the correct PLC terminal-block landing (TBDI-0.02 / TBAI-0.07 …)
    to each field-signal conduit by matching the conduit's field-device description to
    the device text on each I/O sheet. Channel # is left blank + flagged for a quick
    verify (it is graphical on ladder sheets). Never touches POWER feeders. Returns
    count of conduits term-backfilled."""
    if not sheets:
        return 0
    _SIGNAL = ("CONTROL", "TSP", "MFGCABLE", "MFG_CABLE", "CAT6", "CAT-6")
    n = 0
    for rec in records or []:
        if rec.get("wires"):                     # already termed (power / channel-address)
            continue
        grp = next((g for g in rec.get("fill", [])
                    if _key(g.get("type")) in _SIGNAL), None)
        if grp is None:
            continue
        # field end = the end that is NOT the PLC / PLC cabinet. The device name may
        # live on the CONDUIT ends (source/dest) OR — after the cable-schedule merge —
        # on the signal group's cable endpoints (s_desc/d_desc). Gather both per end.
        _plc = re.compile(r"\b(MASTER\s+)?PLC\b|PLC CABINET|NW SWITCH")
        s_text = " ".join((rec.get("source") or [])
                          + [x for g in rec.get("fill", []) for x in (g.get("s_desc") or [])])
        d_text = " ".join((rec.get("dest") or [])
                          + [x for g in rec.get("fill", []) for x in (g.get("d_desc") or [])])
        s_plc = bool(_plc.search(s_text.upper()))
        d_plc = bool(_plc.search(d_text.upper()))
        if s_plc and not d_plc:
            field, plc_is_src = d_text, True
        elif d_plc and not s_plc:
            field, plc_is_src = s_text, False
        else:
            # can't disambiguate the PLC end — match device tokens from both ends,
            # and default the TB landing to the destination end.
            field, plc_is_src = s_text + " " + d_text, False
        ftoks = set(_tokens(field))
        if not ftoks:
            continue
        # analog signal (TSP) lands on an AI block; discrete control on a DI block —
        # a small module preference breaks ties when the device name appears on both
        # the input and output sheets, without overriding a strong name match.
        pref = "AI" if _key(grp.get("type")) == "TSP" else "DI"
        best, best_score = None, 0.0
        for sh in sheets:
            score = len(ftoks & sh["tokens"]) + (0.5 if sh["module"] == pref else 0.0)
            if score > best_score:
                best, best_score = sh, score
        if not best or best_score < 1:
            continue
        tb = best["tb"]
        # PLC side gets the TB tag; channel blank (graphical) + flagged to verify.
        plc_end = ("", tb, "")
        field_end = ("", "", "")
        k = max(1, int(grp.get("wire_ct") or grp.get("count") or 1))
        wires = []
        for _ in range(min(k, 4)):
            wires.append({"src": plc_end, "dst": field_end} if plc_is_src
                         else {"src": field_end, "dst": plc_end})
        rec["wires"] = wires
        grp["slots"] = len(wires)
        (grp.__setitem__("s_desc", ["MASTER PLC " + best["module"]]) if plc_is_src
         else grp.__setitem__("d_desc", ["MASTER PLC " + best["module"]]))
        rec.setdefault("flags", []).append(f"edc_io_tb={tb}(verify channel#)")
        n += 1
    if n:
        log(f"EDC PLC-I/O (ladder sheets): {len(sheets)} terminal blocks → "
            f"{n} conduit(s) landed on their TB (channel # flagged to verify).")
    return n


def apply_edc_terms(records, term_map):
    """Write transcribed S/D Tag + Term onto the matching conduit's primary fill.
    Phases stay ØA/ØB/ØC. Returns count of conduits termed."""
    if not term_map:
        return 0
    by = {_key(r.get("name")): r for r in records or []}
    n = 0
    for cid, tm in term_map.items():
        rec = by.get(_key(cid))
        if not rec or not rec.get("fill"):
            continue
        want = _key(tm.get("fill_type") or "")
        grp = next((g for g in rec["fill"] if not want or _key(g.get("type")) == want),
                   rec["fill"][0])
        s_terms = tm.get("s_terms") or []
        d_terms = tm.get("d_terms") or []
        stag = tm.get("s_tag", "")
        dtag = tm.get("d_tag", "")
        k = max(len(s_terms), len(d_terms), 1)
        wires = []
        for i in range(min(k, 4)):
            wires.append({"src": ("", stag, s_terms[i] if i < len(s_terms) else ""),
                          "dst": ("", dtag, d_terms[i] if i < len(d_terms) else "")})
        if wires:
            rec["wires"] = wires
            grp["slots"] = len(wires)
            rec.setdefault("flags", []).append("edc_terms")
            n += 1
    return n


# ── vision (optional) ───────────────────────────────────────────────────────
_MODEL = "claude-opus-4-8"
_API_URL = "https://api.anthropic.com/v1/messages"
VISION_PROMPT = (
    "These are AIC EDC wiring drawings (three-line / terminal / PLC I/O / analog "
    "input). For each conduit you can identify, return the terminal landings as "
    "JSON keyed by conduit tag: {\"<conduit>\": {\"s_tag\":\"\",\"s_terms\":[],"
    "\"d_tag\":\"\",\"d_terms\":[],\"fill_type\":\"POWER|CONTROL|TSP|MFG_CABLE\"}}. "
    "Rules: keep power phases as ØA/ØB/ØC (+N/GND) on BOTH ends — never relabel to "
    "terminal codes; PLC points use the channel address (e.g. 0.2.01.05); analog "
    "loops use the AI channel + loop tag. Output ONLY the JSON."
)


def transcribe_via_api(image_paths, model=_MODEL, timeout=180):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not image_paths:
        return None
    import base64, urllib.request
    content = [{"type": "text", "text": VISION_PROMPT}]
    for p in image_paths[:20]:          # cap payload
        try:
            with open(p, "rb") as fh:
                content.append({"type": "image", "source": {"type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(fh.read()).decode()}})
        except OSError:
            continue
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
        return json.loads(txt[s:e + 1]) if s >= 0 and e > s else None
    except Exception:
        return None


def build_edc_packet(image_paths, out_dir, project=""):
    if not image_paths:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "ASK_CLAUDE_EDC.md")
    lines = [f"# IDP Extractor — EDC terminal transcription{(' (' + project + ')') if project else ''}",
             "", "The conduit terminals live on the AIC EDC wiring sheets below (vector "
             "drawings, no text layer). Open them, then reply with the terminal JSON "
             "described in `idp_edc.VISION_PROMPT`. Rendered sheets:", ""]
    lines += [f"- `{p}`" for p in image_paths]
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
        pg = find_edc_sheets(pdf)
        print(f"{os.path.basename(pdf)}: {len(pg)} EDC drawing sheet(s): "
              f"{[p + 1 for p in pg][:30]}")
