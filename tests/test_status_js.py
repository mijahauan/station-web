"""status.js behaviour: the Station page drawn in node from API answers.

A small fake DOM records what the page writes; ``fetch`` answers from the
dict each test hands in.  Timers are recorded, never run, so the 60 s
refresh is visible without waiting for it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATUS = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static" / "js" / "status.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = r"""
function mk(tag) {
  const n = { tag, children: [], textContent: '', className: '', style: {}, attrs: {},
    appendChild(k) { this.children.push(k); return k; },
    replaceChildren(...k) { this.children = k; this.textContent = ''; },
    setAttribute(a, v) { this.attrs[a] = v; }, getAttribute(a) { return this.attrs[a]; },
    addEventListener() {},
    classList: { toggle() {} } };
  return n;
}
const ids = {};
const tbody = mk('tbody');
globalThis.window = globalThis;
globalThis.innerWidth = 1200;
globalThis.location = { hostname: 'h', hash: '' };
globalThis.timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push(ms); return 0; };
globalThis.document = {
  readyState: 'complete',
  body: { getAttribute: () => PAGE },
  createElement: mk,
  createTextNode: t => ({ textContent: String(t), children: [], className: '' }),
  getElementById: id => (ids[id] = ids[id] || mk('div')),
  querySelector: () => tbody,
  querySelectorAll: () => [],
  addEventListener() {},
};
globalThis.fetch = async url => {
  const key = url.replace('/api/status/', '');
  return { ok: true, status: 200, json: async () => ANSWERS[key] };
};
function text(n) { return (n.textContent || '') + n.children.map(text).join(''); }
function tds(i) { return tbody.children[i].children.map(text); }
function cells(n, cls) {
  const out = [];
  (function walk(x) { if (cls === undefined || x.className === cls) out.push(text(x)); x.children.forEach(walk); })(n);
  return out;
}
"""


def _station(body, expr):
    script = (f"const PAGE = 'station'; const ANSWERS = {{station: {json.dumps(body)}}};\n"
              + PRELUDE + STATUS.read_text()
              + f"\nsetImmediate(() => setImmediate(() => console.log(JSON.stringify({expr}))));\n")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


SNAP_OK = {"available": True, "command": "smd x", "ran_at": "2026-10-27T12:00:00Z",
           "age_s": 30, "stale": False, "exit": 0, "error": None, "failed": False}


def _body(**over):
    names = ("check", "components", "catalog", "instances", "validate",
             "viewers", "manifest", "heartbeat")
    b = {"identity": {"callsign": "AC0G", "grid_square": "EM38", "sigmond": {}},
         "rows": [], "viewers": {"viewers": [], "conflicts": []},
         "heartbeat": {"state": "unavailable"},
         "snapshots": {n: dict(SNAP_OK) for n in names}}
    b.update(over)
    return b


def _row(**over):
    r = {"name": "c", "description": None, "kind": "client", "installed": True,
         "local": "abc", "manifest_status": "match", "behind": 0,
         "fetched_at": "2026-10-27T11:00:00Z", "repo_known": True,
         "instances": {"rows": 1, "configured": 1, "enabled": 1, "active": 1},
         "check": {"id": "decode.c", "mark": "ok"}}
    r.update(over)
    return r


def test_heartbeat_three_states():
    unavailable = _station(_body(), "text(ids.heartbeat)")
    assert unavailable == "heartbeat snapshot unavailable"
    no_tick = _station(_body(heartbeat={"state": "no_tick", "stale": False}),
                       "[text(ids.heartbeat), cells(ids.heartbeat, 'sp-muted')]")
    assert no_tick[0] == "no heartbeat tick waiting (ticks leave the station once uploaded)"
    assert no_tick[1] == [no_tick[0]]
    assert "not running" not in no_tick[0]
    tick = _station(_body(heartbeat={"state": "tick", "verdict": "VALID", "reason": "all good",
                                     "emitted_at": "2026-10-27T11:58:00Z"}),
                    "text(ids.heartbeat)")
    assert tick == "Last heartbeat verdict VALID: all good at 2026-10-27 11:58Z"


def test_failed_snapshot_warns_and_columns_say_unknown():
    snaps = _body()["snapshots"]
    for n in ("components", "check"):
        snaps[n] = dict(SNAP_OK, failed=True, exit=1, error="boom")
    body = _body(snapshots=snaps,
                 rows=[_row(repo_known=False, behind=None, fetched_at=None, check=None)])
    out = _station(body, "[text(ids['clients-warning']), text(ids['snapshot-footer']),"
                         " ids['clients-warning'].style.display, tds(0)]")
    warning, footer, display, row = out
    assert display == "block"
    assert "components snapshot failed: boom" in warning
    assert "check snapshot failed: boom" in warning
    assert "components snapshot failed, exit 1: boom" in footer
    # Behind main and Check: unknown, never "never fetched" or a blank dash.
    assert row[4] == "unknown" and row[6] == "unknown"


def test_empty_heartbeat_spool_is_no_footer_warning():
    snaps = _body()["snapshots"]
    snaps["heartbeat"] = dict(SNAP_OK, failed=False, exit=1,
                              error="admin heartbeat show: no heartbeat ticks in /x")
    footer = _station(_body(snapshots=snaps,
                            heartbeat={"state": "no_tick", "stale": False}),
                      "text(ids['snapshot-footer'])")
    assert "failed" not in footer


def test_fetched_but_uncounted_reads_unknown():
    out = _station(_body(rows=[_row(behind=None)]), "tds(0)")
    assert out[4].startswith("unknown, fetched ") and out[4].endswith(" ago")
    never = _station(_body(rows=[_row(behind=None, fetched_at=None)]), "tds(0)")
    assert never[4] == "never fetched"


def test_no_instance_rows_reads_in_words():
    out = _station(_body(rows=[_row(instances={"rows": 0, "configured": 0,
                                              "enabled": 0, "active": 0})]),
                   "[tds(0), cells(tbody.children[0], 'sp-muted')]")
    assert out[0][5] == "no per-reporter instances"
    assert "no per-reporter instances" in out[1]
    counted = _station(_body(rows=[_row()]), "tds(0)")
    assert counted[5] == "1 configured · 1 enabled · 1 running"


def test_manifest_superset_green_not_listed_muted():
    out = _station(_body(rows=[_row(manifest_status="superset"),
                               _row(name="d", manifest_status="not_listed")]),
                   "[cells(tbody.children[0], 'sp-green'), cells(tbody.children[1], 'sp-muted'),"
                   " cells(tbody.children[1], 'sp-amber')]")
    assert "superset" in out[0]
    assert "not listed" in out[1] and "not listed" not in out[2]


def test_station_page_schedules_a_minute_refresh():
    assert 60000 in _station(_body(), "timers")
