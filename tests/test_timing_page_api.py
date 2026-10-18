"""The Timing page's own endpoints: the T3 history and the chrony envelope.

The T3 history draws a median line with a sigma band, so it decimates on
the server to at most ``MAX_POINTS`` buckets and pulls wrong-lock suspects
out before it takes the medians.  The chrony envelope folds the NTP pool
into one min/median/max band and keeps FUSE and HPPS apart as witnesses.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import _write_rows
from station_web.services import chrony_service as cs
from station_web.services import fusion_service as fs


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


# ── outlier classification (pure) ───────────────────────────────────────

def test_classify_quiet_series_has_no_suspects():
    vals = [0.3 + 0.01 * ((i * 7) % 5) for i in range(200)]
    kinds, _, scale = fs.classify_suspects(vals)
    assert set(kinds) == {"ok"}
    assert scale >= fs.SCALE_FLOOR_MS


def test_classify_marks_lattice_and_outlier_separately():
    vals = [0.3 + 0.01 * ((i * 7) % 5) for i in range(200)]
    vals[50] += 10.02          # one tick-lattice step: wrong lock
    vals[80] += 5.1            # another lattice step
    vals[120] += 7.5           # far off, but between lattice points
    vals[160] += 0.5           # within 5 sigma of the floor: stays ok
    kinds, resid, _ = fs.classify_suspects(vals)
    assert kinds[50] == "lattice" and kinds[80] == "lattice"
    assert kinds[120] == "outlier"
    assert kinds[160] == "ok"
    assert abs(resid[50] - 10.02) < 0.05
    assert sum(k != "ok" for k in kinds) == 3


def test_classify_handles_short_and_empty_input():
    assert fs.classify_suspects([]) == ([], [], fs.SCALE_FLOOR_MS)
    kinds, _, _ = fs.classify_suspects([1.0, 1.1])
    assert kinds == ["ok", "ok"]


# ── T3 history endpoint ─────────────────────────────────────────────────

def _seed_fusion(settings, n, step_s=60, spike_at=()):
    t0 = datetime.now(timezone.utc) - timedelta(seconds=step_s * n + 30)
    rows = []
    for i in range(n):
        v = 0.31 + 0.004 * (i % 17)
        if i in spike_at:
            v += 10.0
        rows.append({"timestamp_utc": _iso(t0 + timedelta(seconds=step_s * i)),
                     "minute_boundary": int((t0 + timedelta(seconds=step_s * i)).timestamp()),
                     "d_clock_fused_ms": v, "d_clock_raw_ms": v + 14.0,
                     "uncertainty_ms": 0.22, "statistical_uncertainty_ms": 0.1,
                     "systematic_uncertainty_ms": 0.1, "propagation_uncertainty_ms": 0.1,
                     "n_broadcasts": 4, "n_stations": 2, "stations_used": "WWV,WWVH",
                     "wwv_count": 3, "wwvh_count": 1, "bpm_count": 0,
                     "consistency_flag": "OK", "global_solve_verified": True,
                     "global_solve_n_obs": 4, "calibration_applied": True,
                     "outliers_rejected": 0, "quality_grade": "A", "kalman_state": "LOCKED",
                     "quality_flag": "GOOD", "processing_version": "7.0.0",
                     "single_station_mode": False})
    _write_rows(settings.data_root, settings.storage, "L3", "fusion_timing", "fusion",
                "fusion", rows)


def test_t3_history_shape_and_calibration(settings, client):
    _seed_fusion(settings, 300, spike_at=(100, 200))
    j = client.get("/api/metrology/t3/history?start=-1d&end=now").json()
    assert j["available"] is True
    n = j["n_points"]
    for key in ("time", "calibrated_ms", "raw_ms", "sigma_ms"):
        assert len(j[key]) == n, key
    assert j["time"][0].endswith("Z")
    assert j["fields"] == {"calibrated": "d_clock_fused_ms", "raw": "d_clock_raw_ms",
                           "sigma": "uncertainty_ms"}
    cal = j["calibration"]
    assert cal["offset_ms"] == pytest.approx(14.0, abs=1e-6)
    assert cal["n"] == 300
    s = j["suspects"]
    assert j["n_suspect"] == 2 and s["kind"] == ["lattice", "lattice"]
    assert len(s["time"]) == len(s["value_ms"]) == len(s["raw_ms"]) == len(s["residual_ms"]) == 2
    # the suspects stay out of the median line
    assert max(j["calibrated_ms"]) < 1.0
    assert j["classification"]["window_points"] == fs.ROLLING_WINDOW


def test_t3_history_decimates_to_the_bound(settings, client):
    # one row in four on the lattice: 2000 suspects, more than the cap, while
    # the rolling median still follows the other three
    _seed_fusion(settings, 8000, step_s=10, spike_at=tuple(range(1, 8000, 4)))
    j = client.get("/api/metrology/t3/history?start=-2d&end=now").json()
    assert j["n_rows"] == 8000
    assert j["n_suspect"] == 2000
    assert 0 < j["n_points"] <= fs.MAX_POINTS
    assert len(j["time"]) <= fs.MAX_POINTS
    assert j["bucket_s"] is not None
    # suspects are capped too, and say so
    assert len(j["suspects"]["time"]) <= fs.MAX_POINTS
    assert j["suspects_truncated"] is (j["n_suspect"] > fs.MAX_POINTS)


def test_t3_history_empty_window_says_so(client):
    j = client.get("/api/metrology/t3/history?start=-1h&end=now").json()
    assert j["available"] is False and j["reason"]
    assert j["time"] == [] and j["n_points"] == 0


def test_t3_history_window_cap_and_bad_time(client):
    r = client.get("/api/metrology/t3/history?start=-30d&end=now")
    assert r.status_code == 400
    r = client.get("/api/metrology/t3/history?start=<script>&end=now")
    assert r.status_code == 400 and "<script>" not in r.text


# ── chrony envelope ─────────────────────────────────────────────────────

def _chrony_row(t, name, mode, offset_us, sys_s=1.0e-4, state="-"):
    return {"timestamp_utc": _iso(t), "unix_time": t.timestamp(), "source_name": name,
            "source_mode": mode, "source_state": state, "stratum": 1, "reach": 255,
            "offset_us": offset_us, "error_us": 500.0, "n_samples": 6,
            "frequency_ppm": -1.0, "freq_skew_ppm": 0.02, "std_dev_us": 300.0,
            "sys_offset_s": sys_s}


def _seed_chrony(settings, minutes=30):
    t0 = datetime.now(timezone.utc) - timedelta(minutes=minutes + 1)
    rows = []
    for m in range(minutes):
        t = t0 + timedelta(minutes=m)
        rows += [_chrony_row(t, "FUSE", "#", 900.0), _chrony_row(t, "HPPS", "#", 20.0),
                 _chrony_row(t, "ntp-a", "^", -100.0), _chrony_row(t, "ntp-b", "^", 300.0),
                 _chrony_row(t, "ntp-c", "^", 50.0, state="*")]
    _write_rows(settings.data_root, settings.storage, "DIAG", "chrony_stats", "fusion",
                "fusion", rows)


def test_chrony_envelope_shape(settings, client):
    _seed_chrony(settings)
    j = client.get("/api/chrony/envelope?start=-2h&end=now").json()
    assert j["available"] is True
    n = len(j["time"])
    assert 0 < n <= cs.MAX_POINTS and j["time"][0].endswith("Z")
    ntp = j["ntp"]
    assert sorted(ntp["members"]) == ["ntp-a", "ntp-b", "ntp-c"]
    for key in ("min_ms", "median_ms", "max_ms", "n_sources"):
        assert len(ntp[key]) == n, key
    assert ntp["min_ms"][0] == pytest.approx(-0.1)
    assert ntp["median_ms"][0] == pytest.approx(0.05)
    assert ntp["max_ms"][0] == pytest.approx(0.3)
    assert set(j["witnesses"]) == {"FUSE", "HPPS"}
    assert j["witnesses"]["FUSE"]["offset_ms"][0] == pytest.approx(0.9)
    assert j["witnesses"]["HPPS"]["role"] == "noselect witness"
    assert len(j["system_offset_ms"]) == n
    assert j["system_offset_ms"][0] == pytest.approx(0.1)
    assert j["label"] == "host clock minus registration"


def test_chrony_envelope_decimates(settings, client):
    _seed_chrony(settings, minutes=1)
    t0 = datetime.now(timezone.utc) - timedelta(days=3)
    rows = [_chrony_row(t0 + timedelta(seconds=60 * i), "ntp-a", "^", float(i))
            for i in range(3 * 1440)]
    _write_rows(settings.data_root, settings.storage, "DIAG", "chrony_stats", "fusion",
                "fusion", rows)
    j = client.get("/api/chrony/envelope?start=-4d&end=now").json()
    assert len(j["time"]) <= cs.MAX_POINTS
    assert j["bucket_s"] > 60


def test_chrony_envelope_empty_and_caps(client):
    j = client.get("/api/chrony/envelope?start=-1h&end=now").json()
    assert j["available"] is False and j["time"] == []
    assert client.get("/api/chrony/envelope?start=-30d&end=now").status_code == 400
    r = client.get("/api/chrony/envelope?start=<b>&end=now")
    assert r.status_code == 400 and "<b>" not in r.text
