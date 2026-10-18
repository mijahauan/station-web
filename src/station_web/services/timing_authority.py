"""The timing authority, read from the files hf-timestd publishes.

Three sources, all read-only, none in the product database:

- ``authority.json`` (``[data] authority_path``, default
  ``/run/hf-timestd/authority.json``): the current snapshot.  The tier in
  force, the tiers available, the published sigma, the T6 anchor state, the
  host-clock verdict and the registration block.  hf-timestd rewrites it
  every few seconds by atomic rename, so a file that will not parse marks a
  fault (HTTP 500), not an empty answer.
- ``authority_history.db``, table ``authority_snapshot``: one row per
  authority cycle (about 30 s), keyed by ``utc_published`` (TEXT PRIMARY
  KEY, so SQLite keeps an autoindex on it).  Reads name their columns,
  bound ``utc_published`` so SQLite seeks that index, cap the window and
  decimate to ``MAX_POINTS``.
- ``status/radiod-timing-watchdog.json`` (the latest verdict) and
  ``status/radiod-timing-incidents.jsonl`` (append-only, unbounded).  The
  incidents file is read from its end, at most ``TAIL_BYTES``.

Fields hf-timestd does not publish yet (``u_epoch_ns`` with k and p,
``stability_ns`` over ``tau_s``, the ruler's provenance) read as the words
``"not published"``.  This module never supplies a number for them.

A station without hf-timestd answers ``available: false`` with a reason.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from station_web import heavy
from station_web.config import Settings
from station_web.timeutil import as_utc, iso_z, utcnow

NOT_PUBLISHED = "not published"
#: A snapshot older than this reads ``stale``; hf-timestd rewrites it every few seconds.
STALE_S = 30.0
#: The longest history window one request may ask for.
MAX_WINDOW = timedelta(days=7)
#: The history answer carries at most this many points.
MAX_POINTS = 1500
#: Transitions listed per answer, at most.
MAX_TRANSITIONS = 500
#: Bytes read from the end of the incidents file, at most.
TAIL_BYTES = 256 * 1024
#: Bytes read of the watchdog status file, at most (it holds one small object).
STATUS_BYTES = 64 * 1024
#: Incidents per answer, at most.
MAX_INCIDENTS = 100
#: heavy.guard family for the history read.
HEAVY_FAMILY = "authority history"

ESTIMATORS = ("t6", "t5", "t4", "t3")
HISTORY_COLUMNS = (["utc_published", "t_level_active", "sigma_ns"]
                   + [f"{e}_{f}" for e in ESTIMATORS for f in ("offset_ms", "sigma_ms")])
#: History columns that might one day carry the registration's counter epoch.
EPOCH_COLUMNS = ("registration_counter_epoch_id", "counter_epoch_id")

_SLACK = timedelta(seconds=2)
_WITNESS_UNITS = {"pair_ms": "ms", "gps_second_s": "s"}
_INCIDENT_FIELDS = ("ts", "verdict", "severity", "detail", "channel", "delta_sec",
                    "suppressed_repeats")

#: ``open`` as this module calls it; tests swap it to watch the reads.
_open = open


def _unavailable(reason: str, **extra) -> dict:
    return {"available": False, "reason": reason, **extra}


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _ts_z(value) -> Optional[str]:
    dt = _parse_ts(value)
    return iso_z(dt) if dt else None


def _list(value) -> list:
    """A list field, which the history table stores JSON-encoded."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            v = json.loads(value)
        except ValueError:
            return []
        return v if isinstance(v, list) else []
    return []


# ── disagreement flags ──────────────────────────────────────────────────

_PAIR = re.compile(r"^(T\d)<->(T\d):([\d.]+)ms>([\d.]+)ms$")
_MAJORITY = re.compile(r"^majority-downgrade:(\w+)->(\w*)$")
_DEMOTE = re.compile(r"^demote-t6-breach->t5:(\d+)cycles$")
_REJECTED = re.compile(r"^chrony-rejected-(.+):state=(.*)$")
_MISSING = re.compile(r"^chrony-missing-(.+)$")


