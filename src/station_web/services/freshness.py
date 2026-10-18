"""Health = are the products fresh?  No process or disk probes: sigmond owns those.

Each product gets one of five verdicts (the first three borrowed from the
heartbeat schema):

- VALID: the newest row is no older than the product's expected cadence.
- INVALID: the newest row is older than expected.  A producer that
  stopped, however long ago: a product that wrote rows on this station and
  then went quiet is a fault until someone fixes it or removes the table.
  Rows under some channel other than the writer's also read INVALID.
- INDETERMINATE: there is no table at all (the producing client is not
  installed here), or no database.
- SILENT: the table exists but has never held a row on this station
  ("producer silent").  On B4, hamsci-physics created ``L3_dtec`` and
  ``L3_physics`` and never filled them.  (Retention pruning that emptied a
  table would also read SILENT; nothing on these stations prunes yet.)
- INFORMATIONAL: an event product (``EVENT_PRODUCTS``) with no cadence;
  only its age is reported.

Only VALID and INVALID products are *applicable* and judge ``status``:
any INVALID is "error", all VALID is "healthy", and nothing to judge is
"degraded".  SILENT and INDETERMINATE stay visible in the table, but
neither one alone holds the station at "degraded".
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from station_web.config import Settings
from station_web.services import timing_authority
from station_web.services.channels import _db_path, discover_channels

logger = logging.getLogger(__name__)

#: (level, name) -> expected max age in seconds
EXPECTED = {
    ("L2", "timing_measurements"): 300,  # flushed ~every 2 min; 300 leaves headroom
    ("L3", "fusion_timing"): 120, ("DIAG", "chrony_stats"): 120,
    # L3_tec: the hourly reanalysis writes the previous hour at :05, so its
    # newest row is ~70 min old just before each run.
    ("L3", "tec"): 7200, ("L3", "dtec"): 300, ("L3", "physics"): 300,
    ("L3", "gnss_vtec"): 900,
}
#: products written under a fixed channel rather than per receiver channel
#: (matches tests/conftest.py's ``seeded`` fixture — the only ground truth
#: for which channel each writer actually uses).  ``L3_gnss_vtec`` carries
#: channel 'GNSS': both writers that produce it (gnss-vtec's own service and
#: the hf-timestd bridge) hard-code that literal, so the per-receiver channel
#: sweep would never have found a row.  A tuple names several channels; the
#: newest row across them counts.  ``L3_tec`` has two writers: the hourly
#: reanalysis (``REANALYZED``) and physics-fusion (``AGGREGATED``).
FIXED_CHANNEL = {("L3", "fusion_timing"): "fusion", ("DIAG", "chrony_stats"): "fusion",
                 ("L3", "physics"): "global", ("L3", "tec"): ("REANALYZED", "AGGREGATED"),
                 ("L3", "dtec"): "AGGREGATED", ("L3", "gnss_vtec"): "GNSS"}

#: (level, name) -> fixed channel, for products with no cadence expectation at
#: all: hamsci-physics writes a TID row only when it detects a travelling
#: ionospheric disturbance, so "older than N seconds" is not a fault — it is
#: just quiet.  These never get a VALID/INVALID verdict and never count
#: toward ``status``; age is reported for operator information only.
EVENT_PRODUCTS = {("L3", "tid"): "AGGREGATED"}


def _parse_utc(ts: str) -> datetime:
    """A stored timestamp as an AWARE UTC datetime.

    Every writer is supposed to store ``...Z``, but a naive value has
    reached these tables before (a producer that formatted a naive
    ``datetime.utcnow()``).  Subtracting a naive datetime from an aware
    ``now`` raises TypeError, which would turn one malformed row into a
    500 on /api/health/system.  Treat naive as UTC — the only meaning it
    could have here — rather than failing the whole health table.
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _existing_tables(settings: Settings) -> set[str]:
    """The product tables that actually exist in the database.

    A table that was never created means its producing client is not
    installed on this station, which is a different fact from "installed
    and silent".  ``table()`` uses it to mark a product not applicable
    instead of reporting a permanent INDETERMINATE that reads as a fault.
    """
    db = _db_path(settings)
    if not db.exists():
        return set()
    names: set[str] = set()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for level, name in list(EXPECTED) + list(EVENT_PRODUCTS):
                tbl = f"{level}_{name}"
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                               (tbl,)).fetchone():
                    names.add(tbl)
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("freshness table probe on %s failed: %s", db, exc)
        return set()
    return names


def _max_timestamp(settings: Settings, level: str, name: str, channel: str) -> str | None:
    """Newest ``timestamp_utc`` for one (level, name, channel).

    A single indexed ``MAX`` query — no reader, no full-window page
    fetch just to keep the last row. A missing database or a missing
    table (product never written) is the expected, common state and
    resolves quietly to ``None``.
    """
    db = _db_path(settings)
    if not db.exists():
        return None
    table = f"{level}_{name}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                f'SELECT MAX(timestamp_utc) FROM "{table}" WHERE channel = ?', (channel,)
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("freshness MAX query on %s/%s failed: %s", table, channel, exc)
        return None
    return row[0] if row else None


def _holds_any_row(settings: Settings, table: str) -> bool:
    """True when ``table`` holds at least one row, on any channel."""
    db = _db_path(settings)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("freshness row probe on %s failed: %s", table, exc)
        return True            # unknown: never let a probe failure hide a fault


