import sqlite3
from datetime import datetime, timedelta, timezone

from conftest import PROC_VERSION, _write_rows

from station_web.services import freshness


def test_seeded_products_valid(seeded):
    """Every product whose table EXISTS is fresh, so the station is healthy.

    ``L3_gnss_vtec`` has no table here — gnss-vtec is not installed — so it
    is not applicable, and an absent client must not hold the station at
    "degraded" forever.  That is the whole point of the applicable flag."""
    t = freshness.table(seeded)
    assert t["products"]["L3_fusion_timing"]["verdict"] == "VALID"
    assert t["products"]["DIAG_chrony_stats"]["verdict"] == "VALID"
    assert t["products"]["L3_gnss_vtec"]["verdict"] == "INDETERMINATE"      # no rows: honest
    assert t["products"]["L3_gnss_vtec"]["applicable"] is False
    assert "no L3_gnss_vtec table" in t["products"]["L3_gnss_vtec"]["reason"]
    assert t["products"]["L3_fusion_timing"]["applicable"] is True
    assert "reason" not in t["products"]["L3_fusion_timing"]
    assert t["status"] == "healthy"


def test_tid_is_an_informational_event_product(seeded):
    """TID is written only when hamsci-physics detects a travelling
    ionospheric disturbance — there is no cadence to violate.  A fresh row
    (the seeded fixture writes one ~90 s old) still reports INFORMATIONAL,
    never VALID, and never counts toward ``status``."""
    p = freshness.table(seeded)["products"]["L3_tid"]
    assert p["verdict"] == "INFORMATIONAL"
    assert p["expected_s"] is None
    assert isinstance(p["age_s"], float)
    assert p["channel"] == "AGGREGATED"
    assert p["applicable"] is True
    assert "event product" in p["reason"]


def test_physics_cadence_is_300s(seeded):
    assert freshness.EXPECTED[("L3", "physics")] == 300
    assert freshness.table(seeded)["products"]["L3_physics"]["expected_s"] == 300


def test_stale_tid_does_not_affect_status(seeded):
    """A TID row three days old must not turn the station unhealthy: it is
    evidence there was no event recently, not evidence of a broken writer.

    Replaces the seeded fixture's fresh row (written ~90 s ago) with a
    3-day-old one via the same writer helper conftest uses, so the row
    ``_max_timestamp`` actually sees is the stale one."""
    root, storage = seeded.data_root, seeded.storage
    con = sqlite3.connect(storage["sqlite_path"])
    con.execute('DELETE FROM "L3_tid"')
    con.commit()
    con.close()

    old = datetime.now(timezone.utc) - timedelta(days=3)
    old_ts = old.isoformat().replace("+00:00", "Z")
    old_mb = int(old.replace(second=0, microsecond=0).timestamp())
    _write_rows(root, storage, "L3", "tid", "AGGREGATED", "fusion/tid", [
        {"timestamp_utc": old_ts, "minute_boundary_utc": old_mb,
         "event_id": "TID-OLD-0001", "period_minutes": 24.0,
         "amplitude_ms": 0.18, "velocity_m_s": 210.0, "direction_deg": 137.0,
         "correlation_coefficient": 0.74, "significance_p": 0.01,
         "confidence": 0.72, "n_paths_correlated": 3,
         "leading_path": "WWV_20000", "lagging_path": "WWV_15000",
         "lag_minutes": 6.5, "processing_version": PROC_VERSION},
    ])

    t = freshness.table(seeded)
    p = t["products"]["L3_tid"]
    assert p["verdict"] == "INFORMATIONAL"
    assert p["age_s"] > 3 * 24 * 3600 - 10
    assert t["status"] == "healthy"


def test_gnss_vtec_reads_the_gnss_channel(seeded):
    """Both writers of L3_gnss_vtec use channel 'GNSS'.  A row on that
    channel must register — before the FIXED_CHANNEL entry existed, the
    per-receiver sweep looked under WWV_20000 and found nothing forever."""
    assert freshness.FIXED_CHANNEL[("L3", "gnss_vtec")] == "GNSS"
    con = sqlite3.connect(seeded.storage["sqlite_path"])
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    con.execute('CREATE TABLE "L3_gnss_vtec" (timestamp_utc TEXT, channel TEXT, vtec_tecu REAL)')
    con.execute('INSERT INTO "L3_gnss_vtec" VALUES (?, ?, ?)', (ts, "GNSS", 12.4))
    con.commit()
    con.close()

    p = freshness.table(seeded)["products"]["L3_gnss_vtec"]
    assert p["applicable"] is True and p["verdict"] == "VALID" and p["channel"] == "GNSS"


