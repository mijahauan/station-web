"""Every kept route registers and answers on an EMPTY data root.
A products-only client must degrade to empty payloads, never 500."""
import pytest

KEPT_PREFIXES = ["/api/station", "/api/stations", "/api/health", "/api/metrology",
                 "/api/stability", "/api/propagation", "/api/space-weather", "/api/correlations",
                 "/api/physics", "/api/tec", "/api/tid", "/api/dashboard", "/api/phase",
                 "/api/grape", "/api/ionogram", "/api/chrony", "/api/docs",
                 "/api/living-docs"]
CUT_PATHS = ["/api/logs/services", "/api/timing-validation/status", "/api/health/storage"]


def _routes(client):
    """Every GET path the app serves.

    FastAPI 0.141 keeps an included router lazy — ``app.routes`` holds one
    opaque ``_IncludedRouter`` per ``include_router`` call and only resolves
    the paths when a request arrives.  The OpenAPI schema is the public way
    to enumerate them; the app's own routes add back the few that stay out
    of the schema (the Swagger and ReDoc pages).
    """
    paths = {p for p, ops in client.app.openapi()["paths"].items() if "get" in ops}
    paths |= {r.path for r in client.app.routes
              if hasattr(r, "methods") and "GET" in r.methods}
    return sorted(paths)


def test_kept_routers_registered(client):
    paths = _routes(client)
    for prefix in KEPT_PREFIXES:
        assert any(p.startswith(prefix) for p in paths), prefix


def test_cut_routes_are_gone(client):
    for p in CUT_PATHS:
        assert client.get(p).status_code == 404, p


def test_parameterless_gets_never_500_on_empty_root(client):
    failures = []
    for path in _routes(client):
        # "/api/docs" exactly is FastAPI's own Swagger page; the docs router
        # lives at /api/living-docs and IS swept.
        if "{" in path or path == "/api/docs":
            continue
        r = client.get(path)
        if r.status_code >= 500:
            failures.append((path, r.status_code, r.text[:200]))
    assert not failures, failures
