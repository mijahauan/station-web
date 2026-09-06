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
