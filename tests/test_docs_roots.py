import pytest


@pytest.fixture
def docs_client(settings, tmp_path):
    from station_web import config as cfgmod
    from station_web.app import create_app
    from fastapi.testclient import TestClient
    r1 = tmp_path / "r1"; r1.mkdir()
    (r1 / "GUIDE.md").write_text("# Guide\n\nIntro.\n\n## Setup\n\nDo this.\n")
    (r1 / "secret.txt").write_text("nope")
    settings.raw["docs"] = {"roots": [{"name": "one", "path": str(r1)},
                                      {"name": "gone", "path": str(tmp_path / "gone")}]}
    cfgmod.config.use(settings)
    with TestClient(create_app(settings, control_socket=None)) as c:
        yield c


def test_list_reports_presence(docs_client):
    j = docs_client.get("/api/living-docs/list").json()
    by = {r["name"]: r for r in j["roots"]}
    assert by["one"]["present"] is True and by["one"]["documents"] == ["GUIDE.md"]
    assert by["gone"]["present"] is False and by["gone"]["documents"] == []


def test_get_and_section(docs_client):
    j = docs_client.get("/api/living-docs/one/GUIDE.md").json()
    assert "# Guide" in j["content"]
    s = docs_client.get("/api/living-docs/one/GUIDE.md/section/setup").json()
    assert "Do this." in s["content"]


@pytest.mark.parametrize("bad", ["..%2F..%2Fetc%2Fpasswd", "secret.txt", "GUIDE.md%2F..%2Fx.md", "a%00b.md"])
def test_rejects_escapes_and_non_markdown(docs_client, bad):
    assert docs_client.get(f"/api/living-docs/one/{bad}").status_code in (400, 404)


def test_unknown_root_404(docs_client):
    assert docs_client.get("/api/living-docs/nope/GUIDE.md").status_code == 404


def test_evidence_endpoints_gone(docs_client):
    assert docs_client.get("/api/living-docs/evidence/bootstrap/location").status_code in (400, 404)
