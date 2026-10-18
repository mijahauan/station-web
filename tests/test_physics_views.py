"""Stage 2C: the charts that stated wrong physics, and one Doppler estimator.

- The ionogram's "implied F2 virtual height" (c·Δτ/2 on an oblique path)
  is gone, and so is its unsourced "Griffiths 2026" attribution.
- Doppler-delay plots Doppler in Hz against delay.  The Doppler comes
  from phase_service's estimator, the same one the phase page and the
  Observatory draw, never a wrapped phase on a Cartesian axis.
- A SHARED channel carries one composite carrier (WWV + WWVH + BPM), so it
  yields ONE trace, never three station-labelled copies of one carrier.
- dTEC computes its noise floor from the data, in the chart's unit, and its
  reference comparison uses only distinct-frequency paths.
"""
import json
import math
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from conftest import PROC_VERSION, _iso, _minute_boundary, _write_rows
from shared_fixture import SHARED, write_shared

from station_web.palette import COMPOSITE, STATION_COLORS
from station_web.services import phase_service

ROOT = Path(__file__).resolve().parents[1] / "src" / "station_web"
STATIC = ROOT / "static"
# S2D folded ionogram.html, phase.html and dtec.html into sections of the
# Propagation and Ionosphere pages; their code lives in static/js/*-view.js
JS = STATIC / "js"
MINE = [STATIC / "propagation.html", JS / "tof-view.js", JS / "propagation.js",
        STATIC / "ionosphere.html", JS / "phase-view.js", JS / "dtec-view.js",
        JS / "ionosphere.js", ROOT / "routers" / "ionogram.py",
        ROOT / "routers" / "phase.py", ROOT / "routers" / "tec.py",
        ROOT / "services" / "phase_service.py", ROOT / "services" / "tec_service.py",
        ROOT / "services" / "propagation_service.py"]
COMPOSITE_LABEL = "composite: WWV + WWVH + BPM"


@pytest.fixture(autouse=True)
def _fresh_phase_cache():
    phase_service.clear_cache()
    yield
    phase_service.clear_cache()


# ── 1. the F2 height and its citation are gone ──────────────────────────

@pytest.mark.parametrize("path", MINE, ids=lambda p: p.name)
def test_no_virtual_height_claim_left(path):
    text = path.read_text()
    for banned in ("virtual height", "Virtual Height", "c·Δτ", "Griffiths 2026",
                   "f2_height", "heightPlot", "statF2h"):
        assert banned not in text, f"{path.name} still says {banned!r}"


def test_ionogram_page_is_called_time_of_flight():
    # once ionogram.html; now the Propagation page's #time-of-flight section
    text = (STATIC / "propagation.html").read_text()
    section = text[text.index('id="time-of-flight"'):]
    h2 = re.search(r"<h2>(.*?)</h2>", section, re.S).group(1)
    assert h2.strip().startswith("Time of flight")
    assert "Griffiths" not in text          # no real reference exists in the docs


def test_timeseries_reply_has_no_height(seeded_client):
    j = seeded_client.get("/api/ionogram/arrivals/timeseries?channel=WWV_20000"
                          "&start=-1h&end=now&min_snr_db=0").json()
    assert j["status"] == "ok"
    assert "f2_height" not in j


# ── 2. Doppler-delay: Hz from the phase estimator ───────────────────────

FD_HZ = 0.05            # injected carrier Doppler on WWV_20000
N_MIN = 10


