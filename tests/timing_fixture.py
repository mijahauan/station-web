"""The timing authority's three files, as hf-timestd writes them on B4.

- ``authority.json``: the current snapshot.  ``T6_SNAPSHOT`` is a verbatim
  copy of B4's at 2026-10-18 16:42Z (T6 AUTHORITATIVE).  ``WITNESS_SNAPSHOT``
  rebuilds the earlier B4 state from the stage-2 design note: no tier in
  force, T4 and T3 available, host clock unwitnessed, and a ``registration``
  block in state WITNESS.
- ``authority_history.db``: written through hamsci-dsp's own
  ``AuthoritySnapshotStore``, so the table, its primary key and its index
  match a station's exactly.
- ``status/radiod-timing-watchdog.json`` and ``radiod-timing-incidents.jsonl``.

Import the fixtures into a test module to use them::

    from timing_fixture import authority_files, write_authority   # noqa: F401
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from hamsci_dsp.io.authority_snapshot_store import COLUMNS, AuthoritySnapshotStore


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


T6_SNAPSHOT = {
    "schema": "v1", "utc_published": "2026-10-18T16:42:01.505163Z", "a_level": "A1",
    "t_level_active": "T6", "t_level_available": ["T6", "T5", "T4", "T3"],
    "t_level_witnesses": ["T5", "T4", "T3", "T2"], "rtp_to_utc_offset_ns": -14586174,
    "sigma_ns": 2298, "stations_contributing": [],
    "last_transition_utc": "2026-10-18T16:00:26.420011Z", "disagreement_flags": [],
    "t6_authority_state": "AUTHORITATIVE", "t6_authority_violations": [],
    "t6_hpps_publishing": True, "t6_hpps_publish_mode": "live", "t6_suspect_criteria": "",
    "t6_battery_blocks": 8,
    "host_clock": {"verdict": "ok", "reason": "every witness agrees with the host clock",
                   "witnesses": {
                       "T4": {"kind": "pair_ms", "value": 0.014650000000000002,
                              "bound": 6.000003960601694, "exceeded": False},
                       "T2": {"kind": "pair_ms", "value": 2.796085,
                              "bound": 60.000000396060294, "exceeded": False},
                       "lb1421": {"kind": "gps_second_s", "value": 0.325, "bound": 1.0,
                                  "exceeded": False}},
                   "since_utc": None},
    "governor_radiod": "AC0G-B4-status.local",
    "registration": {"source": "label", "state": "CANDIDATE", "sigma_ms": None,
                     "counter_epoch_id": "ep-1476374209", "raw_pair_residual_ms": None,
                     "contributing": [], "stations": [], "age_s": 56.11058497428894},
}

WITNESS_SNAPSHOT = {
    "schema": "v1", "utc_published": "2026-10-18T14:43:00.120000Z", "a_level": "A1",
    "t_level_active": None, "t_level_available": ["T4", "T3"], "t_level_witnesses": [],
    "rtp_to_utc_offset_ns": None, "sigma_ns": None, "stations_contributing": [],
    "last_transition_utc": "2026-10-18T12:10:04.000000Z",
    "disagreement_flags": ["T4<->T3:7.412ms>6.000ms:advisory", "majority-downgrade:T6->T4"],
    "host_clock": {"verdict": "unwitnessed", "reason": "no witness reported", "witnesses": {},
                   "since_utc": None},
    "registration": {"source": "label", "state": "WITNESS", "sigma_ms": 1.0,
                     "counter_epoch_id": "ep-1476370001", "raw_pair_residual_ms": 0.4,
                     "contributing": ["WWV_10000", "WWV_15000", "SHARED_5000"],
                     "stations": ["WWV", "WWVH"], "age_s": 174.0,
                     "residual_vs_t6_ms": -19.2},
}


def write_authority(path, snapshot, published: datetime | None = None):
    """Write ``snapshot`` to ``path``; ``published`` overrides utc_published."""
    snap = dict(snapshot)
    if published is not None:
        snap["utc_published"] = iso_z(published)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap))
    return snap


def history_row(t: datetime, i: int, tier="T6") -> dict:
    """One authority_snapshot row shaped like B4's (see sample-authority-history)."""
    return {
        "utc_published": iso_z(t), "schema_version": "v1", "a_level": "A1",
        "t_level_active": tier, "t_level_available": ["T6", "T5", "T4", "T3"],
        "t_level_witnesses": ["T5", "T4", "T3", "T2"],
        "rtp_to_utc_offset_ns": -14586174 + i, "sigma_ns": 2000 + (i % 7) * 100,
        "t6_available": 1, "t6_offset_ms": 0.002 + 0.0001 * (i % 5), "t6_sigma_ms": 0.0023,
        "t5_available": 1, "t5_offset_ms": -11.9, "t5_sigma_ms": 11.9,
        "t4_available": 1, "t4_offset_ms": 0.017, "t4_sigma_ms": 2.0,
        "t3_available": 1, "t3_offset_ms": -0.02 + 0.001 * (i % 3), "t3_sigma_ms": 1.24,
        "t3_kalman_state": "LOCKED", "rf_gain": 15.4, "host_clock_verdict": "ok",
    }


