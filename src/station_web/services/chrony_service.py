"""
Chrony statistics service for the web API.

Reads chrony source data entirely from the ``DIAG_chrony_stats`` product
written by the fusion service — station-web never queries the host chrony
daemon directly and never imports the timing core; it is a read-only UI
over data products.

- ``get_live_snapshot`` / ``get_source_comparison`` take the latest row
  per source within a lookback window (the "live" view).
- ``get_history`` reads a longer window and returns per-source time
  series (unchanged from earlier phases).
"""

import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from hamsci_dsp.io import make_data_product_reader

from station_web import heavy, projected
from station_web.timeutil import as_utc, iso_z

logger = logging.getLogger(__name__)

#: fusion writes one row per source per minute; a row older than this
#: is not a live snapshot any more.
STALE_AFTER_S = 120.0

#: how far back ``_latest_rows`` looks for the latest row per source.
LOOKBACK_S = 600.0

#: chrony source-state ordering: selected first, then combined, etc.
STATE_ORDER = {"*": 0, "+": 1, "-": 2, "x": 3, "~": 4, "?": 5}


class ChronyService:
    """Service for chrony source comparison data."""

    def __init__(self, data_root: Path, storage_config: dict | None = None):
        self.data_root = Path(data_root)
        self.fusion_dir = self.data_root / 'phase2' / 'fusion'
        self.storage_config = storage_config

    def _latest_rows(self, window_s: float = LOOKBACK_S) -> List[dict]:
        """The most recent row per ``source_name`` within ``window_s``."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(seconds=window_s)).isoformat().replace("+00:00", "Z")
        end = now.isoformat().replace("+00:00", "Z")
        reader = make_data_product_reader(
            data_dir=self.fusion_dir, product_level="DIAG",
            product_name="chrony_stats", channel="fusion",
            storage_config=self.storage_config or {},
        )
        try:
            rows = reader.read_time_range(start=start, end=end)
        finally:
            if hasattr(reader, "close"):
                reader.close()
        latest: Dict[str, dict] = {}
        for r in rows:                              # ordered by timestamp; last wins
            if r.get("source_name"):
                latest[r["source_name"]] = r
        return list(latest.values())

    def get_live_snapshot(self) -> Optional[Dict[str, Any]]:
        """Latest chrony source snapshot from ``DIAG_chrony_stats``.

        Returns ``None`` when there are no rows in the lookback window.
        """
        rows = self._latest_rows()
        if not rows:
            return None
        newest = max(r["unix_time"] for r in rows)
        age = datetime.now(timezone.utc).timestamp() - newest
        sources = [{
            "name": r["source_name"], "mode": r.get("source_mode", ""), "state": r.get("source_state", ""),
            "stratum": r.get("stratum"), "reach": r.get("reach"), "offset_us": r.get("offset_us"),
            "error_us": r.get("error_us"), "n_samples": r.get("n_samples"), "std_dev_us": r.get("std_dev_us"),
            # chronyc sourcestats: this source's estimated frequency and skew
            "frequency_ppm": r.get("frequency_ppm"), "freq_skew_ppm": r.get("freq_skew_ppm"),
        } for r in rows]
        sources.sort(key=lambda s: (STATE_ORDER.get(s["state"], 9), s["name"]))
        # sys_offset_s describes the host's tracking offset at the moment a
        # row was written — it must come from the newest row(s), not from
        # whichever source happens first in iteration order, or a stale
        # source's reading can silently override a fresh one's.
        newest_rows = [r for r in rows if r["unix_time"] == newest]
        track = next((r for r in newest_rows if r.get("sys_offset_s") is not None), None)
        if track is None:
            track = next((r for r in rows if r.get("sys_offset_s") is not None), None)
        tracking = None
        if track is not None:
            # chronyc tracking, denormalised onto every row of a snapshot.
            # The product carries only these three tracking fields.
            rms = track.get("rms_offset_s")
            tracking = {"sys_offset_us": round(track["sys_offset_s"] * 1e6, 2),
                        "rms_offset_us": round(rms * 1e6, 2) if rms is not None else None,
                        "reference_id": track.get("reference_id")}
        return {
            "timestamp_utc": datetime.fromtimestamp(newest, timezone.utc).isoformat().replace("+00:00", "Z"),
            "unix_time": newest, "age_s": round(age, 1), "stale": age > STALE_AFTER_S,
            "tracking": tracking,
            "sources": sources,
        }

    def get_source_comparison(self) -> Dict[str, Any]:
        """Formatted source comparison table (sources, selected-first)."""
        snap = self.get_live_snapshot()
        if snap is None:
            return {"sources": [], "stale": True, "age_s": None, "note": "no DIAG_chrony_stats rows"}
        return {"sources": snap["sources"], "tracking": snap["tracking"],
                "stale": snap["stale"], "age_s": snap["age_s"], "timestamp_utc": snap["timestamp_utc"]}

    def get_history(self, hours: int = 24, start: Optional[datetime] = None,
                    end: Optional[datetime] = None) -> Dict[str, Any]:
        """Get historical chrony stats from the DIAG_chrony_stats SQLite
        table.  Returns per-source time series of offset and std_dev,
        plus a deduplicated system-tracking offset series.

        The window is ``[start, end]`` (aware UTC datetimes) when given,
        else the last ``hours`` ending now.
        """
        if end is None:
            end = datetime.now(timezone.utc)
        if start is None:
            start = end - timedelta(hours=hours)
        start_iso = start.isoformat().replace('+00:00', 'Z')
        end_iso = end.isoformat().replace('+00:00', 'Z')

        try:
            reader = make_data_product_reader(
                data_dir=self.fusion_dir,
                product_level='DIAG',
                product_name='chrony_stats',
                channel='fusion',
                storage_config=self.storage_config or {},
            )
        except Exception as e:
            logger.warning(f"chrony history reader init failed: {e}")
            return {'sources': {}, 'system': []}

        try:
            try:
                rows = reader.read_time_range(start=start_iso, end=end_iso)
            except Exception as e:
                logger.warning(f"chrony history read failed: {e}")
                return {'sources': {}, 'system': []}
        finally:
            close_fn = getattr(reader, 'close', None)
            if close_fn is not None:
                try:
                    close_fn()
                except Exception:
                    pass

        if not rows:
            return {'sources': {}, 'system': [], 'start': start_iso, 'end': end_iso}

        sources_data: Dict[str, Dict[str, Any]] = {}
        seen_sys_times: set = set()
        system_ts: List[Dict[str, Any]] = []

        for row in rows:
            src_name = row.get('source_name')
            unix_time = row.get('unix_time')
            if not src_name or unix_time is None:
                continue
            entry = sources_data.setdefault(src_name, {
                'timestamps': [],
                'offset_ms': [],
                'std_dev_ms': [],
                'states': [],
                'n_points': 0,
            })
            offset_us = row.get('offset_us')
            std_dev_us = row.get('std_dev_us')
            state = row.get('source_state', '')
            entry['timestamps'].append(unix_time)
            entry['offset_ms'].append(
                round(offset_us / 1000.0, 4) if offset_us is not None else None
            )
            entry['std_dev_ms'].append(
                round(std_dev_us / 1000.0, 4) if std_dev_us is not None else None
            )
            entry['states'].append(state)
            entry['n_points'] += 1

            sys_offset_s = row.get('sys_offset_s')
            if sys_offset_s is not None:
                bucket = round(unix_time, 1)
                if bucket not in seen_sys_times:
                    seen_sys_times.add(bucket)
                    system_ts.append({
                        'unix_time': unix_time,
                        'sys_offset_us': round(sys_offset_s * 1e6, 2),
                    })

        return {
            'sources': sources_data,
            'system': system_ts,
            'start': start_iso,
            'end': end_iso,
            'n_sources': len(sources_data),
        }


# ════════════════════════════════════════════════════════════════════
# The chrony envelope (Timing page, "Host clock" section).
#
# chrony reports each source's offset as the local clock minus that
# source.  FUSE and HPPS carry hf-timestd's registration (FUSE from the
# T3 tick fusion, HPPS from the T6 pulse), so their offsets read as the
# host clock minus the registration: D_clock.  By the electorate rule
# (MEASUREMENT_MODEL §7.1.1) a source recovered from the sample stream
# may not vote on the host clock, so the page draws both as noselect
# witnesses in fixed colours.  NTP servers and peers fold into one
# min/median/max envelope, so twenty servers draw as one grey band.
# ════════════════════════════════════════════════════════════════════

#: points per trace the page receives, at most
MAX_POINTS = 1500
#: fusion writes a row per source per minute; no bucket gets narrower
MIN_BUCKET_S = 60.0
#: longest window the envelope serves
ENVELOPE_MAX_WINDOW = timedelta(days=7)
HEAVY_FAMILY = "chrony envelope"
#: hf-timestd's refclocks, recovered from the sample stream
WITNESSES = ("FUSE", "HPPS")
WITNESS_ROLE = "noselect witness"
#: other refclocks (a T5 PPS, say) each keep a line; at most this many
MAX_OTHER_REFCLOCKS = 8
NTP_MODES = ("^", "=")

_ENVELOPE_COLUMNS = ("unix_time", "source_name", "source_mode", "offset_us", "sys_offset_s")


def _med(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def get_envelope(start: datetime, end: datetime, max_points: int = MAX_POINTS) -> Dict[str, Any]:
    """Bucketed chrony offsets over ``[start, end]``.

    Streams five projected columns of DIAG_chrony_stats in time order and
    finishes each bucket before the next begins, so memory holds one bucket
    plus the output arrays.
    """
    start, end = as_utc(start), as_utc(end)
    t0s, t1s = start.timestamp(), end.timestamp()
    bucket_s = max(MIN_BUCKET_S, (t1s - t0s) / max_points)
    out: Dict[str, Any] = {
        "available": False, "start": iso_z(start), "end": iso_z(end),
        "bucket_s": bucket_s, "n_rows": 0,
        "label": "host clock minus registration",
        "sign": "chrony offset: local clock minus source",
        "time": [],
        "ntp": {"min_ms": [], "median_ms": [], "max_ms": [], "n_sources": [], "members": []},
        "witnesses": {w: {"offset_ms": [], "role": WITNESS_ROLE} for w in WITNESSES},
        "other_refclocks": {},
        "system_offset_ms": [],
    }
    members: set = set()
    others: Dict[str, List[Optional[float]]] = {}
    acc: Dict[str, Any] = {"b": None}

    def reset():
        acc.update(ntp=[], wit={w: [] for w in WITNESSES}, oth={}, sys=[])

    def flush():
        b = acc["b"]
        if b is None:
            return
        k = len(out["time"])
        out["time"].append(iso_z(datetime.fromtimestamp(
            min(t0s + (b + 0.5) * bucket_s, t1s), tz=timezone.utc)))
        ntp = acc["ntp"]
        out["ntp"]["min_ms"].append(min(ntp) if ntp else None)
        out["ntp"]["median_ms"].append(_med(ntp))
        out["ntp"]["max_ms"].append(max(ntp) if ntp else None)
        out["ntp"]["n_sources"].append(len(ntp))
        for w in WITNESSES:
            out["witnesses"][w]["offset_ms"].append(_med(acc["wit"][w]))
        for name, series in others.items():
            series.extend([None] * (k - len(series)))
            series.append(_med(acc["oth"].get(name, [])))
        out["system_offset_ms"].append(_med(acc["sys"]))
        reset()

    reset()
    with heavy.guard(HEAVY_FAMILY):
        for part in projected.iter_rows("DIAG_chrony_stats", "fusion", start, end,
                                        _ENVELOPE_COLUMNS):
            for unix_time, name, mode, offset_us, sys_s in part:
                if unix_time is None or not name:
                    continue
                out["n_rows"] += 1
                b = min(max(int((float(unix_time) - t0s) // bucket_s), 0), max_points - 1)
                if b != acc["b"]:
                    flush()
                    acc["b"] = b
                if sys_s is not None:
                    acc["sys"].append(float(sys_s) * 1e3)
                if offset_us is None:
                    continue
                ms = float(offset_us) / 1000.0
                if name in WITNESSES:
                    acc["wit"][name].append(ms)
                elif mode in NTP_MODES:
                    members.add(name)
                    acc["ntp"].append(ms)
                elif mode == "#":
                    if name not in others and len(others) < MAX_OTHER_REFCLOCKS:
                        others[name] = []
                    if name in others:
                        acc["oth"].setdefault(name, []).append(ms)
        flush()

    n = len(out["time"])
    for name, series in others.items():
        series.extend([None] * (n - len(series)))
        out["other_refclocks"][name] = {"offset_ms": series}
    out["ntp"]["members"] = sorted(members)
    out["available"] = n > 0
    if n == 0:
        out["reason"] = "no DIAG_chrony_stats rows in this window"
    return out