def explain_flag(flag: str) -> str:
    """One authority disagreement flag in plain language.

    The forms come from hf-timestd's AuthorityManager; an unknown form
    returns the flag unchanged rather than a guess.
    """
    f = str(flag)
    advisory = f.endswith(":advisory")
    core = f[: -len(":advisory")] if advisory else f
    tail = "; advisory only" if advisory else ""
    m = _PAIR.match(core)
    if m:
        a, b, d, lim = m.groups()
        return f"{a} and {b} disagree by {d} ms (limit {lim} ms){tail}"
    m = _MAJORITY.match(core)
    if m:
        return (f"most witnesses disagree with {m.group(1)}; the tier in force dropped "
                f"to {m.group(2) or 'none'}{tail}")
    if core.startswith("asymmetric-T3-T2:"):
        return f"T3 and T2 disagree asymmetrically: {core.split(':', 1)[1]}{tail}"
    m = _DEMOTE.match(core)
    if m:
        return f"T6 breached its drift limit for {m.group(1)} cycles; demoted to T5{tail}"
    m = _REJECTED.match(core)
    if m:
        return f"chrony rejects source {m.group(1)} (state {m.group(2)}){tail}"
    m = _MISSING.match(core)
    if m:
        return f"chrony does not list source {m.group(1)}{tail}"
    return f


# ── the snapshot ────────────────────────────────────────────────────────

def read_authority(path: Path) -> Optional[dict]:
    """The parsed snapshot, or None when the file is absent.

    A file that exists but will not parse raises: hf-timestd writes it by
    atomic rename, so a torn file is a fault worth a 500.
    """
    try:
        with _open(path, "r", encoding="utf-8") as fh:
            text = fh.read(STATUS_BYTES * 4)
    except FileNotFoundError:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("authority snapshot is not a JSON object")
    return data


def _sigma(snap: dict, reg: Optional[dict]) -> dict:
    if snap.get("sigma_ns") is not None:
        return {"value": snap["sigma_ns"], "unit": "ns", "field": "sigma_ns"}
    if reg and reg.get("sigma_ms") is not None:
        return {"value": reg["sigma_ms"], "unit": "ms", "field": "registration.sigma_ms"}
    return {"value": None, "unit": None, "field": None, "text": NOT_PUBLISHED}


def _u_epoch(snap: dict):
    if snap.get("u_epoch_ns") is None:
        return NOT_PUBLISHED
    return {"value": snap["u_epoch_ns"], "unit": "ns", "field": "u_epoch_ns",
            "k": snap.get("u_epoch_k"), "p": snap.get("u_epoch_p")}


def _stability(snap: dict):
    if snap.get("stability_ns") is None:
        return NOT_PUBLISHED
    return {"value": snap["stability_ns"], "unit": "ns", "field": "stability_ns",
            "tau_s": snap.get("tau_s")}


def _registration(snap: dict) -> Optional[dict]:
    reg = snap.get("registration")
    if not isinstance(reg, dict):
        return None
    out = {k: reg.get(k) for k in ("state", "source", "sigma_ms", "age_s", "counter_epoch_id",
                                   "raw_pair_residual_ms")}
    out["contributing"] = _list(reg.get("contributing"))
    out["stations"] = _list(reg.get("stations"))
    if "residual_vs_t6_ms" in reg:
        out["residual_vs_t6_ms"] = reg.get("residual_vs_t6_ms")
    return out


def _t6(snap: dict) -> Optional[dict]:
    keys = {"authority_state": "t6_authority_state", "violations": "t6_authority_violations",
            "hpps_publishing": "t6_hpps_publishing", "hpps_publish_mode": "t6_hpps_publish_mode",
            "suspect_criteria": "t6_suspect_criteria", "battery_blocks": "t6_battery_blocks"}
    if not any(v in snap for v in keys.values()):
        return None
    out = {k: snap.get(v) for k, v in keys.items()}
    out["violations"] = _list(out["violations"])
    return out


