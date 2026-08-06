"""
idp_vision_assist.py — hand the OCR-hard spots to a VISION pass (the bridge to the skill).

The offline OCR pipeline gets ~93% of the fill right and lands the correct panel/TB tags,
but two things need eyes on the drawing: (a) exact PLC I/O CHANNEL numbers behind a landed
TB tag, (b) exact panelboard CIRCUIT numbers on a branch, and (c) the full cable list of a
conduit whose dense cables-in-conduit cell the OCR under-read. This module PINPOINTS those
conduits, renders only the sheets that answer them, and either:
  • auto-calls Claude vision when ANTHROPIC_API_KEY is set, applying the answers, or
  • writes a precise ASK_CLAUDE_VISION_TERMS.md packet (which conduit needs what) so a
    Claude pass / the skill fills exactly those cells — nothing more.

So the exe stays fast + offline for everything it can do, and only the pinpointed hard
cells go to vision. Returns (n_applied, packet_path).
"""
import os
import re
import json


def find_gaps(records):
    """Pinpoint the conduits + fields that need a drawing read. Returns a list of
    {conduit, need, hint} — need in {channel, circuit, cables}."""
    gaps = []
    for r in records or []:
        name = r.get("name", "")
        flags = " ".join(r.get("flags") or [])
        # (a) TB landed but channel unknown  → edc_io_tb=...(verify channel#)
        if "verify channel" in flags:
            tb = re.search(r"edc_io_tb=([A-Z0-9.\-]+)", flags)
            gaps.append({"conduit": name, "need": "channel",
                         "hint": f"landed on {tb.group(1) if tb else 'a TB'}; read its channel #"})
        # (b) branch circuit derived-but-unverified, or a panel branch with only phases
        if "panelboard_circuits_by_load" in flags:
            gaps.append({"conduit": name, "need": "circuit",
                         "hint": "verify the panelboard CKT # for this branch"})
        else:
            src = " ".join(r.get("source") or []).upper()
            is_panel = "PANEL" in src
            pwr = next((g for g in r.get("fill", []) if str(g.get("type")) == "POWER"
                        and not g.get("is_ground")), None)
            termed = any((w.get("src") or ("", "", ""))[2] for w in (r.get("wires") or []))
            if is_panel and pwr and not termed:
                gaps.append({"conduit": name, "need": "circuit",
                             "hint": "panel branch — read its CKT # from the panelboard schedule"})
        # (c) dense cable cell likely under-read: a real signal/power run with a single
        # coarse group and no cable-schedule upgrade (couldn't match its cable list)
        if "fill_from_cable_schedule" not in flags and r.get("fill") and \
                "ocr_conduit_only_fill_from_cables" in flags:
            gaps.append({"conduit": name, "need": "cables",
                         "hint": "verify the full cables-in-conduit list (dense cell)"})
    return gaps


def _render(pdfs, out_dir, log):
    """Render the sheets that answer the gaps: panelboard + PLC I/O (terms) and the
    conduit+cable schedule (cable lists). Returns image paths."""
    imgs = []
    try:
        import idp_edc
        import idp_vision
        import idp_cable_schedule as _C
    except Exception:
        return imgs
    for p in pdfs:
        if not str(p).lower().endswith(".pdf"):
            continue
        pages = []
        try:
            pages += idp_edc.find_edc_sheets(p)                 # three-line / PLC I/O / panelboard
        except Exception:
            pass
        try:
            pages += idp_vision.find_schedule_pages(p)          # conduit + cable schedule
        except Exception:
            pass
        pages = sorted(set(pages))[:12]
        if pages:
            try:
                imgs += idp_vision.render_pages(p, pages, out_dir, prefix="assist")
            except Exception as e:
                log(f"   (render skipped for {os.path.basename(p)}: {e})")
    return imgs


_PROMPT = (
    "You are reading AIC electrical drawings to FILL specific missing terminals on an IDP. "
    "For ONLY the conduits listed, return JSON keyed by conduit tag: "
    '{"<conduit>": {"s_term":"", "d_term":"", "cables":["C-001", ...]}}. '
    "Rules: PLC I/O channel = the TB tag + channel (e.g. TBAI-0.07:02); panelboard branch = "
    "the CKT number (e.g. CKT-26); keep power phases ØA/ØB/ØC. Only fill what the drawings "
    "clearly show; omit a field you can't read. Output ONLY the JSON."
)


