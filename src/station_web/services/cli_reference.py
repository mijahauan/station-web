"""The command reference page's facts: sigmond's ``cli-reference`` snapshot
plus the examples people wrote.

sigmond's walker records each installed program's commands and arguments;
the snapshot timer publishes them as ``cli-reference.json``
(``smd admin cli-reference show --json``).  Examples come only from people:
``docs/commands/<program>.md`` in the docs root named after the program's
component, one ``## <command path>`` section per command.

The snapshot and the docs count as input, not trust.  A program name must
look like a file name before it reaches a path.  A file under a docs root
gets read only when the docs router's own checks pass (name shape, resolved
path strictly inside the root, regular file).  A missing, stale or malformed
snapshot degrades with a reason; this module never raises on bad input.

The parsed examples and the search index stay cached, keyed by the snapshot
file's identity and the docs roots' ``.md`` file signatures.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from station_web.routers.docs import MAX_LISTED, _confined, _disk_names, _slug, _valid_name
from station_web.services.docs_index import docs_roots
from station_web.services.snapshot import read_snapshot

logger = logging.getLogger(__name__)

#: Bytes read of one docs file, at most.
MAX_DOC_BYTES = 256 * 1024
#: A program name travels into a file name, so it gets a plain shape.
_PROGRAM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "built": None}


def _s(v: Any) -> Optional[str]:
    return v if isinstance(v, str) else None


def _str_list(v: Any) -> Optional[List[str]]:
    if not isinstance(v, list):
        return None
    return [x for x in v if isinstance(x, str)]


def _arg(a: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(a, dict):
        return None
    return {"name": _s(a.get("name")) or "", "flags": _str_list(a.get("flags")) or [],
            "kind": _s(a.get("kind")) or "option", "help": _s(a.get("help")) or "",
            "choices": _str_list(a.get("choices")), "default": _s(a.get("default")),
            "required": a.get("required") is True, "nargs": _s(a.get("nargs")),
            "metavar": _s(a.get("metavar"))}


def _command(c: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(c, dict) or not _s(c.get("path")):
        return None
    args = c.get("arguments")
    return {"path": c["path"], "help": _s(c.get("help")) or "",
            "description": _s(c.get("description")) or "",
            "arguments": [x for x in (_arg(a) for a in args)
                          if x is not None] if isinstance(args, list) else [],
            "example": None}


def _program(p: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(p, dict) or not _s(p.get("program")):
        return None
    cmds = p.get("commands")
    return {"program": p["program"], "component": _s(p.get("component")),
            "version": _s(p.get("version")), "stale": p.get("stale") is True,
            "error": _s(p.get("error")),
            "aliases": _str_list(p.get("aliases")) or [],
            "commands": [x for x in (_command(c) for c in cmds)
                         if x is not None] if isinstance(cmds, list) else []}


def _order(progs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """smd first, then A to Z."""
    return sorted(progs, key=lambda p: (p["program"] != "smd", p["program"].lower(),
                                        p["program"]))


def _sections(text: str) -> List[Tuple[str, str]]:
    """``[(heading, body)]`` for each ``## `` section, fences respected."""
    out: List[Tuple[str, List[str]]] = []
    fenced = False
    for ln in text.splitlines():
        if _FENCE.match(ln):
            fenced = not fenced
        m = None if fenced else re.match(r"^##\s+(.*?)\s*#*\s*$", ln)
        if m and not ln.startswith("###"):
            out.append((m.group(1).strip().strip("`").strip(), []))
        elif out:
            out[-1][1].append(ln)
        elif not fenced and re.match(r"^#\s", ln):
            continue
    return [(h, "\n".join(b).strip("\n")) for h, b in out]