def _newest_across_channels(settings: Settings, level: str, name: str, channels: list[str]):
    """The newest row for a per-receiver-channel product, across every
    discovered channel — not just the alphabetically first one.

    Returns ``(timestamp, channel)``, both ``None`` if no channel has a
    row (or there are no channels at all).
    """
    best_dt = None
    best_ts: str | None = None
    best_channel: str | None = None
    for ch in channels:
        ts = _max_timestamp(settings, level, name, ch)
        if ts is None:
            continue
        dt = _parse_utc(ts)
        if best_dt is None or dt > best_dt:
            best_dt, best_ts, best_channel = dt, ts, ch
    return best_ts, best_channel


#: the timing authority's files: name -> expected max age in seconds.  Both
#: are INFORMATIONAL: a station without hf-timestd has neither, and a stale
#: authority shows on the Status page, named, without judging ``status``.
#: authority.json uses timing_authority.STALE_S (75 s, about 2.5 writer
#: periods of 30 s plus the tick), so both pages agree on when it is stale.
AUTHORITY_EXPECTED = {"authority.json": int(timing_authority.STALE_S),
                      "authority_history.db": 120}


def _authority_entries(settings: Settings, now: datetime) -> dict[str, dict]:
    """Age of authority.json (its ``utc_published``) and of the newest
    authority_history.db row (``MAX`` on the primary key: one index seek)."""
    out: dict[str, dict] = {}
    for name, expected in AUTHORITY_EXPECTED.items():
        ts, present = None, False
        try:
            if name == "authority.json":
                snap = timing_authority.read_authority(settings.authority_path)
                present = snap is not None
                ts = snap.get("utc_published") if snap else None
            else:
                con = timing_authority.connect_history(settings.authority_history_path)
                if con is not None:
                    try:
                        present = True
                        ts = con.execute(
                            "SELECT MAX(utc_published) FROM authority_snapshot").fetchone()[0]
                    finally:
                        con.close()
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("freshness probe of %s failed: %s", name, exc)
            present = True
            ts = None
        entry = {"verdict": "INFORMATIONAL", "expected_s": expected, "channel": None,
                 "applicable": present, "age_s": None, "stale": None}
        if not present:
            entry["reason"] = f"{name} not available on this station (hf-timestd not publishing here)"
        else:
            age = None if not ts else round((now - _parse_utc(ts)).total_seconds(), 1)
            entry["age_s"] = age
            entry["stale"] = age is None or age > expected
            entry["reason"] = ("timing authority: informational, never judges status"
                               if not entry["stale"] else
                               f"timing authority stale: {name} older than {expected} s")
        out[name] = entry
    return out


def table(settings: Settings) -> dict:
    now = datetime.now(timezone.utc)
    channels = [c["name"] for c in discover_channels(settings)]
    present = _existing_tables(settings)
    products: dict[str, dict] = {}
    for (level, name), expected in EXPECTED.items():
        tbl = f"{level}_{name}"
        fixed = FIXED_CHANNEL.get((level, name))
        if tbl not in present:
            ts, channel = None, (fixed if isinstance(fixed, str) else None)
        elif isinstance(fixed, str):
            ts, channel = _max_timestamp(settings, level, name, fixed), fixed
        else:
            ts, channel = _newest_across_channels(settings, level, name,
                                                  list(fixed) if fixed else channels)
        age = None if ts is None else (now - _parse_utc(ts)).total_seconds()
        if tbl not in present:
            verdict = "INDETERMINATE"
            reason = f"no {tbl} table — producing client not installed"
        elif age is None and not _holds_any_row(settings, tbl):
            verdict = "SILENT"
            reason = f"producer silent: {tbl} exists but has never held a row here"
        elif age is None:
            verdict = "INVALID"
            where = (fixed if isinstance(fixed, str)
                     else ", ".join(fixed) if fixed else "any receiver channel")
            reason = f"{tbl} holds rows, but none on {where}"
        else:
            verdict = "VALID" if age <= expected else "INVALID"
            reason = None
        entry = {"verdict": verdict, "age_s": None if age is None else round(age, 1),
                 "expected_s": expected, "channel": channel,
                 "applicable": verdict in ("VALID", "INVALID")}
        if reason:
            entry["reason"] = reason
        products[tbl] = entry
    for (level, name), channel in EVENT_PRODUCTS.items():
        tbl = f"{level}_{name}"
        ts = _max_timestamp(settings, level, name, channel) if tbl in present else None
        age = None if ts is None else round((now - _parse_utc(ts)).total_seconds(), 1)
        entry = {"verdict": "INFORMATIONAL", "age_s": age, "expected_s": None,
                 "channel": channel, "applicable": tbl in present,
                 "reason": "event product — written only when an event is "
                           "detected; age is informational"}
        products[tbl] = entry
    products.update(_authority_entries(settings, now))
    judged = [p for p in products.values()
              if p["applicable"] and p["verdict"] != "INFORMATIONAL"]
    verdicts = {p["verdict"] for p in judged}
    if not judged:
        status = "degraded"                      # nothing to judge: not healthy, not an error
    elif "INVALID" in verdicts:
        status = "error"
    elif verdicts == {"VALID"}:
        status = "healthy"
    else:
        status = "degraded"
    return {"status": status, "timestamp": now.isoformat().replace("+00:00", "Z"),
            "products": products, "channels": channels}
