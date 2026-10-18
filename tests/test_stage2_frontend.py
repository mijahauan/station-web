"""Stage 2 foundation: one palette, one nav, dark only, the registration panel.

The palette and the nav live in common.js so one edit reaches every page.
These tests hold each page to that: no page keeps its own station colour
table, every page carries exactly one nav placeholder and no hard-coded
links, and the registration panel escapes every API value and prints
"not published" for values hf-timestd does not publish.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
COMMON = STATIC / "js" / "common.js"
CSS = STATIC / "css" / "styles.css"
PAGES = sorted(p.name for p in STATIC.glob("*.html"))

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = """
const window = globalThis;
globalThis.location = { origin: 'http://x', pathname: '/' };
globalThis.document = {
  readyState: 'loading', title: 'Page - station-web',
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
};
"""


def _run(expr_js: str):
    script = PRELUDE + COMMON.read_text() + f"\nconsole.log(JSON.stringify({expr_js}));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       env={"TZ": "America/Chicago", "PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ── palette ─────────────────────────────────────────────────────────────

@needs_node
def test_palette_station_colours_are_fixed_and_distinct():
    out = _run("[stationColor('WWV'), stationColor('WWVH'), stationColor('BPM'),"
               " stationColor('SHARED_10000'), stationColor('composite'), stationColor('WWV_20000'),"
               " stationColor('nonsense'), PALETTE.station.composite]")
    wwv, wwvh, bpm, shared, comp, wwv_ch, unknown, neutral = out
    assert len({wwv, wwvh, bpm, neutral}) == 4
    assert shared == comp == unknown == neutral
    assert wwv_ch == wwv


@needs_node
def test_palette_frequency_ramp_is_ordered():
    out = _run("[PALETTE.freqOrderMHz, PALETTE.freqRamp, freqColor(2.5), freqColor(10), freqColor(25),"
               " freqColor(9.9), freqColor(null)]")
    order, ramp, f25, f10, f250, near10, none = out
    assert order == [2.5, 5, 10, 15, 20, 25] and len(ramp) == 6 and len(set(ramp)) == 6
    assert (f25, f10, f250, near10) == (ramp[0], ramp[2], ramp[5], ramp[2])
    assert none == _run("PALETTE.station.composite")


def _luminance(hexcol):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


@needs_node
def test_ramps_order_by_lightness():
    """An ordered quantity reads in grey too: each ramp step is lighter (or
    darker) than the last, so order survives any colour-vision deficiency."""
    ramp, tiers = _run("[PALETTE.freqRamp, PALETTE.tierOrder.map(t => tierColor(t))]")
    for seq in (ramp, list(reversed(tiers))):
        lum = [_luminance(c) for c in seq]
        assert lum == sorted(lum), seq


@needs_node
def test_tier_colours_cover_t6_to_t0():
    out = _run("[PALETTE.tierOrder, PALETTE.tierOrder.map(t => tierColor(t)), tierColor(null)]")
    order, cols, none = out
    assert order == ["T6", "T5", "T4", "T3", "T2", "T1", "T0"]
    assert len(set(cols)) == 7 and none not in cols


@needs_node
def test_status_badge_carries_text_and_glyph_not_colour_alone():
    out = _run("[statusBadge('ok', 'VALID'), statusBadge('fault', '<b>x</b>'), statusKind('INVALID'),"
               " statusKind('VALID'), statusKind('degraded'), statusKind('whatever')]")
    ok, fault, k_inv, k_val, k_deg, k_unk = out
    assert "VALID" in ok and PALETTE_GLYPHS["ok"] in ok
    assert "&lt;b&gt;x&lt;/b&gt;" in fault and PALETTE_GLYPHS["fault"] in fault
    assert (k_inv, k_val, k_deg, k_unk) == ("fault", "ok", "warn", "unknown")


PALETTE_GLYPHS = {"ok": "✓", "warn": "▲", "fault": "✕", "unknown": "○"}

#: a colour literal: #rgb / #rrggbb / #rrggbbaa, or rgb()/rgba()
COLOUR = r"(#[0-9a-fA-F]{3}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{8}\b|rgba?\()"
#: a station named in code, markup or a CSS selector / custom property
STATION_WORD = r"(\b(WWVH?|BPM)\b|station-(wwvh?|bpm)\b|--(wwvh?|bpm)-color\b)"
STATION_LINE = re.compile(f"{STATION_WORD}.*{COLOUR}|{COLOUR}.*{STATION_WORD}")
LOCAL_TABLE = re.compile(r"\b(STATION_COLORS|TILE_COLORS|FREQ_COLORS|SOLAR_COLORS|"
                         r"SOLAR_STATION_COLORS|WWVH?_COLOR)\b")


@pytest.mark.parametrize("page", PAGES)
def test_no_page_keeps_its_own_station_colours(page):
    """A line that names a station and holds a colour literal is a private
    station colour; the palette (stationColor, var(--station-*)) replaces it."""
    lines = (STATIC / page).read_text().splitlines()
    hits = [f"{i}: {ln.strip()}" for i, ln in enumerate(lines, 1) if STATION_LINE.search(ln)]
    assert not hits, hits
    tables = [f"{i}: {ln.strip()}" for i, ln in enumerate(lines, 1) if LOCAL_TABLE.search(ln)]
    assert not tables, tables


def test_css_station_tokens_match_the_palette():
    m = re.search(r"station:\s*\{([^}]*)\}", COMMON.read_text())
    js = dict(re.findall(r"(\w+):\s*'(#[0-9A-Fa-f]{6})'", m.group(1)))
    css = CSS.read_text()
    for st in ("WWV", "WWVH", "BPM", "composite"):
        tok = re.search(rf"--station-{st.lower()}:\s*(#[0-9A-Fa-f]{{6}})", css).group(1)
        assert tok.lower() == js[st].lower(), st


def test_backend_station_colours_match_the_palette():
    """phase and ionogram send a colour per series; it must be the palette's."""
    from station_web.routers import ionogram
    from station_web.services import phase_service
    m = re.search(r"station:\s*\{([^}]*)\}", COMMON.read_text())
    js = dict(re.findall(r"(\w+):\s*'(#[0-9A-Fa-f]{6})'", m.group(1)))
    for table in (phase_service.STATION_COLORS, ionogram.STATION_COLORS):
        for st in ("WWV", "WWVH", "BPM"):
            assert table[st].lower() == js[st].lower(), (st, table[st], js[st])


