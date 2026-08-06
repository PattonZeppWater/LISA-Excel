"""
idp_escalate.py — when the extractor is unsure, ask Claude.

The exe can't invoke Claude on its own, so this builds a self-contained,
copy-paste-ready escalation packet (ASK_CLAUDE.md) describing exactly what the
tool couldn't resolve and the specific decisions it needs — structured so
Claude's answer can be pasted straight back into Remembered Logic. If an
ANTHROPIC_API_KEY is present in the environment, it will additionally call the
Claude API directly (stdlib urllib, no extra dependency) and save the reply.

collect_uncertain(records)  -> uncertain items from a just-extracted record set
                               (low-confidence symbols, unresolved conduit types,
                               missing terminations)
build_packet(items, ...)     -> writes ASK_CLAUDE.md, returns its path
ask_claude_api(packet_text)  -> optional live API call; returns reply text or None
"""
from __future__ import annotations

import json
import os

_MODEL = "claude-sonnet-5"
_API_URL = "https://api.anthropic.com/v1/messages"


def _localappdata_dir():
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    d = os.path.join(base, "AIC_IDP_Extractor")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.dirname(os.path.abspath(__file__))
    return d


def collect_uncertain(records):
    """Pull the items a just-extracted record set is NOT confident about."""
    items = []
    for rec in records or []:
        name = rec.get("name", "")
        names = [n for n in (list(rec.get("source", [])) + list(rec.get("dest", []))) if n]
        if str(rec.get("ctype", "")).strip() in ("XXX", ""):
            items.append({"conduit": name, "issue": "conduit_type_unresolved",
                         "context": f"source={rec.get('source')}, dest={rec.get('dest')}",
                         "question": f"What Conduit Type (RMC/PVC/RGS/FLEX/...) should {name} be?"})
        for i, g in enumerate(rec.get("fill", []) or []):
            for side, ck, sk in (("S", "s_symbol_conf", "s_symbol"),
                                 ("D", "d_symbol_conf", "d_symbol")):
                conf = g.get(ck)
                if conf is not None and conf < 0.6 and g.get(sk):
                    dev = (names[0] if side == "S" and names else
                           names[-1] if names else name)
                    items.append({"conduit": name, "issue": f"low_confidence_{side}_symbol",
                                 "context": f"device={dev!r}, type={g.get('type')}, "
                                            f"wire_ct={g.get('wire_ct') or g.get('count')}, "
                                            f"guessed={g.get(sk)} (conf {conf})",
                                 "question": f"Which symbol block should {dev!r} land on "
                                             f"({side} side, {g.get('type')})? We guessed {g.get(sk)}."})
    return items


def from_training_report(report):
    """Turn a idp_training.run_training report's 'uncertain' gaps into items."""
    items = []
    for gap in (report or {}).get("uncertain", []):
        items.append({
            "conduit": gap.get("conduit", ""), "issue": gap.get("kind", "gap"),
            "context": f"{gap.get('field')}: finished IDP shows {gap.get('ground')!r}, "
                       f"our workbook has {gap.get('ours')!r}",
            "question": f"For {gap.get('conduit')} {gap.get('field')}: the finished drawing "
                        f"says {gap.get('ground')!r} but we produced {gap.get('ours')!r} — "
                        f"which is correct, and what rule should we remember?"})
    return items


