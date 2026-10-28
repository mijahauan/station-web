"""Write a ``viewers.json`` snapshot envelope for the viewer-proxy tests."""
import json
from datetime import datetime, timezone
from pathlib import Path


def write_viewers_file(snapshot_dir, rows, conflicts=None):
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    env = {"name": "viewers", "command": "smd admin viewers --json",
           "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "exit": 0, "error": None,
           "data": {"viewers": rows, "conflicts": conflicts or []}}
    (snapshot_dir / "viewers.json").write_text(json.dumps(env))
