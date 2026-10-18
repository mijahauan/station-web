"""The products the consolidated pages surface (stage 2, S2D).

Four routes, each with its shape, its caps, its index seek and its honest
answer on a station that lacks the product:

- ``/api/propagation/stats``: hourly MUF and mode probability (L3C_propagation_stats).
- ``/api/propagation/detections``: the detection funnel per broadcast
  (L2_detection_attempts), counted in SQL.
- ``/api/tec/gnss-vtec``: GNSS VTEC, the reference for the HF TEC chart.
- ``/api/timing/wwvb``: the WWVB decode ledger.
"""
import re
import sqlite3
import time

import pytest

from error_assert import assert_opaque_500
from s2d_fixture import ATTEMPTS, VTEC_ROWS, s2d_products, vtec_value  # noqa: F401
from station_web import config as cfgmod
from station_web import heavy
from station_web.services import gnss_vtec, propagation_products, wwvb


# ── /api/propagation/stats ────────────────────────────────────────────────

def test_stats_shape_and_weighting(seeded_client, s2d_products):
    r = seeded_client.get("/api/propagation/stats?start=-24h&end=now")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["available"] is True
    assert j["source"] == "L3C_propagation_stats"
    assert j["modes_order"] == ["1E", "1F", "2F", "3F", "GW", "unknown"]
    assert set(j["stations"]) == {"WWV", "WWVH"}
    wwvh = j["stations"]["WWVH"]
    assert len(wwvh["time"]) == 2 and all(t.endswith("Z") for t in wwvh["time"])
    # hour 1: 10 MHz all 1F (n=30), 15 MHz all 2F (n=10) -> n-weighted
    assert wwvh["mode_probability"]["1F"][0] == pytest.approx(0.75)
    assert wwvh["mode_probability"]["2F"][0] == pytest.approx(0.25)
    assert wwvh["n_observations"][0] == 40
    assert wwvh["muf_mhz"] == [18.5, None]
    assert wwvh["muf_confidence"][0] == pytest.approx(0.6)
    assert wwvh["mode_probability"]["1E"][1] == pytest.approx(0.5)
    wwv = j["stations"]["WWV"]
    assert wwv["muf_mhz"] == [24.4]
    assert j["quality_counts"] == {"GOOD": 3, "MARGINAL": 1}


def test_stats_station_filter(seeded_client, s2d_products):
    j = seeded_client.get("/api/propagation/stats?start=-24h&end=now&station=wwvh").json()
    assert set(j["stations"]) == {"WWVH"}


def test_stats_unknown_station_is_fixed_text_400(seeded_client, s2d_products):
    r = seeded_client.get("/api/propagation/stats?station=<b>EVIL")
    assert r.status_code == 400
    assert "EVIL" not in r.text


def test_stats_window_cap(seeded_client):
    r = seeded_client.get("/api/propagation/stats?start=-30d&end=now")
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Window longer than")


def test_stats_no_database(client):
    j = client.get("/api/propagation/stats").json()
    assert j["available"] is False
    assert "L3C_propagation_stats" in j["reason"]


def test_stats_projects_and_seeks(seeded_client, s2d_products, traced_sql):
    seeded_client.get("/api/propagation/stats?start=-24h&end=now")
    _assert_projected_and_seeking(traced_sql, "L3C_propagation_stats")


