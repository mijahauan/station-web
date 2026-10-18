"""The docs router reaches markdown in subdirectories of a root.

hf-timestd keeps its authoritative design notes one level down
(``docs/design/MEASUREMENT_MODEL.md``).  A flat ``*.md`` glob never listed
them, so the Docs page could not open the measurement model.  Depth comes
with the same confinement as before: no ``..``, no symlink out of the root,
bounded names.
"""
import pytest


@pytest.fixture
def docs_client(settings, tmp_path):
    from station_web import config as cfgmod
    from station_web.app import create_app
    from fastapi.testclient import TestClient
    r1 = tmp_path / "r1"
    (r1 / "design").mkdir(parents=True)
    (r1 / "GUIDE.md").write_text("# Guide\n")
    (r1 / "design" / "MEASUREMENT_MODEL.md").write_text(
        "# Measurement model\n\n## Ruler\n\nOne ruler.\n")
    (r1 / ".hidden").mkdir()
    (r1 / ".hidden" / "X.md").write_text("x")
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "SECRET.md").write_text("s")
    (r1 / "linked").symlink_to(outside, target_is_directory=True)
    settings.raw["docs"] = {"roots": [{"name": "one", "path": str(r1)}]}
    cfgmod.config.use(settings)
    with TestClient(create_app(settings, control_socket=None)) as c:
        yield c


def test_list_includes_subdirectory_docs(docs_client):
    roots = docs_client.get("/api/living-docs/list").json()["roots"]
    docs = roots[0]["documents"]
    assert "GUIDE.md" in docs
    assert "design/MEASUREMENT_MODEL.md" in docs
    # hidden directories and symlinked-out directories stay out
    assert not any(d.startswith(".hidden") or d.startswith("linked") for d in docs)


def test_get_subdirectory_doc(docs_client):
    for url in ("/api/living-docs/one/design/MEASUREMENT_MODEL.md",
                "/api/living-docs/one/design%2FMEASUREMENT_MODEL.md"):
        r = docs_client.get(url)
        assert r.status_code == 200, url
        assert "One ruler." in r.json()["content"]


def test_section_of_subdirectory_doc(docs_client):
    r = docs_client.get("/api/living-docs/one/design/MEASUREMENT_MODEL.md/section/ruler")
    assert r.status_code == 200
    assert "One ruler." in r.json()["content"]


@pytest.mark.parametrize("bad", [
    "design/..%2FGUIDE.md",          # a literal "../" the client would normalise away
    "design%2F..%2F..%2Foutside%2FSECRET.md",
    "linked/SECRET.md",
    ".hidden/X.md",
    "/etc/passwd.md",
    "a/" * 10 + "deep.md",
    "design/" + "b" * 200 + ".md",
])
def test_subdirectory_escapes_refused(docs_client, bad):
    assert docs_client.get(f"/api/living-docs/one/{bad}").status_code in (400, 404), bad
