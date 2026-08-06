"""
idp_terms.py — OFFLINE terminal-landing generators for the FillIndex.

Two deterministic passes that need no drawing vision (they work off the records
+ the conduit-schedule notes), productized from the Crows Landing runner work:

  apply_panelboard_circuits(records, panelboard=None)
      120/208V BRANCH circuits fed from a panelboard: the schedule note carries
      the breaker/circuit list (e.g. "L1-2,4,6"); set the panel-side S/D Term to
      those circuit numbers, tag = the panel. If a parsed `panelboard` map
      {ckt: {"breaker","desc"}} is supplied (from idp_panelboard), also set the
      breaker amps as the rating.

  apply_phase_terms(records)
      3-phase POWER FEEDERS land on phases; set both ends to ØA/ØB/ØC (+ GND/N),
      tags = the equipment. Phases are NEVER relabeled. Skips branch circuits
      (they get circuit-number terms above) and anything already termed.

Both are wired into idp_write.write_workbook so every write gets them; they only
POPULATE terms, never change fill type/count/color.
"""
from __future__ import annotations

import os
import re
import sys
import json

_PHASE3 = ["ØA", "ØB", "ØC"]

# ── device TAG1 per symbol block (what the FILLWIRELABEL tool reads for the label's
# middle field) ─────────────────────────────────────────────────────────────────
_DEV_TAG_CACHE = None


