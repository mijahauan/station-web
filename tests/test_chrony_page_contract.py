"""The chrony page and ChronyService agree on field names and the window.

On B4 the page read ``tracking.system_time_offset_us``, ``offset_ms`` and
``reach_oct`` while the service sent ``sys_offset_us`` and ``offset_us``;
two of four panels died on a TypeError.  The history always ended "now",
so stepping the selector back a day still showed the last 24 h.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import _write_rows

# chrony folded into the Timing page; js/timing.js now reads these fields
PAGE = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static" / "js" / "timing.js"


def _row(t, name, **extra):
    r = {"timestamp_utc": t.isoformat().replace("+00:00", "Z"), "unix_time": t.timestamp(),
         "source_name": name, "source_mode": "#", "source_state": "*", "stratum": 0,
         "reach": 255, "offset_us": 50.0, "error_us": 500.0, "n_samples": 6,
         "frequency_ppm": -1.1, "freq_skew_ppm": 0.02, "std_dev_us": 300.0}
    r.update(extra)
    return r


def _seed_tracking(settings):
    t = datetime.now(timezone.utc) - timedelta(seconds=30)
    _write_rows(settings.data_root, settings.storage, "DIAG", "chrony_stats", "fusion", "fusion", [
        _row(t, "HPPS", sys_offset_s=2.5e-5, rms_offset_s=4.0e-5,
             reference_id="46555345 (FUSE)"),
    ])


def test_comparison_carries_what_the_page_reads(seeded, seeded_client):
    _seed_tracking(seeded)
    j = seeded_client.get("/api/chrony/comparison").json()
    t = j["tracking"]
    assert t["sys_offset_us"] == 25.0
    assert t["rms_offset_us"] == 40.0
    assert t["reference_id"] == "46555345 (FUSE)"
    page = PAGE.read_text()
    for field in ("sys_offset_us", "rms_offset_us", "reference_id"):
        assert f"t.{field}" in page, field
    src = j["sources"][0]
    for field in ("name", "mode", "state", "stratum", "offset_us", "error_us",
                  "std_dev_us", "frequency_ppm", "freq_skew_ppm", "n_samples", "reach"):
        assert field in src, field
        assert f"s.{field}" in page, field


def test_page_reads_no_field_the_product_lacks():
    page = PAGE.read_text()
    for gone in ("system_time_offset_us", "root_delay_ms", "update_interval_s",
                 "leap_status", "skew_ppm}", "reach_oct", "s.type", "s.offset_ms",
                 "s.error_ms", "TSL1", "TSL2"):
        # \b keeps "series.offset_ms" (history, which does carry it) out of "s.offset_ms"
        assert not re.search(r"\b" + re.escape(gone), page), gone


def test_history_honours_the_selected_window(seeded, seeded_client):
    old = datetime.now(timezone.utc) - timedelta(days=2)
    _write_rows(seeded.data_root, seeded.storage, "DIAG", "chrony_stats", "fusion", "fusion",
                [_row(old, "OLDSRC", sys_offset_s=1e-4)])
    day_start = old.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1) - timedelta(seconds=1)
    iso = lambda d: d.isoformat().replace("+00:00", "Z")
    j = seeded_client.get(f"/api/chrony/history?start={iso(day_start)}&end={iso(day_end)}").json()
    assert set(j["sources"]) == {"OLDSRC"}
    assert j["start"] == iso(day_start)
    # the default still reads the last 24 h, which excludes the old row
    j = seeded_client.get("/api/chrony/history").json()
    assert "OLDSRC" not in j["sources"] and "FUSE" in j["sources"]


def test_history_rejects_bad_window(seeded_client):
    assert seeded_client.get("/api/chrony/history?start=nonsense&end=now").status_code == 400
    r = seeded_client.get("/api/chrony/history?start=2026-10-02T00:00:00Z&end=2026-10-01T00:00:00Z")
    assert r.status_code == 400


def test_page_requests_the_window_not_hours():
    page = PAGE.read_text()
    assert re.search(r"/chrony/envelope', \{ start: range\.start, end: range\.end \}", page)
    assert "hours=" not in page
