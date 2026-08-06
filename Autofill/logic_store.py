"""
logic_store.py — user-editable "Remembered Logic" for extraction.

A persistent, editable ruleset the user maintains from the Control Panel. It is
loaded and APPLIED at the start of every scan, so hand-taught logic keeps working
across runs. Stored in a stable, non-synced location (%LOCALAPPDATA%), so it
survives restarts and OneDrive locks.

Rule shape: {type, match, result, context, note}
  type = "header_alias"   -> teach the mapping table a column-header alias
          match = raw header text, result = canonical field
          context = "conduit" | "cable" | "" (optional)
  type = "value_rule"     -> teach a value normalization
          context = field (e.g. "conduit_type"), match = raw, result = canonical
  type = "symbol_keyword" -> teach the symbol engine a device keyword
          match = keyword/regex, result = library device token (e.g. "XFMR_3PH")
  type = "text_fix"       -> fix an OCR/text misread everywhere it appears in the read text
          match = the WRONG text the reader produced (e.g. "KLDS"),
          result = the CORRECT text (e.g. "MDS"). Whole-word, case-insensitive.
  type = "note"           -> free-text reminder ("where to look for tags/terms")
          (not machine-applied; shown for reference)

Plus a free-text `notes` field for general guidance.
"""
from __future__ import annotations

import json
import os
import re

RULE_TYPES = ["header_alias", "value_rule", "symbol_keyword", "text_fix", "note"]


def apply_text_fixes(records, log=None):
    """Apply the user's 'text_fix' rules to the READ TEXT on each conduit record — a whole-word,
    case-insensitive find→replace of a misread token (e.g. an OCR 'KLDS' → the correct 'MDS').
    Runs over the equipment names/descriptions the reader produced (name, source, dest, the fill
    groups' s_desc/d_desc, and the notes), so a taught correction is applied no matter which
    reader (OCR / Excel / text) produced it. Returns the number of substitutions made."""
    try:
        fixes = []
        for r in load().get("rules", []):
            if r.get("type") != "text_fix":
                continue
            m = (r.get("match") or "").strip()
            res = (r.get("result") or "").strip()
            if m:
                # whole-token match (letters/digits boundary), case-insensitive
                fixes.append((re.compile(r"(?<![A-Za-z0-9])" + re.escape(m) + r"(?![A-Za-z0-9])",
                                         re.I), res))
        if not fixes:
            return 0
    except Exception:
        return 0

    def _fix_str(s):
        out = str(s)
        for rx, res in fixes:
            out = rx.sub(res, out)
        return out

    n = 0
    for rec in records or []:
        for k in ("name", "deviations"):
            v = rec.get(k)
            if isinstance(v, str) and v:
                nv = _fix_str(v)
                if nv != v:
                    rec[k] = nv; n += 1
        for k in ("source", "dest"):
            v = rec.get(k)
            if isinstance(v, list):
                for i, x in enumerate(v):
                    if isinstance(x, str) and x:
                        nx = _fix_str(x)
                        if nx != x:
                            v[i] = nx; n += 1
        for g in (rec.get("fill") or []):
            for k in ("s_desc", "d_desc"):
                v = g.get(k)
                if isinstance(v, list):
                    for i, x in enumerate(v):
                        if isinstance(x, str) and x:
                            nx = _fix_str(x)
                            if nx != x:
                                v[i] = nx; n += 1
    if log and n:
        log("Text fixes: applied %d taught OCR/text correction(s) to the read names." % n)
    return n


