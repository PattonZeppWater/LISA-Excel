"""
idp_layouts.py — learned DOCUMENT-LAYOUT recognition + fast targeting.

Two jobs:
  1) TARGETING (speed): decide from a file's FOLDER + NAME + first-page text whether it's
     even worth deep-inspecting, and its likely ROLE — so a whole project folder isn't
     OCR/full-scanned file-by-file. Irrelevant subtrees (quotes, estimating, photos,
     correspondence) are skipped; likely sources are recognized by signature.
  2) MEMORY: remember what a confirmed layout looks like (a compact token FINGERPRINT per
     role) and match new files to it by similarity — so the exe gets better at spotting
     an EDC three-line / PLC-I/O / panelboard / cut sheet / conduit-cable schedule across
     projects, the way the skill recognizes them by convention.

Fingerprint = the set of significant UPPERCASE tokens on the first pages. Match = overlap
of a file's tokens with each role's signature tokens (seed + learned). Fast: first pages
only, no OCR, no whole-document scan.
"""
import os
import re
import json

_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                    or os.path.expanduser("~"), "AIC_IDP_Extractor")
_PATH = os.path.join(_DIR, "layouts.json")

# ── seed signatures: the distinctive tokens each document TYPE shows on its face ──
SEED = {
    "conduit_schedule": {"CONDUIT", "SCHEDULE", "FROM", "TO", "SIZE", "TYP", "ROUTING",
                         "CABLES", "TRADE", "MATERIAL"},
    "cable_schedule": {"CABLE", "SCHEDULE", "CONDUCTOR", "INSUL", "SIZE", "XLPE", "AWG",
                       "PAIR", "TSP", "GND"},
    "edc_three_line": {"THREE", "LINE", "ONE", "DIAGRAM", "MSB", "ATS", "XFMR", "MCC",
                       "BREAKER", "FEEDER", "KVA", "SWITCHBOARD"},
    "edc_plc_io": {"PLC", "TBDI", "TBAI", "TBDO", "TBAO", "DIGITAL", "ANALOG", "INPUT",
                   "OUTPUT", "RACK", "CHANNEL", "MODULE", "SPARE"},
    "panelboard": {"PANELBOARD", "PANEL", "BREAKER", "POLE", "CKT", "CIRCUIT", "TRIP",
                   "BUS", "MAIN", "POW", "LINE", "NEUTRAL", "AMP"},
    "cut_sheet": {"DATASHEET", "DATA", "SHEET", "CATALOG", "SUBMITTAL", "DIMENSIONS",
                  "SPECIFICATIONS", "MODEL", "PART", "TYPICAL", "MOUNTING", "RATED"},
    "finished_idp": {"INTERCONNECTION", "DIAGRAM", "IDP", "SOURCE", "DESTINATION", "FIELD",
                     "SUPPORTING", "DOCUMENTS", "DEVIATIONS"},
    "cover_letter": {"SUBMITTAL", "COVER", "LETTER", "TRANSMITTAL", "REVIEW", "COMMENTS",
                     "ACCEPTED", "REVISE", "RESUBMIT"},
}

# ── folder / filename relevance: which parts of a project tree hold IDP sources ──
# BOOST wins over SKIP (so 'Vendor Cut Sheets' under 'Vendor Quotes' is still inspected).
_FOLDER_BOOST = ("plans", "bid document", "submittal", "engineering", "edc", "cut sheet",
                 "cutsheet", "drawings", "dwg", "idp", "specification", "electrical",
                 "one-line", "three-line", "panelboard")
_FOLDER_SKIP = ("estimating", "change order", "quotes received", "quotes extracted",
                "quote received", "pricing", "correspondence", "email", "photos", "photo",
                "pmo", "billing", "invoice", "rfp ", "proposal", "schedule of values",
                "safety", "closeout", "meeting", "pre-lien", "lien")
