"""The docs browser's folders and page facts, from sigmond's ``docs`` snapshot.

station-web runs no git, so sigmond's snapshot timer writes ``docs.json``
(``smd admin docs index --json``): one root per installed component's
``docs/`` folder, the unlisted ``checks`` root, and per page its header
fields, last commit and INDEX.md membership.

The file counts as input, not as trust.  A root counts only when its
resolved path lies under ``settings.docs_allowed_base`` (default
``ALLOWED_DOCS_BASE``); anything else gets dropped with a log line.  The
snapshot names folders only: the docs router still walks the disk to decide
which pages exist and may be served.

When the snapshot reads missing, stale, failed or empty of usable roots,
``docs_roots`` falls back to ``config.docs_roots`` and says why.

The parsed file stays cached, keyed by its path, mtime, size and inode, so a
page request does not re-parse it.  Freshness gets recomputed on every call
from the cached ``ran_at``.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from station_web.config import DEFAULT_DOCS_ALLOWED_BASE
from station_web.services.snapshot import _parse_ts, read_snapshot, staleness

logger = logging.getLogger(__name__)

#: A snapshot root counts only under this path (``[docs] allowed_base`` overrides).
ALLOWED_DOCS_BASE = DEFAULT_DOCS_ALLOWED_BASE

#: A root's name travels in the URL path, so it gets the same shape as a
#: directory component in the docs router: no leading dot, bounded.
_ROOT_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")

#: The page fields copied from the snapshot, and the value a page the
#: snapshot lacks gets for each.
DOC_FIELDS: Dict[str, Any] = {
    "title": None, "audience": None, "status": None, "last_commit": None,
    "age_days": None, "indexed": False, "canonical": False, "section": None,
}

_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "parsed": None}


def _str_or_none(v: Any) -> Optional[str]:
    return v if isinstance(v, str) else None


def _doc_fields(d: Dict[str, Any]) -> Dict[str, Any]:
    age = d.get("age_days")
    return {
        "title": _str_or_none(d.get("title")),
        "audience": _str_or_none(d.get("audience")),
        "status": _str_or_none(d.get("status")),
        "last_commit": _str_or_none(d.get("last_commit")),
        "age_days": age if type(age) is int else None,
        "indexed": d.get("indexed") is True,
        "canonical": d.get("canonical") is True,
        "section": _str_or_none(d.get("section")),
    }


def _parse(snap: Dict[str, Any]) -> Dict[str, Any]:
    """What one read of the file says, before any filesystem check."""
    out: Dict[str, Any] = {"ran": None, "reason": None, "roots": [],
                           "generated_at": None, "stale_days": None, "warned": set()}
    if not snap.get("available"):
        out["reason"] = f"docs snapshot unavailable: {snap.get('error')}"
        return out
    out["ran"] = _parse_ts(snap.get("ran_at"))
    data = snap.get("data")
    if snap.get("exit") not in (0, None) or not isinstance(data, dict) \
            or not isinstance(data.get("roots"), list):
        out["reason"] = "docs snapshot holds no index (the command failed)"
        return out
    out["generated_at"] = _str_or_none(data.get("generated_at"))
    sd = data.get("stale_days")
    out["stale_days"] = sd if type(sd) is int else None
    for r in data["roots"]:
        if not isinstance(r, dict):
            continue
        docs = r.get("documents")
        meta = {}
        for d in docs if isinstance(docs, list) else []:
            if isinstance(d, dict) and isinstance(d.get("name"), str):
                meta.setdefault(d["name"], _doc_fields(d))
        hi = r.get("has_index")
        out["roots"].append({"name": r.get("name"), "path": r.get("path"),
                             "listed": r.get("listed") is not False,
                             "has_index": hi if isinstance(hi, bool) else None,
                             "documents": meta})
    return out


def _load(settings) -> Dict[str, Any]:
    path = Path(settings.snapshot_dir) / "docs.json"
    try:
        st = os.stat(path)
    except OSError:
        return _parse(read_snapshot("docs", settings))
    key = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
    with _lock:
        if _cache["key"] == key:
            return _cache["parsed"]
    parsed = _parse(read_snapshot("docs", settings))
    with _lock:
        _cache["key"], _cache["parsed"] = key, parsed
    return parsed


def _under(base: Path, raw_path: Any) -> Optional[Path]:
    if not isinstance(raw_path, str) or not os.path.isabs(raw_path):
        return None
    try:
        p = Path(raw_path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return p if base in p.parents else None


def _fallback(settings, reason: str) -> Dict[str, Any]:
    roots = [{"name": r["name"], "path": r["path"], "listed": True, "has_index": None}
             for r in settings.docs_roots]
    return {"roots": roots, "doc_meta": {}, "stale_days": None,
            "meta": {"source": "fallback", "reason": reason, "generated_at": None}}


def _warn_once(parsed: Dict[str, Any], what: str, msg: str, *args: Any) -> None:
    """Log a dropped root once per version of the file, not once per request."""
    if what not in parsed["warned"]:
        parsed["warned"].add(what)
        logger.warning(msg, *args)


def docs_state(settings, now: Optional[float] = None) -> Dict[str, Any]:
    """Roots, page facts and provenance, all from one read of the snapshot."""
    parsed = _load(settings)
    if parsed["reason"] is not None:
        return _fallback(settings, parsed["reason"])
    if parsed["ran"] is None or staleness(parsed["ran"], now)[1]:
        return _fallback(settings, "docs snapshot is stale")
    try:
        base = Path(settings.docs_allowed_base).resolve()
    except (OSError, RuntimeError):
        return _fallback(settings, "docs allowed base unreadable")
    roots: List[Dict[str, Any]] = []
    meta: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seen = set()
    for r in parsed["roots"]:
        name = r["name"]
        if not isinstance(name, str) or not _ROOT_NAME.fullmatch(name) or name in seen:
            _warn_once(parsed, repr(("name", name)),
                       "docs snapshot: dropped root %r (bad or repeated name)", name)
            continue
        p = _under(base, r["path"])
        if p is None:
            _warn_once(parsed, repr(("path", name, r["path"], str(base))),
                       "docs snapshot: dropped root %r at %r (not under %s)",
                       name, r["path"], base)
            continue
        seen.add(name)
        roots.append({"name": name, "path": str(p), "listed": r["listed"],
                      "has_index": r["has_index"]})
        for doc, fields in r["documents"].items():
            meta[(name, doc)] = fields
    if not roots:
        return _fallback(settings, f"docs snapshot named no folder under {base}")
    return {"roots": roots, "doc_meta": meta, "stale_days": parsed["stale_days"],
            "meta": {"source": "snapshot", "reason": None,
                     "generated_at": parsed["generated_at"]}}


def docs_roots(settings, now: Optional[float] = None
               ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """``(roots, meta)``: each root ``{"name", "path", "listed", "has_index"}``."""
    s = docs_state(settings, now)
    return s["roots"], s["meta"]


def doc_meta(settings, now: Optional[float] = None) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Per-page fields from the snapshot, keyed ``(root, name)``; empty in fallback."""
    return docs_state(settings, now)["doc_meta"]
