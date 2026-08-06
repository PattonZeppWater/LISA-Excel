"""
idp_edc_symbols.py — read the actual DEVICE BLOCKS off the EDC diagrams and confirm each
conduit's S/D Symbol against the known block library (Phase 1b / FillIndex).

The IDP symbol blocks are named by their COMPONENT SEQUENCE (e.g. `CB-CB-TB_Square`,
`DISC_Fuse-TB_Round`, `Fuse-TB_Square-Shld`). The EDC drawings draw exactly that sequence
at each conduit landing and label the components in the text layer (CIRCUIT BREAKER, KNIFE-
DISCONNECT TERMINAL, FUSE, FEED-THROUGH TERMINAL, SHIELD, …). So we can CONFIRM a symbol by
reading the component tokens near a conduit's device tag on the EDC page, assembling the
ordered sequence, and matching it to a library block — instead of guessing from the
equipment name (which wrongly turns a "VFD(PERMEATE PUMP)" label into a VFD symbol).

Conservative by design: a symbol is set ONLY when the tag is found on an EDC page AND the
adjacent component sequence maps to a real library block. Otherwise nothing is written and
the downstream gate leaves the cell blank. Offline: text layer only, no vision, no API.
"""
import os
import re

# The SYMBOL at a conduit LANDING is the terminal-block structure drawn there — breakers,
# disconnects, fuses, feed-through/shielded terminal blocks — NOT the equipment the conduit
# runs to. So we read ONLY those landing components and assemble them into a library block.
# Equipment labels (VFD, MOTOR, TRANSFORMER) are deliberately NOT read as landing symbols:
# a conduit to a "VFD(PERMEATE PUMP)" lands on a terminal block, it is not itself a VFD block.
_COMPONENT_PATTERNS = [
    (re.compile(r"KNIFE[\s-]*DISCONNECT|DISCONNECT\s*SWITCH|\bDISCONNECT\b"), "DISC"),
    (re.compile(r"CIRCUIT\s*BREAKER|\bBREAKER\b"), "CB"),
    (re.compile(r"\bFUSE\b|FUSED\b"), "Fuse"),
    (re.compile(r"FEED[\s-]*THROUGH|TERMINAL\s*BLOCK|FEED[\s-]*THRU"), "TB_Square"),
    (re.compile(r"SHIELD(ED)?\s*TERMINAL|GROUND(ED)?\s*SHIELD|\bSHLD\b"), "Shld"),
    # equipment tokens intentionally excluded — surge/relay/contactor/VFD/motor/xfmr are not
    # landing blocks; a landing with none of the above terminal components stays blank.
    (re.compile(r"SURGE\s*PROTECT|\bSPD\b|\bRELAY\b|CONTACTOR|VARIABLE\s*FREQUENCY|"
                r"\bVFD\b|\bMOTOR\b|MOTOR\s*STARTER|TRANSFORMER|\bXFMR\b"), None),
]

_STOP_WORDS = re.compile(r"BILL\s*OF\s*MATERIAL|COVER\s*LETTER|REVIEW\s*COMMENT")


def _library_bases_by_side(library):
    """{'L': set(base_names), 'R': set(...)} present in the block library."""
    out = {"L": set(), "R": set()}
    for base, sides in (library or {}).items():
        for s in ("L", "R"):
            if sides.get(s, 0) > 0:
                out[s].add(base)
    return out


def _match_library_base(tokens, side, bases):
    """Assemble the ordered component tokens into a block base and match it to the library.
    STRICT: only an EXACT ordered chain or an exact token-multiset match counts — no loose
    "closest / superset" containment, which produced unstable, unverifiable confirmations
    (a nearby stray "BREAKER" note could over-match). Returns the library base or None
    (=> leave the symbol blank, which the confidence gate keeps blank)."""
    tokens = [t for t in tokens if t]
    if not tokens:
        return None
    chain = "-".join(tokens)
    if chain in bases[side]:
        return chain
    # order-insensitive: a library base whose token multiset equals ours exactly
    want = sorted(tokens)
    for base in bases[side]:
        if sorted(base.split("-")) == want:
            return base
    return None