_NAME_SKIP_EXT = (".jpg", ".jpeg", ".png", ".docx", ".doc", ".pptx", ".zip", ".msg", ".eml",
                  ".dwg", ".dwf", ".csv", ".txt")


def load():
    try:
        with open(_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"learned": {}, "examples": []}


def save(data):
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except Exception:
        pass


def _first_text(path, pages=2):
    try:
        import fitz
        d = fitz.open(path)
        try:
            return " ".join(d[i].get_text("text") for i in range(min(len(d), pages)))
        finally:
            d.close()
    except Exception:
        return ""


def fingerprint(path):
    """Compact signature of a file's face: the set of significant UPPERCASE tokens on the
    first pages, plus a couple of structural flags. Cheap — first pages only."""
    t = _first_text(path)
    toks = {w for w in re.findall(r"[A-Z]{3,}", t.upper())}
    flags = set()
    if re.search(r"\b\d{2}\.\d{3,4}-\d+", t):
        flags.add("HAS_DWG_NUM")
    if re.search(r"ADVANCED INTEGRATION|W\.?\s*M\.?\s*LYLES", t.upper()):
        flags.add("HAS_AIC")
    return {"tokens": toks, "flags": flags}


def _sig_for(role, learned):
    """Signature token set for a role = seed ∪ learned."""
    s = set(SEED.get(role, set()))
    s |= set(learned.get(role, []))
    return s


def best_role(fp, min_seed=4):
    """Best matching role for a fingerprint. Score weights SEED tokens (the stable
    layout signature) double vs learned tokens, and CONFIDENCE gates on the SEED overlap
    alone — so one over-fit learned example can't make an unrelated doc (e.g. a cover
    letter that borrows device names) masquerade as a real layout. Returns
    (role, score, confident). Fast: pure set overlap, first-page only."""
    learned = load().get("learned", {})
    toks = fp.get("tokens", set())
    flags = fp.get("flags", set())
    best, best_score, best_seed = None, 0, 0
    for role in SEED:
        seed_n = len(toks & SEED[role])
        learn_n = len(toks & set(learned.get(role, [])))
        score = 2 * seed_n + learn_n
        if role.startswith("edc") and "HAS_DWG_NUM" in flags:
            score += 1
        if role == "cut_sheet" and "HAS_AIC" in flags:
            score -= 3                  # an AIC title block ⇒ not a vendor cut sheet
        if score > best_score:
            best, best_score, best_seed = role, score, seed_n
    return best, best_score, (best_seed >= min_seed)


def learn(path, role):
    """Remember a CONFIRMED example's distinctive tokens for `role`, so similar layouts
    are recognized faster next time. Keeps the learned token set bounded."""
    if role not in SEED:
        return
    fp = fingerprint(path)
    # distinctive = tokens not already generic to many roles; keep it lean
    generic = set()
    for r, s in SEED.items():
        if r != role:
            generic |= s
    keep = [w for w in (fp["tokens"] - generic) if len(w) >= 4][:40]
    data = load()
    cur = set(data.setdefault("learned", {}).get(role, []))
    cur |= set(keep)
    data["learned"][role] = sorted(cur)[:200]
    ex = data.setdefault("examples", [])
    ex.append({"role": role, "name": os.path.basename(path),
               "tokens": sorted(fp["tokens"])[:60]})
    data["examples"] = ex[-300:]
    save(data)


def folder_relevance(path):
    """'skip' | 'boost' | 'normal' for a file, from its folder path + extension — so
    irrelevant subtrees (quotes, photos, estimating) are never deep-inspected. BOOST is
    checked BEFORE skip, so a needed 'Cut Sheets' folder nested under 'Vendor Quotes'
    still gets inspected."""
    low = path.replace("\\", "/").lower()
    if os.path.splitext(low)[1] in _NAME_SKIP_EXT:
        return "skip"
    if any(k in low for k in _FOLDER_BOOST):
        return "boost"
    if any(k in low for k in _FOLDER_SKIP):
        return "skip"
    return "normal"