def test_empty_root_is_indeterminate_not_error(settings):
    """No database at all: nothing is applicable, so nothing is a fault —
    but the station is not healthy either.  ``L3_tid`` is the one exception:
    an event product reports INFORMATIONAL regardless of data, never
    INDETERMINATE — there is no cadence for it to have failed."""
    t = freshness.table(settings)
    non_event = {k: v for k, v in t["products"].items() if k != "L3_tid"}
    assert all(p["verdict"] == "INDETERMINATE" for p in non_event.values())
    assert all(p["applicable"] is False for p in t["products"].values())
    assert t["products"]["L3_tid"]["verdict"] == "INFORMATIONAL"
    assert t["products"]["L3_tid"]["age_s"] is None
    assert t["status"] == "degraded"


def test_a_stale_applicable_product_is_an_error(seeded):
    """An applicable product that went quiet is INVALID, and one INVALID
    makes the whole table 'error' — the flag never masks a real fault."""
    con = sqlite3.connect(seeded.storage["sqlite_path"])
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    con.execute('UPDATE "L3_fusion_timing" SET timestamp_utc = ?', (old,))
    con.commit()
    con.close()

    t = freshness.table(seeded)
    assert t["products"]["L3_fusion_timing"]["verdict"] == "INVALID"
    assert t["status"] == "error"


def test_a_naive_stored_timestamp_does_not_raise(seeded):
    """A producer that stored a NAIVE timestamp must not 500 the health
    table: subtracting naive from an aware ``now`` raises TypeError, which
    no ValueError guard would catch.  Naive can only mean UTC here."""
    con = sqlite3.connect(seeded.storage["sqlite_path"])
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()   # no Z, no offset
    assert not naive.endswith("Z")
    con.execute('UPDATE "L3_physics" SET timestamp_utc = ?', (naive,))
    con.commit()
    con.close()

    p = freshness.table(seeded)["products"]["L3_physics"]
    assert p["verdict"] == "VALID" and p["age_s"] is not None


def test_health_system_route_shape(seeded_client):
    j = seeded_client.get("/api/health/system").json()
    assert set(j) >= {"status", "timestamp", "products", "channels"}
    assert "processes" not in j and "disk_usage_percent" not in j
    from station_web.app import STATUS
    assert STATUS["products"]["L3_fusion_timing"] == "VALID"
    # /metrics must not carry a permanent 0 for a client that is not here.
    assert "L3_gnss_vtec" not in STATUS["products"]


def test_receiver_products_use_the_freshest_channel(seeded):
    """A per-receiver-channel product must pick the newest row across
    EVERY discovered channel — not just the alphabetically first one.
    ``BPM_5000`` sorts before ``WWV_20000`` and went quiet an hour ago;
    the freshness verdict must still come from the live WWV channel."""
    root, storage = seeded.data_root, seeded.storage
    old_t = datetime.now(timezone.utc) - timedelta(seconds=3600)
    old_ts = old_t.isoformat().replace("+00:00", "Z")
    old_mb = int(old_t.replace(second=0, microsecond=0).timestamp())
    _write_rows(root, storage, "L2", "timing_measurements", "BPM_5000",
                "BPM_5000/clock_offset", [
        {"timestamp_utc": old_ts, "minute_boundary_utc": old_mb, "rtp_timestamp": 480000,
         "station": "BPM", "frequency_mhz": 5.0,
         "discrimination_method": "TONE", "discrimination_confidence": 0.94,
         "tone_detected": True, "raw_arrival_time_ms": 8.42,
         "clock_offset_ms": 0.31, "uncertainty_ms": 0.25,
         "expanded_uncertainty_ms": 0.50, "coverage_factor": 2.0,
         "confidence_level": 0.95, "u_rtp_timestamp_ms": 0.02,
         "u_ionospheric_ms": 0.18, "u_multipath_ms": 0.12,
         "u_discrimination_ms": 0.05, "u_gpsdo_ms": 0.001,
         "u_propagation_model_ms": 0.09, "degrees_of_freedom": 12,
         "quality_grade": "A", "confidence": 0.94, "quality_flag": "GOOD",
         "traceability_chain": "UTC(NIST)->BPM->RTP->host",
         "processing_version": PROC_VERSION, "processed_at": old_ts,
         "calibration_date": "2026-09-01", "gpsdo_locked": True,
         "snr_db": 18.0, "propagation_mode": "1F2"},
    ])

    t = freshness.table(seeded)
    p = t["products"]["L2_timing_measurements"]
    assert p["verdict"] == "VALID" and p["channel"] == "WWV_20000"


def test_no_channels_means_indeterminate(settings):
    t = freshness.table(settings)
    assert t["products"]["L2_timing_measurements"] == {
        "verdict": "INDETERMINATE", "age_s": None, "expected_s": 120, "channel": None,
        "applicable": False,
        "reason": "no L2_timing_measurements table — producing client not installed"}
