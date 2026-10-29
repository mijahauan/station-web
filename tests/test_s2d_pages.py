"""Six pages instead of thirteen (stage 2 design §6), and the products §7 adds.

- NAV_ITEMS lists exactly Status, Timing, Propagation, Ionosphere, Test
  signal and GRAPE; Documentation closes the top bar on every station
  (Michael, 2026-10-29: in the bar, not a small footer link).
- Every old URL keeps working: it redirects to the section it became, and
  its nav entry lights up.  No page is left without a nav entry, a
  redirect, or a link from a page that has one.
- station.html?id=X lands on the Propagation page with the filter at X.
- The builders the new pages use escape what they insert, label GNSS VTEC
  as the reference, centre the path map on the receiver, and hide series
  no product backs.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
JS = STATIC / "js"
COMMON = JS / "common.js"
PAGES = sorted(p.name for p in STATIC.glob("*.html"))

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = """
const window = globalThis;
globalThis.location = { origin: 'http://x', pathname: '/', search: '' };
globalThis.document = {
  readyState: 'loading', title: 'Page - station-web',
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
};
"""


def _run(expr_js: str, *extra: str):
    script = PRELUDE + COMMON.read_text()
    for name in extra:
        script += "\n" + (JS / name).read_text()
    script += f"\nconsole.log(JSON.stringify({expr_js}));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       env={"TZ": "America/Chicago", "PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


SIX = ["Station", "Check", "Status", "Timing", "Propagation", "Ionosphere", "Test signal", "GRAPE",
       "Documentation"]

# old URL -> (where it lands, the nav label it lights up)
RETIRED = {
    "phase.html": ("/static/ionosphere.html#phase", "Ionosphere"),
    "dtec.html": ("/static/ionosphere.html#dtec", "Ionosphere"),
    "ionogram.html": ("/static/propagation.html#time-of-flight", "Propagation"),
    "chrony.html": ("/static/metrology.html#chrony", "Timing"),
    "station.html": ("/static/propagation.html", "Propagation"),
}


# ── the nav and the footer ───────────────────────────────────────────────

@needs_node
def test_nav_holds_the_core_and_science_pages():
    items = _run("NAV_ITEMS.concat(SCIENCE_NAV_ITEMS, TRAILING_NAV_ITEMS).map(i => [i.label, i.href])")
    assert [label for label, _ in items] == SIX
    for _, href in items:
        assert href in ("/", "/check") or (STATIC / href.removeprefix("/static/")).is_file(), href


@needs_node
def test_documentation_closes_the_top_bar_and_leaves_the_footer():
    core, full, footer = _run("[siteNavHTML('/', false), siteNavHTML('/'), siteFooterHTML('/')]")
    for nav in (core, full):
        assert 'href="/static/docs.html"' in nav and ">Documentation<" in nav
        assert nav.rstrip().endswith(">Documentation</a>"), "Documentation is the bar's last entry"
    assert "docs.html" not in footer
    assert 'href="/commands"' in footer


@needs_node
def test_footer_renders_on_every_page_through_common_js():
    # every page loads common.js (test_pages_static), and common.js fills the footer itself
    src = COMMON.read_text()
    assert "renderSiteFooter" in src and "DOMContentLoaded" in src
    assert _run("typeof window.renderSiteFooter") == "function"


@needs_node
@pytest.mark.parametrize("page,label", [
    ("ionosphere.html", "Ionosphere"), ("propagation.html", "Propagation"),
    ("metrology.html", "Timing"), ("health.html", "Status"),
    ("test_signal.html", "Test signal"), ("grape.html", "GRAPE"),
] + [(p, lab) for p, (_, lab) in RETIRED.items()])
def test_each_url_lights_up_its_nav_entry(page, label):
    html = _run(f"siteNavHTML('/static/{page}')")
    current = re.findall(r'aria-current="page">([^<]+)</a>', html)
    assert current == [label], (page, current)


@needs_node
def test_phase_route_lights_up_ionosphere():
    html = _run("siteNavHTML('/phase')")
    assert re.findall(r'aria-current="page">([^<]+)</a>', html) == ["Ionosphere"]


# ── old URLs ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", [p for p in RETIRED if p != "station.html"])
def test_retired_page_redirects_to_its_section(page):
    target, _ = RETIRED[page]
    text = (STATIC / page).read_text()
    assert f'content="0; url={target}"' in text
    assert f'href="{target}"' in text
    assert "data-site-nav" in text


def test_station_page_redirects_with_its_filter():
    text = (STATIC / "station.html").read_text()
    assert 'content="0; url=/static/propagation.html"' in text
    assert "retiredStationTarget(" in text
    assert "location.replace" in text


@needs_node
def test_station_query_maps_to_the_propagation_filter():
    out = _run("[retiredStationTarget('?id=WWV'), retiredStationTarget('?id=wwvh'),"
               " retiredStationTarget('?id=BPM&x=1'), retiredStationTarget('?id=<script>'),"
               " retiredStationTarget(''), retiredStationTarget('?id=CHU')]")
    assert out == ["/static/propagation.html?station=WWV",
                   "/static/propagation.html?station=WWVH",
                   "/static/propagation.html?station=BPM",
                   "/static/propagation.html", "/static/propagation.html",
                   "/static/propagation.html"]


def test_phase_route_redirects(client):
    r = client.get("/phase", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"] == "/static/ionosphere.html#phase"


@pytest.mark.parametrize("page", PAGES)
def test_every_static_page_is_served(client, page):
    assert client.get(f"/static/{page}").status_code == 200


def test_no_orphan_pages():
    """Each page has a nav entry, redirects, or is linked from a page that has one."""
    nav_src = COMMON.read_text()
    nav_hrefs = set(re.findall(r"href: '/static/([\w.]+)'", nav_src)) | {"index.html", "home.html", "check.html", "commands.html"}
    footer = set(re.findall(r"href: '/static/([\w.]+)'", nav_src.split("SITE_FOOTER_LINKS")[1]))
    linked = set()
    for p in nav_hrefs:
        if (STATIC / p).is_file():
            linked |= set(re.findall(r'href="/static/([\w.]+)', (STATIC / p).read_text()))
    for page in PAGES:
        text = (STATIC / page).read_text()
        redirect = 'http-equiv="refresh"' in text
        assert page in nav_hrefs or redirect or page in footer or page in linked, page


def test_no_page_links_a_retired_url_except_its_stub():
    retired = set(RETIRED)
    for page in PAGES:
        if page in retired:
            continue
        text = (STATIC / page).read_text()
        for r in retired:
            assert f"/static/{r}" not in text, (page, r)
    for name in ("propagation.js", "ionosphere.js", "phase-view.js", "dtec-view.js", "tof-view.js"):
        text = (JS / name).read_text()
        for r in retired:
            assert f"/static/{r}" not in text, (name, r)


# ── the Propagation page ─────────────────────────────────────────────────

def test_propagation_page_sections():
    text = (STATIC / "propagation.html").read_text()
    for anchor in ("snr", "modes", "muf", "detections", "map", "time-of-flight"):
        assert f'id="{anchor}"' in text, anchor
    for btn in ("ALL", "WWV", "WWVH", "BPM"):
        assert f'data-station="{btn}"' in text
    for src in ("common.js", "physics-views.js", "propagation.js", "tof-view.js"):
        assert f'<script src="/static/js/{src}"></script>' in text


@needs_node
def test_station_filter_reads_the_query():
    out = _run("[propStationFromQuery('?station=WWVH'), propStationFromQuery('?station=bpm'),"
               " propStationFromQuery('?station=CHU'), propStationFromQuery(''),"
               " propStationFromQuery('?station=%3Cb%3E')]", "propagation.js")
    assert out == ["WWVH", "BPM", "ALL", "ALL", "ALL"]


@needs_node
def test_path_map_is_azimuthal_equidistant_on_the_receiver():
    fig = _run("propPathMapFigure({lat: 38.9, lon: -92.3, short: 'AC0G', label: 'rx'},"
               " {WWV: {lat: 40.68, lon: -105.04, label: 'WWV'},"
               "  BPM: {lat: 34.95, lon: 109.56, label: 'BPM'}}, 'BPM')", "propagation.js")
    proj = fig["layout"]["geo"]["projection"]
    assert proj["type"] == "azimuthal equidistant"
    assert proj["rotation"]["lon"] == -92.3 and proj["rotation"]["lat"] == 38.9
    paths = [t for t in fig["data"] if t.get("mode") == "lines"]
    # great circle drawn by plotly between the two end points
    assert {t["name"] for t in paths} == {"WWV path", "BPM path"}
    bpm = next(t for t in paths if t["name"] == "BPM path")
    wwv = next(t for t in paths if t["name"] == "WWV path")
    assert bpm["line"]["width"] > wwv["line"]["width"]          # the filtered station stands out


@needs_node
def test_funnel_escapes_and_filters():
    data = {"available": True, "reasons_order": ["corr_snr_low", "<img src=x>"],
            "broadcasts": [
                {"channel": "SHARED_10000", "station": "WWV", "frequency_mhz": 10, "attempts": 10,
                 "detected": 6, "rejected": 4, "detection_rate": 0.6,
                 "reasons": {"corr_snr_low": 3, "<img src=x>": 1}},
                {"channel": "<b>X</b>", "station": "BPM", "frequency_mhz": 10, "attempts": 5,
                 "detected": 0, "rejected": 5, "detection_rate": 0, "reasons": {"corr_snr_low": 5}}]}
    j = json.dumps(data)
    html_all, html_bpm, fig = _run(
        f"[propFunnelTableHTML({j}, 'ALL'), propFunnelTableHTML({j}, 'BPM'),"
        f" propFunnelFigure({j}, 'ALL')]", "propagation.js")
    assert "<img" not in html_all and "&lt;img src=x&gt;" in html_all
    assert "<b>X</b>" not in html_all and "&lt;b&gt;X&lt;/b&gt;" in html_all
    assert "WWV" not in html_bpm.replace("WWVH", "")
    names = [t["name"] for t in fig["data"]]
    assert names[0] == "detected" and "corr_snr_low" in names
    assert all(t["orientation"] == "h" for t in fig["data"])


@needs_node
def test_muf_and_mode_figures():
    stats = {"available": True, "modes_order": ["1E", "1F", "2F", "3F", "GW", "unknown"],
             "stations": {"WWV": {"time": ["2026-10-18T01:00:00Z", "2026-10-18T02:00:00Z"],
                                  "muf_mhz": [24.4, None], "muf_confidence": [0.7, None],
                                  "n_observations": [55, 40],
                                  "mode_probability": {"1E": [0.1, 0], "1F": [0.7, 1], "2F": [0.2, 0],
                                                       "3F": [0, 0], "GW": [0, 0], "unknown": [0, 0]}},
                          "WWVH": {"time": ["2026-10-18T01:00:00Z"], "muf_mhz": [18.0],
                                   "muf_confidence": [0.5], "n_observations": [30],
                                   "mode_probability": {k: [0.5 if k == "1F" else 0.1] for k in
                                                        ["1E", "1F", "2F", "3F", "GW", "unknown"]}}}}
    j = json.dumps(stats)
    muf_all, muf_wwvh, modes = _run(
        f"[propMufFigure({j}, 'ALL'), propMufFigure({j}, 'WWVH'), propModeFigure({j}, 'WWV')]",
        "propagation.js")
    assert {t["name"] for t in muf_all["data"]} == {"WWV", "WWVH"}
    assert [t["name"] for t in muf_wwvh["data"]] == ["WWVH"]
    assert muf_all["data"][0]["line"]["color"].startswith("#")
    assert [t["name"] for t in modes["data"]] == ["1E", "1F", "2F", "3F", "GW", "unknown"]
    assert all(t.get("stackgroup") == "modes" for t in modes["data"])


# ── the Ionosphere page ──────────────────────────────────────────────────

def test_ionosphere_page_sections():
    text = (STATIC / "ionosphere.html").read_text()
    for anchor in ("space-weather", "phase", "dtec", "tec"):
        assert f'id="{anchor}"' in text, anchor
    for src in ("common.js", "physics-views.js", "ionosphere.js", "phase-view.js", "dtec-view.js"):
        assert f'<script src="/static/js/{src}"></script>' in text
    # Doppler first, then phase variability and S4
    order = [text.index(i) for i in ('id="doppler-plot"', 'id="scintillation-plot"', 'id="s4-plot"')]
    assert order == sorted(order)
    assert 'id="doppler-ruler"' in text


@needs_node
def test_space_weather_tiles():
    cur = {"available": True, "kp": 2.3, "kp_time": "2026-10-18T12:00:00Z", "kp_source": "gfz",
           "f107": 142.0, "f107_time": "2026-10-18T00:00:00Z", "f107_source": "<i>swpc</i>",
           "ap": None, "ap_time": None, "age_s": 60, "stale": False}
    html, none = _run(f"[ionoSpaceWeatherTilesHTML({json.dumps(cur)}),"
                      " ionoSpaceWeatherTilesHTML({available: false, reason: 'no cache'})]",
                      "ionosphere.js")
    for label in ("Kp", "F10.7", "Ap"):
        assert f">{label}<" in html
    assert "2.3" in html and "142" in html
    assert "&lt;i&gt;swpc&lt;/i&gt;" in html and "<i>swpc" not in html
    assert "not available" in html                    # Ap absent
    assert "no cache" in none


@needs_node
def test_series_with_no_product_hide():
    out = _run("[ionoSeriesAvailable({available: false, data: []}, {available: false, data: []}),"
               " ionoSeriesAvailable({timestamps: ['2026-10-18T00:00:00Z'], flux: [1e-6]}, null),"
               " ionoSeriesAvailable(null, {timestamps: ['x'], kp: [3]})]", "ionosphere.js")
    assert out == [False, True, True]


@needs_node
def test_tec_chart_overlays_vtec_as_the_reference():
    tec = {"paths": {"WWV": {"timestamps": ["2026-10-18T01:00:00Z"], "tec_tecu": [14.7],
                             "residual_rms_ms": [0.1]}}}
    vtec = {"available": True, "label": "GNSS VTEC over the receiver (reference)",
            "time": ["2026-10-18T01:00:00Z"], "vtec_tecu": [12.0], "n_satellites": [9]}
    fig, novtec = _run(f"[ionoTecFigure({json.dumps(tec)}, {json.dumps(vtec)}),"
                       f" ionoTecFigure({json.dumps(tec)}, {{available: false, reason: 'none'}})]",
                       "ionosphere.js")
    ref = [t for t in fig["data"] if "reference" in t["name"].lower()]
    assert len(ref) == 1 and ref[0]["mode"] == "lines"
    assert ref[0]["line"]["color"] != "#56B4E9"
    assert any(t["name"] == "WWV" for t in fig["data"])
    assert not [t for t in novtec["data"] if "reference" in t["name"].lower()]
    assert "none" in json.dumps(novtec["layout"].get("annotations", []))


# ── WWVB on the Timing page ──────────────────────────────────────────────

@needs_node
def test_wwvb_panel_escapes_and_names_its_role():
    data = {"available": True, "file": "2026-10-18.jsonl", "role": "names the minute <b>",
            "age_s": 30, "n_malformed": 0,
            "passes": {"n": 4, "with_frames": 1, "last": {"ts": "2026-10-18T12:00:00Z",
                                                          "snr_db": 14.2}},
            "frames": {"n": 2, "plausible": 1,
                       "last_plausible": {"ts": "2026-10-18T12:00:00Z",
                                          "minute": "2026-10-18T12:01:00Z",
                                          "vs_wallclock_s": 59.8, "parity_errors": 0,
                                          "sync_errors": 0}},
            "recent_frames": [{"ts": "<script>", "minute": "x", "plausible": False,
                               "vs_wallclock_s": 1, "parity_errors": 7, "sync_errors": 3}]}
    html, missing = _run(f"[wwvbPanelHTML({json.dumps(data)}),"
                         " wwvbPanelHTML({available: false, reason: 'No WWVB ledger'})]",
                         "timing.js")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;" in html
    assert "2026-10-18T12:01:00Z" in html and "14.2" in html
    assert "No WWVB ledger" in missing


def test_timing_page_has_the_wwvb_section():
    text = (STATIC / "metrology.html").read_text()
    assert 'id="wwvb"' in text and 'id="wwvb-panel"' in text


def test_status_tiles_link_to_the_propagation_filter():
    text = (STATIC / "index.html").read_text()
    assert "/static/propagation.html?station=" in text


def test_path_map_draws_from_the_vendored_topojson():
    """plotly fetches coastlines from cdn.plot.ly unless told otherwise."""
    js = (JS / "propagation.js").read_text()
    assert "topojsonURL: '/static/vendor/topojson/'" in js
    assert (STATIC / "vendor" / "topojson" / "world_110m.json").is_file()


@pytest.mark.parametrize("name", ["propagation.js", "ionosphere.js", "phase-view.js"])
def test_redraw_never_wipes_a_live_plot(name):
    """Clearing a plotted div's DOM and then calling Plotly.react leaves an empty
    frame: react sees the old _fullLayout and draws nothing (the path map
    rendered blank after its second draw)."""
    text = (JS / name).read_text()
    for m in re.finditer(r"^(.*)el\.innerHTML = '';", text, re.M):
        assert "js-plotly-plot" in m.group(1), m.group(0)
    assert not re.search(r"getElementById\('[\w-]+-plot'\)\.innerHTML\s*=", text)


@needs_node
def test_plotly_names_and_labels_escape_database_strings():
    """Plotly renders a subset of HTML in trace names and category labels;
    every one built from a database string goes through escapeHtml."""
    data = {"available": True, "reasons_order": ["<img src=x>"],
            "broadcasts": [{"channel": "<b>X</b>", "station": "<i>S</i>", "frequency_mhz": 10,
                            "attempts": 5, "detected": 0, "rejected": 5, "detection_rate": 0,
                            "reasons": {"<img src=x>": 5}}]}
    stats = {"available": True, "modes_order": ["<b>M</b>"],
             "stations": {"<i>S</i>": {"time": ["2026-10-18T01:00:00Z"], "muf_mhz": [20.0],
                                       "muf_confidence": [0.5], "n_observations": [3],
                                       "mode_probability": {"<b>M</b>": [1.0]}}}}
    fig, muf, modes = _run(
        f"[propFunnelFigure({json.dumps(data)}, 'ALL'), propMufFigure({json.dumps(stats)}, 'ALL'),"
        f" propModeFigure({json.dumps(stats)}, 'ALL')]", "propagation.js")
    strings = []
    for f in (fig, muf, modes):
        for t in f["data"]:
            strings.append(t["name"])
            strings.extend(y for y in (t.get("y") or []) if isinstance(y, str))
    assert strings and not any("<" in s for s in strings), strings
    assert "&lt;img src=x&gt;" in strings


def test_trace_names_from_data_are_escaped_in_page_scripts():
    """The pages whose trace builders are closures: check the source."""
    phase = (STATIC / "js" / "phase-view.js").read_text()
    assert "return escapeHtml(`${s.channel} / ${s.label || s.station}`)" in phase
    for f, needle in (("dtec-view.js", "name: escapeHtml(s.label ||"),
                      ("ionosphere.js", "name: escapeHtml(vtec.label ||"),
                      ("tof-view.js", "name: escapeHtml(data.doppler_label ||")):
        assert needle in (STATIC / "js" / f).read_text(), f
