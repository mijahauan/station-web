"""The Timing page on the measurement model (station-web Stage 2, §2-§4).

The page shows the registration in full, a strip of the tier in force and
its published sigma, every estimator's offset with its own sigma band, the
T3 estimate drawn as a median with a band and its wrong-lock suspects apart,
the stability of the T3 estimate, and the host clock (chrony folded in).
It drops the A-D grades, the "Steel Ruler" tiles, the browser-computed
weights and the GUM block, and claims nothing the station does not publish.
The builders live in js/timing.js and run here under node.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
COMMON = STATIC / "js" / "common.js"
TIMING_JS = STATIC / "js" / "timing.js"
PAGE = STATIC / "metrology.html"
MODEL = Path("/home/mjh/hamsci/repos/hf-timestd/docs/design/MEASUREMENT_MODEL.md")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = """
const window = globalThis;
globalThis.location = { origin: 'http://x', pathname: '/static/metrology.html' };
globalThis.document = {
  readyState: 'loading', title: 'Timing - station-web',
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
};
"""


def _run(expr_js: str):
    script = (PRELUDE + COMMON.read_text() + "\n" + TIMING_JS.read_text() +
              f"\nconsole.log(JSON.stringify({expr_js}));\n")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       env={"TZ": "America/Chicago", "PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _static_files():
    for p in sorted(STATIC.rglob("*")):
        if p.is_file() and "vendor" not in p.parts and p.suffix in (".html", ".js", ".css"):
            yield p


# ── what the page no longer says ────────────────────────────────────────

RETIRED = [
    (re.compile(r"\bgrade\b|grade-[a-d]\b|quality_grade|gradeColors", re.I), "A-D grades"),
    (re.compile(r"steel[ -]?ruler", re.I), "Steel Ruler tiles"),
    (re.compile(r"traceab", re.I), "traceability claim"),
    (re.compile(r"GUM[- ]compliant", re.I), "GUM-compliant claim"),
    (re.compile(r"validated against GPS VTEC", re.I), "VTEC validation claim"),
    (re.compile(r"±\s*0\.5\b|&plusmn;\s*0\.5\b"), "±0.5 ms claim"),
]


@pytest.mark.parametrize("pattern,what", RETIRED, ids=[w for _, w in RETIRED])
def test_no_retired_claims_left_in_static(pattern, what):
    hits = [f"{p.relative_to(STATIC)}:{n}: {ln.strip()[:80]}"
            for p in _static_files()
            for n, ln in enumerate(p.read_text().splitlines(), 1) if pattern.search(ln)]
    assert not hits, f"{what} still in static/:\n" + "\n".join(hits[:10])


def test_timing_page_drops_the_old_story():
    text = PAGE.read_text() + TIMING_JS.read_text()
    for gone in ("Inverse Variance", "w<sub>i</sub>", "The unknown we solve for",
                 "transmission time equation", "T_UTC(NIST)", "T<sub>UTC(NIST)</sub>",
                 "Criteria Checklist", "ISO GUM", "Kalman Filter State", "Process Noise",
                 "UTC Offset", "1 / (uncertainty", "totalWeight"):
        assert gone not in text, gone


# ── what it says instead ────────────────────────────────────────────────

def test_page_shows_the_full_registration_panel_on_top():
    page = PAGE.read_text()
    js = TIMING_JS.read_text()
    assert re.search(r"renderRegistrationPanel\('registration-panel', data, 'full'\)", js)
    # the panel comes before every chart on the page
    first_plot = min(page.index(i) for i in ('id="strip-plot"', 'id="t3-plot"', 'id="chrony-plot"'))
    assert page.index('id="registration-panel"') < first_plot


def test_section_titles():
    page = PAGE.read_text()
    for title in ("T3 estimate of t₀ (tick fusion)",
                  "Stability of the T3 estimate against the host clock",
                  "This measures tick noise in the T3 estimate, not the GPSDO.",
                  "host clock minus registration", "noselect witness", "Budget",
                  "Not published by this station yet"):
        assert title in page, title


def test_budget_links_measurement_model_section_6():
    page = PAGE.read_text()
    assert ('/static/docs.html?root=hf-timestd&amp;doc=design/MEASUREMENT_MODEL.md#6-the-budget'
            in page)


def test_docs_anchors_name_real_sections():
    """Every #anchor the page links into MEASUREMENT_MODEL.md names a heading."""
    if not MODEL.exists():
        pytest.skip("hf-timestd checkout not present")
    from station_web.routers.docs import _slug
    slugs = {_slug(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.*)$", MODEL.read_text(), re.M)}
    anchors = re.findall(r"MEASUREMENT_MODEL\.md#([a-z0-9-]+)", PAGE.read_text())
    assert anchors
    for a in anchors:
        assert a in slugs, a


def test_docs_page_honours_a_linked_document():
    docs = (STATIC / "docs.html").read_text()
    assert "URLSearchParams(window.location.search)" in docs
    assert "params.get('root')" in docs and "params.get('doc')" in docs
    # only a document the listing offers gets loaded
    assert "includes(params.get('doc'))" in docs


def test_how_it_works_is_short_and_plain():
    page = PAGE.read_text()
    block = page.split("How the timing works", 1)[1].split("</p>", 1)[0]
    text = re.sub(r"<[^>]+>", "", block).split(">", 1)[-1]
    sentences = [s for s in re.split(r"(?<=[.;])\s+", " ".join(text.split())) if s.strip()]
    assert 5 <= len(sentences) <= 9, sentences
    assert len(re.findall(r"\bis\b", text)) <= 1
    for idea in ("UTC instant", "registration", "ruler", "tiers", "D_clock", "chrony"):
        assert idea in text, idea


def test_page_has_no_typed_in_numbers_for_unpublished_values():
    page = PAGE.read_text()
    budget = page.split('card-title">Budget', 1)[1]
    assert not re.search(r"\d+(\.\d+)?\s*(ns|µs|ms)\b", budget)


def test_chrony_page_points_at_timing():
    chrony = (STATIC / "chrony.html").read_text()
    assert 'url=/static/metrology.html#chrony' in chrony
    assert 'href="/static/metrology.html#chrony"' in chrony
    assert 'id="chrony"' in PAGE.read_text()
    nav = re.search(r"const NAV_ITEMS = \[(.*?)\];", COMMON.read_text(), re.S).group(1)
    assert "label: 'Chrony'" not in nav
    assert "also: ['/static/chrony.html']" in nav


def test_adev_clears_its_spinner_and_draws_decade_ticks():
    js = TIMING_JS.read_text()
    adev = js.split("async function loadTimingAdev", 1)[1].split("\nasync function", 1)[0]
    assert "showLoading" not in adev
    assert "drawPlot('adev-plot'" in adev
    draw = js.split("function drawPlot", 1)[1].split("\n}", 1)[0]
    assert "innerHTML = ''" in draw


def test_page_window_reads_follow_the_selector():
    js = TIMING_JS.read_text()
    for url in ("/timing/registration/history", "/metrology/t3/history", "/chrony/envelope"):
        assert re.search(re.escape(url) + r"', \{ start: range\.start, end: range\.end \}", js), url


# ── builders, under node ────────────────────────────────────────────────

HIST = {
    "available": True, "n_rows": 6, "bucket_s": None, "n_points": 6,
    "time": ["2026-10-18T00:00:00Z", "2026-10-18T00:01:00Z", "2026-10-18T00:02:00Z",
             "2026-10-18T00:03:00Z", "2026-10-18T00:04:00Z", "2026-10-18T00:05:00Z"],
    "tier_active": ["T4", "T4", None, "T6", "T6", "T6"],
    "sigma_ns": [2.0e6, 2.0e6, None, 2300, 2400, 2500],
    "estimators": {
        "t6": {"offset_ms": [None, None, None, 0.002, 0.003, 0.002],
               "sigma_ms": [None, None, None, 0.002, 0.002, 0.002]},
        "t5": {"offset_ms": [None] * 6, "sigma_ms": [None] * 6},
        "t4": {"offset_ms": [0.01] * 6, "sigma_ms": [2.0] * 6},
        "t3": {"offset_ms": [-0.02] * 6, "sigma_ms": [1.2] * 6},
    },
    "transitions": [{"time": "2026-10-18T00:02:00Z", "from": "T4", "to": None},
                    {"time": "2026-10-18T00:03:00Z", "from": None, "to": "<b>T6</b>"}],
}


@needs_node
def test_strip_figure_bands_tiers_and_marks_transitions():
    out = _run(f"(() => {{ const f = registrationStripFigure({json.dumps(HIST)}, null);"
               " return [f.layout.yaxis.type, f.layout.yaxis.ticktext, f.layout.shapes,"
               " f.data.map(d => d.name), f.data[0].y, tierColor('T6'), tierColor('T4'),"
               " tierColor(null)]; })()")
    ytype, ticktext, shapes, names, sigma, c6, c4, cnone = out
    assert ytype == "log"
    assert "1 µs" in ticktext and "1 ms" in ticktext
    rects = [s for s in shapes if s["type"] == "rect"]
    lines = [s for s in shapes if s["type"] == "line"]
    assert [r["fillcolor"] for r in rects] == [c4, cnone, c6]
    assert rects[0]["x0"] == HIST["time"][0] and rects[0]["x1"] == HIST["time"][2]
    assert len(lines) == 2
    assert "transition" in names and "T6 in force" in names and "no tier in force" in names
    assert sigma[2] is None


@needs_node
def test_strip_transition_text_is_escaped():
    out = _run(f"registrationStripFigure({json.dumps(HIST)}, null).data"
               ".find(d => d.name === 'transition').text")
    assert "<b>" not in json.dumps(out) and "&lt;b&gt;T6&lt;/b&gt;" in out[1]


@needs_node
def test_estimator_overlay_draws_each_with_its_own_band():
    out = _run(f"(() => {{ const f = estimatorOverlayFigure({json.dumps(HIST)}, null);"
               " return f.data.map(d => [d.name || null, d.fill || null, d.line.color,"
               " d.showlegend === false]); })()")
    # T5 published nothing: three estimators, three traces each (low, high+fill, line)
    assert len(out) == 9
    named = [d[0] for d in out if d[0]]
    assert named == ["T6", "T4", "T3"]
    assert [d[1] for d in out].count("tonexty") == 3
    t6 = _run("tierColor('T6')")
    assert out[0][2] == out[1][2] == out[2][2] == t6


T3 = {
    "available": True, "n_rows": 5, "n_points": 3, "n_suspect": 2, "suspects_truncated": False,
    "time": ["2026-10-18T00:00:00Z", "2026-10-18T00:01:00Z", "2026-10-18T00:03:00Z"],
    "calibrated_ms": [0.3, 0.31, 0.32], "raw_ms": [14.3, 14.31, 14.32], "sigma_ms": [0.2, 0.2, 0.2],
    "suspects": {"time": ["2026-10-18T00:02:00Z", "2026-10-18T00:04:00Z"],
                 "value_ms": [10.3, 7.8], "raw_ms": [24.3, 21.8],
                 "residual_ms": [10.0, 7.5], "kind": ["lattice", "outlier"]},
    "calibration": {"offset_ms": 14.0, "min_ms": 13.9, "max_ms": 14.1, "n": 5},
}


@needs_node
def test_t3_figure_band_raw_and_suspects_apart():
    out = _run(f"(() => {{ const f = t3Figure({json.dumps(T3)}, null);"
               " return [f.data.map(d => [d.name || null, d.yaxis || 'y', d.mode,"
               " d.marker ? d.marker.symbol : null, (d.x || []).length]),"
               " !!f.layout.yaxis3]; })()")
    traces, has_raw_axis = out
    assert has_raw_axis
    names = [t[0] for t in traces if t[0]]
    assert any("calibrated" in n for n in names)
    raw = [t for t in traces if t[0] and "raw" in t[0]]
    assert raw and raw[0][1] == "y3"
    lattice = [t for t in traces if t[0] and "lattice" in t[0]]
    outlier = [t for t in traces if t[0] and "rolling median" in t[0]]
    assert lattice[0][3] == "x" and lattice[0][4] == 1 and lattice[0][2] == "markers"
    assert outlier[0][4] == 1
    # no per-point error bars anywhere
    assert "error_y" not in TIMING_JS.read_text()


@needs_node
def test_t3_facts_name_the_calibration_and_escape():
    evil = "<img src=x onerror=alert(1)>"
    latest = {"d_clock_ms": 0.31, "d_clock_raw_ms": 14.31, "uncertainty_ms": 0.22,
              "n_broadcasts": evil, "stations_used": ["WWV", evil], "timestamp": evil}
    html = _run(f"t3FactsHTML({json.dumps(latest)}, {json.dumps(dict(T3, n_suspect=evil))})")
    assert "<img" not in html
    assert "d_clock_raw_ms" in html and "d_clock_fused_ms" in html
    assert "+14.000 ms" in html and "raw minus calibrated" in html


ENV = {
    "available": True, "time": ["2026-10-18T00:00:30Z", "2026-10-18T00:01:30Z"],
    "label": "host clock minus registration",
    "ntp": {"min_ms": [-0.1, -0.2], "median_ms": [0.05, 0.0], "max_ms": [0.3, 0.2],
            "n_sources": [3, 3], "members": ["a", "b", "c"]},
    "witnesses": {"FUSE": {"offset_ms": [0.9, 0.8], "role": "noselect witness"},
                  "HPPS": {"offset_ms": [0.02, 0.03], "role": "noselect witness"}},
    "other_refclocks": {"<i>PPS</i>": {"offset_ms": [0.0, 0.0]}},
    "system_offset_ms": [0.1, 0.1],
}


@needs_node
def test_chrony_envelope_figure_collapses_the_pool_and_fixes_witness_colours():
    out = _run(f"(() => {{ const f = chronyEnvelopeFigure({json.dumps(ENV)}, null);"
               " return [f.data.map(d => [d.name || null, d.line.color, d.fill || null]),"
               " tierColor('T3'), tierColor('T6'), NTP_GREY]; })()")
    traces, c3, c6, grey = out
    named = [t for t in traces if t[0]]
    ntp = [t for t in named if t[0].startswith("NTP pool")]
    assert len(ntp) == 1 and ntp[0][1] == grey
    assert [t[2] for t in traces].count("tonexty") == 1
    fuse = [t for t in named if t[0].startswith("FUSE")][0]
    hpps = [t for t in named if t[0].startswith("HPPS")][0]
    assert fuse[1] == c3 and hpps[1] == c6
    for w in (fuse, hpps):
        assert "noselect witness" in w[0] and "host clock minus registration" in w[0]
    assert not any("<i>" in t[0] for t in named)


@needs_node
def test_chrony_rows_escape_and_flag_a_voting_witness():
    evil = "<script>x</script>"
    sources = [
        {"name": evil, "mode": evil, "state": evil, "stratum": evil, "offset_us": 12.0,
         "error_us": 1.0, "std_dev_us": 2.0, "frequency_ppm": 0.1, "freq_skew_ppm": 0.01,
         "n_samples": evil, "reach": 255},
        {"name": "HPPS", "mode": "#", "state": "*", "stratum": 0, "offset_us": 20.0,
         "error_us": 1.0, "std_dev_us": 2.0, "frequency_ppm": None, "freq_skew_ppm": None,
         "n_samples": 6, "reach": 255},
        {"name": "FUSE", "mode": "#", "state": "-", "stratum": 0, "offset_us": 900.0,
         "error_us": 1.0, "std_dev_us": 2.0, "frequency_ppm": None, "freq_skew_ppm": None,
         "n_samples": 6, "reach": 255},
    ]
    html = _run(f"chronySourceRowsHTML({json.dumps(sources)})")
    assert "<script>" not in html and "&lt;script&gt;" in html
    rows = html.split("</tr>")
    assert "yet voting" in rows[1] and "noselect witness" in rows[2]
    track = _run("chronyTrackingHTML({tracking: {reference_id: '<b>x</b>', sys_offset_us: 1,"
                 " rms_offset_us: 2}, age_s: 5, stale: true, timestamp_utc: '<i>t</i>'})")
    assert "<b>" not in track and "<i>" not in track and "STALE" in track


@needs_node
def test_decade_ticks_and_adev_layout():
    out = _run("[decadeTicks([2300, 2.0e6], nsDecadeLabel),"
               " adevLayout({adev: [3e-9, 2e-11], tau_seconds: [10, 10000]})]")
    ticks, layout = out
    assert ticks["ticktext"] == ["1 µs", "10 µs", "100 µs", "1 ms", "10 ms"]
    assert layout["yaxis"]["type"] == "log"
    assert layout["yaxis"]["ticktext"] == ["10<sup>-11</sup>", "10<sup>-10</sup>", "10<sup>-9</sup>",
                                           "10<sup>-8</sup>"]
    assert layout["xaxis"]["ticktext"][0] == "10 s"


def test_t3_card_names_what_it_plots():
    """The T3 card's values carry D_clock (MEASUREMENT_MODEL §0.2), not t0 itself."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
            / "metrology.html").read_text()
    assert "plotted as host clock minus the T3 estimate (d_clock_fused_ms)" in html
