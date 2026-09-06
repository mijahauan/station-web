from datetime import datetime, timedelta, timezone

from conftest import PROC_VERSION, _write_rows

from station_web.services import freshness


def test_seeded_products_valid(seeded):
    t = freshness.table(seeded)
    assert t["products"]["L3_fusion_timing"]["verdict"] == "VALID"
    assert t["products"]["DIAG_chrony_stats"]["verdict"] == "VALID"
    assert t["products"]["L3_gnss_vtec"]["verdict"] == "INDETERMINATE"      # not seeded: honest
    assert t["status"] in {"healthy", "degraded"}


def test_empty_root_is_indeterminate_not_error(settings):
    t = freshness.table(settings)
    assert all(p["verdict"] == "INDETERMINATE" for p in t["products"].values())
    assert t["status"] == "degraded"


def test_health_system_route_shape(seeded_client):
    j = seeded_client.get("/api/health/system").json()
    assert set(j) >= {"status", "timestamp", "products", "channels"}
    assert "processes" not in j and "disk_usage_percent" not in j
    from station_web.app import STATUS
    assert STATUS["products"]["L3_fusion_timing"] == "VALID"


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
    assert t["products"]["L2_timing_measurements"] == {"verdict": "INDETERMINATE", "age_s": None,
                                                        "expected_s": 120, "channel": None}
