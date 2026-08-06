"""
idp_wiring.py — PROTOTYPE wiring-diagram term extractor for AIC EDC PLC I/O
schematics (e.g. 73.1163_EDC_PLC_Panel_*.pdf).

These pages carry the terminal-level detail that conduit schedules / lists lack.
Layout (in PDF text space the page is rotated 90°, so each I/O point is a
CONSTANT-X column): device description → PLC tag (Y####) → module terminal →
wire address (d.d.dd.dd) → interposing relay (IRn) → TB terminals (00A/00B) →
field device description.

extract_bindings(pdf, pages=None) clusters words into per-point columns and
returns one binding dict per real (non-spare) I/O point:
  {page, plc_tag, plc_term, wire_addr, relay, tb_terminals[], source_desc, field_desc}

This is tuned to the PLC-I/O schematic style; other EDC sheet styles need their
own profile. It reads the text layer only (no OCR).
"""
from __future__ import annotations

import re
import fitz

TAG_RE = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]?$")   # PLC point tag: Y1001B, Y10A, L1001, SI1001B, P5001, F5001
WIRE_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+[+-]?$")   # wire address 0.0.02.00 / 0.0.03.00+
RELAY_RE = re.compile(r"^(IR|CR|R)\d+$")            # interposing relay
TB_RE = re.compile(r"^\d{2}[AB]$")                  # digital TB terminal 00A/00B
PAIR_RE = re.compile(r"^\d{2}[+-]$")                # analog signal-pair terminal 00+/00-
CHAN_RE = re.compile(r"^(II|VI|IQ|VQ|COM)\d*$")     # analog channel terminal II0/COM0/VI0
INST_RE = re.compile(r"^(LIT|LE|FIT|FE|FT|PIT|PT|PSH|PSL|LSH|LSL|FSH|FSL|"
                     r"TSH|TIT|TT|AIT|SI|ZS|ZSH|ZSL|LS|FS|PS|TS|CIT|AE)$")
INT_RE = re.compile(r"^\d{1,3}$")
KEYWORDS = {"OUTPUT", "INPUT", "SPARE", "NC", "NO", "PLC", "FIELD", "SUBPANEL",
            "DROP", "RACK", "SLOT", "DO", "DI", "MODICON", "MODULE", "CHANNEL",
            "REV", "DATE", "NAME", "CHANGES", "TB-DO", "TB-DI", "TB-AI", "SHLD",
            "CBDC5", "CBDC6", "24N", "FROM", "LEQ"}


def _cx(w):
    return (w[0] + w[2]) / 2


