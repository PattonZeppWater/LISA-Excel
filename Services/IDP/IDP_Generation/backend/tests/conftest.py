"""Pytest setup for the IDP backend pure-logic harness (Slice 1).

These tests cover the AutoCAD/Excel-free logic: parser grouping & mapping,
workbook_mapper fill derivation, and the pure autocad_bridge helpers.  No AutoCAD
or Excel app is required -- they run anywhere the venv (with pywin32) is present.
Each test corresponds to a bug we actually hit, so they double as a regression net.
"""
import os
import sys
import json

import pytest

# Make `from services import ...` work regardless of pytest's working dir.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def dup_instrument():
    """Parsed JSON of a real workbook whose conduit S01AIT003 holds two identical
    pasted 4-term AIT-003 loops (4 fill rows). Exercises continuation grouping."""
    with open(os.path.join(_FIXTURES, "dup_instrument.json"), encoding="utf-8") as fh:
        return json.load(fh)


def fill_row(**over):
    """Build a fill-row dict with sensible defaults; override per test."""
    row = {
        "Cond_Tag": "C1",
        "Wire_Count": 1,
        "Wire_Type": "POWER",
        "Src_TermBlockDesc": "TB-TB_Square_L",
        "Dst_TermBlockDesc": "TB-TB_Square_R",
    }
    row.update(over)
    return row
