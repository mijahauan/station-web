"""A table that exists but stays empty is a silent producer, not a fault (D9).

On B4 ``L3_dtec`` and ``L3_physics`` exist (hamsci-physics created them)
but never get a row.  They counted as applicable, read INDETERMINATE, and
held the station at "degraded" forever.  A product now counts as
applicable only if it held a row within ``APPLICABLE_WINDOW_DAYS``;
otherwise it reads SILENT, visibly, without judging the station.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from station_web.services import freshness


def _empty(settings, table):
    con = sqlite3.connect(settings.storage["sqlite_path"])
    con.execute(f'DELETE FROM "{table}"')
    con.commit()
    con.close()


def _age(settings, table, days):
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    con = sqlite3.connect(settings.storage["sqlite_path"])
    con.execute(f'UPDATE "{table}" SET timestamp_utc = ?', (old,))
    con.commit()
    con.close()


def test_empty_tables_read_silent_and_station_stays_healthy(seeded):
    _empty(seeded, "L3_dtec")
    _empty(seeded, "L3_physics")
    t = freshness.table(seeded)
    for name in ("L3_dtec", "L3_physics"):
        p = t["products"][name]
        assert p["verdict"] == "SILENT", p
        assert p["applicable"] is False
        assert "producer silent" in p["reason"]
    assert t["status"] == "healthy"


def test_a_row_older_than_the_window_is_silent_not_invalid(seeded):
    _age(seeded, "L3_physics", freshness.APPLICABLE_WINDOW_DAYS + 1)
    t = freshness.table(seeded)
    p = t["products"]["L3_physics"]
    assert p["verdict"] == "SILENT" and p["age_s"] > freshness.APPLICABLE_WINDOW_DAYS * 86400
    assert t["status"] == "healthy"


def test_a_row_inside_the_window_but_stale_is_still_an_error(seeded):
    """Silence is weeks of nothing; a producer that stopped yesterday is a
    fault and must still turn the station red."""
    _age(seeded, "L3_physics", 1)
    t = freshness.table(seeded)
    assert t["products"]["L3_physics"]["verdict"] == "INVALID"
    assert t["status"] == "error"


def test_only_silent_products_is_degraded(seeded):
    """Nothing left to judge is not "healthy" either."""
    con = sqlite3.connect(seeded.storage["sqlite_path"])
    for tbl in ("L2_timing_measurements", "L3_fusion_timing", "DIAG_chrony_stats",
                "L3_tec", "L3_dtec", "L3_physics"):
        con.execute(f'DELETE FROM "{tbl}"')
    con.commit()
    con.close()
    t = freshness.table(seeded)
    assert {p["verdict"] for k, p in t["products"].items() if k != "L3_tid"} <= {"SILENT", "INDETERMINATE"}
    assert t["status"] == "degraded"


def test_docstring_names_every_verdict():
    doc = freshness.__doc__
    for v in ("VALID", "INVALID", "INDETERMINATE", "SILENT", "INFORMATIONAL"):
        assert v in doc, v
    assert "Four verdicts" not in doc
