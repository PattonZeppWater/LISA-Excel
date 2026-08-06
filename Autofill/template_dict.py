"""
template_dict.py — load the authoritative IDP template ground truth.

The schema in Handoff/template_dictionary.json is the single source of truth for
column positions, enums, and rules. Both the extractor and any AI working on this
project load it here instead of reconstructing the schema from memory.

    from template_dict import DICT, enum, verify_against_code
    DICT["enums"]["fill_type_ct1"]        # the Type domain at Wire Ct 1
    enum("conduit_type")                  # -> ['XXX','PVC',...]
    verify_against_code()                 # Milestone-0 done-check
"""
from __future__ import annotations

import json
import os
import sys

_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_JSON = os.path.join(_HERE, "Handoff", "template_dictionary.json")


def load():
    with open(_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


DICT = load()


def enum(name):
    """Return an enum list by name, e.g. enum('conduit_type')."""
    return DICT["enums"][name]


def verify_against_code():
    """Milestone-0 done-check: confirm the JSON's FillIndex write columns agree
    with idp_write's CI_*/FI_* constants. Returns (ok, list_of_mismatches)."""
    problems = []
    try:
        import idp_write as w
    except Exception as e:  # pragma: no cover
        return False, [f"could not import idp_write: {e}"]

    fi = DICT["sheets"]["FillIndex"]["write_columns_1_indexed"]
    # Explicit tuple/scalar comparisons against the known constants:
    pairs = [
        ("color_1_4",  tuple(fi["color_1_4"]),  tuple(w.FI_COLOR)),
        ("s_tag_1_4",  tuple(fi["s_tag_1_4"]),  tuple(w.FI_STAG)),
        ("s_term_1_4", tuple(fi["s_term_1_4"]), tuple(w.FI_STERM)),
        ("d_symbol",   fi["d_symbol"],          w.FI_DSYM),
        ("d_tag_1_4",  tuple(fi["d_tag_1_4"]),  tuple(w.FI_DTAG)),
        ("d_term_1_4", tuple(fi["d_term_1_4"]), tuple(w.FI_DTERM)),
        ("wl_mode_1_4", tuple(fi["wl_mode_1_4"]), tuple(w.FI_MODES)),
    ]
    for name, from_json, from_code in pairs:
        if from_json != from_code:
            problems.append(f"{name}: json={from_json} vs code={from_code}")

    labels_json = {int(k): v for k, v in fi["wire_label_1_4"].items()}
    if labels_json != w.FI_LABELS:
        problems.append(f"wire_label_1_4: json={labels_json} vs code={w.FI_LABELS}")

    return (len(problems) == 0), problems


if __name__ == "__main__":
    ok, probs = verify_against_code()
    print("template_dictionary.json <-> idp_write constants:",
          "OK" if ok else "MISMATCH")
    for p in probs:
        print("  -", p)
