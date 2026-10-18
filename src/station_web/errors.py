"""A fault is not an empty window.

Services used to wrap every read in ``except Exception: return None``, and
routers turned that ``None`` into 404 "No ... available" or an empty 200.
A TypeError in the reader, a NameError in the service and a quiet station
all looked the same on the page.  Services now let unexpected exceptions
propagate; routers answer them with HTTP 500 and a detail that names the
failure, and keep 404/empty answers for windows that hold no rows.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException


def internal_error(logger: logging.Logger, what: str, exc: BaseException) -> HTTPException:
    """Log ``exc`` with its traceback and build the 500 the router raises."""
    logger.error("%s failed: %s", what, exc, exc_info=exc)
    return HTTPException(status_code=500, detail=f"{what} failed: {type(exc).__name__}: {exc}")
