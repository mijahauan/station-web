"""Heavy routes read only the columns they use, and every product query seeks the index.

Final review S1, S2: routes read whole rows (``SELECT *`` through hamsci-dsp's
``read_time_range``) over their windows.  On B4 the only index on the product
tables is ``(channel, timestamp_utc)``, and a query that does not lead with
``channel = ?`` scanned the whole index for 13 s (measured 2026-10-18).
"""
import re
import sqlite3

import pytest

from station_web import config as cfgmod


@pytest.fixture
def traced(monkeypatch):
    statements = []
    real = sqlite3.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.set_trace_callback(statements.append)
        return con
    monkeypatch.setattr(sqlite3, "connect", connect)
    return statements


PRODUCT = re.compile(r"\bFROM\s+\"?((?:L1|L2|L3|DIAG)_\w+)", re.I)

# Routes whose rows this fix projects: no SELECT * may reach their tables.
PROJECTED = [
    ("/api/physics/scintillation/history?start=-6h&end=now", "L2_tick_phase"),
    ("/api/physics/scintillation/paths", "L2_tick_phase"),
    ("/api/metrology/fusion/history?start=-24h&end=now", "L3_fusion_timing"),
    ("/api/stability/adev?start=-24h&end=now", "L3_fusion_timing"),
    ("/api/tec/dtec?start=-24h&end=now&min_snr_db=0", "L3_dtec"),
    ("/api/tec/history?start=-24h&end=now", "L3_tec"),
    ("/api/propagation/tec?start=-24h&end=now", "L3_tec"),
    ("/api/propagation/timeline?start=-6h&end=now", "L2_timing_measurements"),
    ("/api/propagation/timeline?start=-6h&end=now", "L2_tick_timing"),
]


@pytest.mark.parametrize("url,table", PROJECTED, ids=[f"{u.split('?')[0]}:{t}" for u, t in PROJECTED])
def test_route_projects_its_columns(seeded_client, traced, url, table):
    r = seeded_client.get(url)
    assert r.status_code in (200, 404), (url, r.status_code, r.text[:200])
    reads = [s for s in traced if table in s and s.lstrip().upper().startswith("SELECT")
             and "sqlite_master" not in s and "PRAGMA" not in s.upper()]
    assert reads, f"{url} never read {table}"
    star = [s for s in reads if re.match(r"\s*SELECT\s+\*", s, re.I)]
    assert not star, star


# Every route a page calls with a window: no product query may scan.
SEEKING = [
    "/api/physics/scintillation/history?start=-6h&end=now",
    "/api/physics/scintillation/paths",
    "/api/metrology/fusion/history?start=-24h&end=now",
    "/api/stability/adev?start=-24h&end=now",
    "/api/tec/dtec?start=-24h&end=now&min_snr_db=0",
    "/api/tec/history?start=-24h&end=now",
    "/api/propagation/tec?start=-24h&end=now",
    "/api/propagation/timeline?start=-6h&end=now",
    "/api/ionogram/combined?start=-6h&end=now",
    "/api/ionogram/channels",
    "/api/phase/combined?start=-6h&end=now",
]


@pytest.mark.parametrize("url", SEEKING, ids=[u.split("?")[0] for u in SEEKING])
def test_product_queries_seek_the_index(seeded_client, traced, url):
    r = seeded_client.get(url)
    assert r.status_code in (200, 404), (url, r.status_code, r.text[:200])
    con = sqlite3.connect(str(cfgmod.config.storage["sqlite_path"]))
    try:
        for s in traced:
            m = PRODUCT.search(s)
            if not m or not s.lstrip().upper().startswith("SELECT"):
                continue
            plan = " | ".join(row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + s))
            assert f"SCAN {m.group(1)}" not in plan, (url, s, plan)
    finally:
        con.close()
