"""A table that exists but stays empty is a silent producer, not a fault (D9).

On B4 ``L3_dtec`` and ``L3_physics`` exist (hamsci-physics created them)
but never get a row.  They counted as applicable, read INDETERMINATE, and
held the station at "degraded" forever.  Such a table now reads SILENT,
visibly, without judging the station.

SILENT means only that: the table has never held a row here.  A product
that wrote rows and then stopped stays INVALID however long ago it
stopped (final review S6).  An earlier rule let a producer dead for more
than seven days read SILENT, and the station flip back to "healthy" with
nobody having fixed anything.
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


def test_a_producer_dead_for_weeks_stays_an_error(seeded):
    """A producer that wrote and then stopped is a fault, however long ago."""
    for days in (1, 8, 400):
        _age(seeded, "L3_physics", days)
        t = freshness.table(seeded)
        p = t["products"]["L3_physics"]
        assert p["verdict"] == "INVALID", (days, p)
        assert p["applicable"] is True
        assert p["age_s"] > days * 86400 - 60
        assert t["status"] == "error", days


def test_rows_only_under_another_channel_are_not_silent(seeded):
    """The table has held rows, just not on the channel the writer uses:
    something changed, and the page must say so rather than go quiet."""
    con = sqlite3.connect(seeded.storage["sqlite_path"])
    con.execute('UPDATE "L3_physics" SET channel = ?', ("elsewhere",))
    con.commit()
    con.close()
    p = freshness.table(seeded)["products"]["L3_physics"]
    assert p["verdict"] == "INVALID" and p["applicable"] is True
    assert "reason" in p


def test_only_silent_products_is_degraded(seeded):
    """Nothing left to judge is not "healthy" either."""
    con = sqlite3.connect(seeded.storage["sqlite_path"])
    for tbl in ("L2_timing_measurements", "L3_fusion_timing", "DIAG_chrony_stats",
                "L3_tec", "L3_dtec", "L3_physics"):
        con.execute(f'DELETE FROM "{tbl}"')
    con.commit()
    con.close()
    t = freshness.table(seeded)
    assert {p["verdict"] for k, p in t["products"].items() if k != "L3_tid"
            and k not in freshness.AUTHORITY_EXPECTED} <= {"SILENT", "INDETERMINATE"}
    assert t["status"] == "degraded"


def test_docstring_names_every_verdict():
    doc = freshness.__doc__
    for v in ("VALID", "INVALID", "INDETERMINATE", "SILENT", "INFORMATIONAL"):
        assert v in doc, v
    assert "Four verdicts" not in doc
