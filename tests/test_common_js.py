"""common.js behaviour, run under node in a non-UTC zone.

Operators sit in Central and Pacific time while every log and product runs
on UTC.  A formatter that calls ``toLocale*`` prints the browser's local
clock with no zone marker; a JS ``Date`` handed to plotly draws a local-time
axis.  These tests load the real common.js in node with TZ set to Chicago
and check what the helpers print.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static" / "js" / "common.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# Just enough DOM for common.js to load: it registers a DOMContentLoaded
# hook and reads window.location in api.get.
PRELUDE = """
const window = globalThis;
globalThis.location = { origin: 'http://x' };
globalThis.document = {
  readyState: 'loading', title: 'Page - station-web',
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
};
"""


def _run(expr_js: str, tz: str = "America/Chicago"):
    script = PRELUDE + COMMON.read_text() + f"\nconsole.log(JSON.stringify({expr_js}));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       env={"TZ": tz, "PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_formatters_print_utc_with_a_marker():
    out = _run("[formatTime('2026-10-18T14:20:05Z'), formatTimestamp('2026-10-18T14:20:05Z'),"
               " formatDate('2026-10-18T03:00:00Z')]")
    assert out == ["14:20:05Z", "2026-10-18 14:20:05 UTC", "2026-10-18"]


def test_plot_time_is_an_iso_utc_string():
    out = _run("[plotTime(1760797205), plotTime(1760797205000), plotTime(new Date(Date.UTC(2026,9,18,14,20,5)))]")
    assert out == ["2025-10-18T14:20:05.000Z", "2025-10-18T14:20:05.000Z",
                   "2026-10-18T14:20:05.000Z"]


def test_lat_lon_is_sign_aware():
    assert _run("formatLatLon(38.9187, -92.1277)") == "38.92°N, 92.13°W"
    assert _run("formatLatLon(-33.9, 151.2, 1)") == "33.9°S, 151.2°E"


def test_no_locale_time_calls():
    assert "toLocale" not in COMMON.read_text()


def test_escape_html_neutralises_markup():
    out = _run("escapeHtml('<img src=x onerror=\"alert(1)\">&\\'')")
    assert out == "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&#39;"
    assert _run("[escapeHtml(null), escapeHtml(7)]") == ["", "7"]


def test_grape_page_escapes_status_values():
    """Every grape_status.json field the page interpolates goes through escapeHtml."""
    import re
    page = (COMMON.parents[1] / "grape.html").read_text()
    assert "escapeHtml(" in page
    raw = re.findall(r"\$\{(run\.\w+|err\.message|ch|info\.\w+)[^}]*\}", page)
    assert not raw, f"unescaped interpolations: {raw}"
