"""Request-origin checks shared by the routes that act on a request."""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit


def same_origin(headers: Mapping[str, str]) -> bool:
    """Refuse a browser request another site started.  A client that sends
    neither header (curl) passes; a browser always sends one."""
    site = headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        return False
    origin = headers.get("origin")
    if origin is not None:
        return urlsplit(origin).netloc == headers.get("host")
    return True
