"""The registration routes with hf-timestd stopped.

authority_history.db runs in WAL mode inside a directory the station-web
unit mounts read-only.  While hf-timestd holds the database open, the
``-wal`` and ``-shm`` files exist and a ``mode=ro`` reader works.  A clean
stop deletes both; a plain ``mode=ro`` open must then create ``-shm``,
cannot, and fails "attempt to write a readonly database".  The routes must
still answer: the snapshot with its age, the history from the file.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from station_web.services import freshness
from station_web.services import timing_authority as ta
from timing_fixture import T6_SNAPSHOT, history_row, write_authority, write_history

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")


@pytest.fixture
def stopped(settings):
    """A closed WAL history in a read-only directory, as a clean stop leaves it."""
    now = datetime.now(timezone.utc)
    write_authority(settings.authority_path, T6_SNAPSHOT, published=now - timedelta(seconds=300))
    hist = settings.authority_history_path
    write_history(hist, [history_row(now - timedelta(seconds=30 * k), k) for k in range(20)])
    assert not os.path.exists(f"{hist}-wal") and not os.path.exists(f"{hist}-shm")
    d = hist.parent
    mode = d.stat().st_mode
    os.chmod(d, 0o555)
    try:
        yield settings
    finally:
        os.chmod(d, mode)


def test_history_db_is_wal(settings):
    import sqlite3
    write_history(settings.authority_history_path, [history_row(datetime.now(timezone.utc), 0)])
    con = sqlite3.connect(settings.authority_history_path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        con.close()


def test_registration_answers_with_hf_timestd_stopped(client, stopped):
    r = client.get("/api/timing/registration")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True and j["stale"] is True
    assert j["counter_epoch_changes_24h"]["count"] is None


def test_history_answers_with_hf_timestd_stopped(client, stopped):
    r = client.get("/api/timing/registration/history?start=-1h&end=now")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True and j["n_rows"] == 20


def test_freshness_reads_the_history_with_hf_timestd_stopped(stopped):
    e = freshness._authority_entries(stopped, datetime.now(timezone.utc))
    assert e["authority_history.db"]["age_s"] is not None


def test_unreadable_history_degrades_the_epoch_count(client, settings):
    """A history file that will not open costs the epoch count, not the snapshot."""
    write_authority(settings.authority_path, T6_SNAPSHOT,
                    published=datetime.now(timezone.utc) - timedelta(seconds=3))
    settings.authority_history_path.parent.mkdir(parents=True, exist_ok=True)
    settings.authority_history_path.write_bytes(b"not a database, " * 64)
    r = client.get("/api/timing/registration")
    assert r.status_code == 200
    c = r.json()["counter_epoch_changes_24h"]
    assert c["count"] is None and "unreadable" in c["reason"]


def test_unreadable_history_route_answers_unavailable(client, settings):
    settings.authority_history_path.parent.mkdir(parents=True, exist_ok=True)
    settings.authority_history_path.write_bytes(b"not a database, " * 64)
    r = client.get("/api/timing/registration/history?start=-1h&end=now")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False and "unreadable" in j["reason"]
