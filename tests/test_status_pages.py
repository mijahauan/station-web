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


def _js():
    return (STATIC / "js" / "status.js").read_text()


def _fn(name, js=None):
    """The source of one function in status.js, up to the next function."""
    body = (js or _js()).split(f"function {name}", 1)[1]
    return body.split("\n    function ", 1)[0]


def test_heartbeat_empty_spool_never_reads_not_running():
    js = _js()
    assert "not running" not in js
    hb = _fn("renderHeartbeat", js)
    assert "no_tick" in hb
    assert "no heartbeat tick waiting (ticks leave the station once uploaded)" in hb
    no_tick = hb.split("'no_tick'", 1)[1].split("} else", 1)[0]
    assert "sp-muted" in no_tick
    assert "'tick'" in hb and "h.verdict" in hb and "h.reason" in hb and "h.emitted_at" in hb
    assert "unavailable" in hb


def test_failed_snapshots_warn_in_table_and_footer():
    js = _js()
    m = re.search(r"const TABLE_SNAPSHOTS = \[(.*?)\];", js)
    assert "'check'" in m.group(1)
    assert "snapFailed(sn)" in _fn("renderTableWarning", js)
    footer = _fn("renderSnapshotFooter", js)
    assert "snapFailed" in footer and "failed" in footer and ".exit" in footer
    failed = _fn("snapFailed", js)
    assert "sn.failed" in failed and "sn.error" in failed


def test_unknown_columns_never_read_never_fetched_or_blank():
    js = _js()
    behind = _fn("behindText", js)
    assert behind.index("repo_known === false") < behind.index("never fetched")
    assert "`unknown, ${ago}`" in behind
    cell = _fn("checkCell", js)
    assert cell.index("if (!chk) return el('span', { cls: 'sp-amber', text: 'unknown' })") \
        < cell.index("'–'")


def test_no_instance_rows_reads_as_words_not_zeros():
    js = _js()
    assert "const NO_INSTANCES = 'no per-reporter instances';" in js
    assert "c.rows === 0" in _fn("instancesText", js)
    assert "sp-muted" in _fn("instancesCell", js)


def test_manifest_superset_is_green_and_not_listed_neutral():
    cell = _fn("manifestCell")
    assert "status === 'superset') return el('span', { cls: 'sp-green'" in cell
    assert "status === 'not_listed') return el('span', { cls: 'sp-muted'" in cell


def test_other_check_ids_get_no_explanation_link():
    line = _fn("checkLine")
    assert "!String(ln.id).startsWith('other.')" in line


def test_pages_refresh_every_minute_without_overlapping_check_now():
    js = _js()
    assert "const REFRESH_MS = 60000;" in js
    assert "setInterval" not in js
    every = _fn("refreshEvery", js)
    assert "setTimeout(" in every and "refreshEvery(load)" in every
    assert "if (!checkNowPolling)" in every
    assert "refreshEvery(loadStation)" in js and "refreshEvery(loadCheck)" in js
    now = _fn("checkNow", js)
    assert "checkNowPolling = true" in now and "checkNowPolling = false" in now
    assert "button.disabled = false;" not in now.replace(
        "const done = () => { button.disabled = false; checkNowPolling = false; };", "")