# ── dark only ───────────────────────────────────────────────────────────

def test_styles_declare_dark_only():
    css = CSS.read_text()
    assert re.search(r":root\s*\{[^}]*color-scheme:\s*dark", css)
    assert "prefers-color-scheme" not in css


@pytest.mark.parametrize("page", PAGES)
def test_page_declares_dark_only(page):
    text = (STATIC / page).read_text()
    assert '<meta name="color-scheme" content="dark">' in text
    assert "prefers-color-scheme" not in text


# ── nav ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", PAGES)
def test_nav_present_once_and_rendered_by_common_js(page):
    text = (STATIC / page).read_text()
    assert len(re.findall(r"<nav\b", text)) == 1, page
    assert len(re.findall(r"<nav\b[^>]*\bdata-site-nav\b", text)) == 1, page
    assert 'class="nav-link' not in text, f"{page} hard-codes nav links"


def test_nav_items_point_at_shipped_pages():
    m = re.search(r"const NAV_ITEMS = \[(.*?)\];", COMMON.read_text(), re.S)
    hrefs = re.findall(r"href:\s*'([^']+)'", m.group(1))
    assert hrefs[0] == "/"
    for h in hrefs[1:]:
        assert (STATIC / h.removeprefix("/static/")).exists(), h


@needs_node
def test_nav_marks_the_current_page():
    out = _run("[siteNavHTML('/static/chrony.html'), siteNavHTML('/static/health.html'),"
               " siteNavHTML('/')]")
    chrony, health, root = out
    assert re.search(r'href="/static/chrony.html"[^>]*aria-current="page"', chrony)
    assert re.search(r'href="/"[^>]*aria-current="page"', health)   # Health sits under Status
    assert re.search(r'href="/"[^>]*aria-current="page"', root)
    assert chrony.count('aria-current') == 1


def test_mobile_nav_folds_under_768px():
    css = CSS.read_text()
    m = re.search(r"@media \(max-width: 768px\)\s*\{(.*?)\n\}", css, re.S)
    assert m and ".nav-toggle" in m.group(1) and ".nav" in m.group(1)
    assert ".header.nav-open" in css


# ── registration panel ──────────────────────────────────────────────────