def _host_clock(snap: dict) -> Optional[dict]:
    hc = snap.get("host_clock")
    if not isinstance(hc, dict):
        return None
    rows = []
    for name, w in (hc.get("witnesses") or {}).items():
        if not isinstance(w, dict):
            continue
        kind = w.get("kind")
        rows.append({"name": name, "kind": kind, "value": w.get("value"),
                     "unit": _WITNESS_UNITS.get(kind, "ppm"), "bound": w.get("bound"),
                     "exceeded": w.get("exceeded")})
    return {"verdict": hc.get("verdict"), "reason": hc.get("reason"),
            "since_utc": hc.get("since_utc"), "witnesses": rows}


def registration_snapshot(settings: Settings, now: Optional[datetime] = None) -> dict:
    """``GET /api/timing/registration``: the current snapshot, normalised."""
    snap = read_authority(settings.authority_path)
    if snap is None:
        return _unavailable("timing authority not available on this station: hf-timestd "
                            "has not published authority.json here")
    now = as_utc(now) if now is not None else utcnow()
    published = _parse_ts(snap.get("utc_published"))
    age = None if published is None else round((now - published).total_seconds(), 1)
    reg = _registration(snap)
    a_level = snap.get("a_level")
    provenance = snap.get("ruler_provenance") or NOT_PUBLISHED
    return {
        "available": True,
        "schema": snap.get("schema"),
        "utc_published": _ts_z(snap.get("utc_published")),
        "age_s": age,
        "stale": age is None or age > STALE_S,
        "stale_after_s": STALE_S,
        "tier_active": snap.get("t_level_active") or None,
        "tiers_available": _list(snap.get("t_level_available")),
        "witnesses": _list(snap.get("t_level_witnesses")),
        "a_level": a_level,
        "ruler": {"a_level": a_level,
                  "disciplined": {"A1": True, "A0": False}.get(a_level),
                  "provenance": provenance},
        "ruler_provenance": provenance,
        "sigma": _sigma(snap, reg),
        "u_epoch": _u_epoch(snap),
        "stability": _stability(snap),
        "rtp_to_utc_offset_ns": snap.get("rtp_to_utc_offset_ns"),
        "registration": reg,
        "t6": _t6(snap),
        "host_clock": _host_clock(snap),
        "disagreement_flags": [{"flag": f, "text": explain_flag(f)}
                               for f in _list(snap.get("disagreement_flags"))],
        "last_transition_utc": snap.get("last_transition_utc"),
        "governor_radiod": snap.get("governor_radiod"),
        "counter_epoch_changes_24h": counter_epoch_changes(settings, now),
    }


# ── history ─────────────────────────────────────────────────────────────

#: The reason a history file that exists but will not open gives.
UNREADABLE = "authority_history.db unreadable"


def connect_history(path: Path) -> Optional[sqlite3.Connection]:
    """A read-only connection to authority_history.db, or None when absent.

    The database runs in WAL mode, in a directory this unit mounts
    read-only.  While hf-timestd holds it open, ``-wal`` and ``-shm`` exist
    and ``mode=ro`` reads them.  A clean stop deletes both, and a plain
    ``mode=ro`` open would then have to create ``-shm`` and fails ("attempt
    to write a readonly database").  With no ``-wal`` file the main file
    holds every committed row, so ``immutable=1`` reads it without touching
    ``-shm``.
    """
    if not path.exists():
        return None
    uri = path.absolute().as_uri() + "?mode=ro"
    if not Path(f"{path}-wal").exists():
        uri += "&immutable=1"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


_connect = connect_history


