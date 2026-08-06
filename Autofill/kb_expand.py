"""
kb_expand.py — grow the knowledge base from each building processed.

Called after extraction so the Python program keeps LEARNING over time: every
conduit type, fill type, symbol, and color it sees is recorded (and reinforced)
in the persistent knowledge base (%LOCALAPPDATA%\\AIC_IDP_Extractor). The more
buildings run through, the more the tool "remembers".
"""
from __future__ import annotations


def expand_from_records(records):
    """Record conventions seen in `records` into the knowledge base. Safe no-op
    if the KB is unavailable. Returns a short summary string."""
    learned = {"conduit_type": 0, "fill_type": 0, "symbol": 0, "color": 0}
    try:
        from mapping_table import KnowledgeBase
        kb = KnowledgeBase()
    except Exception:
        return "kb unavailable"

    for r in records or []:
        ct = str(r.get("ctype", "")).strip()
        if ct and ct != "XXX":
            kb.learn_value("conduit_type", ct, ct)
            learned["conduit_type"] += 1
        for f in r.get("fill", []):
            t = str(f.get("type", "")).strip()
            if t:
                kb.learn_value("fill_type", t, t)
                learned["fill_type"] += 1
            for s in (f.get("s_symbol"), f.get("d_symbol")):
                s = str(s or "").strip()
                if s:
                    kb.learn_value("symbol", s, s)
                    learned["symbol"] += 1
            for c in f.get("colors", []) or []:
                c = str(c or "").strip()
                if c:
                    kb.learn_value("wire_color", c, c)
                    learned["color"] += 1
    return ("kb grew — "
            f"{learned['conduit_type']} types, {learned['fill_type']} fill-types, "
            f"{learned['symbol']} symbols, {learned['color']} colors recorded")


if __name__ == "__main__":
    print(expand_from_records([{"ctype": "RMC", "fill": [
        {"type": "POWER", "s_symbol": "CB-TB_Square_L", "d_symbol": "CB-TB_Square_R", "colors": ["BROWN"]}]}]))