def build_packet(items, project="", out_dir=None):
    """Write a copy-paste-ready ASK_CLAUDE.md; return its path (or '' if nothing
    to ask)."""
    items = items or []
    if not items:
        return ""
    out_dir = out_dir or _localappdata_dir()
    path = os.path.join(out_dir, "ASK_CLAUDE.md")
    lines = [
        f"# IDP Extractor — questions for Claude{(' (' + project + ')') if project else ''}",
        "",
        "The extractor could not confidently resolve the items below. Paste this "
        "whole file to Claude (the IDP skills are loaded). For each item, reply "
        "with a one-line Remembered-Logic rule in the form:",
        "",
        "```",
        "RULE <type> | match=<text> | result=<value> | context=<optional>",
        "```",
        "where <type> is `symbol_keyword` (device→symbol token), `value_rule` "
        "(context=conduit_type, a normalized value), or `header_alias`.",
        "",
        f"**{len(items)} open item(s):**",
        "",
    ]
    for i, it in enumerate(items, 1):
        lines += [f"### {i}. [{it.get('conduit','?')}] {it.get('issue','')}",
                  f"- Context: {it.get('context','')}",
                  f"- Question: {it.get('question','')}",
                  ""]
    text = "\n".join(lines)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        # also emit a machine-readable sibling so an attached Claude Code chat can
        # read the open questions straight off disk and resolve them in-chat
        # (no user copy-paste) — see resolve_in_chat note below.
        with open(os.path.splitext(path)[0] + ".json", "w", encoding="utf-8") as fh:
            json.dump({"project": project, "items": items, "status": "open"}, fh, indent=2)
    except OSError:
        return ""
    return path


# Fixed path both the exe and an attached Claude Code chat agree on. The exe
# writes questions here; the chat reads, answers, applies rules, marks resolved.
PENDING_JSON = os.path.join(_localappdata_dir(), "ASK_CLAUDE.json")


def load_pending():
    """Return the open-questions packet (dict) the exe last wrote, or None."""
    try:
        with open(PENDING_JSON, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if d.get("status") == "open" else None
    except Exception:
        return None


def mark_resolved(applied_count):
    """Stamp the packet resolved after the chat has applied answers."""
    try:
        with open(PENDING_JSON, encoding="utf-8") as fh:
            d = json.load(fh)
        d["status"] = "resolved"
        d["applied"] = applied_count
        with open(PENDING_JSON, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except Exception:
        pass


def ask_claude_api(packet_text, model=_MODEL, timeout=60):
    """If ANTHROPIC_API_KEY is set, ask Claude directly (stdlib urllib) and
    return the reply text. Returns None if no key / any failure (caller then
    falls back to the copy-paste packet)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            import idp_settings
            key = idp_settings.get_api_key()
        except Exception:
            key = None
    if not key:
        return None
    import urllib.request
    body = json.dumps({
        "model": model, "max_tokens": 2000,
        "messages": [{"role": "user", "content": packet_text}],
    }).encode("utf-8")
    req = urllib.request.Request(_API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = data.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text") or None
    except Exception:
        return None


def apply_rule_lines(text):
    """Parse 'RULE <type> | match=.. | result=.. | context=..' lines (from
    Claude's reply) and add them to Remembered Logic. Returns count added."""
    if not text:
        return 0
    import logic_store
    data = logic_store.load()
    existing = {(r.get("type"), str(r.get("match", "")).strip().upper())
                for r in data.get("rules", [])}
    added = 0
    for ln in text.splitlines():
        ln = ln.strip().lstrip("`").strip()
        if not ln.upper().startswith("RULE "):
            continue
        parts = [p.strip() for p in ln[5:].split("|")]
        rtype = parts[0].strip() if parts else ""
        kv = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        m, res = kv.get("match", ""), kv.get("result", "")
        if rtype not in ("symbol_keyword", "value_rule", "header_alias") or not m or not res:
            continue
        if (rtype, m.upper()) in existing:
            continue
        existing.add((rtype, m.upper()))
        data.setdefault("rules", []).append(
            {"type": rtype, "match": m, "result": res,
             "context": kv.get("context", ""), "note": "from Claude escalation reply"})
        added += 1
    if added:
        logic_store.save(data)
    return added


if __name__ == "__main__":
    demo = [{"conduit": "C001", "issue": "low_confidence_S_symbol",
             "context": "device='WEIRD XYZ', type=CONTROL", "question": "Which symbol?"}]
    print("packet:", build_packet(demo, project="demo"))