def _read(base: Path, name: str) -> Optional[str]:
    """One docs file under ``base``, through the docs router's confinement."""
    if not _valid_name(name):
        return None
    try:
        rb = base.resolve()
        p = (rb / name).resolve()
        if not _confined(rb, p):
            return None
        with open(p, "rb") as fh:
            return fh.read(MAX_DOC_BYTES).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _signature(roots: List[Dict[str, Any]]) -> Tuple:
    sig = []
    for r in roots:
        base = Path(r["path"])
        files = []
        try:
            for p in base.rglob("*.md"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append((p.relative_to(base).as_posix(), st.st_mtime_ns, st.st_size))
                if len(files) >= MAX_LISTED * 2:
                    break
        except OSError:
            pass
        sig.append((r["name"], r["path"], tuple(sorted(files))))
    return tuple(sig)


def _heading_hits(roots: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for r in roots:
        if not r.get("listed", True):
            continue
        base = Path(r["path"])
        for name in _disk_names(base):
            text = _read(base, name)
            if text is None:
                continue
            fenced = False
            for ln in text.splitlines():
                if _FENCE.match(ln):
                    fenced = not fenced
                    continue
                m = None if fenced else _HEADING.match(ln)
                if not m or not m.group(2):
                    continue
                anchor = _slug(m.group(2))
                if not anchor:
                    continue
                href = (f"/static/docs.html?root={quote(r['name'], safe='')}"
                        f"&doc={quote(name, safe='/')}#{quote(anchor, safe='')}")
                out.append({"kind": "heading", "text": m.group(2),
                            "context": f"{r['name']}/{name}", "href": href})
    return out


def _build(env: Dict[str, Any], roots: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = env.get("data")
    raw = data.get("programs") if isinstance(data, dict) else None
    progs = _order([x for x in (_program(p) for p in raw) if x is not None]) \
        if isinstance(raw, list) else []
    # A program recorded under another's alias is that program (an older
    # snapshot may still hold both): list it once, under the kept name.
    aliased = {(p["component"], a) for p in progs for a in p["aliases"]}
    progs = [p for p in progs if (p["component"], p["program"]) not in aliased]
    by_root = {r["name"]: Path(r["path"]) for r in roots}
    missing: List[str] = []
    unmatched: List[Dict[str, str]] = []
    for prog in progs:
        examples: Dict[str, str] = {}
        base = by_root.get(prog["component"]) if prog["component"] else None
        text = None
        if base is not None and _PROGRAM.fullmatch(prog["program"]):
            text = _read(base, f"commands/{prog['program']}.md")
        paths = {c["path"] for c in prog["commands"]}
        for heading, body in _sections(text or ""):
            if heading in paths:
                examples.setdefault(heading, body)
            else:
                unmatched.append({"program": prog["program"], "heading": heading})
        for c in prog["commands"]:
            c["example"] = examples.get(c["path"])
            if c["example"] is None:
                missing.append(c["path"])
    index: List[Dict[str, str]] = []
    for prog in progs:
        for c in prog["commands"]:
            flags = " ".join(f for a in c["arguments"] for f in a["flags"])
            index.append({"kind": "command", "text": c["path"],
                          "context": c["help"] or c["description"], "flags": flags,
                          "href": "/commands#" + c["path"].replace(" ", "-")})
    index += _heading_hits(roots)
    return {"programs": progs, "missing_examples": missing,
            "unmatched_examples": unmatched, "index": index}


def _failure(env: Dict[str, Any]) -> Optional[str]:
    if not env["available"]:
        return env["error"] or "snapshot unavailable"
    data = env["data"]
    if env.get("exit") not in (0, None) or not isinstance(data, dict) \
            or not isinstance(data.get("programs"), list):
        return "snapshot holds no reference (the command failed)"
    return None


def _built(settings, now: Optional[float]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    env = read_snapshot("cli-reference", settings, now)
    freshness = {"available": env["available"], "stale": env["stale"],
                 "ran_at": env["ran_at"], "age_s": env["age_s"],
                 "error": _failure(env)}
    if freshness["error"]:
        return freshness, {"programs": [], "missing_examples": [],
                           "unmatched_examples": [], "index": []}
    roots, _meta = docs_roots(settings, now)
    try:
        st = os.stat(Path(settings.snapshot_dir) / "cli-reference.json")
        snap_key = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        snap_key = None
    key = (snap_key, _signature(roots))
    with _lock:
        if snap_key is not None and _cache["key"] == key:
            return freshness, _cache["built"]
    built = _build(env, roots)
    with _lock:
        _cache["key"], _cache["built"] = key, built
    return freshness, built


def reference_view(settings, now: Optional[float] = None) -> Dict[str, Any]:
    freshness, b = _built(settings, now)
    return {"freshness": freshness, "programs": b["programs"],
            "missing_examples": b["missing_examples"],
            "unmatched_examples": b["unmatched_examples"]}


def search_index(settings, now: Optional[float] = None) -> List[Dict[str, str]]:
    return _built(settings, now)[1]["index"]
