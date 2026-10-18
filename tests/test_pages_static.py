"""Static checks over the HTML pages.

Each test names a defect the 2026-10-18 review found on B4 and keeps it from
coming back.  Every page, phase.html included, now passes every check.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
ALL_PAGES = sorted(p.name for p in STATIC.glob("*.html"))
PAGES = ALL_PAGES


def _text(name):
    return (STATIC / name).read_text()


@pytest.mark.parametrize("page", ALL_PAGES)
def test_common_js_loads_exactly_once(page):
    """A second classic <script> redeclares ``class TimestdAPI`` and throws."""
    n = len(re.findall(r'<script[^>]+src="/static/js/common\.js"', _text(page)))
    assert n == 1, f"{page} loads common.js {n} times"


WORD_CHU = re.compile(r"\bCHU\b")


@pytest.mark.parametrize("page", PAGES)
def test_no_chu(page):
    """CHU left the fleet; no tile, link, option, default or caption keeps it."""
    hits = [ln.strip() for ln in _text(page).splitlines() if WORD_CHU.search(ln)]
    assert not hits, hits[:5]


@pytest.mark.parametrize("page", PAGES)
def test_no_local_time(page):
    """toLocale* prints the browser's zone with no marker; pages print UTC."""
    assert "toLocale" not in _text(page)


# A JS Date handed to plotly draws in the browser's local zone.  These are
# the shapes the pages used to build x arrays from Dates.
DATE_INTO_PLOT = re.compile(r"=>\s*new Date\([^()]*(\([^()]*\))?[^()]*\)\s*\)|push\(new Date\(")


@pytest.mark.parametrize("page", PAGES)
def test_no_date_objects_into_plot_arrays(page):
    hits = DATE_INTO_PLOT.findall(_text(page))
    assert not hits, hits


@pytest.mark.parametrize("page", PAGES)
def test_title_names_station_web(page):
    title = re.search(r"<title>(.*?)</title>", _text(page), re.S).group(1)
    assert "station-web" in title and "hf-timestd" not in title, title


def test_receiver_location_not_hard_coded():
    iono = _text("ionosphere.html")
    for literal in ("Missouri", "EM38", "lat: 38.9", "lon: -92.1"):
        assert literal not in iono, literal
    dtec = _text("dtec.html")
    assert "WWV (SW)" not in dtec and "BPM (W)" not in dtec
    assert "°E`" not in _text("station.html")


def test_docs_page_has_no_dead_calls():
    docs = _text("docs.html")
    assert "/api/logs" not in docs
    assert "log-panel" not in docs
    assert docs.count("fusion/latest") == 1


def test_health_subtitle_says_freshness():
    health = _text("health.html")
    assert "Process and channel monitoring" not in health
    assert "freshness" in health.split('class="header-subtitle"')[1].split("</div>")[0].lower()


def test_index_counts_channel_names():
    """/api/health/system lists channel NAMES; filtering on .status read 0 forever."""
    index = _text("index.html")
    assert "ch.status === 'active'" not in index
    assert "health.channels" in index


def test_phase_summary_cards_escape_database_values():
    """N2: channel, station and colour come from the database; escape them
    like every other page does."""
    text = _text("phase.html")
    for field in ("ch.channel", "ch.station", "ch.color"):
        assert f"${{{field}}}" not in text, field
        assert f"escapeHtml({field})" in text, field


def test_docs_unknown_widget_is_escaped():
    assert "${widgetType}" not in _text("docs.html")
