"""The Station page's rows and the check view, built from snapshots.

Pure functions over the snapshot envelopes' ``data``; ``services/snapshot.py``
does the reading.  Nothing here supplies a station fact of its own.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from station_web.config import Settings
from station_web.services import snapshot as snap

#: sigmond's stage order (``STAGES`` in lib/sigmond/commands/check.py).
STAGES = ("platform", "sensor", "decode", "sink", "upload")
#: Worst first.  ``--`` is "not on this station" and outranks nothing.
_MARK_RANK = {"FAIL": 3, "wait": 2, "ok": 1, "--": 0}


def _list(data: Any, key: Optional[str] = None) -> List[dict]:
    if key and isinstance(data, dict):
        data = data.get(key)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _instance_counts(instances: Any, client: str) -> Optional[Dict[str, int]]:
    if not isinstance(instances, dict):
        return None                 # no snapshot: zero would be a claim
    rows = [r for r in _list(instances, "instances") if r.get("client") == client]
    return {"configured": sum(1 for r in rows if r.get("has_config")),
            "enabled": sum(1 for r in rows if r.get("enabled") == "enabled"),
            "active": sum(1 for r in rows if r.get("active") == "active")}


def _worst_check(check: Any, client: str) -> Optional[Dict[str, str]]:
    worst = None
    for ln in _list(check, "lines"):
        if ln.get("client") != client:
            continue
        if worst is None or (_MARK_RANK.get(ln.get("mark"), 0)
                             > _MARK_RANK.get(worst.get("mark"), 0)):
            worst = ln
    return None if worst is None else {"id": worst.get("id"), "mark": worst.get("mark")}


def build_rows(catalog, components, instances, manifest, check) -> List[Dict[str, Any]]:
    """One row per catalog entry, then installed components the catalog lacks."""
    comp_by_name = {c.get("name"): c for c in _list(components)}
    man_ok = isinstance(manifest, dict) and manifest.get("present")
    man_comps = manifest.get("components") if man_ok else None
    man_comps = man_comps if isinstance(man_comps, dict) else {}

    def row(name, description, kind, installed):
        comp = comp_by_name.get(name) or {}
        fetched = comp.get("fetched_at")
        if not installed:
            mstatus = None
        elif not isinstance(manifest, dict):
            mstatus = "unknown"     # snapshot unavailable, nothing compared
        elif not man_ok:
            mstatus = "no_manifest"
        else:
            mstatus = man_comps.get(name, "not_listed")
        return {"name": name, "description": description,
                "kind": kind or comp.get("kind"), "installed": bool(installed),
                "local": comp.get("local"), "manifest_status": mstatus,
                # A count against a ref never fetched is no count at all.
                "behind": comp.get("behind") if fetched else None,
                "fetched_at": fetched,
                "instances": _instance_counts(instances, name),
                "check": _worst_check(check, name)}

    rows, seen = [], set()
    for c in _list(catalog):
        name = c.get("name")
        seen.add(name)
        rows.append(row(name, c.get("description"), c.get("kind"),
                        c.get("installed")))
    for name, comp in comp_by_name.items():
        if name not in seen and comp.get("local"):
            rows.append(row(name, None, comp.get("kind"), True))
    return rows


def _heartbeat(env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = env.get("data")
    roll = data.get("rollup") if isinstance(data, dict) else None
    if not isinstance(roll, dict):
        return None
    return {"verdict": roll.get("verdict"), "reason": roll.get("reason"),
            "emitted_at": data.get("emitted_at")}


def station_summary(settings: Settings, now: Optional[float] = None) -> Dict[str, Any]:
    envs = {n: snap.read_snapshot(n, settings, now) for n in snap.NAMES}
    d = {n: e["data"] for n, e in envs.items()}
    sigmond = next((c for c in _list(d["components"]) if c.get("name") == "sigmond"), {})
    st = settings.station
    viewers = d["viewers"] if isinstance(d["viewers"], dict) else None
    return {
        "identity": {"callsign": st.get("callsign", ""),
                     "grid_square": st.get("grid_square", ""),
                     "sigmond": {"local": sigmond.get("local"),
                                 "behind": sigmond.get("behind")
                                 if sigmond.get("fetched_at") else None,
                                 "fetched_at": sigmond.get("fetched_at")}},
        "rows": build_rows(d["catalog"], d["components"], d["instances"],
                           d["manifest"], d["check"]),
        "viewers": viewers,
        "heartbeat": _heartbeat(envs["heartbeat"]),
        "snapshots": {n: {k: e[k] for k in
                          ("available", "command", "ran_at", "age_s", "stale",
                                 "error")}
                      for n, e in envs.items()},
    }


def check_view(settings: Settings, now: Optional[float] = None) -> Dict[str, Any]:
    env = snap.read_snapshot("check", settings, now)
    data = env["data"] if isinstance(env["data"], dict) else None
    if not env["available"] or data is None:
        verdict = "NONE"            # nothing to pass, whatever the reason
    elif env["stale"]:
        verdict = "STALE"           # an old PASS is not a pass
    else:
        verdict = data.get("verdict") or "NONE"
    lines = _list(data, "lines")
    stages = []
    for stage in STAGES:
        in_stage = [ln for ln in lines if ln.get("stage") == stage]
        if in_stage:
            stages.append({"stage": stage, "lines": in_stage})
    venv = snap.read_snapshot("validate", settings, now)
    # No rules run is not "no rule failed": null says the answer is missing.
    if venv["available"] and not venv["stale"] and isinstance(venv["data"], dict):
        results = _list(venv["data"], "results")
        validate = ([r for r in results if r.get("severity") == "fail"]
                    + [r for r in results if r.get("severity") == "warn"])
    else:
        validate = None
    return {"verdict": verdict, "stale": env["stale"], "ran_at": env["ran_at"],
            "age_s": env["age_s"], "stages": stages, "validate": validate,
            "validate_snapshot": {k: venv[k] for k in
                                  ("available", "stale", "age_s", "error")},
            "available": env["available"], "error": env["error"]}