def assist(records, pdfs, log=lambda *a: None, out_dir=None, allow_api=True):
    """Bridge the OCR-hard cells via vision. Auto-applies via API key if set AND allow_api
    is True; else writes a precise hand-off packet. Returns (n_applied, packet_path).

    A SCAN passes allow_api=False so the exe NEVER contacts Claude to produce its output —
    it stays fully offline and only writes a pinpointed packet for an optional later pass.
    """
    gaps = find_gaps(records)
    if not gaps:
        return 0, ""
    by_conduit = {}
    for g in gaps:
        by_conduit.setdefault(g["conduit"], []).append(f"{g['need']}: {g['hint']}")
    log(f"Vision-assist: {len(by_conduit)} conduit(s) need a drawing read "
        f"({sum(1 for g in gaps if g['need']=='channel')} channels, "
        f"{sum(1 for g in gaps if g['need']=='circuit')} circuits, "
        f"{sum(1 for g in gaps if g['need']=='cables')} cable lists).")
    if out_dir is None:
        try:
            import idp_escalate
            out_dir = idp_escalate._localappdata_dir()
        except Exception:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(pdfs[0])), "_vision") if pdfs else "."
    os.makedirs(out_dir, exist_ok=True)

    # AUTO path: Claude vision if a key is set (reuses idp_edc's transcriber).
    # Skipped entirely during a scan (allow_api=False) so output never depends on Claude.
    if allow_api and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import idp_edc
            imgs = _render(pdfs, out_dir, log)
            if imgs:
                ans = idp_edc.transcribe_via_api(imgs, prompt=_PROMPT) if _accepts_prompt(idp_edc) \
                    else idp_edc.transcribe_via_api(imgs)
                n = _apply(records, ans or {})
                log(f"Vision-assist: applied {n} field(s) from the Claude vision pass.")
                return n, ""
        except Exception as e:
            log(f"   (vision API pass skipped: {e})")

    # PACKET path: render the sheets + write exactly what to read, for a Claude pass
    imgs = _render(pdfs, out_dir, log)
    packet = os.path.join(out_dir, "ASK_CLAUDE_VISION_TERMS.md")
    try:
        with open(packet, "w", encoding="utf-8") as fh:
            fh.write("# Vision assist — fill ONLY these pinpointed cells\n\n")
            fh.write("The exe extracted everything OCR can read. These specific conduits need "
                     "a drawing read (channels / circuits / dense cable lists). Open the "
                     "rendered sheets in this folder and return the JSON described below.\n\n")
            fh.write("## Conduits needing a read\n\n")
            for c, needs in sorted(by_conduit.items()):
                fh.write(f"- **{c}** — " + "; ".join(needs) + "\n")
            fh.write("\n## Rendered sheets\n\n")
            for im in imgs:
                fh.write(f"- {os.path.basename(im)}\n")
            fh.write("\n## Return format\n\n```json\n")
            fh.write(json.dumps({c: {"s_term": "", "d_term": "", "cables": []}
                                 for c in sorted(by_conduit)}, indent=1))
            fh.write("\n```\n\n" + _PROMPT + "\n")
        log(f"Vision-assist: wrote pinpointed packet → {packet} ({len(imgs)} sheet(s) rendered).")
    except Exception as e:
        log(f"   (vision packet write failed: {e})")
        return 0, ""
    return 0, packet


def _accepts_prompt(mod):
    try:
        import inspect
        return "prompt" in inspect.signature(mod.transcribe_via_api).parameters
    except Exception:
        return False


def _apply(records, ans):
    """Apply vision answers (channel/circuit/cables) onto the matching conduits."""
    by = {str(r.get("name")): r for r in records or []}
    n = 0
    for cid, fields in (ans or {}).items():
        rec = by.get(cid) or by.get(str(cid).upper())
        if not rec:
            continue
        st, dt = fields.get("s_term"), fields.get("d_term")
        if st or dt:
            grp = next((g for g in rec.get("fill", []) if not g.get("is_ground")), None)
            k = int((grp or {}).get("wire_ct") or 1) if grp else 1
            rec.setdefault("wires", [])
            rec["wires"] = [{"src": ("", "", st or ""), "dst": ("", "", dt or "")}]
            rec.setdefault("flags", []).append("vision_terms")
            n += 1
    return n