T6 = {"available": True, "utc_published": "2026-10-18T16:42:01.505163Z", "age_s": 3.5,
      "stale": False, "stale_after_s": 30, "tier_active": "T6",
      "tiers_available": ["T6", "T5", "T4", "T3"], "witnesses": ["T5", "T4", "T3", "T2"],
      "a_level": "A1", "ruler": {"a_level": "A1", "disciplined": True, "provenance": "not published"},
      "ruler_provenance": "not published",
      "sigma": {"value": 2298, "unit": "ns", "field": "sigma_ns"},
      "u_epoch": "not published", "stability": "not published",
      "rtp_to_utc_offset_ns": -14586174,
      "registration": {"state": "CANDIDATE", "source": "label", "sigma_ms": None, "age_s": 56.1,
                       "counter_epoch_id": "ep-1476374209", "raw_pair_residual_ms": None,
                       "contributing": [], "stations": []},
      "t6": {"authority_state": "AUTHORITATIVE", "violations": [], "hpps_publishing": True,
             "hpps_publish_mode": "live", "suspect_criteria": "", "battery_blocks": 8},
      "host_clock": {"verdict": "ok", "reason": "every witness agrees with the host clock",
                     "since_utc": None,
                     "witnesses": [{"name": "T2", "kind": "pair_ms", "value": 2.79, "unit": "ms",
                                    "bound": 60.0, "exceeded": False}]},
      "disagreement_flags": [], "last_transition_utc": "2026-10-18T16:00:26.420011Z",
      "governor_radiod": "AC0G-B4-status.local",
      "counter_epoch_changes_24h": {"count": None,
                                    "reason": "authority_history.db does not record counter_epoch_id"}}


def _panel(data, variant):
    return _run(f"registrationPanelHTML({json.dumps(data)}, {json.dumps(variant)})")


@needs_node
def test_panel_compact_shows_the_essentials():
    html = _panel(T6, "compact")
    assert "T6" in html and "2.30 µs" in html and "sigma_ns" in html
    assert "A1" in html and "provenance not published" in html
    assert "ep-1476374209" in html and "does not record counter_epoch_id" in html
    assert "every witness agrees with the host clock" in html


@needs_node
def test_panel_full_says_not_published():
    html = _panel(T6, "full")
    assert html.count("not published") >= 3          # u_epoch, stability, provenance
    assert "AUTHORITATIVE" in html and "AC0G-B4-status.local" in html
    assert "2026-10-18 16:00:26 UTC" in html


@needs_node
def test_panel_tier_none_lists_available():
    data = dict(T6, tier_active=None, tiers_available=["T4", "T3"],
                sigma={"value": 1.0, "unit": "ms", "field": "registration.sigma_ms"},
                disagreement_flags=[{"flag": "majority-downgrade:T6->T4",
                                     "text": "most witnesses disagree with T6"}])
    html = _panel(data, "compact")
    assert "None" in html and "T4, T3" in html
    assert "registration.sigma_ms" in html and "1.00 ms" in html
    assert "most witnesses disagree with T6" in html


@needs_node
def test_panel_escapes_every_value():
    evil = "<img src=x onerror=alert(1)>"
    data = dict(T6, tier_active=evil, tiers_available=[evil], witnesses=[evil],
                a_level=evil, ruler_provenance=evil,
                ruler={"a_level": evil, "disciplined": None, "provenance": evil},
                sigma={"value": evil, "unit": evil, "field": evil},
                registration={"state": evil, "source": evil, "sigma_ms": evil, "age_s": evil,
                              "counter_epoch_id": evil, "contributing": [evil], "stations": [evil],
                              "residual_vs_t6_ms": evil},
                t6={"authority_state": evil, "violations": [evil], "suspect_criteria": evil},
                host_clock={"verdict": evil, "reason": evil, "since_utc": evil,
                            "witnesses": [{"name": evil, "kind": evil, "value": evil, "unit": evil,
                                           "bound": evil, "exceeded": evil}]},
                disagreement_flags=[{"flag": evil, "text": evil}],
                governor_radiod=evil, last_transition_utc=evil,
                counter_epoch_changes_24h={"count": evil, "reason": evil})
    for variant in ("compact", "full"):
        html = _panel(data, variant)
        assert "<img" not in html, variant


@needs_node
def test_panel_unavailable_and_stale():
    html = _panel({"available": False, "reason": "timing authority not available on this station"},
                  "compact")
    assert "not available on this station" in html
    html = _panel(dict(T6, stale=True, age_s=95.0), "compact")
    assert "STALE" in html and "95" in html


# ── the Status page ─────────────────────────────────────────────────────

def test_status_page_drops_the_retired_views():
    index = (STATIC / "index.html").read_text()
    for gone in ("latest-dclock", "D_clock", "Recent Activity", "Quick Links",
                 "fusion/latest", "fusion/history", "quality_grade"):
        assert gone not in index, gone
    assert re.search(r"(load|render)RegistrationPanel\([^)]*'compact'\)", index)
    assert "/timing/alarms" in index
    assert 'href="/static/health.html"' in index


def test_status_page_names_failing_products():
    index = (STATIC / "index.html").read_text()
    assert "applicable" in index and "reason" in index and ".stale" in index