def write_history(db_path, rows):
    """Create the table with hamsci-dsp's store, then bulk-insert ``rows``."""
    AuthoritySnapshotStore(db_path).close()
    con = sqlite3.connect(db_path)
    try:
        cols = ",".join(COLUMNS)
        qs = ",".join("?" for _ in COLUMNS)
        con.executemany(
            f"INSERT OR IGNORE INTO authority_snapshot ({cols}) VALUES ({qs})",
            [[json.dumps(r[c]) if isinstance(r.get(c), list) else r.get(c) for c in COLUMNS]
             for r in rows])
        con.commit()
    finally:
        con.close()


WATCHDOG_STATUS = {
    "ts": "2026-10-18T15:56:31.571364+00:00", "verdict": "MAPPING_JUMP", "severity": "warn",
    "detail": "radiod RTP↔UTC jumped -1282.2s with sane epochs (gpsd unread) — "
              "transient status tear or a genuine seconds-level slip; monitor and escalate "
              "only if recurrent.",
    "channel": "SHARED_2500", "delta_sec": -1282.167, "suppressed_repeats": 0,
    "radiod": {"gps_time_ns": 1476374209382338121, "rtp_timesnap": 0,
               "implied_utc": 1792338991.382, "old_mapping_utc": 1792337709.216,
               "epoch_off_sec": -0.013},
    "external": {"gpsd_unix": None, "gpsd_mode": None, "gpsd_epoch_off_sec": None,
                 "chrony_refname": "192.168.1.80", "chrony_stratum": 2,
                 "chrony_offset_sec": 3.454e-06, "chrony_leap": "Normal",
                 "errors": ["gpspipe-not-found"]},
    "system_unix": 1792338991.395,
}


def incident(i: int) -> dict:
    t = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return dict(WATCHDOG_STATUS, ts=t.isoformat(), delta_sec=-float(i),
                detail=f"incident {i}")


def write_watchdog(status_dir, n_incidents=3, status=True):
    status_dir.mkdir(parents=True, exist_ok=True)
    if status:
        (status_dir / "radiod-timing-watchdog.json").write_text(
            json.dumps(WATCHDOG_STATUS, indent=2))
    if n_incidents is not None:
        with open(status_dir / "radiod-timing-incidents.jsonl", "w") as fh:
            for i in range(n_incidents):
                fh.write(json.dumps(incident(i)) + "\n")


@pytest.fixture
def authority_files(settings):
    """All three files present: a fresh T6 snapshot, 24 h of history at 30 s,
    and the watchdog with three incidents."""
    now = datetime.now(timezone.utc)
    write_authority(settings.authority_path, T6_SNAPSHOT, published=now - timedelta(seconds=3))
    rows = [history_row(now - timedelta(seconds=30 * k), k,
                        tier="T6" if k < 1200 else "T4")
            for k in range(2880)]
    write_history(settings.authority_history_path, rows)
    write_watchdog(settings.timing_status_dir)
    return settings
