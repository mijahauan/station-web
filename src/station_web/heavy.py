"""One heavy read at a time, per family of routes.

A heavy read pulls a window of product rows into memory.  The unit runs
under ``MemoryMax=600M`` with no swap, and Starlette's threadpool runs up
to 40 sync routes at once, so parallel heavy reads once added up past the
limit and the kernel killed station-web (phase.html, D1).

Each family of heavy routes (ionogram, timeline, scintillation history,
Allan deviation) holds its own semaphore of one.  A request waits a few
seconds for it, then answers 503, rather than parking a worker thread
behind someone else's read.  The phase service keeps its own semaphore
(``phase_service._HEAVY``) on the same terms.  With every family bounded
to one read, and every read bounded in memory, the total stays bounded.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict, Iterator

from fastapi import HTTPException

#: How long a request waits for its family's semaphore before it answers 503.
WAIT_S = 5.0

_sems: Dict[str, threading.Semaphore] = {}
_sems_lock = threading.Lock()


class Busy(HTTPException):
    """Another heavy read of the same family held the semaphore past the wait.

    An ``HTTPException`` (503), so a router's ``except HTTPException: raise``
    passes it through unchanged.
    """

    def __init__(self, what: str):
        super().__init__(status_code=503, detail=f"{what} busy; try again shortly")


def semaphore(family: str) -> threading.Semaphore:
    with _sems_lock:
        sem = _sems.get(family)
        if sem is None:
            sem = _sems[family] = threading.Semaphore(1)
        return sem


@contextmanager
def guard(family: str, wait_s: float | None = None) -> Iterator[None]:
    """Hold ``family``'s semaphore for the block; raise ``Busy`` after the wait."""
    sem = semaphore(family)
    if not sem.acquire(timeout=WAIT_S if wait_s is None else wait_s):
        raise Busy(family)
    try:
        yield
    finally:
        sem.release()
