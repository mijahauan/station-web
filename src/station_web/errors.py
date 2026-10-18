"""A fault is not an empty window.

Services used to wrap every read in ``except Exception: return None``, and
routers turned that ``None`` into 404 "No ... available" or an empty 200.
A TypeError in the reader, a NameError in the service and a quiet station
all looked the same on the page.  Services now let unexpected exceptions
propagate; routers answer them with HTTP 500, and keep 404/empty answers
for windows that hold no rows.

The 500 names what failed and carries a short opaque error id, nothing
more.  Station pages may face the internet, and an exception's text can
carry file paths, SQL and configuration.  The full exception and its
traceback go to the journal under the same id, so an operator can match
a page's error to its cause.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException


def internal_error(logger: logging.Logger, what: str, exc: BaseException) -> HTTPException:
    """Log ``exc`` with its traceback under a fresh error id; build the 500."""
    eid = secrets.token_hex(4)
    logger.error("%s failed [error id %s]: %s", what, eid, exc, exc_info=exc)
    return HTTPException(status_code=500,
                         detail=f"{what} failed (error id {eid}); see the station-web journal")
