"""The Station page ("/") and the Check page ("/check"): served, wired to the
shared chrome, and listed first in the nav."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
COMMON = (STATIC / "js" / "common.js").read_text()


def test_root_serves_the_station_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == (STATIC / "home.html").read_text()


def test_check_serves_the_check_page(client):
    r = client.get("/check")
    assert r.status_code == 200
    assert r.text == (STATIC / "check.html").read_text()


def test_both_pages_carry_the_nav_and_status_js():
    for name in ("home.html", "check.html"):
        text = (STATIC / name).read_text()
        assert "data-site-nav" in text, name
        assert text.count('src="/static/js/status.js"') == 1, name
        assert text.count('src="/static/js/common.js"') == 1, name


def test_check_page_has_the_check_now_button():
    text = (STATIC / "check.html").read_text()
    assert re.search(r"<button[^>]*id=\"check-now\"[^>]*>\s*Check now", text)


def test_nav_lists_station_and_check_first():
    m = re.search(r"const NAV_ITEMS = \[(.*?)\];", COMMON, re.S)
    items = re.findall(r"href:\s*'([^']+)',\s*label:\s*'([^']+)'", m.group(1))
    assert items[:2] == [("/", "Station"), ("/check", "Check")]


def test_science_nav_items_wait_for_the_features_call():
    assert "/api/features" in COMMON
    assert "SCIENCE_NAV_ITEMS" in COMMON
    m = re.search(r"const NAV_ITEMS = \[(.*?)\];", COMMON, re.S)
    assert "Timing" not in m.group(1)


def test_pages_render_server_strings_without_innerhtml():
    js = (STATIC / "js" / "status.js").read_text()
    assert "innerHTML" not in js
    assert "toLocale" not in js


def test_counts_never_concatenate_null():
    js = (STATIC / "js" / "status.js").read_text()
    assert "${c.configured}" not in js and "${c.enabled}" not in js and "${c.active}" not in js
    assert "'unknown'" in js.split("function instancesText", 1)[1].split("}", 1)[0]


def test_station_page_has_the_stale_snapshot_banner():
    assert 'id="clients-warning"' in (STATIC / "home.html").read_text()
    js = (STATIC / "js" / "status.js").read_text()
    assert "renderTableWarning" in js and "sp-stale" in js


def test_viewer_port_is_coerced_with_number():
    js = (STATIC / "js" / "status.js").read_text()
    assert "Number(w.port)" in js
    assert "${w.port}" not in js


def test_check_now_polls_without_overlap():
    js = (STATIC / "js" / "status.js").read_text()
    assert "setInterval" not in js and "setTimeout(poll" in js


def test_each_check_line_links_its_explanation_page():
    js = (STATIC / "js" / "status.js").read_text()
    line = js.split("function checkLine", 1)[1].split("function renderCheck", 1)[0]
    assert "What this means" in line
    assert "/static/docs.html?root=checks&doc=${encodeURIComponent(ln.id + '.md')}" in line
    assert "innerHTML" not in line
