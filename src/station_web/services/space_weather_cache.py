"""Space weather as hamsci-physics last saw it.  Read-only, no fetching."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
CACHE_REL = Path("iono_cache") / "space_weather.json"
STALE_AFTER_S = 3 * 3600          # hamsci-physics refreshes every 30 min; healthcheck allows 180
FIELDS = ("f107", "f107_time", "f107_source", "kp", "kp_time", "kp_source",
          "ap", "ap_time", "ap_source", "fetched_at")


class SpaceWeatherCache:
    def __init__(self, data_root: Path):
        self.path = Path(data_root) / CACHE_REL

    def current(self) -> dict:
        out = {k: None for k in FIELDS}
        out.update({"available": False, "age_s": None, "stale": True, "source_file": str(self.path)})
        if not self.path.exists():
            return out
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("space weather cache unreadable: %s", exc)
            return out
        for k in FIELDS:
            out[k] = data.get(k)
        fetched = data.get("fetched_at")
        if fetched:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched.replace("Z", "+00:00"))).total_seconds()
                out["age_s"] = round(age, 1)
                out["stale"] = age > STALE_AFTER_S
            except ValueError:
                pass
        out["available"] = out["f107"] is not None or out["kp"] is not None
        return out

    @staticmethod
    def series_unavailable(kind: str) -> dict:
        return {"available": False, "kind": kind, "data": [],
                "reason": f"station-web reads hamsci-physics's snapshot cache; "
                          f"no {kind} time-series product exists yet"}
