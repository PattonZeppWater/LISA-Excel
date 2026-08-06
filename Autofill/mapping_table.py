"""
mapping_table.py — a persistent, self-learning mapping / knowledge base for IDP
PDF extraction.

Purpose (Layer 3 "Mapping Table" + Layer 8 "Knowledge Base" of the IDP
philosophy in conduit_project_notes.md): translate the many ways a source
document names things into the app's canonical schema, and REMEMBER every
confirmed mapping so extraction becomes more deterministic — and less dependent
on guessing — the more documents it sees.

It stores three kinds of knowledge, persisted to `knowledge_base.json`:

  * header_aliases  : raw column header -> canonical field
                      e.g. "TRADE SIZE" -> conduit_size
  * value_norms     : raw cell value    -> canonical value (per field)
                      e.g. conduit_type "PVC-40" -> "PVC"
  * schedule_titles : recognized schedule-table titles
                      e.g. "CABLE AND CONDUIT SCHEDULE"

Every entry tracks how many times it has been seen/confirmed and when, so
confidence grows with use. A fuzzy match that a human (or a high-confidence
run) confirms is written back as an exact alias — so the next document with that
header resolves deterministically, with no guessing. That is the "AI usage
decays over time" behaviour from the spec.

Resolution order (cheapest -> most expensive):
    exact canonical name -> known alias -> fuzzy match (>= threshold) -> UNKNOWN

Dependency-free (standard library only). Safe to import from the extractor, and
runnable as a CLI for inspection/teaching:

    python mapping_table.py stats
    python mapping_table.py resolve-header "RACEWAY DIA."
    python mapping_table.py resolve-header "NO." --context cable
    python mapping_table.py learn-header "RACEWAY DIA." conduit_size
    python mapping_table.py normalize conduit_type "PVC-40"
    python mapping_table.py learn-value conduit_type "SCH40 PVC" PVC
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher

# Persist the KB in a STABLE, non-synced location. It must NOT live in a
# OneDrive-synced folder: OneDrive locks files mid-sync, which throws
# "Access is denied" on save. A per-user app-data dir is durable and lock-free.
def _default_kb_path():
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    d = os.path.join(base, "AIC_IDP_Extractor")
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "knowledge_base.json")
    except Exception:
        b = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
             else os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(b, "knowledge_base.json")


DEFAULT_KB_PATH = _default_kb_path()
# one-time migration: bring forward a KB that older builds wrote next to the source
_LEGACY_KB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "knowledge_base.json")

FUZZY_ACCEPT = 0.82   # min similarity to accept a fuzzy header match
# after this many confirmations a fuzzy-derived alias is treated as solid
PROMOTE_AFTER = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s) -> str:
    """Canonical comparison form: upper, collapse whitespace, drop trailing
    punctuation and surrounding noise so 'Conduit  Size ' == 'CONDUIT SIZE'."""
    s = re.sub(r"\s+", " ", str(s or "").strip()).upper()
    return s.strip(" .:#-")


@dataclass
class Resolution:
    value: str | None       # canonical field / value, or None if unknown
    confidence: float       # 0.0 - 1.0
    method: str             # "exact" | "alias" | "fuzzy" | "unknown"
    matched: str = ""       # the stored key that matched (for transparency)


# --- the conventions we already know, used to seed an empty KB ---------------
# Shared headers ("NO.", "TYPE") map to different canonical fields depending on
# whether we're reading a conduit table or a cable table; each canonical field
# is tagged with the context(s) it belongs to so the resolver can disambiguate.
_SEED_HEADERS = {
    # canonical field: (contexts, [aliases])
    "conduit_name":   (["conduit"], ["no", "conduit no", "conduit id", "conduit tag",
                                       "cond no", "conduit name", "tag"]),
    "conduit_size":   (["conduit"], ["conduit size", "trade size", "size", "cond size",
                                       "conduit dia", "raceway size", "raceway dia"]),
    "conduit_type":   (["conduit"], ["type", "material", "conduit type", "raceway type"]),
    "cable_number":   (["conduit"], ["cable number", "cable no", "cables", "cable nos"]),
    "cable_no":       (["cable"],   ["no", "cable no", "cable number", "wire no"]),
    "cable_spec":     (["cable"],   ["cable", "conductor", "cable size", "wire",
                                      "cable description", "cond qty size"]),
    "cable_type":     (["cable"],   ["type", "cable type"]),
    "routing":        (["cable"],   ["routing", "route", "conduit", "raceway",
                                      "in conduit", "routed in"]),
    "source":         (["conduit", "cable"], ["from", "source", "origin", "fed from",
                                               "from equip", "feeder from"]),
    "destination":    (["conduit", "cable"], ["to", "destination", "dest", "load",
                                               "feeds", "to equip"]),
    "remarks":        (["conduit", "cable"], ["remarks", "remark", "notes", "comments"]),
    "ref_documents":  (["conduit"], ["ref documents", "reference", "ref dwg",
                                      "reference drawing", "ref doc"]),
}

_SEED_VALUES = {
    "conduit_type": {
        "PVC-40": "PVC", "PVC-80": "PVC", "PVC 40": "PVC", "PVC 80": "PVC",
        "SCH40 PVC": "PVC", "SCH80 PVC": "PVC",
        "GRC": "RGS", "RIGID": "RGS", "RGC": "RGS", "RIGID STEEL": "RGS",
        "RMC-PVC": "RMC-PVC", "PVC/RGS": "PVC/RGS",
    },
}

_SEED_TITLES = [
    "CONDUIT SCHEDULE", "CABLE AND CONDUIT SCHEDULE", "CONDUIT AND CABLE SCHEDULE",
    "CABLE SCHEDULE", "RACEWAY SCHEDULE", "RACEWAY AND CABLE SCHEDULE",
]


class KnowledgeBase:
    """Load once, resolve/learn many times, persists to JSON."""

    def __init__(self, path: str = DEFAULT_KB_PATH, autoload: bool = True):
        self.path = path
        self.headers: dict = {}   # canonical -> {"contexts":[...], "aliases":{alias:{count,last_seen}}}
        self.values: dict = {}    # field -> {raw_norm: {"value":v,"count":n,"last_seen":..}}
        self.titles: dict = {}    # title_norm -> {count,last_seen}
        self.log: list = []       # recent learning events (audit trail, capped)
        # migrate a legacy KB (older builds saved next to the source file)
        if (autoload and not os.path.exists(path)
                and os.path.exists(_LEGACY_KB_PATH) and _LEGACY_KB_PATH != path):
            try:
                with open(_LEGACY_KB_PATH, "r", encoding="utf-8") as src:
                    with open(path, "w", encoding="utf-8") as dst:
                        dst.write(src.read())
            except OSError:
                pass
        if autoload and os.path.exists(path):
            self.load()
        else:
            self._seed()
            self.save()

    # ---- persistence ----------------------------------------------------
    def load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.headers = data.get("headers", {})
        self.values = data.get("values", {})
        self.titles = data.get("titles", {})
        self.log = data.get("log", [])

    def save(self):
        data = {"_meta": {"updated": _now(),
                          "note": "Self-learning IDP extraction knowledge base."},
                "headers": self.headers, "values": self.values,
                "titles": self.titles, "log": self.log[-500:]}
        # Resilient save: atomic replace, falling back to a direct write, and
        # finally degrading to in-memory-only rather than crashing the caller
        # (e.g. if the target is transiently locked by a sync client).
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except OSError:
                pass  # keep learning in memory; persistence will retry next save

    def _seed(self):
        for field, (contexts, aliases) in _SEED_HEADERS.items():
            self.headers[field] = {
                "contexts": contexts,
                "aliases": {_norm(a): {"count": 1, "last_seen": _now()} for a in aliases},
            }
        for field, mapping in _SEED_VALUES.items():
            self.values[field] = {
                _norm(k): {"value": v, "count": 1, "last_seen": _now()}
                for k, v in mapping.items()
            }
        for t in _SEED_TITLES:
            self.titles[_norm(t)] = {"count": 1, "last_seen": _now()}
        self._record("seed", "initialized knowledge base")

    def _record(self, kind: str, detail: str):
        self.log.append({"when": _now(), "kind": kind, "detail": detail})

    # ---- header resolution ---------------------------------------------
    def resolve_header(self, raw: str, context: str | None = None) -> Resolution:
        """Map a raw column header to a canonical field."""
        key = _norm(raw)
        if not key:
            return Resolution(None, 0.0, "unknown")

        def ctx_ok(field):
            return context is None or context in self.headers[field].get("contexts", [])

        # 1) exact: the canonical field name itself
        if key.lower().replace(" ", "_") in self.headers and ctx_ok(key.lower().replace(" ", "_")):
            return Resolution(key.lower().replace(" ", "_"), 1.0, "exact", key)

        # 2) known alias (prefer context-matching field on ties)
        alias_hits = []
        for field, info in self.headers.items():
            if key in info.get("aliases", {}):
                alias_hits.append(field)
        alias_hits.sort(key=lambda f: (0 if ctx_ok(f) else 1))
        if alias_hits:
            field = alias_hits[0]
            cnt = self.headers[field]["aliases"][key]["count"]
            conf = min(0.99, 0.9 + 0.01 * cnt)
            return Resolution(field, conf, "alias", key)

        # 3) fuzzy against all known aliases
        best_field, best_alias, best_score = None, None, 0.0
        for field, info in self.headers.items():
            if not ctx_ok(field):
                continue
            for alias in info.get("aliases", {}):
                score = SequenceMatcher(None, key, alias).ratio()
                if score > best_score:
                    best_field, best_alias, best_score = field, alias, score
        if best_field and best_score >= FUZZY_ACCEPT:
            return Resolution(best_field, round(best_score, 3), "fuzzy", best_alias)

        return Resolution(None, round(best_score, 3), "unknown", best_alias or "")

    def learn_header(self, raw: str, field: str, context: str | None = None):
        """Record a confirmed header->field mapping (adds/reinforces an alias)."""
        key = _norm(raw)
        if not key or not field:
            return
        rec = self.headers.setdefault(field, {"contexts": [], "aliases": {}})
        if context and context not in rec["contexts"]:
            rec["contexts"].append(context)
        a = rec["aliases"].setdefault(key, {"count": 0, "last_seen": _now()})
        a["count"] += 1
        a["last_seen"] = _now()
        self._record("learn_header", f"{key!r} -> {field} (count={a['count']})")
        self.save()

    # ---- value normalization -------------------------------------------
    def normalize_value(self, field: str, raw: str) -> Resolution:
        key = _norm(raw)
        table = self.values.get(field, {})
        if key in table:
            e = table[key]
            return Resolution(e["value"], min(0.99, 0.9 + 0.01 * e["count"]), "alias", key)
        return Resolution(None, 0.0, "unknown")

    def learn_value(self, field: str, raw: str, value: str):
        key = _norm(raw)
        if not key:
            return
        table = self.values.setdefault(field, {})
        e = table.setdefault(key, {"value": value, "count": 0, "last_seen": _now()})
        e["value"] = value
        e["count"] += 1
        e["last_seen"] = _now()
        self._record("learn_value", f"{field}: {key!r} -> {value!r} (count={e['count']})")
        self.save()

    # ---- schedule titles -----------------------------------------------
    def is_schedule_title(self, text: str) -> Resolution:
        key = _norm(text)
        if not key:
            return Resolution(None, 0.0, "unknown")
        # substring match against any known title (handles "CABLE AND CONDUIT SCHEDULE")
        for title in self.titles:
            if title in key or key in title:
                return Resolution(title, 0.95, "alias", title)
        best, score = None, 0.0
        for title in self.titles:
            s = SequenceMatcher(None, key, title).ratio()
            if s > score:
                best, score = title, s
        if best and score >= FUZZY_ACCEPT:
            return Resolution(best, round(score, 3), "fuzzy", best)
        return Resolution(None, round(score, 3), "unknown", best or "")

    def learn_title(self, title: str):
        key = _norm(title)
        if not key:
            return
        e = self.titles.setdefault(key, {"count": 0, "last_seen": _now()})
        e["count"] += 1
        e["last_seen"] = _now()
        self._record("learn_title", f"{key!r} (count={e['count']})")
        self.save()

    # ---- convenience: resolve a whole header row -----------------------
    def map_header_row(self, cells, context: str | None = None, learn: bool = True):
        """Given a list of raw header cells, return {canonical_field: col_index}.
        High-confidence resolutions are reinforced (learned) when learn=True."""
        out = {}
        for idx, cell in enumerate(cells):
            res = self.resolve_header(cell, context=context)
            if res.value and res.value not in out:
                out[res.value] = idx
                # reinforce exact/alias hits so their confidence keeps climbing;
                # do NOT auto-learn fuzzy guesses (those await confirmation)
                if learn and res.method in ("exact", "alias"):
                    self.learn_header(cell, res.value, context=context)
        return out

    # ---- reporting ------------------------------------------------------
    def stats(self) -> dict:
        return {
            "path": self.path,
            "fields": len(self.headers),
            "aliases": sum(len(v.get("aliases", {})) for v in self.headers.values()),
            "value_fields": len(self.values),
            "value_norms": sum(len(v) for v in self.values.values()),
            "titles": len(self.titles),
            "learning_events": len(self.log),
        }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _cli(argv):
    kb = KnowledgeBase()
    if not argv or argv[0] in ("stats", "-h", "--help", "help"):
        s = kb.stats()
        print("Knowledge base:", s["path"])
        for k, v in s.items():
            if k != "path":
                print(f"  {k:16s}: {v}")
        print("\nCommands: resolve-header <text> [--context conduit|cable] | "
              "learn-header <text> <field> [--context ...] | "
              "normalize <field> <value> | learn-value <field> <raw> <value> | "
              "title <text> | log")
        return

    cmd = argv[0]
    ctx = None
    if "--context" in argv:
        i = argv.index("--context")
        ctx = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    if cmd == "resolve-header":
        r = kb.resolve_header(argv[1], context=ctx)
        print(f"{argv[1]!r} -> {r.value} ({r.method}, conf={r.confidence}, matched={r.matched!r})")
    elif cmd == "learn-header":
        kb.learn_header(argv[1], argv[2], context=ctx)
        print(f"learned: {argv[1]!r} -> {argv[2]}" + (f" [{ctx}]" if ctx else ""))
    elif cmd == "normalize":
        r = kb.normalize_value(argv[1], argv[2])
        print(f"{argv[1]}: {argv[2]!r} -> {r.value} ({r.method}, conf={r.confidence})")
    elif cmd == "learn-value":
        kb.learn_value(argv[1], argv[2], argv[3])
        print(f"learned value: {argv[1]}: {argv[2]!r} -> {argv[3]!r}")
    elif cmd == "title":
        r = kb.is_schedule_title(argv[1])
        print(f"{argv[1]!r} -> title? {r.value} ({r.method}, conf={r.confidence})")
    elif cmd == "log":
        for e in kb.log[-20:]:
            print(f"  {e['when']}  {e['kind']:14s} {e['detail']}")
    else:
        print("unknown command:", cmd)


if __name__ == "__main__":
    _cli(sys.argv[1:])