def _cluster_columns(words):
    """Group words into constant-X columns (one per I/O point).

    Adjacent I/O columns nearly touch, so gap-based clustering fails. Instead
    anchor on the regularly-spaced OUTPUT/INPUT labels (one per point), derive
    the column pitch from their spacing, and bin every word to its nearest
    anchor (±half-pitch)."""
    anchors = sorted(_cx(w) for w in words if w[4].strip().upper() in ("OUTPUT", "INPUT"))
    # dedupe anchors closer than a plausible half-pitch
    dedup = []
    for a in anchors:
        if not dedup or a - dedup[-1] > 12:
            dedup.append(a)
    if len(dedup) < 2:
        return []
    diffs = [b - a for a, b in zip(dedup, dedup[1:])]
    diffs.sort()
    pitch = diffs[len(diffs) // 2] or 36.0
    half = pitch * 0.5
    return [[w for w in words if abs(_cx(w) - ax) <= half] for ax in dedup]


def _phrases(band):
    """Join alpha words into device-name phrases by Y proximity."""
    def _norm(s):
        return re.sub(r"[^A-Z0-9]", "", s.upper())
    alpha = [w for w in band if any(ch.isalpha() for ch in w[4])
             and _norm(w[4]) not in KEYWORDS and not TAG_RE.match(w[4])
             and not RELAY_RE.match(w[4]) and not TB_RE.match(w[4])]
    alpha.sort(key=lambda w: w[1])
    def _join(run):
        # page text is rotated 90°: an image text-line runs along descending X,
        # and successive lines step down in Y. Read by X-column (high→low) then Y.
        ordered = sorted(run, key=lambda w: (-round(w[0] / 6), w[1]))
        return " ".join(w[4] for w in ordered)

    out, run, last_y = [], [], None
    for w in alpha:
        if last_y is not None and w[1] - last_y > 22:
            if run:
                out.append((run[0][1], _join(run)))
            run = []
        run.append(w)
        last_y = w[1]
    if run:
        out.append((run[0][1], _join(run)))
    return out   # [(y, phrase), ...] top → bottom


def _binding_for_column(band, page_no):
    ws = sorted(band, key=lambda w: w[1])
    # anchor: the I/O point's INPUT/OUTPUT label
    anchor_y = next((w[1] for w in ws if w[4].strip().upper() in ("INPUT", "OUTPUT")), None)
    if anchor_y is None:
        return None
    # PLC point tag = first tag-like token at/after the anchor (skips fuses above it)
    tag = ""
    for w in ws:
        if w[1] >= anchor_y - 2 and TAG_RE.match(w[4]) \
                and not RELAY_RE.match(w[4]) and not TB_RE.match(w[4]) \
                and not CHAN_RE.match(w[4]) and w[4].upper() not in KEYWORDS:
            tag = w[4]
            break
    if not tag:
        return None                      # spare / not a real I/O point
    wire = next((w[4] for w in ws if WIRE_RE.match(w[4])), "")
    relay = next((w[4] for w in ws if RELAY_RE.match(w[4])), "")
    tbs, pairs, chans = [], [], []
    for w in ws:
        for pat, bucket in ((TB_RE, tbs), (PAIR_RE, pairs), (CHAN_RE, chans)):
            if pat.match(w[4]) and w[4] not in bucket:
                bucket.append(w[4])
    shield = any(w[4].upper() == "SHLD" for w in ws)
    field_inst = next((w[4] for w in ws if INST_RE.match(w[4])), "")
    # analog (signal pair / shield) vs discrete
    kind = "TSP" if (shield or pairs) else "CONTROL"
    terminals = tbs or pairs or chans
    phr = _phrases(band)
    source_desc = phr[0][1] if phr else ""
    field_desc = phr[-1][1] if len(phr) > 1 else source_desc
    return {"page": page_no, "plc_tag": tag, "kind": kind, "wire_addr": wire,
            "relay": relay, "terminals": terminals, "shield": shield,
            "field_inst": field_inst, "source_desc": source_desc, "field_desc": field_desc}


SHEET_RE = re.compile(r"(DIGITAL|ANALOG)\s+(INPUT|OUTPUT)", re.I)


def _is_io_sheet(page):
    return bool(SHEET_RE.search(page.get_text()))


def extract_bindings(pdf, pages=None):
    """Return term-level bindings for the PLC-I/O sheets of an EDC PDF.
    When pages is None, auto-selects only DIGITAL/ANALOG INPUT/OUTPUT sheets."""
    d = fitz.open(pdf)
    out = []
    try:
        rng = pages if pages is not None else range(d.page_count)
        auto = pages is None
        for i in rng:
            if i < 0 or i >= d.page_count:
                continue
            if auto and not _is_io_sheet(d[i]):
                continue
            for band in _cluster_columns(d[i].get_text("words")):
                b = _binding_for_column(band, i + 1)
                if b:
                    out.append(b)
    finally:
        d.close()   # never leak the document handle, even on a malformed page
    return _dedup(out)


def _dedup(binds):
    """Collapse repeats of the same (page, tag); keep the richest (most terms)."""
    best = {}
    for b in binds:
        k = (b["page"], b["plc_tag"])
        cur = best.get(k)
        if cur is None or (len(b["terminals"]) + bool(b["wire_addr"])) > \
                (len(cur["terminals"]) + bool(cur["wire_addr"])):
            best[k] = b
    return list(best.values())


if __name__ == "__main__":
    import sys
    pdf = sys.argv[1]
    pages = [int(x) - 1 for x in sys.argv[2:]] if len(sys.argv) > 2 else None
    binds = extract_bindings(pdf, pages)
    print(f"{len(binds)} I/O bindings")
    for b in binds:
        print(f"  pg{b['page']:>3} tag={b['plc_tag']:<8} {b['kind']:<7} "
              f"term={','.join(b['terminals']):<12} inst={b['field_inst']:<4} "
              f"relay={b['relay']:<5} wire={b['wire_addr']:<12} "
              f"desc={b['field_desc']!r}")
