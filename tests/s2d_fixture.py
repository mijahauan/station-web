"""Products the consolidated pages add (stage 2, S2D), as their producers write them.

- ``L2_detection_attempts``: hf-timestd's metrology service writes one row
  per measurement attempt, detected or rejected, under the receiver channel.
- ``L3_gnss_vtec``: the gnss-vtec service (and the hf-timestd bridge) write
  1 Hz VTEC under the fixed channel ``GNSS``.
- ``L3C_propagation_stats``: hamsci-physics's reanalysis writes one HOURLY
  row per (station, frequency) under the fixed channel ``REANALYSIS``.
- ``<data root>/wwvb/YYYY-MM-DD.jsonl``: hf-timestd's ``WwvbLedger`` writes
  one ``pass`` line per decode pass and one ``frame`` line per frame.

Every product row goes in through hamsci-dsp's writer, so the schema and the
``(channel, timestamp_utc)`` index match a station's.

Import into a test module::

    from s2d_fixture import s2d_products   # noqa: F401
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import PROC_VERSION, _iso, _minute_boundary, _write_rows

#: (channel, station, frequency MHz) -> (detected, {reason: rejected count})
ATTEMPTS = {
    ("WWV_20000", "WWV", 20.0): (40, {"corr_snr_low": 15, "arrival_tolerance": 5}),
    ("SHARED_10000", "WWV", 10.0): (30, {"corr_snr_low": 10}),
    ("SHARED_10000", "WWVH", 10.0): (6, {"corr_snr_low": 20, "correlation_flat": 4}),
    ("SHARED_10000", "BPM", 10.0): (0, {"bpm_snr_low": 12}),
}
#: GNSS VTEC: rows at 1 Hz, the newest 30 s ago; every 10th row BAD.
VTEC_ROWS = 2000


def _attempt(t, channel, station, f_mhz, detected, reason):
    return {"timestamp_utc": _iso(t), "minute_boundary_utc": _minute_boundary(t),
            "channel": channel, "station": station, "frequency_mhz": f_mhz,
            "frequency_hz": 1000, "utc_second": int(t.timestamp()),
            "detected": detected, "rejection_reason": None if detected else reason,
            "corr_snr_db": 12.0 if detected else 2.0, "processing_version": PROC_VERSION}


def write_attempts(root, storage, now, attempts=ATTEMPTS, minutes_ago=30):
    by_channel: dict = {}
    for (channel, station, f), (n_det, rej) in attempts.items():
        rows = by_channel.setdefault(channel, [])
        k = 0
        for _ in range(n_det):
            rows.append(_attempt(now - timedelta(minutes=minutes_ago, seconds=k),
                                 channel, station, f, True, None))
            k += 1
        for reason, n in rej.items():
            for _ in range(n):
                rows.append(_attempt(now - timedelta(minutes=minutes_ago, seconds=k),
                                     channel, station, f, False, reason))
                k += 1
    for channel, rows in by_channel.items():
        rows.sort(key=lambda r: r["timestamp_utc"])
        _write_rows(root, storage, "L2", "detection_attempts", channel,
                    f"{channel}/detection_attempts", rows)


def vtec_value(i: int) -> float:
    return 12.0 + 0.001 * i


def write_vtec(root, storage, now, n=VTEC_ROWS):
    t_end = now - timedelta(seconds=30)
    rows = []
    for i in range(n):
        t = t_end - timedelta(seconds=n - 1 - i)
        rows.append({"timestamp_utc": _iso(t), "unix_timestamp": t.timestamp(),
                     "vtec_tecu": vtec_value(i) if i % 10 else 150.0,
                     "n_satellites": 9 if i % 10 else 2,
                     "quality_flag": "GOOD" if i % 10 else "BAD",
                     "processing_version": PROC_VERSION, "min_elevation_deg": 20.0,
                     "dcb_corrected": True})
    _write_rows(root, storage, "L3", "gnss_vtec", "GNSS", "gnss_vtec", rows)


def stats_row(t_end, station, f_mhz, probs, n_obs, muf=None, conf=None, quality="GOOD"):
    keys = ("1e", "1f", "2f", "3f", "gw", "unknown")
    row = {"timestamp_utc": _iso(t_end), "period_start": _iso(t_end - timedelta(hours=1)),
           "aggregation_period": "HOURLY", "station": station, "frequency_mhz": f_mhz,
           "n_observations": n_obs, "data_completeness": 0.9, "quality_flag": quality,
           "processing_version": PROC_VERSION, "mean_snr_db": 15.0}
    for k, p in zip(keys, probs):
        row[f"mode_{k}_probability"] = p
    if muf is not None:
        row["estimated_muf_mhz"] = muf
        row["muf_confidence"] = conf
    return row


def write_stats(root, storage, now):
    """Two hours of WWVH at two frequencies (n-weighted), beside conftest's WWV row."""
    h1 = (now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    h2 = h1 + timedelta(hours=1)
    rows = [
        stats_row(h1, "WWVH", 10.0, (0.0, 1.0, 0.0, 0.0, 0.0, 0.0), 30, 18.5, 0.6),
        stats_row(h1, "WWVH", 15.0, (0.0, 0.0, 1.0, 0.0, 0.0, 0.0), 10, 18.5, 0.6),
        stats_row(h2, "WWVH", 10.0, (0.5, 0.5, 0.0, 0.0, 0.0, 0.0), 20, None, None, "MARGINAL"),
    ]
    _write_rows(root, storage, "L3C", "propagation_stats", "REANALYSIS",
                "science/propagation_stats", rows)
    return h1, h2


def ledger_lines(now):
    t = now - timedelta(minutes=10)
    z = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")        # noqa: E731
    minute = (t + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return [
        {"ts": z(t - timedelta(minutes=2)), "kind": "pass", "buffer_s": 120.0,
         "mean_amp": 0.01, "carrier_offset_hz": 0.0, "seconds_detected": 95, "bits": 0,
         "frames": 0, "frames_implausible": 0, "snr_db": None},
        {"ts": z(t), "kind": "pass", "buffer_s": 120.0, "mean_amp": 0.2,
         "carrier_offset_hz": 0.01, "seconds_detected": 88, "bits": 60, "frames": 2,
         "frames_implausible": 1, "snr_db": 14.2},
        {"ts": z(t), "kind": "frame", "minute": z(minute), "dst": "S", "leap": "0",
         "parity_errors": 0, "sync_errors": 0, "inverted": False,
         "vs_wallclock_s": round((minute - t.replace(microsecond=0)).total_seconds(), 2),
         "mean_amp": 0.2, "plausible": True},
        {"ts": z(t), "kind": "frame", "minute": "2031-01-01T00:00:00Z", "dst": "?",
         "leap": "?", "parity_errors": 7, "sync_errors": 3, "inverted": True,
         "vs_wallclock_s": 9e6, "mean_amp": 0.2, "plausible": False},
    ]


def write_ledger(root, now, lines=None):
    d = root / "wwvb"
    d.mkdir(parents=True, exist_ok=True)
    # an older day, which the reader must not prefer over today's file
    (d / f"{(now - timedelta(days=1)).date().isoformat()}.jsonl").write_text(
        json.dumps({"ts": "2000-01-01T00:00:00Z", "kind": "pass"}) + "\n")
    path = d / f"{now.date().isoformat()}.jsonl"
    body = "\n".join(json.dumps(x) for x in (lines if lines is not None else ledger_lines(now)))
    path.write_text(body + "\nnot json at all\n")
    return path


@pytest.fixture
def s2d_products(seeded):
    """conftest's ``seeded`` root plus the four products above."""
    root, storage = seeded.data_root, seeded.storage
    now = datetime.now(timezone.utc)
    write_attempts(root, storage, now)
    write_vtec(root, storage, now)
    write_stats(root, storage, now)
    write_ledger(root, now)
    return seeded
