"""The timing-authority service and its three routes.

The registration snapshot, its history and the radiod timing alarms come
from three files hf-timestd writes outside the product database.  Each
route must answer honestly on a station without hf-timestd (200 with
``available: false`` and a reason, never a 500), and the history read must
stay bounded: projected columns, an index seek, a window cap and at most
``MAX_POINTS`` points.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from station_web.services import timing_authority as ta
from timing_fixture import (T6_SNAPSHOT, WITNESS_SNAPSHOT, authority_files,  # noqa: F401
                            history_row, incident, iso_z, write_authority, write_history,
                            write_watchdog)

NOW = datetime(2026, 10, 18, 16, 42, 5, tzinfo=timezone.utc)


# ── snapshot normalisation ──────────────────────────────────────────────

def test_t6_snapshot_normalises(settings):
    write_authority(settings.authority_path, T6_SNAPSHOT)
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["available"] is True
    assert s["tier_active"] == "T6"
    assert s["tiers_available"] == ["T6", "T5", "T4", "T3"]
    assert s["witnesses"] == ["T5", "T4", "T3", "T2"]
    assert s["a_level"] == "A1"
    assert s["ruler"] == {"a_level": "A1", "disciplined": True, "provenance": "not published"}
    assert s["ruler_provenance"] == "not published"
    assert s["sigma"] == {"value": 2298, "unit": "ns", "field": "sigma_ns"}
    assert s["u_epoch"] == "not published"
    assert s["stability"] == "not published"
    assert s["t6"]["authority_state"] == "AUTHORITATIVE"
    assert s["t6"]["violations"] == []
    assert s["host_clock"]["verdict"] == "ok"
    assert s["host_clock"]["reason"] == "every witness agrees with the host clock"
    rows = {w["name"]: w for w in s["host_clock"]["witnesses"]}
    assert rows["T2"]["unit"] == "ms" and rows["lb1421"]["unit"] == "s"
    assert rows["T4"]["exceeded"] is False and rows["T4"]["bound"] == pytest.approx(6.0, 1e-6)
    assert s["disagreement_flags"] == []
    assert s["last_transition_utc"] == "2026-10-18T16:00:26.420011Z"
    assert s["age_s"] == pytest.approx(3.5, abs=0.1)
    assert s["stale"] is False
    assert s["registration"]["state"] == "CANDIDATE"
    assert s["registration"]["counter_epoch_id"] == "ep-1476374209"
    assert s["registration"]["sigma_ms"] is None


def test_witness_snapshot_normalises(settings):
    write_authority(settings.authority_path, WITNESS_SNAPSHOT)
    s = ta.registration_snapshot(settings, now=datetime(2026, 10, 18, 14, 43, 10, tzinfo=timezone.utc))
    assert s["tier_active"] is None
    assert s["tiers_available"] == ["T4", "T3"]
    assert s["witnesses"] == []
    # no sigma_ns: the published sigma comes from the registration block, and says so
    assert s["sigma"] == {"value": 1.0, "unit": "ms", "field": "registration.sigma_ms"}
    r = s["registration"]
    assert (r["state"], r["source"], r["sigma_ms"], r["age_s"]) == ("WITNESS", "label", 1.0, 174.0)
    assert r["contributing"] == ["WWV_10000", "WWV_15000", "SHARED_5000"]
    assert r["stations"] == ["WWV", "WWVH"]
    assert r["residual_vs_t6_ms"] == -19.2
    assert s["host_clock"]["verdict"] == "unwitnessed"
    assert s["host_clock"]["witnesses"] == []
    assert s["t6"] is None
    flags = s["disagreement_flags"]
    assert [f["flag"] for f in flags] == WITNESS_SNAPSHOT["disagreement_flags"]
    assert flags[0]["text"] == "T4 and T3 disagree by 7.412 ms (limit 6.000 ms); advisory only"
    assert flags[1]["text"] == "most witnesses disagree with T6; the tier in force dropped to T4"


def test_no_sigma_anywhere_reads_not_published(settings):
    snap = dict(WITNESS_SNAPSHOT, registration=dict(WITNESS_SNAPSHOT["registration"], sigma_ms=None))
    write_authority(settings.authority_path, snap)
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["sigma"] == {"value": None, "unit": None, "field": None, "text": "not published"}


def test_published_u_epoch_and_stability_pass_through(settings):
    snap = dict(T6_SNAPSHOT, u_epoch_ns=4100, u_epoch_k=2, u_epoch_p=0.95,
                stability_ns=310, tau_s=1000, ruler_provenance="observed")
    write_authority(settings.authority_path, snap)
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["u_epoch"] == {"value": 4100, "unit": "ns", "field": "u_epoch_ns", "k": 2, "p": 0.95}
    assert s["stability"] == {"value": 310, "unit": "ns", "field": "stability_ns", "tau_s": 1000}
    assert s["ruler_provenance"] == "observed" and s["ruler"]["provenance"] == "observed"


def test_stale_snapshot_is_flagged(settings):
    write_authority(settings.authority_path, T6_SNAPSHOT)
    s = ta.registration_snapshot(settings, now=datetime(2026, 10, 18, 16, 45, 0, tzinfo=timezone.utc))
    assert s["age_s"] > ta.STALE_S and s["stale"] is True


@pytest.mark.parametrize("flag,text", [
    ("demote-t6-breach->t5:12cycles", "T6 breached its drift limit for 12 cycles; demoted to T5"),
    ("chrony-rejected-HPPS:state=x", "chrony rejects source HPPS (state x)"),
    ("chrony-missing-FUSE", "chrony does not list source FUSE"),
    ("asymmetric-T3-T2:31ms>25ms", "T3 and T2 disagree asymmetrically: 31ms>25ms"),
    ("something-new", "something-new"),
])
def test_flags_in_plain_language(flag, text):
    assert ta.explain_flag(flag) == text


def test_missing_authority_file_is_honest(settings):
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["available"] is False
    assert "not available on this station" in s["reason"]


def test_counter_epoch_changes_need_a_column(settings):
    write_authority(settings.authority_path, T6_SNAPSHOT)
    write_history(settings.authority_history_path, [history_row(NOW, 0)])
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["counter_epoch_changes_24h"]["count"] is None
    assert "does not record" in s["counter_epoch_changes_24h"]["reason"]


def test_counter_epoch_changes_counted_when_recorded(settings):
    """A future history column carrying the epoch id gets counted."""
    write_authority(settings.authority_path, T6_SNAPSHOT)
    write_history(settings.authority_history_path,
                  [history_row(NOW - timedelta(minutes=k), k) for k in range(10)])
    con = sqlite3.connect(settings.authority_history_path)
    con.execute("ALTER TABLE authority_snapshot ADD COLUMN registration_counter_epoch_id TEXT")
    con.execute("UPDATE authority_snapshot SET registration_counter_epoch_id = "
                "CASE WHEN utc_published < ? THEN 'ep-1' ELSE 'ep-2' END",
                (iso_z(NOW - timedelta(minutes=4, seconds=30)),))
    con.commit()
    con.close()
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["counter_epoch_changes_24h"] == {"count": 1, "ids": ["ep-1", "ep-2"],
                                              "field": "registration_counter_epoch_id"}


# ── routes ──────────────────────────────────────────────────────────────

def test_registration_route_missing_files(client):
    r = client.get("/api/timing/registration")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_registration_route(client, authority_files):
    r = client.get("/api/timing/registration")
    assert r.status_code == 200
    j = r.json()
    assert j["tier_active"] == "T6" and j["stale"] is False


@pytest.mark.parametrize("text", ['{"schema": "v1", "a_lev', '[1, 2]', ''])
def test_malformed_authority_file_reads_unreadable(client, settings, text):
    """A torn or wrong-shaped file says so: a 200 naming it unreadable,
    never a 500 and never the file's text."""
    settings.authority_path.parent.mkdir(parents=True, exist_ok=True)
    settings.authority_path.write_text(text)
    r = client.get("/api/timing/registration")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False and j["unreadable"] is True
    assert "unreadable" in j["reason"] and "a_lev" not in r.text