def _write_doppler_fixture(settings, now=None):
    """WWV_20000: tick_phase with a known Doppler, and rank-0 arrivals in the
    same minutes with a delay that walks 0.1 ms a minute."""
    now = now or datetime.now(timezone.utc)
    first = _minute_boundary(now) - 60 * (N_MIN + 2)
    phase_rows, arrivals = [], []
    for i in range(N_MIN):
        mb = first + 60 * i
        for sec in range(1, 56):
            t = mb + sec + 0.5
            phi = -2 * math.pi * FD_HZ * (t - first)
            phase_rows.append({
                "timestamp_utc": _iso(datetime.fromtimestamp(mb + 65, timezone.utc)),
                "minute_boundary_utc": mb, "channel": "WWV_20000", "station": "WWV",
                "frequency_mhz": 20.0, "window_start_second": sec,
                "window_end_second": sec + 1, "window_center_second": sec + 0.5,
                "phase_rad": 0.0, "carrier_phase_rad": (phi + math.pi) % (2 * math.pi) - math.pi,
                "dc_carrier_phase_rad": 0.0, "timing_offset_ms": 0.3,
                "timing_uncertainty_ms": 0.05, "snr_db": 20.0, "correlation_peak": 0.6,
                "coherence_quality": 0.9, "valid_ticks": 1,
                "processed_at": _iso(now), "processing_version": PROC_VERSION,
            })
        for sec in (10, 20, 30):
            arrivals.append({
                "timestamp_utc": _iso(datetime.fromtimestamp(mb + 65, timezone.utc)),
                "minute_boundary_utc": mb, "channel": "WWV_20000", "station": "WWV",
                "frequency_mhz": 20.0, "utc_second": mb + sec, "peak_rank": 0,
                "arrival_ms": 0.0, "timing_error_ms": 0.1 * i, "corr_snr_db": 20.0,
                "model_expected_ms": 4.0, "carrier_phase_rad": 2.9,
                "detection_method": "edge_tick", "sec_in_minute": sec})
    root, storage = settings.data_root, settings.storage
    _write_rows(root, storage, "L2", "tick_phase", "WWV_20000", "WWV_20000/tick_phase",
                phase_rows)
    _write_rows(root, storage, "L1", "all_arrivals", "WWV_20000",
                "WWV_20000/all_arrivals", arrivals)
    return first


@pytest.fixture
def doppler_client(settings):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    first = _write_doppler_fixture(settings)
    with TestClient(create_app(settings, control_socket=None)) as c:
        c.first_minute = first
        yield c


def test_doppler_delay_returns_hz_against_delay(doppler_client):
    j = doppler_client.get("/api/ionogram/doppler-delay?channel=WWV_20000"
                           "&start=-1h&end=now&include_kde=false").json()
    assert j["status"] == "ok", j
    assert j["doppler_unit"] == "Hz" and j["delay_unit"] == "ms"
    s = j["scatter"]
    assert "carrier_phase_rad" not in s and "phase_unwrapped_rad" not in s
    assert len(s["doppler_hz"]) == len(s["delay_ms"]) == N_MIN
    for hz in s["doppler_hz"]:
        assert hz == pytest.approx(FD_HZ, abs=2e-3)
    want = [4.0 + 0.1 * i for i in range(N_MIN)]
    assert s["delay_ms"] == [pytest.approx(w, abs=1e-6) for w in want]
    assert s["minute_boundary"][0] == doppler_client.first_minute
    assert j["doppler_estimator"] == phase_service.DOPPLER_ESTIMATOR


