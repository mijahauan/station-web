"""The WWVB decode ledger: which minute WWVB named, and how often it could.

hf-timestd's ``WwvbLedger`` appends compact JSON lines to
``<data root>/wwvb/YYYY-MM-DD.jsonl``, one file per UTC day:

- ``kind: "pass"``: one decode pass, success or not (``snr_db``,
  ``seconds_detected``, ``bits``, ``frames``, ``frames_implausible``);
- ``kind: "frame"``: one decoded frame (``minute`` UTC, DST and leap
  fields, parity and sync errors, polarity, ``vs_wallclock_s`` = decoded
  minute minus the host clock at decode, ``plausible``).

A WWVB frame names the minute.  It cannot name the second: the registration
does that (MEASUREMENT_MODEL §1, §3), and WWVB arrives through the
receiver's own samples, so §7.1.1 keeps it off the host clock's electorate.
What it can show is whether the host clock names the right minute.

The newest file gets one seek and one read of at most
``timing_authority.TAIL_BYTES`` from its end, as the incidents file does.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import List, Optional

from station_web.config import Settings
from station_web.services import timing_authority
from station_web.timeutil import utcnow

MAX_FRAMES = 50
_DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
ROLE = ("WWVB names the minute, not the second: the registration places the second "
        "(MEASUREMENT_MODEL §1, §3). WWVB arrives through the receiver's own samples, so "
        "§7.1.1 keeps it off the host clock's electorate; a frame checks that the host "
        "clock names the right minute.")
_PASS_FIELDS = ("ts", "snr_db", "seconds_detected", "bits", "frames", "frames_implausible")
_FRAME_FIELDS = ("ts", "minute", "plausible", "vs_wallclock_s", "parity_errors",
                 "sync_errors", "inverted", "dst", "leap")


def _project(obj: dict, fields) -> dict:
    out = {}
    for f in fields:
        v = obj.get(f)
        out[f] = v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
    return out


def _parse_ts(ts) -> Optional[datetime]:
    try:
        return timing_authority._parse_ts(ts)
    except Exception:                    # noqa: BLE001 — a bad stamp is an absent stamp
        return None


def summary(settings: Settings, n: int = 10) -> dict:
    n = max(0, min(int(n), MAX_FRAMES))
    d = settings.wwvb_dir
    files = sorted(p for p in d.iterdir() if _DAY_FILE.match(p.name)) if d.is_dir() else []
    if not files:
        return {"available": False,
                "reason": "No WWVB ledger on this station: hf-timestd writes one only where "
                          "its [wwvb] decoder runs.", "role": ROLE}
    path = files[-1]
    passes: List[dict] = []
    frames: List[dict] = []
    bad = 0
    for line in timing_authority.tail_lines(path):
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(obj, dict):
            bad += 1
            continue
        if obj.get("kind") == "pass":
            passes.append(_project(obj, _PASS_FIELDS))
        elif obj.get("kind") == "frame":
            frames.append(_project(obj, _FRAME_FIELDS))
    plausible = [f for f in frames if f.get("plausible") is True]
    last_ts = None
    for rec in (passes[-1:] + frames[-1:]):
        t = _parse_ts(rec.get("ts"))
        if t is not None and (last_ts is None or t > last_ts):
            last_ts = t
    return {
        "available": True, "file": path.name, "role": ROLE,
        "age_s": None if last_ts is None else round((utcnow() - last_ts).total_seconds(), 1),
        "n_malformed": bad,
        "window": {"first_ts": min((r["ts"] for r in passes[:1] + frames[:1] if r.get("ts")),
                                   default=None),
                   "last_ts": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ") if last_ts else None},
        "passes": {"n": len(passes),
                   "with_frames": sum(1 for p in passes if (p.get("frames") or 0) > 0),
                   "last": passes[-1] if passes else None},
        "frames": {"n": len(frames), "plausible": len(plausible),
                   "last_plausible": plausible[-1] if plausible else None},
        "recent_frames": list(reversed(frames))[:n],
    }