def test_nan_in_authority_file_answers_valid_json(client, settings):
    """hf-timestd's json.dump writes NaN and Infinity bare; they read as null."""
    snap = dict(T6_SNAPSHOT, sigma_ns=float("nan"),
                registration=dict(WITNESS_SNAPSHOT["registration"], age_s=float("inf")),
                host_clock={"verdict": "ok", "witnesses": {
                    "rate": {"kind": "rate_ppm", "value": float("nan"), "bound": 50}}})
    write_authority(settings.authority_path, snap,
                    published=datetime.now(timezone.utc) - timedelta(seconds=3))
    assert "NaN" in settings.authority_path.read_text()
    r = client.get("/api/timing/registration")
    assert r.status_code == 200
    j = json.loads(r.text, parse_constant=lambda c: pytest.fail(f"bare {c} in the answer"))
    assert j["available"] is True
    assert j["sigma"]["field"] == "registration.sigma_ms"
    assert j["registration"]["age_s"] is None
    assert j["host_clock"]["witnesses"][0]["value"] is None


def test_list_shaped_witnesses_read_as_none(settings):
    write_authority(settings.authority_path,
                    dict(T6_SNAPSHOT, host_clock={"verdict": "ok", "witnesses": [1, 2]}))
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["host_clock"]["witnesses"] == []