def _columns(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(authority_snapshot)")}


def _bound(dt: datetime) -> str:
    """A bound in the table's own text form, ``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    return as_utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def history_sql(present: set, start: datetime, end: datetime, columns: Sequence[str] = HISTORY_COLUMNS):
    """The projected, index-bounded history SELECT and its parameters."""
    exprs = [c if c in present else "NULL" for c in columns]
    sql = (f"SELECT {', '.join(exprs)} FROM authority_snapshot "
           "WHERE utc_published BETWEEN ? AND ? ORDER BY utc_published")
    return sql, [_bound(start - _SLACK), _bound(end + _SLACK)]


def epoch_sql(column: str, start: datetime, end: datetime):
    sql = (f"SELECT utc_published, {column} FROM authority_snapshot "
           "WHERE utc_published BETWEEN ? AND ? ORDER BY utc_published")
    return sql, [_bound(start - _SLACK), _bound(end + _SLACK)]


def counter_epoch_changes(settings: Settings, now: datetime) -> dict:
    """How often the registration's counter epoch changed in the last 24 h.

    A change means a radiod restart renumbered the samples.  The history
    table records no epoch id today; when it gains one of
    ``EPOCH_COLUMNS``, this counts the changes.
    """
    # The count must never take the snapshot down: an unreadable history
    # costs this one field, with a reason.
    try:
        conn = _connect(settings.authority_history_path)
    except sqlite3.Error:
        return {"count": None, "reason": UNREADABLE}
    if conn is None:
        return {"count": None, "reason": "authority_history.db not available on this station"}
    try:
        present = _columns(conn)
        col = next((c for c in EPOCH_COLUMNS if c in present), None)
        if col is None:
            return {"count": None,
                    "reason": "authority_history.db does not record counter_epoch_id"}
        sql, params = epoch_sql(col, now - timedelta(hours=24), now)
        ids: List[str] = []
        for _, eid in conn.execute(sql, params):
            if eid is not None and (not ids or ids[-1] != eid):
                ids.append(eid)
        return {"count": max(0, len(ids) - 1), "ids": ids[-20:], "field": col}
    except sqlite3.Error:
        return {"count": None, "reason": UNREADABLE}
    finally:
        conn.close()


def _med(values: list):
    vals = [v for v in values if v is not None]
    return median(vals) if vals else None


def history(settings: Settings, start: datetime, end: datetime) -> dict:
    """``GET /api/timing/registration/history``: the tier, the published sigma
    and every estimator's offset and sigma over ``[start, end]``.

    Windows longer than ``MAX_POINTS`` rows are decimated into equal time
    buckets: each bucket carries the median of each number and the tier in
    force at its last row.  Transitions come from every row, so decimation
    never hides one.
    """
    start, end = as_utc(start), as_utc(end)
    unreadable = _unavailable(f"timing history not available: {UNREADABLE}")
    try:
        conn = _connect(settings.authority_history_path)
    except sqlite3.Error:
        return unreadable
    if conn is None:
        return _unavailable("timing history not available on this station: no "
                            "authority_history.db")
    with heavy.guard(HEAVY_FAMILY):
        try:
            try:
                present = _columns(conn)
            except sqlite3.DatabaseError:
                return unreadable
            if not present:
                return _unavailable("timing history not available on this station: "
                                    "authority_history.db holds no authority_snapshot table")
            span_s = max(1.0, (end - start).total_seconds())
            # The writer publishes about every 30 s; decimate only when the
            # window could hold more rows than the answer may carry.
            bucket_s = None if span_s / 30.0 <= MAX_POINTS else span_s / MAX_POINTS
            sql, params = history_sql(present, start, end)
            out_t, out_tier, out_sigma = [], [], []
            est = {e: {"offset_ms": [], "sigma_ms": []} for e in ESTIMATORS}
            transitions: List[dict] = []
            n_rows = 0
            prev_tier: Any = ...
            bucket: List[tuple] = []
            bucket_key = None

            def flush():
                if not bucket:
                    return
                last = bucket[-1]
                out_t.append(iso_z(last[0]) if bucket_s is None else
                             iso_z(start + timedelta(seconds=(bucket_key + 0.5) * bucket_s)))
                out_tier.append(last[1])
                out_sigma.append(last[2] if bucket_s is None else _med([r[2] for r in bucket]))
                for i, e in enumerate(ESTIMATORS):
                    for j, f in enumerate(("offset_ms", "sigma_ms")):
                        k = 3 + 2 * i + j
                        est[e][f].append(last[k] if bucket_s is None
                                         else _med([r[k] for r in bucket]))

            cur = conn.execute(sql, params)
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                for r in rows:
                    t = _parse_ts(r[0])
                    if t is None or not (start <= t <= end):
                        continue
                    n_rows += 1
                    tier = r[1] or None
                    if prev_tier is not ... and tier != prev_tier:
                        if len(transitions) < MAX_TRANSITIONS:
                            transitions.append({"time": iso_z(t), "from": prev_tier, "to": tier})
                    prev_tier = tier
                    key = 0 if bucket_s is None else int((t - start).total_seconds() // bucket_s)
                    row = (t, tier) + tuple(r[2:])
                    if bucket_s is None:
                        bucket, bucket_key = [row], key
                        flush()
                        continue
                    if key != bucket_key:
                        flush()
                        bucket, bucket_key = [], key
                    bucket.append(row)
            if bucket_s is not None:
                flush()
        finally:
            conn.close()
    return {
        "available": True, "start": iso_z(start), "end": iso_z(end),
        "n_rows": n_rows, "bucket_s": None if bucket_s is None else round(bucket_s, 3),
        "n_points": len(out_t), "time": out_t, "tier_active": out_tier,
        "sigma_ns": out_sigma, "estimators": est, "transitions": transitions,
        "transitions_truncated": len(transitions) >= MAX_TRANSITIONS,
    }


# ── alarms ──────────────────────────────────────────────────────────────

def _project_incident(obj: dict) -> dict:
    out = {k: obj.get(k) for k in _INCIDENT_FIELDS}
    out["ts"] = _ts_z(obj.get("ts")) or obj.get("ts")
    return out


def tail_lines(path: Path, max_bytes: int = TAIL_BYTES) -> List[str]:
    """The complete lines in the last ``max_bytes`` of ``path``, oldest first.

    One seek and one read of at most ``max_bytes``.  When the read starts
    mid-file, its first (partial) line is dropped.
    """
    with _open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - max_bytes)
        fh.seek(start)
        data = fh.read(min(max_bytes, size))
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]
    return [ln.decode("utf-8", "replace") for ln in lines if ln.strip()]


def alarms(settings: Settings, n: int = 20) -> dict:
    """``GET /api/timing/alarms``: the radiod timing watchdog's latest verdict
    and its last ``n`` incidents, newest first."""
    n = max(0, min(int(n), MAX_INCIDENTS))
    d = settings.timing_status_dir
    status_path = d / "radiod-timing-watchdog.json"
    incidents_path = d / "radiod-timing-incidents.jsonl"
    watchdog = None
    try:
        with _open(status_path, "r", encoding="utf-8") as fh:
            obj = json.loads(fh.read(STATUS_BYTES))
        if isinstance(obj, dict):
            watchdog = _project_incident(obj)
            ts = _parse_ts(obj.get("ts"))
            watchdog["age_s"] = None if ts is None else round((utcnow() - ts).total_seconds(), 1)
    except FileNotFoundError:
        pass
    incidents: List[dict] = []
    have_incidents = incidents_path.exists()
    if have_incidents and n:
        for line in reversed(tail_lines(incidents_path)):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                incidents.append(_project_incident(obj))
            if len(incidents) >= n:
                break
    if watchdog is None and not have_incidents:
        return _unavailable("radiod timing watchdog not available on this station: it has "
                            "written no status yet", watchdog=None, incidents=[])
    return {"available": True, "watchdog": watchdog, "incidents": incidents,
            "n": n, "incidents_file_present": have_incidents}
