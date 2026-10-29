"""GET /commands, /commands/reference, /commands/search-index."""
import pytest
from fastapi.testclient import TestClient

from test_cli_reference_service import ALPHA, SMD, write_snapshot, env  # noqa: F401


@pytest.fixture
def cclient(env):
    from station_web import config as cfgmod
    from station_web.app import create_app
    cfgmod.config.use(env.settings)
    with TestClient(create_app(env.settings, control_socket=None)) as c:
        yield c


def test_routes_registered(cclient, env):
    write_snapshot(env.snap, [SMD, ALPHA])
    for path in ("/commands", "/commands/reference", "/commands/search-index"):
        assert cclient.get(path).status_code == 200, path
    assert "Commands" in cclient.get("/commands").text
    ref = cclient.get("/commands/reference").json()
    assert [p["program"] for p in ref["programs"]] == ["smd", "alpha"]
    assert any(e["kind"] == "command" for e in cclient.get("/commands/search-index").json())


def test_absent_snapshot_degrades(cclient):
    r = cclient.get("/commands/reference")
    assert r.status_code == 200
    j = r.json()
    assert j["programs"] == [] and j["freshness"]["available"] is False and j["freshness"]["error"]
    assert cclient.get("/commands/search-index").status_code == 200


def test_malformed_snapshot_is_not_a_500(cclient, env):
    (env.snap / "cli-reference.json").write_text("{not json")
    r = cclient.get("/commands/reference")
    assert r.status_code == 200 and r.json()["freshness"]["error"]


def test_commands_router_is_core(env):
    from station_web.app import CORE_ROUTERS, SCIENCE_ROUTERS
    assert "commands" in CORE_ROUTERS and "commands" not in SCIENCE_ROUTERS
