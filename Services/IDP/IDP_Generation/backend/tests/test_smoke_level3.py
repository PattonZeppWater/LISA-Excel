"""Pytest wrapper for the Level-3 AutoCAD smoke test.

Skips automatically when AutoCAD isn't running, so it's safe in the normal
pure-Python run and exercises the real render path when AutoCAD is open.
"""
import pytest
from smoke_render_plan import run_smoke, autocad_running


@pytest.mark.skipif(not autocad_running(), reason="AutoCAD not running")
def test_render_plan_matches_dwg():
    ok, report = run_smoke(verbose=False)
    assert ok, "Level-3 smoke failed:\n" + "\n".join(report)
