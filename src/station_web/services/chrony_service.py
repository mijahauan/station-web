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
        } for r in rows]
        sources.sort(key=lambda s: (STATE_ORDER.get(s["state"], 9), s["name"]))
        # sys_offset_s describes the host's tracking offset at the moment a
        # row was written — it must come from the newest row(s), not from
        # whichever source happens first in iteration order, or a stale
        # source's reading can silently override a fresh one's.
        newest_rows = [r for r in rows if r["unix_time"] == newest]
        sys_off = next((r.get("sys_offset_s") for r in newest_rows if r.get("sys_offset_s") is not None), None)
        if sys_off is None:
            sys_off = next((r.get("sys_offset_s") for r in rows if r.get("sys_offset_s") is not None), None)
        return {
            "timestamp_utc": datetime.fromtimestamp(newest, timezone.utc).isoformat().replace("+00:00", "Z"),
            "unix_time": newest, "age_s": round(age, 1), "stale": age > STALE_AFTER_S,
            "tracking": {"sys_offset_us": round(sys_off * 1e6, 2)} if sys_off is not None else None,
            "sources": sources,
        }

    def get_source_comparison(self) -> Dict[str, Any]:
        """Formatted source comparison table (sources, selected-first)."""
        snap = self.get_live_snapshot()
        if snap is None:
            return {"sources": [], "stale": True, "age_s": None, "note": "no DIAG_chrony_stats rows"}
        return {"sources": snap["sources"], "tracking": snap["tracking"],
                "stale": snap["stale"], "age_s": snap["age_s"], "timestamp_utc": snap["timestamp_utc"]}

    def get_history(self, hours: int = 24) -> Dict[str, Any]:
        """Get historical chrony stats from the DIAG_chrony_stats SQLite
        table.  Returns per-source time series of offset and std_dev,
        plus a deduplicated system-tracking offset series.
        """
        now = datetime.now(timezone.utc)
        start_iso = (now - timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')
        end_iso = now.isoformat().replace('+00:00', 'Z')

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
            return {'sources': {}, 'system': []}

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
            'hours': hours,
            'n_sources': len(sources_data),
        }
