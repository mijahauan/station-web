"""No family of heavy reads runs unbounded in parallel (final review S3).

Starlette's threadpool runs up to 40 sync routes at once.  Phase held a
semaphore; the timeline, scintillation history, Allan deviation and dTEC
did not, so a burst of requests with shifted windows (fresh cache keys)
read a week of rows each, all at once.  Each family now holds one
semaphore of one, waits a few seconds, then answers 503.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from station_web import heavy
from station_web.services import propagation_service

BUSY = [
    ("timeline", "/api/propagation/timeline?start=-2h&end=now"),
    ("scintillation", "/api/physics/scintillation/history?start=-2h&end=now"),
    ("scintillation", "/api/physics/scintillation/paths"),
    ("stability", "/api/stability/adev?start=-24h&end=now"),
    ("dtec", "/api/tec/dtec?start=-6h&end=now"),
    ("ionogram", "/api/ionogram/combined?start=-6h&end=now"),
]


@pytest.mark.parametrize("family,url", BUSY, ids=[u.split("?")[0] for _, u in BUSY])
def test_a_held_family_answers_503_quickly(seeded_client, monkeypatch, family, url):
    propagation_service.clear_timeline_cache()
    monkeypatch.setattr(heavy, "WAIT_S", 0.2)
    sem = heavy.semaphore(family)
    assert sem.acquire(timeout=1)
    try:
        t0 = time.monotonic()
        r = seeded_client.get(url)
        assert r.status_code == 503, (url, r.status_code, r.text[:200])
        assert time.monotonic() - t0 < 2.0
    finally:
        sem.release()


def test_timeline_computations_never_overlap(seeded_client, monkeypatch):
    propagation_service.clear_timeline_cache()
    live, peak = [0], [0]
    lock = threading.Lock()
    real = propagation_service.PropagationService._compute_mode_timeline

    def tracked(self, *a, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            time.sleep(0.05)
            return real(self, *a, **kw)
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(propagation_service.PropagationService, "_compute_mode_timeline", tracked)

    now = datetime.now(timezone.utc)
    codes = []

    def get(h):
        s = (now - timedelta(hours=h + 2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        codes.append(seeded_client.get(f"/api/propagation/timeline?start={s}&end={e}").status_code)

    # four windows, so the cache cannot absorb them
    threads = [threading.Thread(target=get, args=(h,)) for h in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1
    assert set(codes) <= {200, 404, 503}