def test_stats_fault_is_opaque(seeded_client, s2d_products, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("SECRET /var/lib/timestd/x.db")
    monkeypatch.setattr(propagation_products.projected, "iter_rows", boom)
    assert_opaque_500(seeded_client.get("/api/propagation/stats"), "SECRET")


# ── /api/propagation/detections ───────────────────────────────────────────

def test_detections_funnel_per_broadcast(seeded_client, s2d_products):
    r = seeded_client.get("/api/propagation/detections?start=-6h&end=now")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["available"] is True
    assert j["source"] == "L2_detection_attempts"
    got = {(b["channel"], b["station"]): b for b in j["broadcasts"]}
    assert set(got) == {(c, s) for c, s, _ in ATTEMPTS}
    for (c, s, f), (n_det, rej) in ATTEMPTS.items():
        b = got[(c, s)]
        assert b["detected"] == n_det
        assert b["rejected"] == sum(rej.values())
        assert b["attempts"] == n_det + sum(rej.values())
        assert b["reasons"] == rej
        assert b["frequency_mhz"] == f
    assert got[("SHARED_10000", "BPM")]["detection_rate"] == 0.0
    # reasons ordered by their total, largest first
    assert j["reasons_order"][0] == "corr_snr_low"
    assert j["totals"]["attempts"] == sum(d + sum(r.values()) for d, r in ATTEMPTS.values())


def test_detections_station_filter(seeded_client, s2d_products):
    j = seeded_client.get("/api/propagation/detections?start=-6h&end=now&station=BPM").json()
    assert [(b["channel"], b["station"]) for b in j["broadcasts"]] == [("SHARED_10000", "BPM")]


def test_detections_window_cap(seeded_client, s2d_products):
    r = seeded_client.get("/api/propagation/detections?start=-3d&end=now")
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Window longer than")


def test_detections_window_excludes_old_rows(seeded_client, s2d_products):
    j = seeded_client.get("/api/propagation/detections?start=-10m&end=now").json()
    assert j["available"] is True and j["broadcasts"] == []
    assert j["reason"]


def test_detections_missing_table_is_honest(seeded_client):
    j = seeded_client.get("/api/propagation/detections").json()
    assert j["available"] is False
    assert "L2_detection_attempts" in j["reason"]


def test_detections_counted_in_sql_with_index_seek(seeded_client, s2d_products, traced_sql):
    seeded_client.get("/api/propagation/detections?start=-6h&end=now")
    reads = [s for s in traced_sql if "FROM" in s and "L2_detection_attempts" in s
             and s.lstrip().upper().startswith("SELECT")]
    counting = [s for s in reads if "COUNT(" in s.upper()]
    assert counting, reads
    assert all("GROUP BY" in s.upper() for s in counting)
    _assert_seeking(traced_sql, "L2_detection_attempts")


def test_detections_never_fetch_rows(seeded_client, s2d_products, traced_sql):
    seeded_client.get("/api/propagation/detections?start=-6h&end=now")
    reads = [s for s in traced_sql if "L2_detection_attempts" in s and "sqlite_master" not in s
             and s.lstrip().upper().startswith("SELECT") and "COUNT(" not in s.upper()]
    # only the channel skip-scan, which returns one channel name per seek
    assert all("LIMIT 1" in s for s in reads), reads


# ── /api/tec/gnss-vtec ────────────────────────────────────────────────────

def test_vtec_shape_and_reference_label(seeded_client, s2d_products):
    r = seeded_client.get("/api/tec/gnss-vtec?start=-6h&end=now")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["available"] is True
    assert j["source"] == "L3_gnss_vtec"
    assert "reference" in j["label"].lower()
    assert len(j["time"]) == len(j["vtec_tecu"]) == len(j["n_satellites"]) == j["n_points"]
    assert all(t.endswith("Z") for t in j["time"])
    # BAD rows (150 TECU, 2 satellites) stay out
    assert max(j["vtec_tecu"]) < 20
    assert j["n_excluded_bad"] == VTEC_ROWS // 10
    assert j["n_rows"] == VTEC_ROWS - VTEC_ROWS // 10


def test_vtec_decimates_to_the_point_cap(seeded_client, s2d_products, monkeypatch):
    monkeypatch.setattr(gnss_vtec, "MAX_POINTS", 100)
    j = seeded_client.get("/api/tec/gnss-vtec?start=-6h&end=now").json()
    assert 0 < j["n_points"] <= 100
    assert j["bucket_s"] >= 6 * 3600 / 100 - 1
    lo, hi = vtec_value(0), vtec_value(VTEC_ROWS)
    assert all(lo <= v <= hi for v in j["vtec_tecu"])


def test_vtec_streams_in_chunks(seeded_client, s2d_products, monkeypatch):
    """The read never holds the window: buckets close as rows stream past."""
    seen = []
    real = gnss_vtec.projected.iter_rows

    def spy(*a, **kw):
        kw["chunk"] = 250
        for part in real(*a, **kw):
            seen.append(len(part))
            yield part
    monkeypatch.setattr(gnss_vtec.projected, "iter_rows", spy)
    j = seeded_client.get("/api/tec/gnss-vtec?start=-6h&end=now").json()
    assert j["available"] and max(seen) <= 250 and len(seen) > 1


def test_vtec_window_cap(seeded_client):
    r = seeded_client.get("/api/tec/gnss-vtec?start=-5d&end=now")
    assert r.status_code == 400


def test_vtec_missing_product_is_honest(seeded_client):
    j = seeded_client.get("/api/tec/gnss-vtec").json()
    assert j["available"] is False
    assert "L3_gnss_vtec" in j["reason"]


def test_vtec_projects_and_seeks(seeded_client, s2d_products, traced_sql):
    seeded_client.get("/api/tec/gnss-vtec?start=-6h&end=now")
    _assert_projected_and_seeking(traced_sql, "L3_gnss_vtec")


# ── /api/timing/wwvb ──────────────────────────────────────────────────────

def test_wwvb_summary(seeded_client, s2d_products):
    r = seeded_client.get("/api/timing/wwvb")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["available"] is True
    assert j["passes"]["n"] == 2 and j["passes"]["with_frames"] == 1
    assert j["passes"]["last"]["snr_db"] == pytest.approx(14.2)
    assert j["frames"]["n"] == 2 and j["frames"]["plausible"] == 1
    last = j["frames"]["last_plausible"]
    assert last["minute"].endswith("Z") and last["vs_wallclock_s"] is not None
    assert j["recent_frames"][0]["plausible"] in (True, False)
    assert j["n_malformed"] == 1
    assert j["file"].endswith(".jsonl") and "/" not in j["file"]
    assert "minute" in j["role"] and "second" in j["role"]


def test_wwvb_reads_only_the_tail(s2d_products, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from s2d_fixture import ledger_lines, write_ledger
    now = datetime.now(timezone.utc)
    big = ledger_lines(now) * 6000             # well over 1 MB
    write_ledger(s2d_products.data_root, now, big)
    reads = []
    real_open = wwvb.timing_authority._open

    class Spy:
        def __init__(self, fh):
            self.fh = fh
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.fh.close()
        def seek(self, *a):
            return self.fh.seek(*a)
        def tell(self):
            return self.fh.tell()
        def read(self, n=-1):
            data = self.fh.read(n)
            reads.append(len(data))
            return data
    monkeypatch.setattr(wwvb.timing_authority, "_open", lambda p, m: Spy(real_open(p, m)))
    j = wwvb.summary(s2d_products)
    assert j["available"]
    assert sum(reads) <= wwvb.timing_authority.TAIL_BYTES


def test_wwvb_n_cap(seeded_client, s2d_products):
    assert seeded_client.get("/api/timing/wwvb?n=1000").status_code == 422
    j = seeded_client.get("/api/timing/wwvb?n=1").json()
    assert len(j["recent_frames"]) == 1


def test_wwvb_missing_is_honest(seeded_client):
    j = seeded_client.get("/api/timing/wwvb").json()
    assert j["available"] is False
    assert "WWVB" in j["reason"]


# ── concurrency: each heavy family holds its semaphore ─────────────────────

BUSY = [
    (propagation_products.STATS_FAMILY, "/api/propagation/stats?start=-6h&end=now"),
    (propagation_products.DETECT_FAMILY, "/api/propagation/detections?start=-6h&end=now"),
    (gnss_vtec.HEAVY_FAMILY, "/api/tec/gnss-vtec?start=-6h&end=now"),
]


@pytest.mark.parametrize("family,url", BUSY, ids=[u.split("?")[0] for _, u in BUSY])
def test_a_held_family_answers_503(seeded_client, s2d_products, monkeypatch, family, url):
    monkeypatch.setattr(heavy, "WAIT_S", 0.2)
    sem = heavy.semaphore(family)
    assert sem.acquire(timeout=1)
    try:
        t0 = time.monotonic()
        r = seeded_client.get(url)
        assert r.status_code == 503, (url, r.status_code)
        assert time.monotonic() - t0 < 2.0
    finally:
        sem.release()


# ── helpers ───────────────────────────────────────────────────────────────

@pytest.fixture
def traced_sql(monkeypatch):
    statements = []
    real = sqlite3.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.set_trace_callback(statements.append)
        return con
    monkeypatch.setattr(sqlite3, "connect", connect)
    return statements


def _assert_seeking(statements, table):
    con = sqlite3.connect(str(cfgmod.config.storage["sqlite_path"]))
    try:
        reads = [s for s in statements if table in s and s.lstrip().upper().startswith("SELECT")
                 and "sqlite_master" not in s]
        assert reads, f"{table} never read"
        for s in reads:
            plan = " | ".join(row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + s))
            assert f"SCAN {table}" not in plan, (s, plan)
            assert "SEARCH" in plan, (s, plan)
    finally:
        con.close()


def _assert_projected_and_seeking(statements, table):
    reads = [s for s in statements if table in s and s.lstrip().upper().startswith("SELECT")
             and "sqlite_master" not in s]
    assert reads, f"{table} never read"
    assert not [s for s in reads if re.match(r"\s*SELECT\s+\*", s, re.I)], reads
    _assert_seeking(statements, table)


def test_inventory_names_the_wwvb_ledger(settings):
    from station_web.contract import build_inventory
    details = build_inventory(settings)["instances"][0]["data_path"]["details"]
    assert details["wwvb_ledger"] == str(settings.wwvb_dir)
