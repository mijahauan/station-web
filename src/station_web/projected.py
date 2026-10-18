"""Read only the columns a route uses, in chunks, from the product database.

hamsci-dsp's ``read_time_range`` runs ``SELECT *`` and ``fetchall()``, then
builds one dict per row for the whole window.  ``L3_fusion_timing`` carries
48 columns, ``L2_tick_phase`` about 20, and a route that plots two of them
paid for all of them (final review S1, S2).

``iter_rows`` names its columns, leads its WHERE clause with ``channel = ?``
and a ``timestamp_utc`` range so SQLite seeks the one index these tables
carry, ``(channel, timestamp_utc)``, and fetches in chunks.  Rows come back
in timestamp order and cut exactly to ``[start, end]`` on the parsed
timestamp, as ``read_time_range`` cuts them.  Two differences: a column
missing from the table reads as None, and a SQLite error raises (a fault
is not an empty window).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from hamsci_dsp.io.sqlite_writer import DEFAULT_DB_PATH

from station_web.config import config
from station_web.timeutil import as_utc

#: Rows per fetch: bounds the Python objects alive at once.
CHUNK = 20000
#: The SQL bounds widen by this much; the exact cut happens in Python.
_SLACK = timedelta(seconds=2)


def db_path() -> Path:
    storage = getattr(config, "storage", {}) or {}
    return Path(storage.get("sqlite_path", DEFAULT_DB_PATH))


def connect() -> Optional[sqlite3.Connection]:
    """A read-only connection, or None when the database file is absent."""
    path = db_path()
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def table_columns(conn: sqlite3.Connection, table: str) -> set:
    """The table's column names; empty when the table does not exist."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _parse(ts: str) -> datetime:
    return as_utc(datetime.fromisoformat(str(ts).replace("Z", "+00:00")))


def iter_rows(table: str, channel: str, start: datetime, end: datetime,
              columns: Sequence[str], station: Optional[str] = None,
              chunk: int = CHUNK) -> Iterator[List[Tuple[Any, ...]]]:
    """Yield lists of up to ``chunk`` tuples, one value per name in ``columns``.

    ``timestamp_utc`` need not be among ``columns``; the cut uses it anyway.
    """
    conn = connect()
    if conn is None:
        return
    try:
        present = table_columns(conn, table)
        if not present:
            return
        exprs = ["timestamp_utc"] + [c if c in present else "NULL" for c in columns]
        start, end = as_utc(start), as_utc(end)
        sql = (f"SELECT {', '.join(exprs)} FROM {table} "
               "WHERE channel = ? AND timestamp_utc BETWEEN ? AND ?")
        params: list = [channel, (start - _SLACK).isoformat(), (end + _SLACK).isoformat()]
        if station is not None and "station" in present:
            sql += " AND station = ?"
            params.append(station)
        sql += " ORDER BY timestamp_utc, ROWID"
        cur = conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(chunk)
            if not rows:
                return
            out = []
            for r in rows:
                if not r[0]:
                    continue
                try:
                    t = _parse(r[0])
                except (ValueError, TypeError):
                    continue
                if start <= t <= end:
                    out.append(r[1:])
            if out:
                yield out
    finally:
        conn.close()


def read_rows(table: str, channel: str, start: datetime, end: datetime,
              columns: Sequence[str], station: Optional[str] = None) -> List[Dict[str, Any]]:
    """``iter_rows`` as a list of dicts keyed by ``columns``: a drop-in for
    ``read_time_range`` where the caller uses a known, short list of fields."""
    out: List[Dict[str, Any]] = []
    for part in iter_rows(table, channel, start, end, columns, station):
        out.extend(dict(zip(columns, r)) for r in part)
    return out
