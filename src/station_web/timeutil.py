"""One reading of a time parameter, for every route.

Each router used to parse ``start``/``end`` itself.  Some returned naive
``datetime.utcnow()``, some returned aware datetimes from ISO input, and
the reader in hamsci-dsp compares a parsed bound with each row's aware
timestamp.  A naive bound there raises ``TypeError: can't compare
offset-naive and offset-aware datetimes``, which the services swallowed
as "no data".  A naive ``.timestamp()`` also silently assumes the host
runs on UTC.

Everything here returns an AWARE UTC datetime.  Accepted forms:

- ``now``
- relative to now: ``-90s``, ``-15m``, ``-6h``, ``-1d``, ``-2w``
  (a leading ``+`` or a bare number of seconds is not accepted)
- ISO 8601, with ``Z``, with an offset, or with no zone at all (a value
  with no zone means UTC, the only meaning it can have here); a bare date
  means 00:00 UTC that day.

Relative values count back from *now*, for ``start`` and ``end`` alike.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RELATIVE = re.compile(r"^-(\d+(?:\.\d+)?)([smhdw])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def utcnow() -> datetime:
    """The current instant as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """``dt`` as an aware UTC datetime; a naive value is taken to BE UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_utc(value: str, now: datetime | None = None) -> datetime:
    """Parse one time parameter into an aware UTC datetime.

    Raises ``ValueError`` (which routers turn into HTTP 400) for anything
    it cannot read.
    """
    if value is None:
        raise ValueError("missing time value")
    s = str(value).strip()
    ref = as_utc(now) if now is not None else utcnow()
    if s.lower() == "now":
        return ref
    m = _RELATIVE.match(s)
    if m:
        return ref - timedelta(**{_UNITS[m.group(2)]: float(m.group(1))})
    if s.startswith("-"):
        raise ValueError(f"Invalid relative time {value!r}: use -<n><s|m|h|d|w>, e.g. -6h")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid time {value!r}: use 'now', -<n><s|m|h|d|w>, or ISO 8601") from None
    return as_utc(dt)


def parse_window(start: str, end: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Parse a ``start``/``end`` pair against ONE ``now``; refuse an inverted window."""
    ref = as_utc(now) if now is not None else utcnow()
    t0, t1 = parse_utc(start, ref), parse_utc(end, ref)
    if t0 > t1:
        raise ValueError("Start time must be before end time")
    return t0, t1


def iso_z(dt: datetime) -> str:
    """``dt`` as ISO 8601 UTC with a ``Z`` suffix (naive is taken as UTC).

    ``dt.isoformat() + 'Z'`` produced ``...+00:00Z`` for an aware value and
    a zone-less string's meaning depended on the reader; this does neither.
    """
    return as_utc(dt).isoformat().replace("+00:00", "Z")
