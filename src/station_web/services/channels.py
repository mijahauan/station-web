"""Channels known to the station, discovered from the products themselves.

hf-timestd's config listed channels under [recorder.channel_group]; a
read-only client has no such list.  The distinct ``channel`` values in
L2_timing_measurements say which channels actually produced data.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from hamsci_dsp.io.sqlite_writer import DEFAULT_DB_PATH

from station_web.config import Settings

logger = logging.getLogger(__name__)
_FREQ = re.compile(r"_(\d+)$")           # WWV_10000 -> 10000 kHz


def _db_path(settings: Settings) -> Path:
    return Path(settings.storage.get("sqlite_path", DEFAULT_DB_PATH))


def discover_channels(settings: Settings, table: str = "L2_timing_measurements") -> list[dict]:
    db = _db_path(settings)
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(f'SELECT DISTINCT channel FROM "{table}" ORDER BY channel').fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("channel discovery failed on %s: %s", db, exc)
        return []
    out = []
    for (name,) in rows:
        m = _FREQ.search(name or "")
        out.append({"name": name, "frequency_hz": int(m.group(1)) * 1000 if m else None})
    return out


def discover_channel_infos(settings: Settings) -> list[dict]:
    """The same channels in the ``ChannelInfo`` shape the models expect.

    ``models.station.ChannelInfo`` and ``HealthService`` both read
    ``channel_name`` / ``frequency_hz`` / ``frequency_mhz`` / ``description``,
    the field names hf-timestd's config parser produced.  Keep those names so
    neither the models nor the pages have to change.  A channel whose name
    carries no frequency suffix is reported at 0 Hz rather than dropped — the
    station did produce it.
    """
    infos = []
    for chan in discover_channels(settings):
        freq_hz = chan["frequency_hz"] or 0
        infos.append({
            "channel_name": chan["name"],
            "frequency_hz": freq_hz,
            "frequency_mhz": freq_hz / 1e6,
            "description": chan["name"],
        })
    return infos