def _components_near(page, tag, window=140):
    """Ordered landing-component tokens near `tag` on a PRE-INDEXED page (text, words)."""
    if page is None:
        return []
    text, words = page
    tU = tag.upper()
    if tU not in text:
        return []
    tnorm = re.sub(r"\s+", "", tU)
    anchor = None
    for w in words:
        wu = w[4].upper()
        if re.sub(r"\s+", "", wu) == tnorm or tU in wu:
            anchor = w
            break
    if anchor is None:
        return []
    ay = (anchor[1] + anchor[3]) / 2
    band = [w for w in words if abs((w[1] + w[3]) / 2 - ay) <= window]
    band_text = " ".join(w[4] for w in sorted(band, key=lambda w: w[0])).upper()
    seq = []
    for pat, tok in _COMPONENT_PATTERNS:
        if tok is None:
            continue
        for m in pat.finditer(band_text):
            seq.append((m.start(), tok))
    seq.sort()
    ordered = []
    for _, tok in seq:
        if not ordered or ordered[-1] != tok:
            ordered.append(tok)
    return ordered[:4]   # library blocks chain at most ~4 components


def read_symbols_from_edc(records, edc_pdfs, library, log=lambda *a: None):
    """For each conduit end, read the component sequence at its device tag off the EDC
    drawings and CONFIRM the S/D Symbol against the block library. Sets s_symbol/d_symbol
    (+confidence) only on a real library match; leaves everything else untouched (blank).
    Returns the number of symbols confirmed."""
    try:
        import fitz
    except Exception:
        return 0
    if not library or not edc_pdfs:
        return 0
    bases = _library_bases_by_side(library)
    # Only the conduit end-tags we actually need to resolve — used to gate which pages we
    # bother reading. (Cheap text check first; expensive word-position extraction ONLY for
    # pages that actually mention a tag, so a big EDC set isn't fully parsed every scan.)
    needed = set()
    for rec in records or []:
        for nm in (list(rec.get("source") or []) + list(rec.get("dest") or [])):
            if nm:
                needed.add(str(nm).upper())
    if not needed:
        return 0
    indexed = []                                   # (text, words) for RELEVANT pages only
    for p in edc_pdfs:
        if not str(p).lower().endswith(".pdf"):
            continue
        try:
            d = fitz.open(p)
        except Exception:
            continue
        try:
            for pi in range(d.page_count):
                try:
                    text = d[pi].get_text("text").upper()
                except Exception:
                    continue
                if _STOP_WORDS.search(text) or not any(t in text for t in needed):
                    continue
                try:
                    words = sorted(d[pi].get_text("words"),
                                   key=lambda w: (round(w[1] / 12), w[0]))
                except Exception:
                    words = []
                indexed.append((text, words))
        finally:
            d.close()
    if not indexed:
        return 0
    confirmed = 0
    for rec in records or []:
        src = [s for s in (rec.get("source") or []) if s]
        dst = [s for s in (rec.get("dest") or []) if s]
        for side, names, symk, confk in (("L", src, "s_symbol", "s_symbol_conf"),
                                         ("R", dst, "d_symbol", "d_symbol_conf")):
            tag = names[0] if names else ""
            if not tag:
                continue
            found = None
            for page in indexed:
                toks = _components_near(page, tag)
                if toks:
                    cand = _match_library_base(toks, side, bases)
                    if cand:
                        found = f"{cand}_{side}"
                        break
            if not found:
                continue
            for g in rec.get("fill", []) or []:
                if g.get("is_ground"):
                    continue
                if g.get(symk) != found:
                    g[symk] = found
                    g[confk] = max(float(g.get(confk) or 0), 0.75)
                    g[symk + "_note"] = (found + " (read from EDC diagram blocks, "
                                         "matched to the library)")
                    confirmed += 1
    if confirmed:
        log(f"EDC blocks: confirmed {confirmed} S/D symbol(s) by reading the diagram and "
            "matching the component sequence to the library.")
    return confirmed


# ── VISION path: read GRAPHICAL blocks off the rendered EDC sheets via Claude ────────────
_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-5"


def _needs_symbol(rec):
    """A conduit still missing a confident device symbol on a non-ground group."""
    for g in rec.get("fill", []) or []:
        if g.get("is_ground"):
            continue
        for sk, ck in (("s_symbol", "s_symbol_conf"), ("d_symbol", "d_symbol_conf")):
            if not g.get(sk) or g.get(ck) is None:
                return True
    return False


def _render_pages(page_refs, out_dir, dpi=150, cap=18):
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
                out = os.path.join(out_dir, f"sym_{os.path.basename(path)[:20]}_p{pi + 1}.png")
                try:
                    d[pi].get_pixmap(dpi=dpi).save(out)
                    imgs.append(out)
                except Exception:
                    continue
        finally:
            d.close()
    return imgs


def _vision_symbol_call(image_paths, prompt, timeout=180):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not image_paths:
        return None
    import base64, json, urllib.request
    content = [{"type": "text", "text": prompt}]
    for p in image_paths[:18]:
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