def _folder_store():
    """Remembered Logic lives IN the (synced) app folder as learned_logic.json, so it
    (a) SHIPS with the app, (b) is captured in every Training version snapshot, and
    (c) propagates to other computers via the Update button — the whole point of the
    version-control system. This is the durable home for the device→symbol rules learned
    from the skill/training."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_logic.json")


def _legacy_localappdata_store():
    """The old machine-local location (pre-sync). Read once to migrate, never written to now."""
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "AIC_IDP_Extractor", "extraction_logic.json")


def _store_path():
    """Prefer the synced folder store; fall back to %LOCALAPPDATA% only if the folder isn't
    writable (e.g. a frozen read-only install)."""
    folder = _folder_store()
    try:
        d = os.path.dirname(folder)
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return folder
    except Exception:
        pass
    legacy = _legacy_localappdata_store()
    try:
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        return legacy
    except Exception:
        return folder


PATH = _store_path()
_DEFAULT = {"rules": [], "notes": ""}

# ── Built-in "new logic" seeded into the Remembered Logic panel ──────────────
# value_rule entries also reinforce the KB; note entries are reference-only
# (the actual behavior is enforced by lisa_contract / idp_anatomy on every write).
DEFAULT_RULES = [
    {"type": "value_rule", "match": "ETHERNET", "result": "CAT-6", "context": "type",
     "note": "off-dropdown type normalized to the legal dropdown"},
    {"type": "value_rule", "match": "PULL ROPE", "result": "PULL_ROPE", "context": "type",
     "note": "normalize spacing to the legal value"},
    {"type": "value_rule", "match": "MFR CABLE", "result": "MFG_CABLE", "context": "type",
     "note": "vendor cable → manufactured cable"},
    {"type": "note", "match": "GROUND rows", "result": "fold into primary group (+GRN)",
     "context": "type", "note": "no standalone GROUND type row is emitted"},
    {"type": "note", "match": "Wire Ct", "result": "connection count, not conductors",
     "context": "connection", "note": "3-phase feeder → Ct 3 (ØA/ØB/ØC); POWER lands at Ct 1–4"},
    {"type": "note", "match": "POWER colors", "result": "BRN / ORG / YEL (+GRN gnd)",
     "context": "color", "note": "480V phase colors — auto-applied & amber-flagged"},
    {"type": "note", "match": "TSP", "result": "RED/BLK (may be multi-pair)",
     "context": "color", "note": "signal pair; Wire Ct can be >1"},
    {"type": "note", "match": "CONTROL color", "result": "learned per project",
     "context": "color", "note": "BLU or RED vary by project — never hard-defaulted"},
    {"type": "note", "match": "S/D Symbol", "result": "must be in KEY(Type)_<Ct>_<L|R> dropdown",
     "context": "symbol", "note": "else LISA can't map it — auto-snapped or flagged"},
    {"type": "note", "match": "Wire Label", "result": "TEXTJOIN-computed in workbook",
     "context": "", "note": "fill tags/terms only; never populate the label columns"},
    {"type": "note", "match": "Output", "result": "never overwrite (versioned _vN)",
     "context": "", "note": "previous extraction results are preserved"},
]

DEFAULT_NOTES = (
    "WHERE TO FIND TAGS / TERMS\n"
    "• Enclosure wiring diagrams & terminal (TB) schedules — authoritative for S/D tags + terminal numbers.\n"
    "• Vendor cut sheets / cable schedules — cable IDs, conductor counts, colors.\n"
    "• FILLWIRELABEL LISP output — per-wire labels when the diagram is drawn.\n"
    "• Instrument ISA bubbles (LE/LIT-051, PSH-061) — device tag; terminals (COM/NO/+/-/PWR/GND) at the bubble.\n"
    "\n"
    "LISA INPUT CONTRACT (auto-enforced every write)\n"
    "• Type ∈ Type_<WireCt>; S/D Symbol ∈ KEY(Type)_<Ct>_<L|R> dropdown (KEY = type without dashes).\n"
    "• Wire Ct = connection count, not raw conductor count. 3-phase feeders → Ct 3. POWER lands at Ct 1–4.\n"
    "• Wire Labels are computed in-workbook (TEXTJOIN) — fill tags/terms only.\n"
    "\n"
    "DRAWING CONVENTIONS (auto-applied, amber-flagged)\n"
    "• POWER phases BRN/ORG/YEL (+GRN gnd). TSP = RED/BLK (may be multi-pair). CONTROL color learned per project.\n"
    "• Off-dropdown types normalized: ETHERNET→CAT-6, 'PULL ROPE'→PULL_ROPE, MFR CABLE→MFG_CABLE; GROUND folded into its group.\n"
    "\n"
    "OUTPUT\n"
    "• Workbooks are never overwritten — each run writes a new _vN file."
)


def defaults():
    """The built-in 'new logic' shown in the Remembered Logic panel."""
    return {"rules": [dict(r) for r in DEFAULT_RULES], "notes": DEFAULT_NOTES}


def _dedup(rules):
    """Union rules, dropping exact (type/match/context/result) duplicates — so merging the
    built-in baseline with the learned store never double-lists a rule."""
    seen, out = set(), []
    for r in rules or []:
        k = (r.get("type"), (r.get("match") or "").strip().lower(),
             (r.get("context") or "").strip().lower(), (r.get("result") or "").strip().lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def load():
    data = None
    if os.path.exists(PATH):
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    if data is None:
        # one-time migration: pull the pre-sync machine-local store into the folder store
        legacy = _legacy_localappdata_store()
        if PATH != legacy and os.path.exists(legacy):
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None
    if data is None:
        data = {"rules": [], "notes": ""}
    data.setdefault("rules", [])
    data.setdefault("removed", [])
    data.setdefault("notes", "")
    # always include the built-in baseline (deduped), so the shipped value/note rules are
    # present even on a store that predates them; the learned device→symbol rules ride along.
    merged = _dedup(list(DEFAULT_RULES) + data["rules"])
    # honor user deletions: a rule whose key is in `removed` is suppressed even if it is a
    # built-in DEFAULT (which would otherwise re-merge on every load).
    _rm = {tuple(k) for k in (data.get("removed") or [])}
    data["rules"] = [r for r in merged if _rkey(r) not in _rm]
    if not data.get("notes"):
        data["notes"] = DEFAULT_NOTES
    return data


def _rkey(r):
    """Identity of a rule for dedup/delete: (type, match, context, result), case-folded."""
    return (str(r.get("type", "")).strip().lower(), str(r.get("match", "")).strip().lower(),
            str(r.get("context", "")).strip().lower(), str(r.get("result", "")).strip().lower())


def rule_source(r):
    """'manual' when the user added the rule in the panel; 'generated' otherwise (training-
    learned, ask-Claude, or a built-in default)."""
    return "manual" if str(r.get("source", "")).strip().lower() == "manual" else "generated"


def _raw():
    """The persisted store as-is (NO DEFAULT merge, NO removed-filter) — for editing rules
    and the `removed` suppression list."""
    if os.path.exists(PATH):
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("rules", [])
            d.setdefault("removed", [])
            d.setdefault("notes", "")
            return d
        except Exception:
            pass
    return {"rules": [], "removed": [], "notes": ""}


def add_rule(rule, source="manual"):
    """Add ONE rule (tagged with its source). A manually-added rule un-suppresses any prior
    delete of the same key. Returns the stored rule dict."""
    d = _raw()
    r = dict(rule or {})
    r["source"] = source
    k = _rkey(r)
    d["removed"] = [x for x in d.get("removed", []) if tuple(x) != k]
    d.setdefault("rules", []).append(r)
    save(d)
    return r


def delete_rules(keys):
    """Delete every rule whose key is in `keys` (list of [type,match,context,result]). Drops
    matching stored rules AND suppresses matching built-in DEFAULT rules via `removed`, so the
    delete persists across reloads. Returns the list of removed rule dicts (for undo)."""
    want = {tuple(str(x).strip().lower() for x in k) for k in (keys or [])}
    # capture the full visible dicts first (covers DEFAULT rules too), for undo
    removed = [r for r in load().get("rules", []) if _rkey(r) in want]
    d = _raw()
    d["rules"] = [r for r in d.get("rules", []) if _rkey(r) not in want]
    have = {tuple(x) for x in d.get("removed", [])}
    for k in want:
        if k not in have:
            d.setdefault("removed", []).append(list(k))
    save(d)
    return removed


def undo_delete(rules):
    """Restore a batch of previously-deleted rule dicts (un-suppress their keys + re-add any
    non-default ones)."""
    if not rules:
        return False
    d = _raw()
    keys = {_rkey(r) for r in rules}
    d["removed"] = [x for x in d.get("removed", []) if tuple(x) not in keys]
    existing = {_rkey(r) for r in d.get("rules", [])}
    for r in rules:
        if _rkey(r) not in existing:
            d.setdefault("rules", []).append(dict(r))
    save(d)
    return True


def save(data):
    try:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PATH)
    except OSError:
        try:
            with open(PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass


def apply(data=None):
    """Push the remembered rules into the live engines AND PERSIST the knowledge-base ones so
    the extraction path (which builds its own KnowledgeBase from disk) actually sees them.
    Returns a summary; any wiring failure is REPORTED in it (never silently swallowed) so a
    'training had no effect' complaint is always diagnosable straight from the scan log."""
    data = data or load()
    applied = {"header_alias": 0, "value_rule": 0, "symbol_keyword": 0, "text_fix": 0}
    errors = []

    # header aliases + value normalizations -> mapping-table knowledge base.
    # CRITICAL: the KnowledgeBase is FILE-BACKED and the extraction path loads its OWN copy from
    # disk, so we MUST save() — otherwise every value_rule/header_alias evaporated with this
    # throwaway instance and had no effect on any scan (the old bug).
    try:
        from mapping_table import KnowledgeBase
        kb = KnowledgeBase()
        for r in data.get("rules", []):
            t = r.get("type")
            m = (r.get("match") or "").strip()
            res = (r.get("result") or "").strip()
            ctx = (r.get("context") or "").strip() or None
            if t == "header_alias" and m and res:
                kb.learn_header(m, res, context=ctx)
                applied["header_alias"] += 1
            elif t == "value_rule" and m and res:
                kb.learn_value(ctx or "conduit_type", m, res)
                applied["value_rule"] += 1
        if applied["header_alias"] or applied["value_rule"]:
            kb.save()                          # persist so extraction picks the rules up
    except Exception as e:
        errors.append("knowledge-base (%s)" % e)

    # symbol keyword rules -> symbol inference engine (module global; re-registered each apply
    # so a deleted/edited rule stops applying without a restart).
    try:
        import symbol_infer
        try:
            symbol_infer._USER_RULES.clear()
        except Exception:
            pass
        for r in data.get("rules", []):
            if r.get("type") == "symbol_keyword":
                m = (r.get("match") or "").strip()
                res = (r.get("result") or "").strip()
                if m and res:
                    symbol_infer.register_keyword(m, res)
                    applied["symbol_keyword"] += 1
    except Exception as e:
        errors.append("symbols (%s)" % e)

    # text_fix rules are applied per-scan to the records themselves (logic_store.apply_text_fixes
    # is called in the scan) — count them so the summary reflects that they're active.
    applied["text_fix"] = sum(1 for r in data.get("rules", [])
                              if r.get("type") == "text_fix" and (r.get("match") or "").strip())

    msg = ("applied logic — %d symbol, %d value, %d header alias, %d text-fix"
           % (applied["symbol_keyword"], applied["value_rule"],
              applied["header_alias"], applied["text_fix"]))
    if errors:
        msg += "  ⚠ NOT applied (fix this): " + "; ".join(errors)
    return msg


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        print(apply())
    else:
        d = load()
        print("store:", PATH)
        print("rules:", len(d["rules"]), "| notes:", len(d["notes"]), "chars")
        for r in d["rules"]:
            print("  ", r)
