"""The watchdog's severities (hf-timestd radiod_timing_watchdog) as the pages draw them.

hf-timestd writes info, warn and fail.  `fail` used to draw as `unknown`
because neither index.html's alarmKind nor common.js's statusKind named it.
Both functions run here under node, cut from the shipped files."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SEVERITIES = ["info", "warn", "fail", "INFO", "Fail", None, "bogus"]
WANT = ["ok", "warn", "fault", "ok", "fault", "unknown", "unknown"]


def _func(text, name):
    m = re.search(rf"function {name}\(.*?\n( {{0,8}})\}}\n", text, re.S)
    assert m, name
    return m.group(0)


def _run(src, call):
    script = src + f"\nconsole.log(JSON.stringify({json.dumps(SEVERITIES)}.map(s => {call}(s))));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_index_alarm_kind_maps_every_watchdog_severity():
    src = _func((STATIC / "index.html").read_text(), "alarmKind")
    assert _run(src, "alarmKind") == WANT


def test_common_status_kind_maps_every_watchdog_severity():
    src = _func((STATIC / "js" / "common.js").read_text(), "statusKind")
    assert _run(src, "statusKind") == WANT
