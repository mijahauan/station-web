"""The docs browser takes its folders and page facts from sigmond's ``docs`` snapshot.

sigmond writes ``docs.json`` (``smd admin docs index --json``): one root per
installed component's ``docs/`` folder, plus the unlisted ``checks`` root,
each page with its header fields, its last commit and whether INDEX.md links
it.  station-web reads that file, keeps only roots under the allowed base,
and still walks the disk for which pages exist and may be served.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest


def _stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _doc(name, **over):
    d = {"name": name, "title": None, "audience": None, "status": None,
         "last_commit": None, "age_days": None, "indexed": False,
         "canonical": False, "section": None}
    d.update(over)
    return d


@pytest.fixture
def tree(tmp_path):
    """A fake /opt/git/sigmond: sigmond, hf-timestd and hamsci-dsp checkouts."""
    base = tmp_path / "git"
    sig = base / "sigmond" / "docs"
    (sig / "operator" / "checks").mkdir(parents=True)
    (sig / "INDEX.md").write_text("# Index\n")
    (sig / "operator" / "install.md").write_text("# Install\n\nSteps.\n")
    (sig / "operator" / "checks" / "platform.bringup.md").write_text("# Bring-up\n")
    hft = base / "hf-timestd" / "docs"
    (hft / "design").mkdir(parents=True)
    (hft / "design" / "MEASUREMENT_MODEL.md").write_text(
        "# Measurement model\n\n## Ruler\n\nOne ruler.\n")
    (hft / "DISK_ONLY.md").write_text("# On disk only\n")
    dsp = base / "hamsci-dsp" / "docs"
    dsp.mkdir(parents=True)
    (dsp / "GUIDE.md").write_text("# Guide\n")
    return base


def _roots_data(base):
    return [
        {"name": "hf-timestd", "path": str(base / "hf-timestd" / "docs"),
         "listed": True, "has_index": False,
         "documents": [_doc("design/MEASUREMENT_MODEL.md", title="Measurement model",
                            audience="scientist", status="current",
                            last_commit="2026-07-01T00:00:00Z", age_days=119,
                            indexed=True, canonical=True)]},
        {"name": "hamsci-dsp", "path": str(base / "hamsci-dsp" / "docs"),
         "listed": True, "has_index": False, "documents": [_doc("GUIDE.md")]},
        {"name": "sigmond", "path": str(base / "sigmond" / "docs"),
         "listed": True, "has_index": True,
         "documents": [_doc("INDEX.md", indexed=True),
                       _doc("operator/install.md", title="Install", indexed=True,
                            section=None)]},
        {"name": "checks", "path": str(base / "sigmond" / "docs" / "operator" / "checks"),
         "listed": False, "has_index": True,
         "documents": [_doc("platform.bringup.md", indexed=False)]},
    ]


def _write_snapshot(snapdir, roots, age=timedelta(seconds=30), exit_code=0):
    env = {"name": "docs", "command": "smd admin docs index --json",
           "ran_at": _stamp(datetime.now(timezone.utc) - age), "exit": exit_code,
           "data": {"generated_at": "2026-10-28T12:00:00Z", "stale_days": 90,
                    "roots": roots},
           "error": None}
    (snapdir / "docs.json").write_text(json.dumps(env))


@pytest.fixture
def snapdir(settings, tmp_path, tree):
    d = tmp_path / "snap"
    d.mkdir()
    settings.raw["status"] = {"snapshot_dir": str(d)}
    settings.raw["docs"] = {"allowed_base": str(tree)}
    return d


@pytest.fixture
def docs_client(settings, snapdir):
    from station_web import config as cfgmod
    from station_web.app import create_app
    from fastapi.testclient import TestClient
    cfgmod.config.use(settings)
    with TestClient(create_app(settings, control_socket=None)) as c:
        yield c


def test_snapshot_names_docs_command():
    from station_web.services import snapshot as snap
    assert snap.COMMANDS["docs"] == "smd admin docs index --json"


def test_allowed_base_defaults_to_opt_git_sigmond(settings):
    from pathlib import Path
    from station_web.services import docs_index
    assert docs_index.ALLOWED_DOCS_BASE == Path("/opt/git/sigmond")
    settings.raw.pop("docs", None)
    assert settings.docs_allowed_base == Path("/opt/git/sigmond")


def test_roots_come_from_snapshot(settings, snapdir, tree):
    from station_web.services.docs_index import docs_roots
    _write_snapshot(snapdir, _roots_data(tree))
    roots, meta = docs_roots(settings)
    by = {r["name"]: r for r in roots}
    assert meta == {"source": "snapshot", "reason": None,
                    "generated_at": "2026-10-28T12:00:00Z"}
    assert set(by) == {"hf-timestd", "hamsci-dsp", "sigmond", "checks"}
    assert by["sigmond"]["path"] == str((tree / "sigmond" / "docs").resolve())
    assert by["sigmond"]["listed"] is True
    assert by["checks"]["listed"] is False


def test_root_outside_allowed_base_dropped(settings, snapdir, tree, tmp_path, caplog):
    from station_web.services.docs_index import docs_roots
    outside = tmp_path / "outside"
    outside.mkdir()
    (tree / "evil").symlink_to(outside, target_is_directory=True)
    bad = [
        {"name": "etc", "path": "/etc", "listed": True, "documents": []},
        {"name": "dotdot", "path": f"{tree}/../../etc", "listed": True, "documents": []},
        {"name": "linked", "path": str(tree / "evil"), "listed": True, "documents": []},
    ]
    _write_snapshot(snapdir, _roots_data(tree) + bad)
    with caplog.at_level(logging.WARNING, logger="station_web.services.docs_index"):
        roots, meta = docs_roots(settings)
    names = {r["name"] for r in roots}
    assert meta["source"] == "snapshot"
    assert names == {"hf-timestd", "hamsci-dsp", "sigmond", "checks"}
    for n in ("etc", "dotdot", "linked"):
        assert any(n in rec.getMessage() for rec in caplog.records), n


def test_malformed_root_entries_dropped(settings, snapdir, tree):
    from station_web.services.docs_index import docs_roots
    junk = ["x", {"name": 5, "path": str(tree / "hamsci-dsp" / "docs")},
            {"name": "../up", "path": str(tree / "hamsci-dsp" / "docs")},
            {"name": "nopath"}, {"name": "rel", "path": "hamsci-dsp/docs"}]
    _write_snapshot(snapdir, _roots_data(tree) + junk)
    roots, _ = docs_roots(settings)
    assert {r["name"] for r in roots} == {"hf-timestd", "hamsci-dsp", "sigmond", "checks"}


@pytest.mark.parametrize("case", ["missing", "stale", "failed", "no_roots"])
def test_fallback_when_snapshot_missing_or_stale(settings, snapdir, tree, case):
    from station_web.config import DEFAULT_DOCS_ROOTS
    from station_web.services.docs_index import docs_roots, doc_meta
    if case == "stale":
        _write_snapshot(snapdir, _roots_data(tree), age=timedelta(hours=2))
    elif case == "failed":
        env = {"name": "docs", "ran_at": _stamp(datetime.now(timezone.utc)),
               "exit": 1, "data": None, "error": "boom"}
        (snapdir / "docs.json").write_text(json.dumps(env))
    elif case == "no_roots":
        _write_snapshot(snapdir, [{"name": "etc", "path": "/etc", "listed": True}])
    settings.raw["docs"] = {"allowed_base": str(tree)}   # no "roots": the defaults
    roots, meta = docs_roots(settings)
    assert meta["source"] == "fallback"
    assert isinstance(meta["reason"], str) and meta["reason"]
    assert [{"name": r["name"], "path": r["path"]} for r in roots] == DEFAULT_DOCS_ROOTS
    assert all(r["listed"] is True for r in roots)
    assert doc_meta(settings) == {}


def test_doc_meta_keys_by_root_and_name(settings, snapdir, tree):
    from station_web.services.docs_index import doc_meta
    _write_snapshot(snapdir, _roots_data(tree))
    m = doc_meta(settings)
    row = m[("hf-timestd", "design/MEASUREMENT_MODEL.md")]
    assert row["title"] == "Measurement model" and row["canonical"] is True
    assert ("checks", "platform.bringup.md") in m


def test_list_joins_metadata_by_name(docs_client, snapdir, tree):
    _write_snapshot(snapdir, _roots_data(tree))
    j = docs_client.get("/api/living-docs/list").json()
    assert j["source"] == "snapshot" and j["reason"] is None and j["stale_days"] == 90
    by = {r["name"]: r for r in j["roots"]}
    hft = by["hf-timestd"]
    assert hft["present"] is True and hft["listed"] is True and hft["has_index"] is False
    docs = {d["name"]: d for d in hft["documents"]}
    mm = docs["design/MEASUREMENT_MODEL.md"]
    assert mm == {"name": "design/MEASUREMENT_MODEL.md", "title": "Measurement model",
                  "audience": "scientist", "status": "current",
                  "last_commit": "2026-07-01T00:00:00Z", "age_days": 119,
                  "indexed": True, "canonical": True, "section": None}
    assert docs["DISK_ONLY.md"] == _doc("DISK_ONLY.md")
    assert by["checks"]["listed"] is False
    sig = {d["name"] for d in by["sigmond"]["documents"]}
    assert {"INDEX.md", "operator/install.md",
            "operator/checks/platform.bringup.md"} <= sig


def test_list_in_fallback_says_so(docs_client, settings, tree):
    j = docs_client.get("/api/living-docs/list").json()
    assert j["source"] == "fallback" and j["reason"]
    assert j["stale_days"] is None
    for r in j["roots"]:
        assert r["listed"] is True
        assert all(d["indexed"] is False and d["title"] is None for d in r["documents"])


def test_snapshot_cannot_widen_serving(docs_client, snapdir, tree):
    deep = tree / "hamsci-dsp" / "docs" / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "e.md").write_text("# deep\n")
    (tree / "hamsci-dsp" / "secret.md").write_text("# secret\n")
    roots = _roots_data(tree)
    roots[1]["documents"] += [_doc("../secret.md", indexed=True),
                              _doc("a/b/c/d/e.md", indexed=True)]
    _write_snapshot(snapdir, roots)
    listed = {r["name"]: [d["name"] for d in r["documents"]]
              for r in docs_client.get("/api/living-docs/list").json()["roots"]}
    assert "../secret.md" not in listed["hamsci-dsp"]
    assert "a/b/c/d/e.md" not in listed["hamsci-dsp"]
    for bad in ("..%2Fsecret.md", "a/b/c/d/e.md"):
        assert docs_client.get(f"/api/living-docs/hamsci-dsp/{bad}").status_code in (400, 404)


def test_existing_links_still_resolve(docs_client, snapdir, tree):
    _write_snapshot(snapdir, _roots_data(tree))
    r = docs_client.get("/api/living-docs/checks/platform.bringup.md")
    assert r.status_code == 200 and "# Bring-up" in r.json()["content"]
    r = docs_client.get("/api/living-docs/hf-timestd/design/MEASUREMENT_MODEL.md")
    assert r.status_code == 200 and "One ruler." in r.json()["content"]
    r = docs_client.get("/api/living-docs/hf-timestd/design/MEASUREMENT_MODEL.md/section/ruler")
    assert r.status_code == 200 and "One ruler." in r.json()["content"]
    # The whole of sigmond's docs/ now serves, not docs/scientist alone.
    r = docs_client.get("/api/living-docs/sigmond/operator/install.md")
    assert r.status_code == 200 and "Steps." in r.json()["content"]


def test_parsed_snapshot_cached_by_mtime_and_size(settings, snapdir, tree, monkeypatch):
    from station_web.services import docs_index
    _write_snapshot(snapdir, _roots_data(tree))
    calls = []
    real = docs_index.read_snapshot
    monkeypatch.setattr(docs_index, "read_snapshot",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    docs_index.docs_roots(settings)
    docs_index.doc_meta(settings)
    docs_index.docs_roots(settings)
    assert len(calls) == 1
    # A rewrite (new size, new mtime) re-reads.
    roots = _roots_data(tree)[:2]
    _write_snapshot(snapdir, roots)
    st = (snapdir / "docs.json").stat()
    os.utime(snapdir / "docs.json", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    got, _ = docs_index.docs_roots(settings)
    assert len(calls) == 2
    assert {r["name"] for r in got} == {"hf-timestd", "hamsci-dsp"}


def test_cached_snapshot_still_goes_stale(settings, snapdir, tree):
    import time
    from station_web.services import docs_index
    _write_snapshot(snapdir, _roots_data(tree))
    assert docs_index.docs_roots(settings)[1]["source"] == "snapshot"
    later = time.time() + 3600
    assert docs_index.docs_roots(settings, now=later)[1]["source"] == "fallback"
