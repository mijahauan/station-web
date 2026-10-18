"""phase.html: one combined fetch for the charts, and no CHU left behind."""
import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
        / "phase.html").read_text()


def test_page_names_no_chu_channel():
    # CHU left the fleet; the DC-phase list must come from the data
    assert "CHU" not in PAGE


def test_charts_share_one_combined_request():
    calls = re.findall(r"api\.get\('(/phase/[a-z]+)'", PAGE)
    assert "/phase/combined" in calls
    # the three heavy series no longer go out as separate parallel calls
    for heavy in ("/phase/doppler", "/phase/scintillation"):
        assert heavy not in calls, calls
