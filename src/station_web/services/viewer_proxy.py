"""Front door for sigmond's viewers: find the localhost port behind a name.

The port comes only from the ``viewers`` snapshot.  Nothing here probes a
port, and nothing a browser sends chooses a host or a port.  A stale snapshot
still resolves, because ports rarely move and the proxy's own connect error
reports a dead viewer.  ``active`` does not gate resolution for the same
reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from station_web.config import Settings
from station_web.services.snapshot import read_snapshot

UPSTREAM_HOST = "127.0.0.1"


@dataclass(frozen=True)
class Target:
    name: str
    port: int
    kind: str


class Refusal(Exception):
    """A refusal with a fixed, user-facing message (never exception text)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _kind(name: str) -> str:
    if name == "ka9q-web" or name.startswith("ka9q-web-"):
        return "ka9q-web"
    if name == "gmag-webui":
        return "gmag"
    return "other"


def _valid_port(port) -> bool:
    return (isinstance(port, int) and not isinstance(port, bool)
            and 1 <= port <= 65535)


def resolve(name: str, settings: Settings) -> Target:
    snap = read_snapshot("viewers", settings)
    data = snap.get("data")
    if not snap.get("available") or not isinstance(data, dict) \
            or not isinstance(data.get("viewers"), list):
        raise Refusal(503, "viewer list unavailable")
    row = next((r for r in data["viewers"]
                if isinstance(r, dict) and r.get("name") == name), None)
    if row is None:
        raise Refusal(404, "no such viewer on this station")
    conflicts = data.get("conflicts")
    for c in conflicts if isinstance(conflicts, list) else []:
        names = c.get("names") if isinstance(c, dict) else None
        if isinstance(names, list) and name in names:
            raise Refusal(409, "port %s is claimed by %s" % (
                c.get("port"), " and ".join(str(n) for n in names)))
    port = row.get("port")
    if not _valid_port(port):
        raise Refusal(502, "viewer has no usable port")
    return Target(name=name, port=port, kind=_kind(name))
