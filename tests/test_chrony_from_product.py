from datetime import datetime, timedelta, timezone

from conftest import _write_rows

from station_web.services.chrony_service import ChronyService, STALE_AFTER_S


def test_snapshot_is_latest_row_per_source(seeded):
    svc = ChronyService(data_root=seeded.data_root, storage_config=seeded.storage)
    snap = svc.get_live_snapshot()
    assert snap is not None
    names = {s["name"] for s in snap["sources"]}
    assert names == {"FUSE", "192.168.1.1"}
    fuse = next(s for s in snap["sources"] if s["name"] == "FUSE")
    assert fuse["state"] == "*" and fuse["offset_us"] == 120.0
    assert snap["tracking"]["sys_offset_us"] == 120.0
    assert snap["stale"] is False and snap["age_s"] < STALE_AFTER_S


def test_snapshot_none_without_rows(settings):
    svc = ChronyService(data_root=settings.data_root, storage_config=settings.storage)
    assert svc.get_live_snapshot() is None


def test_comparison_selected_first(seeded):
    svc = ChronyService(data_root=seeded.data_root, storage_config=seeded.storage)
    cmp_ = svc.get_source_comparison()
    assert cmp_["sources"][0]["name"] == "FUSE"
    assert "stale" in cmp_ and "age_s" in cmp_


def test_tracking_uses_newest_row_not_iteration_order(seeded):
    """A source that stopped reporting 300 s ago must not override the
    tracking offset the fresh sources just wrote — even though it still
    shows up in ``sources`` (it's within the 600 s lookback window)."""
    root, storage = seeded.data_root, seeded.storage
    old_t = datetime.now(timezone.utc) - timedelta(seconds=90 + 300)
    _write_rows(root, storage, "DIAG", "chrony_stats", "fusion", "fusion", [
        {"timestamp_utc": old_t.isoformat().replace("+00:00", "Z"),
         "unix_time": old_t.timestamp(),
         "source_name": "OLD", "source_mode": "^", "source_state": "-",
         "stratum": 3, "reach": 377, "offset_us": 999.0, "error_us": 3000.0,
         "n_samples": 8, "frequency_ppm": -1.42, "freq_skew_ppm": 0.03,
         "std_dev_us": 2000.0, "sys_offset_s": 0.5},
    ])

    svc = ChronyService(data_root=root, storage_config=storage)
    snap = svc.get_live_snapshot()
    assert snap is not None
    names = {s["name"] for s in snap["sources"]}
    assert "OLD" in names
    assert snap["tracking"]["sys_offset_us"] == 120.0
