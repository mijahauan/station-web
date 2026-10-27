"""Read sigmond's snapshots: ``<snapshot_dir>/<name>.json``.

sigmond's root timer runs each read-only ``smd`` command and writes its
output, in an envelope, to these files (see sigmond's ``lib/sigmond/
snapshot.py``).  station-web computes no station fact of its own, so this
module only reads.  A missing, unparseable or malformed file answers
``available: false`` with the reason, never an empty pass.  A file older than
``STALE_S`` reads ``stale``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from station_web.config import Settings

#: A snapshot older than this counts as stale (sigmond writes every 5 min).
STALE_S = 900.0
#: Bytes read of one snapshot file, at most.
#: A ran_at further ahead of the clock than this reads as stale.
FUTURE_SKEW_S = 60.0
MAX_BYTES = 4 * 1024 * 1024

#: Name -> command, matching sigmond's ``SNAPSHOT_COMMANDS``.  Used when the
#: file is absent, so the page can still say what it waits for.
COMMANDS: Dict[str, str] = {
    "check": "smd check --json",
    "components": "smd component list --json --no-fetch",
    "catalog": "smd component list --catalog --json",
    "instances": "smd admin instance list --json",
    "validate": "smd admin validate --json",
    "viewers": "smd admin viewers --json",
    "manifest": "smd admin manifest status --json",
    "heartbeat": "smd admin heartbeat show --json",
}
NAMES = tuple(COMMANDS)


def _absent(name: str, why: str) -> Dict[str, Any]:
    return {"available": False, "name": name, "command": COMMANDS.get(name),
            "ran_at": None, "age_s": None, "stale": False, "exit": None,
            "data": None, "error": why}


def _parse_ts(text: Any) -> Optional[float]:
    if not isinstance(text, str):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def read_snapshot(name: str, settings: Settings,
                  now: Optional[float] = None) -> Dict[str, Any]:
    if name not in COMMANDS:
        raise KeyError(name)
    path = settings.snapshot_dir / f"{name}.json"
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return _absent(name, "snapshot not written yet")
    except OSError as e:
        return _absent(name, f"snapshot unreadable ({e.__class__.__name__})")
    if len(raw) > MAX_BYTES:
        return _absent(name, "snapshot too large to read")
    try:
        env = json.loads(raw)
    except ValueError:
        return _absent(name, "snapshot is not valid JSON")
    if not isinstance(env, dict):
        return _absent(name, "snapshot is not an envelope")
    ran = _parse_ts(env.get("ran_at"))
    if ran is None:
        return _absent(name, "snapshot has no usable ran_at")
    raw_age = (time.time() if now is None else now) - ran
    age = max(0.0, raw_age)
    # A ran_at well ahead of the clock cannot be trusted to be fresh.
    stale = age > STALE_S or raw_age < -FUTURE_SKEW_S
    return {"available": True, "name": name,
            "command": env.get("command") or COMMANDS[name],
            "ran_at": env.get("ran_at"), "age_s": age, "stale": stale,
            "exit": env.get("exit"), "data": env.get("data"),
            "error": env.get("error")}