def confirm_symbols_via_vision(records, edc_pdfs, library, log=lambda *a: None, out_dir=None):
    """Read the GRAPHICAL device blocks off the rendered EDC sheets via Claude vision and
    confirm each conduit's S/D Symbol against the library. Opt-in: requires the Vision-assist
    checkbox AND an ANTHROPIC_API_KEY (no key → no-op, offline scan is unaffected). Only fills
    conduits still missing a confident symbol; validates every returned block against the
    library and leaves anything unmatched blank. Returns the number of symbols confirmed."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return 0
    try:
        import fitz
    except Exception:
        return 0
    if not library or not edc_pdfs:
        return 0
    bases = _library_bases_by_side(library)
    legal = {f"{b}_{s}" for s in ("L", "R") for b in bases[s]}
    need = [r for r in (records or []) if _needs_symbol(r)]
    if not need:
        return 0
    # pre-index EDC pages (text + page ref) once
    # landing pages = EDC pages whose TEXT mentions a needed conduit tag (text only, no
    # word-position extraction — we just need which pages to render).
    tags = set()
    for r in need:
        for nm in (list(r.get("source") or []) + list(r.get("dest") or [])):
            if nm:
                tags.add(str(nm).upper())
    if not tags:
        return 0
    page_refs = set()
    for p in edc_pdfs:
        if not str(p).lower().endswith(".pdf"):
            continue
        try:
            d = fitz.open(p)
        except Exception:
            continue
        try:
            for pi in range(d.page_count):
                try:
                    text = d[pi].get_text("text").upper()
                except Exception:
                    continue
                if not _STOP_WORDS.search(text) and any(t in text for t in tags):
                    page_refs.add((p, pi))
        finally:
            d.close()
    if not page_refs:
        log("Vision block-read: none of the unresolved conduit tags were found on the EDC "
            "sheets — nothing to render.")
        return 0
    if out_dir is None:
        try:
            import idp_escalate
            out_dir = idp_escalate._localappdata_dir()
        except Exception:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(edc_pdfs[0])), "_symvision")
    imgs = _render_pages(page_refs, out_dir)
    if not imgs:
        return 0
    valid_bases = sorted(bases["L"] | bases["R"])   # both sides — R-only blocks exist too
    ask = [{"conduit": r.get("name", ""),
            "source": " ".join(str(x) for x in (r.get("source") or [])),
            "destination": " ".join(str(x) for x in (r.get("dest") or []))} for r in need]
    prompt = (
        "You are reading AIC electrical EDC diagrams to identify the SYMBOL BLOCK each conduit "
        "lands on at its Source (L) and Destination (R) ends. The symbol is the TERMINAL-BLOCK "
        "STRUCTURE drawn at the landing (breakers, disconnects, fuses, feed-through/knife "
        "terminal blocks, shields) — NOT the equipment the conduit runs to. A conduit to a VFD "
        "or motor lands on a terminal block; only use a VFD/MTR block if the conduit literally "
        "lands on that device with no terminal block.\n\n"
        "Return ONLY a JSON object keyed by conduit name: "
        '{"<conduit>": {"s_symbol": "<block>_L or \\"\\"", "d_symbol": "<block>_R or \\"\\""}}. '
        "Use ONLY these library block base names (append _L for source, _R for destination): "
        + ", ".join(valid_bases) + ". "
        "If a landing shows no specific block, return \"\" (blank) for that end — never guess.\n\n"
        "Conduits to resolve:\n" + __import__("json").dumps(ask, indent=1))
    log(f"Vision block-read: rendering {len(imgs)} EDC sheet(s) for {len(need)} conduit(s), "
        "asking Claude to match landings to library blocks …")
    ans = _vision_symbol_call(imgs, prompt)
    if not ans:
        log("Vision block-read: no usable reply (check the API key / connectivity).")
        return 0
    by = {str(r.get("name")): r for r in records or []}
    confirmed = 0
    for cid, fields in ans.items():
        rec = by.get(cid) or by.get(str(cid).upper())
        if not rec or not isinstance(fields, dict):
            continue
        for sk, ck, key in (("s_symbol", "s_symbol_conf", "s_symbol"),
                            ("d_symbol", "d_symbol_conf", "d_symbol")):
            val = str(fields.get(key) or "").strip()
            if not val or val not in legal:      # blank or not a real library block → skip
                continue
            for g in rec.get("fill", []) or []:
                if g.get("is_ground"):
                    continue
                g[sk] = val
                g[ck] = max(float(g.get(ck) or 0), 0.9)
                g[sk + "_note"] = val + " (read from EDC diagram via Claude vision)"
                confirmed += 1
                break
    log(f"Vision block-read: confirmed {confirmed} S/D symbol(s) from the EDC diagrams.")
    return confirmed