def test_ionogram_doppler_is_the_phase_page_doppler(doppler_client):
    """Same estimator, same numbers: one Doppler site-wide."""
    dd = doppler_client.get("/api/ionogram/doppler-delay?channel=WWV_20000"
                            "&start=-24h&end=now&include_kde=false").json()
    ph = doppler_client.get("/api/phase/doppler?channel=WWV_20000"
                            "&start=-24h&end=now&smoothing=0").json()
    (series,) = ph["series"]
    by_minute = {int(e // 60) * 60: v for e, v in zip(series["epochs"], series["doppler_raw_hz"])}
    ts = dd["doppler_timeseries"]
    assert ts["minute_boundary"], dd
    for m, v in zip(ts["minute_boundary"], ts["doppler_hz"]):
        assert v == pytest.approx(by_minute[m], abs=1e-5)


def test_doppler_delay_without_tick_phase_says_why(seeded_client):
    # seeded writes L1 arrivals for a minute with no tick_phase rows
    j = seeded_client.get("/api/ionogram/doppler-delay?channel=WWV_20000"
                          "&start=-1h&end=now&min_snr_db=0&include_kde=false").json()
    assert j["doppler_unit"] == "Hz"
    assert j["scatter"]["doppler_hz"] == []
    assert j["doppler_reason"]


# ── 3. SHARED channels: one composite trace ─────────────────────────────

@pytest.fixture
def shared_client(seeded):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    write_shared(seeded)
    with TestClient(create_app(seeded, control_socket=None)) as c:
        yield c


@pytest.mark.parametrize("view", ["phase", "doppler", "scintillation"])
def test_shared_channel_gives_one_composite_trace(shared_client, view):
    j = shared_client.get("/api/phase/combined?start=-2h&end=now").json()
    series = j[view]["series"]
    shared = [s for s in series if s["channel"] == SHARED]
    assert len(shared) == 1, [(s["channel"], s["station"]) for s in shared]
    (s,) = shared
    assert s["station"] == "composite" and s["composite"] is True
    assert s["label"] == COMPOSITE_LABEL
    assert s["color"] == COMPOSITE
    assert s["stations_heard"] == ["BPM", "WWV", "WWVH"]
    single = [x for x in series if x["channel"] == "WWV_20000"]
    assert len(single) == 1 and single[0]["station"] == "WWV"
    assert single[0]["composite"] is False and single[0]["label"] == "WWV"
    assert single[0]["color"] == STATION_COLORS["WWV"]


def test_composite_doppler_is_the_carrier_doppler(shared_client):
    # shared_fixture: phase 0.1 rad per second inside each minute, every station
    j = shared_client.get(f"/api/phase/doppler?channel={SHARED}&start=-2h&end=now"
                          "&smoothing=0").json()
    (s,) = j["series"]
    want = -0.1 / (2 * math.pi)
    assert float(np.median(s["doppler_raw_hz"])) == pytest.approx(want, abs=1e-3)


def test_station_filter_keeps_the_composite_whole(shared_client):
    j = shared_client.get("/api/phase/combined?start=-2h&end=now&station=WWVH").json()
    keys = [(s["channel"], s["station"]) for s in j["doppler"]["series"]]
    assert keys == [(SHARED, "composite")]


def test_summary_cards_follow_the_composite_rule(shared_client):
    j = shared_client.get("/api/phase/summary").json()
    by = {}
    for c in j["channels"]:
        by.setdefault(c["channel"], []).append(c)
    assert len(by[SHARED]) == 1
    card = by[SHARED][0]
    assert card["station"] == "composite" and card["label"] == COMPOSITE_LABEL
    # the card's Doppler comes from the chart's estimator
    assert card["doppler_hz"] == pytest.approx(-0.1 / (2 * math.pi), abs=1e-3)
    assert j["doppler_estimator"] == phase_service.DOPPLER_ESTIMATOR
    assert j["sigma_window_seconds"] == 60


# ── 3b. dTEC: one series per SHARED frequency; distinct-frequency comparison ─

def _dtec_rows(station, freq, n, rate_fn, t_end=None):
    t_end = t_end or datetime.now(timezone.utc) - timedelta(minutes=2)
    rows = []
    for i in range(n):
        t = t_end - timedelta(minutes=n - 1 - i)
        rows.append({
            "timestamp_utc": _iso(t), "minute_boundary": _minute_boundary(t),
            "station": station, "channel": "AGGREGATED", "frequency_mhz": freq,
            "n_ticks": 55, "dtec_mean_tecu": 0.0, "dtec_std_tecu": 0.01,
            "dtec_rate_tecu_per_s": rate_fn(i), "anchor_tec_tecu": 10.0,
            "is_anchored": False, "anchor_status": "NO_ANCHOR",
            "sigma_noise_tecu": 0.004, "mean_snr_db": 20.0, "unwrap_quality": 0.9,
            "n_phase_jumps": 0, "quality_flag": "GOOD", "processing_version": PROC_VERSION})
    return rows


def _write_dtec(settings, rows):
    _write_rows(settings.data_root, settings.storage, "L3", "dtec", "AGGREGATED",
                "science/dtec", rows)


@pytest.fixture
def dtec_client(settings):
    from fastapi.testclient import TestClient
    from station_web.app import create_app

    def make(rows):
        _write_dtec(settings, rows)
        return TestClient(create_app(settings, control_socket=None))
    return make


def test_dtec_shared_frequency_is_one_composite_series(dtec_client):
    rows = []
    for st in ("WWV", "WWVH", "BPM"):
        rows += _dtec_rows(st, 10.0, 20, lambda i: 1e-4)
    rows += _dtec_rows("WWV", 20.0, 20, lambda i: 2e-4)
    with dtec_client(rows) as c:
        j = c.get("/api/tec/dtec?start=-2h&end=now&min_snr_db=0").json()
    assert j["status"] == "ok"
    by_freq = {}
    for key, s in j["series"].items():
        by_freq.setdefault(s["frequency_mhz"], []).append(s)
    assert len(by_freq[10.0]) == 1 and len(by_freq[20.0]) == 1
    comp = by_freq[10.0][0]
    assert comp["station"] == "composite" and comp["composite"] is True
    assert comp["label"] == f"{COMPOSITE_LABEL} 10.0 MHz"
    assert comp["stations_heard"] == ["BPM", "WWV", "WWVH"]
    assert len(comp["timestamps"]) == 20            # one point a minute, not three
    single = by_freq[20.0][0]
    assert single["station"] == "WWV" and single["composite"] is False
    ref = j["reference_comparison"]
    assert ref["available"] is True
    assert sorted(j["series"][k]["frequency_mhz"] for k in ref["keys"]) == [10.0, 20.0]


def test_dtec_reference_comparison_dropped_without_distinct_paths(dtec_client):
    rows = []
    for st in ("WWV", "WWVH", "BPM"):
        rows += _dtec_rows(st, 10.0, 20, lambda i: 1e-4)
    with dtec_client(rows) as c:
        j = c.get("/api/tec/dtec?start=-2h&end=now&min_snr_db=0").json()
    ref = j["reference_comparison"]
    assert ref["available"] is False and ref["keys"] == [] and ref["reason"]


# ── 6. dTEC noise floor: computed, in the chart's unit ──────────────────

def test_dtec_noise_floor_is_computed(dtec_client):
    rng = np.random.default_rng(7)
    sigma = 3e-5
    noise = rng.normal(0.0, sigma, 240)
    trend = lambda i: 2e-4 * math.sin(i / 40.0)     # a slow TID the floor must ignore
    rows = _dtec_rows("WWV", 20.0, 240, lambda i: float(trend(i) + noise[i]))
    with dtec_client(rows) as c:
        j = c.get("/api/tec/dtec?start=-5h&end=now&min_snr_db=0").json()
    nf = j["noise_floor"]
    assert nf["unit"] == "TECU/s"
    assert nf["value"] == pytest.approx(sigma, rel=0.25)
    assert nf["n_series"] == 1 and nf["method"]


def test_dtec_noise_floor_needs_data(dtec_client):
    rows = _dtec_rows("WWV", 20.0, 4, lambda i: 1e-4)
    with dtec_client(rows) as c:
        j = c.get("/api/tec/dtec?start=-1h&end=now&min_snr_db=0").json()
    nf = j["noise_floor"]
    assert nf["value"] is None and nf["unit"] == "TECU/s" and nf["reason"]


# ── 7. Observatory TEC: no mislabelled uncertainty ──────────────────────

def test_tec_summary_names_the_residual_in_ms(seeded_client):
    j = seeded_client.get("/api/propagation/tec?start=-24h&end=now").json()
    p = j["paths"]["WWV"]
    assert "uncertainty_tecu" not in p           # it held residuals_ms
    assert p["residual_rms_ms"] == [pytest.approx(0.14)]
    assert j["tec_uncertainty"] == "not published"


# ── 4. the ruler floor beside every Doppler chart (node) ────────────────

COMMON_JS = STATIC / "js" / "common.js"
PHYS_JS = STATIC / "js" / "physics-views.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
_PRELUDE = """
const window = globalThis;
globalThis.location = { origin: 'http://x', pathname: '/' };
globalThis.document = {
  readyState: 'loading', title: 'Page - station-web',
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
  getElementById() { return null; },
};
"""


def _node(expr_js):
    script = (_PRELUDE + COMMON_JS.read_text() + "\n" + PHYS_JS.read_text()
              + f"\nconsole.log(JSON.stringify({expr_js}));\n")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       env={"TZ": "America/Chicago", "PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@needs_node
def test_ruler_floor_a1_scales_with_frequency():
    a10, a20, a25 = _node("[rulerDopplerFloor('A1', 10), rulerDopplerFloor('A1', 20),"
                          " rulerDopplerFloor('A1', 2.5)]")
    assert a10["state"] == "disciplined"
    assert a10["floor_hz"] == pytest.approx(0.004)
    assert a20["floor_hz"] == pytest.approx(0.008)
    assert a25["floor_hz"] == pytest.approx(0.001)
    assert "0.004 Hz" in a10["text"] and "10 MHz" in a10["text"]


@needs_node
def test_ruler_floor_a0_says_unreliable():
    a0 = _node("rulerDopplerFloor('A0', 10)")
    assert a0["state"] == "undisciplined" and a0["floor_hz"] is None
    assert a0["text"] == "ruler undisciplined: Doppler unreliable"


@needs_node
@pytest.mark.parametrize("a_level", ["null", "undefined", "'A7'", "''"])
def test_ruler_floor_missing_is_unknown(a_level):
    out = _node(f"rulerDopplerFloor({a_level}, 10)")
    assert out["state"] == "unknown" and out["floor_hz"] is None
    assert "not published" in out["text"]


@needs_node
def test_ruler_floor_reads_the_registration_reply():
    out = _node(
        "[rulerAlevel({available: true, ruler: {a_level: 'A1'}}),"
        " rulerAlevel({available: true, a_level: 'A0'}),"
        " rulerAlevel({available: false, reason: 'x'}), rulerAlevel(null)]")
    assert out == ["A1", "A0", None, None]


@needs_node
def test_ruler_floor_html_escapes_and_lists_each_frequency():
    html = _node("rulerFloorHTML({available: true, ruler: {a_level: 'A1'}}, [20, 10, 10])")
    assert "0.004 Hz at 10 MHz" in html and "0.008 Hz at 20 MHz" in html
    assert html.count("0.004 Hz") == 1            # frequencies dedupe
    evil = _node("rulerFloorHTML({available: true, ruler: {a_level: '<img src=x>'}}, [10])")
    assert "<img" not in evil and "not published" in evil
    a0 = _node("rulerFloorHTML({available: true, ruler: {a_level: 'A0'}}, [10])")
    assert "ruler undisciplined: Doppler unreliable" in a0


@needs_node
def test_lattice_aligned_residuals_are_suspect():
    out = _node("[5.1, -10.4, 15.2, 9.6, 7.5, 0.3, 2.0, 20.1, -4.6, 12.4, null]"
                ".map(v => latticeSuspect(v))")
    assert out == [True, True, True, True, False, False, False, False, True, False, False]


# ── pages: what each chart now says ─────────────────────────────────────

def _page(name):
    return (STATIC / name).read_text()


def _js(name):
    return (STATIC / "js" / name).read_text()


@pytest.mark.parametrize("name,js", [("ionosphere.html", "phase-view.js"),
                                     ("propagation.html", "tof-view.js")])
def test_doppler_pages_show_the_ruler_floor(name, js):
    assert '<script src="/static/js/physics-views.js"></script>' in _page(name)
    assert "loadRulerFloor(" in _js(js)


def test_ionogram_doppler_axis_is_hz_not_phase():
    text = _page("propagation.html") + _js("tof-view.js")
    assert "Carrier Phase (rad)" not in text and "carrier_phase_rad" not in text
    assert "Doppler (Hz)" in text and "doppler_hz" in text and "delay_ms" in text
    assert "doppler_method: 'edge_tick'" not in text


def test_phase_page_variability_and_s4_panels():
    page = _page("ionosphere.html")
    text = page[page.index('id="phase"'):page.index('id="dtec"')] + _js("phase-view.js")
    assert "phase variability (60 s detrend)" in text
    assert "Irregularity threshold" not in text and "0.3 rad" not in text
    assert 'id="s4-plot"' in text and "S4 (uncorrected for noise)" in text
    assert "yaxis2" not in text            # S4 left the second right axis
    assert "Scintillation Index" not in text
    assert "s.label" in text              # trace names come from the server's label


def test_phase_summary_cards_escape_the_label():
    text = _js("phase-view.js")
    assert "escapeHtml(ch.label)" in text
    assert "${ch.station}" not in text and "${ch.label}" not in text


def test_dtec_page_labels_and_panels():
    page = _page("ionosphere.html")
    text = page[page.index('id="dtec"'):page.index('id="tec"')] + _js("dtec-view.js")
    assert "phase-rate dTEC (assumes no layer-height motion)" in text
    assert "mTECU/min" not in text and ">~6<" not in text
    assert "noise_floor" in text and "reference_comparison" in text
    assert "overlaying" not in text        # stacked panels, no overlaid axes
    assert "10 MHz Reference Comparison" not in text
    assert "17 propagation paths" not in text


def test_observatory_tec_and_residual_charts():
    # the Observatory split: signal and residuals went to Propagation, TEC to Ionosphere
    prop = _page("propagation.html") + _js("propagation.js")
    iono = _page("ionosphere.html") + _js("ionosphere.js")
    for text in (prop, iono):
        assert "range: [0, 100]" not in text
        assert "plot-dclock-station" not in text and "D_clock by Station" not in text
        assert "Ionospheric Group Delay Residual" not in text
    assert "Arrival residual against the model (ms)" in prop
    assert "latticeSuspect(" in prop
    assert "residual_rms_ms" in iono
    # one Doppler estimator: the Ionosphere page draws phase-view's combined read
    assert "/phase/combined" in _js("phase-view.js")
    assert "doppler_hz" not in _js("propagation.js") and "doppler_hz" not in _js("ionosphere.js")
