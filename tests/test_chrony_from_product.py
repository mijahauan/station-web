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
