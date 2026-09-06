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
    but the station is not healthy either."""
    t = freshness.table(settings)
    assert all(p["verdict"] == "INDETERMINATE" for p in t["products"].values())
    assert all(p["applicable"] is False for p in t["products"].values())
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
