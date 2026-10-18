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
        try:
            return ref - timedelta(**{_UNITS[m.group(2)]: float(m.group(1))})
        except OverflowError:
            raise ValueError("Relative time reaches past the calendar") from None
    if s.startswith("-"):
        raise ValueError(f"Invalid relative time {value!r}: use -<n><s|m|h|d|w>, e.g. -6h")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid time {value!r}: use 'now', -<n><s|m|h|d|w>, or ISO 8601") from None
    return as_utc(dt)


def parse_window(start: str, end: str, now: datetime | None = None,
                 max_span: timedelta | None = None) -> tuple[datetime, datetime]:
    """Parse a ``start``/``end`` pair against ONE ``now``; refuse an inverted window.

    ``max_span`` caps the window's length.  Every route that reads rows
    over the window names one, so no request can read a table end to end.
    The refusal is a ``ValueError`` with fixed text (a 400 at the router).
    """
    ref = as_utc(now) if now is not None else utcnow()
    t0, t1 = parse_utc(start, ref), parse_utc(end, ref)
    if t0 > t1:
        raise ValueError("Start time must be before end time")
    if max_span is not None and t1 - t0 > max_span:
        raise ValueError(f"Window longer than {span_text(max_span)}")
    return t0, t1


def span_text(span: timedelta) -> str:
    """``span`` in the largest whole unit: ``7 days``, ``24 hours``, ``90 minutes``."""
    s = int(span.total_seconds())
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s % size == 0 and s >= size:
            n = s // size
            if unit == "day" and n == 1:
                return "24 hours"
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{s} seconds"


def iso_z(dt: datetime) -> str:
    """``dt`` as ISO 8601 UTC with a ``Z`` suffix (naive is taken as UTC).

    ``dt.isoformat() + 'Z'`` produced ``...+00:00Z`` for an aware value and
    a zone-less string's meaning depended on the reader; this does neither.
    """
    return as_utc(dt).isoformat().replace("+00:00", "Z")