def _load_device_tags():
    """symbol block name -> its baked device TAG1 (device_default), from the symbol
    library catalog. This is exactly the value FILLWIRELABEL reads off the placed
    block for the label. Cached; empty dict if the catalog isn't reachable."""
    global _DEV_TAG_CACHE
    if _DEV_TAG_CACHE is not None:
        return _DEV_TAG_CACHE
    _DEV_TAG_CACHE = {}
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "symbol_library_catalog.json"),
              os.path.join(getattr(sys, "_MEIPASS", here), "symbol_library_catalog.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cat = json.load(f)
            _DEV_TAG_CACHE = {k: (v.get("device_default") or "")
                              for k, v in cat.items() if isinstance(v, dict)}
            break
        except Exception:
            continue
    return _DEV_TAG_CACHE


_NO_DEV_TAG = {"TB", "PULLBOX", "SPARE", "PULLROPE", "SHLD"}


def device_tag_for_symbol(sym):
    """The device TAG1 the FILLWIRELABEL tool prints in the label's middle field —
    the symbol's baked device designator (XFMR, CB, MTR, DISC, GND, …). Looks it up
    in the symbol-library catalog; falls back to the block name's leading token when
    the catalog isn't bundled. Landing/terminal-block families carry no device tag."""
    if not sym:
        return ""
    name = str(sym).strip()
    cat = _load_device_tags()
    if name in cat:
        return cat[name] or ""
    base = re.sub(r"_(L|R)$", "", name)
    tok = re.split(r"[-_]", base)[0]
    return "" if tok.upper() in _NO_DEV_TAG else tok
# panel-circuit reference in a note: "L1-2,4,6", "LP-1,3,5", "P1-39-41"
_CKT_RE = re.compile(r"\b([A-Z]{1,3}\d?)[-\s](\d(?:[\d,\s\-]*\d)?)")
_PANEL_RE = re.compile(r"PANEL|PANELBOARD|\bLP-|\bPP-|\bPNL", re.I)


def _key(t):
    return str(t or "").replace("-", "").upper()


def _has_terms(rec):
    """True if the conduit already carries REAL terminal designations (from the
    EDC or a panelboard pass) — empty-term placeholder wires don't count."""
    for w in rec.get("wires") or []:
        if (w.get("src") or ("", "", ""))[2] or (w.get("dst") or ("", "", ""))[2]:
            return True
    return False


def _strip_cable_list(note):
    """Remove a folded-in CABLES-IN-CONDUIT list (from the conduit schedule) so its
    cable IDs (`C-021`) aren't misread as panel circuit numbers. Drops a leading
    'CABLES: …' segment and any standalone C-### cable token."""
    s = str(note or "")
    s = re.sub(r"(?i)\bCABLES?\s*:.*?(?:\s{2,}|$)", " ", s)   # 'CABLES: C-001 C-002  '
    s = re.sub(r"(?i)\bC-?\d{2,4}[A-Z]?\b", " ", s)           # any stray cable id
    return s


def parse_panel_circuits(note):
    """('L1', [2,4,6]) from a note, or (None, []). Cable-schedule cable IDs folded into
    the note are stripped first so 'C-021' is never mistaken for circuit 21."""
    m = _CKT_RE.search(_strip_cable_list(note))
    if not m:
        return None, []
    out = []
    for part in re.split(r"[,\s]+", m.group(2)):
        part = part.strip()
        if "-" in part:
            a, b = (part.split("-") + [""])[:2]
            if a.isdigit() and b.isdigit():
                out += [a, b]
        elif part.isdigit():
            out.append(part)
    return m.group(1), [int(c) for c in out]


_LOAD_STOP = {"THE", "AND", "FOR", "PANEL", "PANELBOARD", "TO", "OF", "A", "AN",
              "SKID", "BUILDING", "NEW", "WITH", "SYSTEM"}


def _match_load_to_ckts(load_name, panelboard):
    """Derive circuit number(s) for a branch by matching the conduit's LOAD name to
    the panelboard schedule's circuit descriptions (offline, when the schedule was
    text-parseable). Returns [ckt, …] best-match first, or []. This is how a curated
    build assigns e.g. 'HVAC-1' → CKT 26 without a note — done from the real schedule
    rather than a hand-coded map."""
    toks = {w for w in re.findall(r"[A-Z]{3,}", str(load_name or "").upper())
            if w not in _LOAD_STOP}
    if not toks or not panelboard:
        return []
    scored = []
    for ckt, info in panelboard.items():
        dtoks = {w for w in re.findall(r"[A-Z]{3,}", str(info.get("desc") or "").upper())
                 if w not in _LOAD_STOP}
        ov = len(toks & dtoks)
        if ov:
            scored.append((ov, ckt))
    if not scored:
        return []
    scored.sort(reverse=True)
    top = scored[0][0]
    return [c for ov, c in scored if ov == top]


def apply_panelboard_circuits(records, panelboard=None):
    """Set panel-side terminals (breaker/circuit numbers) for branch circuits.
    Offline: uses the schedule note. Returns count populated."""
    n = 0
    for rec in records or []:
        if _has_terms(rec):
            continue
        _, cks = parse_panel_circuits(rec.get("deviations", ""))
        src_names = " ".join(rec.get("source") or [])
        dst_names = " ".join(rec.get("dest") or [])
        panel_is_src = bool(_PANEL_RE.search(src_names))
        panel_is_dst = bool(_PANEL_RE.search(dst_names))
        if not (panel_is_src or panel_is_dst):
            continue
        # no explicit circuit in the note → derive it by matching the LOAD name to
        # the parsed panelboard schedule's descriptions (offline, text-layer panels).
        derived = False
        if not cks and panelboard:
            load = " ".join((rec.get("dest") if panel_is_src else rec.get("source")) or [])
            cks = _match_load_to_ckts(load, panelboard)
            derived = bool(cks)
        if not cks:
            continue
        ptag = (rec.get("source") if panel_is_src else rec.get("dest")) or [""]
        otag = (rec.get("dest") if panel_is_src else rec.get("source")) or [""]
        ptag, otag = (ptag[0] if ptag else ""), (otag[0] if otag else "")
        powg = next((g for g in rec.get("fill", []) if _key(g.get("type")) == "POWER"), None)
        if not powg:
            continue
        wires = []
        for c in cks[:4]:
            panel_end = ("", "", str(c))       # tag BLANK; circuit# is the term
            other_end = ("", "", "")
            wires.append({"src": panel_end, "dst": other_end} if panel_is_src
                         else {"src": other_end, "dst": panel_end})
        rec["wires"] = wires
        powg["slots"] = min(len(cks), 4)
        # equipment names belong in the DESCRIPTION columns, not the Tag columns
        if ptag:
            (powg.__setitem__("s_desc", [ptag]) if panel_is_src
             else powg.__setitem__("d_desc", [ptag]))
        if otag:
            (powg.__setitem__("d_desc", [otag]) if panel_is_src
             else powg.__setitem__("s_desc", [otag]))
        if panelboard:
            amps = sorted({str(panelboard[c].get("breaker", "")).split("/")[0]
                           for c in cks if c in panelboard and panelboard[c].get("breaker")})
            if amps:
                powg["s_rating" if panel_is_src else "d_rating"] = amps[0] + "A"
        rec.setdefault("flags", []).append(
            "panelboard_circuits_by_load(verify ckt#)" if derived else "panelboard_circuits")
        if len(cks) > 4:
            rec.setdefault("flags", []).append(
                f"panelboard: {len(cks)} circuits {cks}, only 4 term slots shown")
        n += 1
    return n


def _is_ground_group(g):
    """True when a fill GROUP is the ground conductor, by any reliable signal:
    an explicit is_ground/auto_ground flag, a GND_L/GND_R symbol, or a single
    green (GRN) POWER conductor. `ensure_ground` tags its synthesized ground with
    `auto_ground` (not `is_ground`), so keying only off `is_ground` mislabeled it
    as a phase (ØA) and let the device-tag pass stamp CB-MAIN/CB-GEN on it."""
    if g.get("is_ground") or g.get("auto_ground"):
        return True
    for k in ("s_symbol", "d_symbol"):
        if str(g.get(k) or "").strip().upper().startswith("GND"):
            return True
    cols = [str(c).strip().upper() for c in (g.get("colors") or [])]
    ct = int(g.get("wire_ct") or g.get("count") or g.get("slots") or 1)
    return ct == 1 and cols == ["GRN"]


def _terms_for_group(g, slots):
    """Convention terminal designations for one fill group (learned from the
    finished IDPs): ground → blank term (GND is carried as the TAG, which is how
    LISA classifies a conductor as GROUND); 3-phase POWER → ØA/ØB/ØC (+N or GND on
    the 4th by wire colour); 2-wire POWER → ØA/ØB; signal/cable → blank (EDC fills
    those). Phases are NEVER relabeled to terminal codes."""
    if _is_ground_group(g):
        return [""]
    k = _key(g.get("type"))
    if k == "POWER":
        # single-phase branch (integral ground): L1 / N, the green conductor becomes
        # the ground (GND tag via its GRN colour in apply_source_info). Not ØA/ØB/ØC.
        if g.get("single_phase"):
            return ["L1", "N"][:slots]
        cols = g.get("colors") or []
        ct = g.get("wire_ct") or g.get("count") or slots
        if ct >= 3:
            terms = list(_PHASE3)
            if slots >= 4:
                terms.append("GND" if (len(cols) >= 4 and str(cols[3]).upper() == "GRN") else "N")
            return terms[:slots]
        if ct == 2:
            return ["ØA", "ØB"][:slots]
        return ["ØA"][:slots]
    return []                                    # CONTROL/TSP/cable → EDC/drawing


def apply_source_info(records):
    """Populate FillIndex SOURCE/DEST info the finished IDPs always show:
      • device DESCRIPTIONS on every fill group (the equipment names, both ends)
      • terminal designations per group — phases on feeders, GND on the ground
        conductor, and (via apply_panelboard_circuits) circuit numbers on branches
    Runs after the EDC pass, so conduits that already carry real EDC terminals keep
    them; this only fills what's still blank. Returns count of conduits touched."""
    n = 0
    for rec in records or []:
        src = (rec.get("source") or [""])[0]
        dst = (rec.get("dest") or [""])[0]
        fill = rec.get("fill") or []
        if not fill:
            continue
        # descriptions on EVERY group (both ends), wherever still blank
        for g in fill:
            if src and not g.get("s_desc"):
                g["s_desc"] = [src]
            if dst and not g.get("d_desc"):
                g["d_desc"] = [dst]
        # keep real EDC / panelboard terminals; only synthesize when none exist
        if _has_terms(rec):
            n += 1
            continue
        wires, any_term = [], False
        for g in fill:
            slots = int(g.get("slots") or g.get("wire_ct") or g.get("count") or 1)
            slots = max(1, min(slots, 4))
            g["slots"] = slots
            terms = _terms_for_group(g, slots)
            grp_gnd = _is_ground_group(g)
            cols = [str(c).strip().upper() for c in (g.get("colors") or [])]
            for i in range(slots):
                t = terms[i] if i < len(terms) else ""
                # A ground conductor — the whole ground group, a slot resolved to
                # the GND term, or a green (GRN) conductor — carries "GND" as its
                # TAG (blank term). LISA classifies a fill as GROUND only when the
                # S/D Tag == "GND"; the blank term also keeps the device-tag pass
                # (apply_power_terminals) from stamping CB-MAIN/CB-GEN on it.
                is_gnd_wire = grp_gnd or str(t).strip().upper() == "GND" \
                    or (i < len(cols) and cols[i] == "GRN")
                if is_gnd_wire:
                    tag, term = "GND", ""
                else:
                    tag, term = "", t
                wires.append({"src": ("", tag, term), "dst": ("", tag, term)})
                any_term = any_term or bool(tag) or bool(term)
        if any_term:
            rec["wires"] = wires
            n += 1
    return n


# backward-compatible alias (older callers)
def apply_phase_terms(records):
    return apply_source_info(records)


def apply_supporting_docs(records, schedule_doc=None, edc_by_kind=None):
    """Populate each conduit's SUPPORTING DOCUMENTS (the Ref Documents table LISA
    draws on every IDP sheet). Every conduit references the design plans' conduit
    schedule / single-line; power feeders also cite the AIC THREE-LINE, control/
    analog the PLC I/O & control wiring, branch circuits the PANELBOARD SCHEDULE.
    Pass `schedule_doc=(dwg,desc,manu)` and `edc_by_kind={'feeder':.., 'control':..,
    'branch':..}` when the real drawing numbers are known (from the plans title
    block + EDC index); defaults describe the doc with a blank number to flag.
    Does not overwrite docs a richer source already set. Returns count."""
    sched = schedule_doc or ("", "CONDUIT SCHEDULE AND SINGLE LINE DIAGRAM", "")
    edc = edc_by_kind or {
        "feeder": ("", "THREE-LINE DIAGRAM", "AIC"),
        "branch": ("", "PANELBOARD SCHEDULE", "AIC"),
        "control": ("", "PLC I/O & CONTROL WIRING DIAGRAM", "AIC"),
        "analog": ("", "PLC I/O - ANALOG INPUT", "AIC"),
        "network": ("", "PLC NETWORK LAYOUT", "AIC"),
        "fiber": ("", "FIBER OPTIC LAYOUT", "AIC"),
    }
    n = 0
    for rec in records or []:
        if rec.get("docs"):
            continue
        name = str(rec.get("name") or "").upper()
        _, cks = parse_panel_circuits(rec.get("deviations", ""))
        # Select the AIC EDC drawing(s) by the conduit's actual FILL TYPES — works for
        # any conduit-tag scheme (P/C/A, K-###, …), not just the AIC prefix convention.
        ftypes = {str(g.get("type") or "").upper() for g in (rec.get("fill") or [])}
        docs = [sched]
        if "POWER" in ftypes:
            docs.append(edc["branch"] if cks else edc["feeder"])
        if "TSP" in ftypes:
            docs.append(edc["analog"])
        if "CONTROL" in ftypes:
            docs.append(edc["control"])
        if "CAT-6" in ftypes or "CAT6" in ftypes:
            docs.append(edc["network"])
        if "FIBER" in ftypes:
            docs.append(edc["fiber"])
        if len(docs) == 1:                       # empty fill → fall back to tag prefix
            if name[:1] in ("C", "A"):
                docs.append(edc["control"])
            elif name[:1] in ("P", "H", "L", "X"):
                docs.append(edc["feeder"])
        rec["docs"] = [d for d in docs if any(str(x).strip() for x in d)]
        if rec["docs"]:
            n += 1
    return n


_ATS_RE = re.compile(r"\bATS\b|AUTOMATIC TRANSFER|TRANSFER SWITCH", re.I)
_GEN_RE = re.compile(r"\bGEN(ERATOR)?\b|ENGINE GEN|\bEG\d*\b", re.I)


def _ats_term(t, pfx):
    """Rewrite a phase term for an ATS terminal: ØA→<pfx>A, N→<pfx>N; GND stays."""
    t = str(t or "")
    m = re.match(r"^Ø([ABC])$", t)
    if m:
        return pfx + m.group(1)
    if t.upper() == "N":
        return pfx + "N"
    return t                                          # GND / blank unchanged


def apply_power_terminals(records):
    """Device-specific POWER terminals learned from the finished IDPs — the piece
    the EDC three-line carries that a conduit schedule doesn't. Today: the ATS,
    whose terminals are prefixed by connection ROLE — Normal (from the utility/MSB
    source), Emergency (to/from the generator), or Load (to the downstream MCC/
    panel). So MSB→ATS lands on ATS `NA/NB/NC/NN`, ATS→generator on `EA/EB/EC/EN`,
    ATS→MCC on `LA/LB/LC/LN` — matching 73.1188 H2202/H2203/H2204. Only the ATS
    end is rewritten; the other end keeps its phase terms. Returns count."""
    _MSBMAIN_RE = re.compile(r"\bMSB\b|MAIN SWITCH ?BOARD|SWITCHBOARD", re.I)
    n = 0
    for rec in records or []:
        wires = rec.get("wires") or []
        if not wires:
            continue
        # only real power feeders (phase terms present)
        if not any(re.match(r"^Ø[ABC]$", str((w.get("src") or ('','',''))[2]))
                   or re.match(r"^Ø[ABC]$", str((w.get("dst") or ('','',''))[2])) for w in wires):
            continue
        src = (rec.get("source") or [""])[0]
        dst = (rec.get("dest") or [""])[0]
        s_ats, d_ats = bool(_ATS_RE.search(src)), bool(_ATS_RE.search(dst))
        touched = False

        # 1) ATS terminals by connection role (Normal/Emergency/Load)
        if s_ats != d_ats:
            other = dst if s_ats else src
            pfx = "E" if _GEN_RE.search(other) else ("L" if s_ats else "N")
            side = "src" if s_ats else "dst"
            for w in wires:
                cur = w.get(side) or ("", "", "")
                nt = _ats_term(cur[2], pfx)
                if nt != cur[2]:
                    w[side] = (cur[0], cur[1], nt); touched = True
            if touched:
                rec.setdefault("flags", []).append(f"ATS_terminals_{pfx}")

        # 2) device tags on the symbol: MSB main → CB-MAIN, generator → CB-GEN
        def _set_tag(side, tag):
            hit = False
            for w in wires:
                cur = w.get(side) or ("", "", "")
                if re.match(r"^Ø[ABC]$|^[NEL][ABC]$", str(cur[2])) and cur[1] != tag:
                    w[side] = (cur[0], tag, cur[2]); hit = True
            return hit
        if _MSBMAIN_RE.search(src) and not s_ats:
            touched |= _set_tag("src", "CB-MAIN")
        if _MSBMAIN_RE.search(dst) and not d_ats:
            touched |= _set_tag("dst", "CB-MAIN")
        if _GEN_RE.search(src):
            touched |= _set_tag("src", "CB-GEN")
        if _GEN_RE.search(dst):
            touched |= _set_tag("dst", "CB-GEN")
        if touched:
            n += 1
    return n
