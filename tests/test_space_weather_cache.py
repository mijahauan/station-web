import json
from datetime import datetime, timedelta, timezone

from station_web.services.space_weather_cache import SpaceWeatherCache, STALE_AFTER_S


def test_current_from_cache(seeded):
    c = SpaceWeatherCache(seeded.data_root).current()
    assert c["available"] is True and c["f107"] == 142.0 and c["kp"] == 2.3
    assert c["age_s"] is not None and c["stale"] is False


def test_missing_cache(settings):
    c = SpaceWeatherCache(settings.data_root).current()
    assert c["available"] is False and c["f107"] is None and c["age_s"] is None


def test_stale_flag(seeded):
    p = seeded.data_root / "iono_cache" / "space_weather.json"
    d = json.loads(p.read_text())
    d["fetched_at"] = (datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S + 60)).isoformat()
    p.write_text(json.dumps(d))
    assert SpaceWeatherCache(seeded.data_root).current()["stale"] is True


def test_series_endpoints_say_unavailable(seeded_client):
    for path in ("/api/space-weather/xray", "/api/space-weather/kp", "/api/space-weather/protons"):
        r = seeded_client.get(path)
        assert r.status_code == 200 and r.json()["available"] is False, path


def test_no_poller_thread_started(seeded_client):
    import threading
    assert not any("space" in t.name.lower() for t in threading.enumerate())


def test_space_weather_summary_shape(seeded_client):
    j = seeded_client.get("/api/space-weather/summary").json()
    assert set(j) >= {"current", "xray", "geomagnetic", "protons", "events"}
    assert j["current"]["f107"] == 142.0
    for k in ("xray", "geomagnetic", "protons", "events"):
        assert j[k]["available"] is False


def test_correlations_summary_says_unavailable(seeded_client):
    j = seeded_client.get("/api/correlations/summary").json()
    assert j["available"] is False and "reason" in j


def test_naive_fetched_at_does_not_raise(seeded):
    """A NAIVE ``fetched_at`` (a producer that formatted ``utcnow()``)
    raises TypeError against an aware ``now`` — not the ValueError the
    guard caught — so one malformed cache file used to 500 every
    space-weather page.  Naive can only mean UTC here."""
    p = seeded.data_root / "iono_cache" / "space_weather.json"
    d = json.loads(p.read_text())
    naive = (datetime.now(timezone.utc) - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
    assert not naive.endswith("Z") and "+" not in naive
    d["fetched_at"] = naive
    p.write_text(json.dumps(d))

    c = SpaceWeatherCache(seeded.data_root).current()
    assert c["age_s"] is not None and 30 < c["age_s"] < 300 and c["stale"] is False