# ── history ─────────────────────────────────────────────────────────────

def test_history_missing_db(client):
    r = client.get("/api/timing/registration/history?start=-24h&end=now")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False and "not available on this station" in j["reason"]


def test_history_decimates_and_projects(client, authority_files):
    r = client.get("/api/timing/registration/history?start=-24h&end=now")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True
    assert j["n_rows"] >= 2870
    assert 0 < j["n_points"] <= ta.MAX_POINTS
    assert len(j["time"]) == len(j["tier_active"]) == len(j["sigma_ns"]) == j["n_points"]
    for est in ("t6", "t5", "t4", "t3"):
        assert len(j["estimators"][est]["offset_ms"]) == j["n_points"]
        assert len(j["estimators"][est]["sigma_ms"]) == j["n_points"]
    assert j["estimators"]["t4"]["sigma_ms"][0] == 2.0
    assert all(t.endswith("Z") for t in j["time"][:5])
    # the fixture switches T4 -> T6 once, 1200 rows (10 h) back
    assert len(j["transitions"]) == 1
    assert (j["transitions"][0]["from"], j["transitions"][0]["to"]) == ("T4", "T6")
    # projection: nothing outside the named columns reaches the answer
    assert "rf_gain" not in json.dumps(j)


def test_history_short_window_not_decimated(client, authority_files):
    j = client.get("/api/timing/registration/history?start=-1h&end=now").json()
    assert j["bucket_s"] is None
    assert 110 <= j["n_points"] <= 121


def test_history_window_cap(client, authority_files):
    r = client.get("/api/timing/registration/history?start=-8d&end=now")
    assert r.status_code == 400
    assert r.json()["detail"] == "Window longer than 7 days"


def test_history_bad_time_is_fixed_text(client, authority_files):
    r = client.get("/api/timing/registration/history?start=<script>&end=now")
    assert r.status_code == 400 and "<script>" not in r.text


def test_history_query_seeks_the_primary_key(settings):
    """The read is a SEARCH on utc_published's autoindex, never a SCAN."""
    write_history(settings.authority_history_path, [history_row(NOW, 0)])
    con = sqlite3.connect(settings.authority_history_path)
    try:
        sql, params = ta.history_sql({"utc_published", "t_level_active", "sigma_ns"},
                                     NOW - timedelta(hours=24), NOW)
        plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params))
        sql2, params2 = ta.epoch_sql("t_level_active", NOW - timedelta(hours=24), NOW)
        plan2 = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql2, params2))
    finally:
        con.close()
    for p in (plan, plan2):
        assert "SEARCH" in p and "utc_published" in p, p
        assert "SCAN" not in p, p


