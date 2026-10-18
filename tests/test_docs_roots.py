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


def test_overlong_name_is_404_not_500(docs_client):
    assert docs_client.get("/api/living-docs/one/" + "a" * 300 + ".md").status_code in (400, 404)


def test_trailing_newline_rejected_by_regex(docs_client):
    assert docs_client.get("/api/living-docs/one/GUIDE.md%0A").status_code == 400


def test_list_hides_unservable_names(docs_client, tmp_path):
    root = tmp_path / "r1"
    (root / "<img src=x onerror=alert(1)>.md").write_text("x")
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "SECRET.md").write_text("s")
    (root / "LINK.md").symlink_to(outside / "SECRET.md")
    docs = {r["name"]: r["documents"] for r in docs_client.get("/api/living-docs/list").json()["roots"]}["one"]
    assert docs == ["GUIDE.md"]


def test_docs_page_survives_a_malformed_hash():
    """decodeURIComponent throws URIError on a hash like #%E0; the page catches it."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
            / "docs.html").read_text()
    assert "const hash = decodeURIComponent(" not in text
    body = text.split("function safeHash", 1)
    assert len(body) == 2 and "catch" in body[1][:300]
