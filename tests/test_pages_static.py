"""Static checks over the HTML pages.

Each test names a defect the 2026-10-18 review found on B4 and keeps it from
coming back.  phase.html belongs to separate work and is excluded where a
check would reach into its content.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
ALL_PAGES = sorted(p.name for p in STATIC.glob("*.html"))
PAGES = [p for p in ALL_PAGES if p != "phase.html"]


def _text(name):
    return (STATIC / name).read_text()


@pytest.mark.parametrize("page", ALL_PAGES)
def test_common_js_loads_exactly_once(page):
    """A second classic <script> redeclares ``class TimestdAPI`` and throws."""
    n = len(re.findall(r'<script[^>]+src="/static/js/common\.js"', _text(page)))
    assert n == 1, f"{page} loads common.js {n} times"