def test_history_holds_the_heavy_semaphore(client, authority_files):
    from station_web import heavy
    sem = heavy.semaphore(ta.HEAVY_FAMILY)
    assert sem.acquire(timeout=1)
    try:
        old = heavy.WAIT_S
        heavy.WAIT_S = 0.05
        r = client.get("/api/timing/registration/history?start=-1h&end=now")
        assert r.status_code == 503
    finally:
        heavy.WAIT_S = old
        sem.release()


# ── alarms ──────────────────────────────────────────────────────────────

def test_alarms_missing(client):
    r = client.get("/api/timing/alarms")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False and "not available on this station" in j["reason"]
    assert j["incidents"] == [] and j["watchdog"] is None


def test_alarms_read(client, authority_files):
    j = client.get("/api/timing/alarms").json()
    assert j["available"] is True
    w = j["watchdog"]
    assert (w["verdict"], w["severity"], w["channel"]) == ("MAPPING_JUMP", "warn", "SHARED_2500")
    assert w["ts"].endswith("Z")
    assert "radiod" not in w and "external" not in w      # projected
    assert [i["detail"] for i in j["incidents"]] == ["incident 2", "incident 1", "incident 0"]


def test_alarms_tail_is_bounded(settings, monkeypatch):
    """A 5 MB incidents file costs one bounded read from the end."""
    write_watchdog(settings.timing_status_dir, n_incidents=None)
    path = settings.timing_status_dir / "radiod-timing-incidents.jsonl"
    with open(path, "w") as fh:
        for i in range(8000):
            fh.write(json.dumps(incident(i)) + "\n")
    assert path.stat().st_size > 4 * ta.TAIL_BYTES
    reads = []
    real_open = open

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
            assert n is not None and 0 <= n <= ta.TAIL_BYTES, n
            data = self.fh.read(n)
            reads.append(len(data))
            return data

    def spy_open(p, mode="r", *a, **k):
        fh = real_open(p, mode, *a, **k)
        return Spy(fh) if str(p) == str(path) else fh

    monkeypatch.setattr(ta, "_open", spy_open)
    j = ta.alarms(settings, n=10)
    assert sum(reads) <= ta.TAIL_BYTES
    assert [i["detail"] for i in j["incidents"]] == [f"incident {i}" for i in range(7999, 7989, -1)]


def test_alarms_skip_malformed_lines(settings):
    write_watchdog(settings.timing_status_dir, n_incidents=2)
    with open(settings.timing_status_dir / "radiod-timing-incidents.jsonl", "a") as fh:
        fh.write("{not json\n")
    j = ta.alarms(settings, n=10)
    assert [i["detail"] for i in j["incidents"]] == ["incident 1", "incident 0"]


def test_alarms_n_is_capped(client, authority_files):
    assert client.get("/api/timing/alarms?n=10000").status_code == 422


def test_inventory_names_the_authority_files(settings):
    from station_web.contract import build_inventory
    files = build_inventory(settings)["instances"][0]["data_path"]["details"]["timing_authority_files"]
    assert str(settings.authority_path) in files
    assert str(settings.authority_history_path) in files


def test_snapshot_one_slow_cycle_old_is_not_stale(settings):
    """hf-timestd writes, then waits 30 s; a 31 s old snapshot sits mid-cycle."""
    write_authority(settings.authority_path, T6_SNAPSHOT, published=NOW - timedelta(seconds=31))
    s = ta.registration_snapshot(settings, now=NOW)
    assert s["stale"] is False and s["stale_after_s"] == 75.0
    write_authority(settings.authority_path, T6_SNAPSHOT, published=NOW - timedelta(seconds=76))
    assert ta.registration_snapshot(settings, now=NOW)["stale"] is True
