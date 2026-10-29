"""The command reference view: sigmond's ``cli-reference`` snapshot plus examples.

The snapshot holds what ``smd admin cli-reference show --json`` prints, in
sigmond's usual envelope.  Examples come from ``docs/commands/<program>.md``
in the docs root named after the program's component.
"""
import json
import os
from datetime import datetime, timezone

import pytest


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _arg(name, **over):
    a = {"name": name, "flags": [], "kind": "positional", "help": "", "choices": None,
         "default": None, "required": False, "nargs": None, "metavar": None}
    a.update(over)
    return a


def _cmd(path, help="", args=()):
    return {"path": path, "help": help, "description": "", "arguments": list(args)}


def _prog(program, commands, component="sigmond", **over):
    p = {"schema": 1, "program": program, "version": "1.0", "entry": "x:main",
         "commands": commands, "error": None, "component": component,
         "built_at": "2026-10-29T10:00:00Z", "stale": False}
    p.update(over)
    return p


SMD = _prog("smd", [_cmd("smd", "the root"), _cmd("smd check", "run checks",
                                                     [_arg("--json", flags=["--json"], kind="flag")]),
                    _cmd("smd admin docs index", "index the docs")])
ZED = _prog("zed", [_cmd("zed run", "run it")], component="hf-timestd")
ALPHA = _prog("alpha", [_cmd("alpha go", "go")], component="hf-timestd")


def write_snapshot(d, programs, ran_at=None, exit=0, data=None):
    env = {"name": "cli-reference", "command": "smd admin cli-reference show --json",
           "ran_at": ran_at or _now(), "exit": exit,
           "data": data if data is not None else {"schema": 1, "programs": programs},
           "error": None}
    (d / "cli-reference.json").write_text(json.dumps(env))


@pytest.fixture
def env(settings, tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    sig = tmp_path / "docs-sigmond"
    (sig / "commands").mkdir(parents=True)
    hft = tmp_path / "docs-hft"
    (hft / "design").mkdir(parents=True)
    (hft / "design" / "MODEL.md").write_text("# Model\n\n## The ruler\n\nText.\n")
    settings.raw["status"] = {"snapshot_dir": str(snap)}
    settings.raw["docs"] = {"roots": [{"name": "sigmond", "path": str(sig)},
                                      {"name": "hf-timestd", "path": str(hft)}]}
    class E:
        pass
    e = E()
    e.settings, e.snap, e.sig, e.hft = settings, snap, sig, hft
    return e


def _view(e):
    from station_web.services import cli_reference
    return cli_reference.reference_view(e.settings)


def _cmds(view, program):
    return {c["path"]: c for p in view["programs"] if p["program"] == program
            for c in p["commands"]}


def test_examples_attach_by_heading(env):
    write_snapshot(env.snap, [SMD])
    (env.sig / "commands" / "smd.md").write_text(
        "# smd\n\n## smd check\n\nRun `smd check --json`.\n\n### Detail\n\nmore\n\n## smd admin docs index\n\nIndexing.\n")
    v = _view(env)
    c = _cmds(v, "smd")
    assert "Run `smd check --json`." in c["smd check"]["example"]
    assert "### Detail" in c["smd check"]["example"]
    assert "Indexing." not in c["smd check"]["example"]
    assert c["smd admin docs index"]["example"].strip() == "Indexing."
    assert c["smd"]["example"] is None


def test_missing_examples_listed(env):
    write_snapshot(env.snap, [SMD])
    (env.sig / "commands" / "smd.md").write_text("## smd check\n\nx\n")
    v = _view(env)
    assert v["missing_examples"] == ["smd", "smd admin docs index"]
    assert v["unmatched_examples"] == []


def test_no_examples_file_marks_every_command_missing(env):
    write_snapshot(env.snap, [SMD])
    v = _view(env)
    assert len(v["missing_examples"]) == 3


def test_unmatched_example_sections_are_reported(env):
    write_snapshot(env.snap, [SMD])
    (env.sig / "commands" / "smd.md").write_text("## smd chek\n\ntypo\n\n## smd check\n\nok\n")
    v = _view(env)
    assert v["unmatched_examples"] == [{"program": "smd", "heading": "smd chek"}]
    assert _cmds(v, "smd")["smd check"]["example"].strip() == "ok"


def test_headings_inside_code_fences_do_not_split(env):
    write_snapshot(env.snap, [SMD])
    (env.sig / "commands" / "smd.md").write_text(
        "## smd check\n\n```\n## not a heading\n```\n")
    v = _view(env)
    assert v["unmatched_examples"] == []
    assert "## not a heading" in _cmds(v, "smd")["smd check"]["example"]


def test_programs_order_smd_first_then_alphabetical(env):
    write_snapshot(env.snap, [ZED, ALPHA, SMD])
    assert [p["program"] for p in _view(env)["programs"]] == ["smd", "alpha", "zed"]


def test_failed_walk_shows_error_not_empty(env):
    bad = _prog("bad", [], error="not an argparse program")
    write_snapshot(env.snap, [SMD, bad])
    v = _view(env)
    p = [p for p in v["programs"] if p["program"] == "bad"][0]
    assert p["error"] == "not an argparse program" and p["commands"] == []


def test_stale_flag_and_fields_pass_through(env):
    write_snapshot(env.snap, [_prog("smd", [_cmd("smd")], stale=True, version="2.0")])
    p = _view(env)["programs"][0]
    assert p["stale"] is True and p["version"] == "2.0" and p["component"] == "sigmond"


def test_absent_snapshot_degrades(env):
    v = _view(env)
    assert v["programs"] == [] and v["missing_examples"] == []
    assert v["freshness"]["available"] is False
    assert "not written" in v["freshness"]["error"]


@pytest.mark.parametrize("data", [None, "x", {"programs": "x"}, {"schema": 1}])
def test_malformed_snapshot_degrades(env, data):
    write_snapshot(env.snap, [], data=data if data is not None else [])
    v = _view(env)
    assert v["programs"] == [] and v["freshness"]["error"]


def test_failed_command_degrades(env):
    write_snapshot(env.snap, [SMD], exit=1)
    v = _view(env)
    assert v["programs"] == [] and v["freshness"]["error"]


def test_garbage_entries_are_skipped(env):
    write_snapshot(env.snap, [SMD], data={"programs": [
        5, {"program": 3}, {"program": "ok", "commands": [None, {"path": 4}, {"path": "ok go",
         "arguments": [7, {"name": "x"}]}]}]})
    v = _view(env)
    assert [p["program"] for p in v["programs"]] == ["ok"]
    assert [c["path"] for c in v["programs"][0]["commands"]] == ["ok go"]
    assert len(v["programs"][0]["commands"][0]["arguments"]) == 1


def test_stale_snapshot_reports_freshness(env):
    write_snapshot(env.snap, [SMD], ran_at="2020-01-01T00:00:00Z")
    f = _view(env)["freshness"]
    assert f["available"] is True and f["stale"] is True and f["age_s"] > 900


def test_hostile_program_name_never_builds_a_path(env, tmp_path):
    (tmp_path / "secret.md").write_text("## ../x go\n\nLEAK\n")
    evil = _prog("../../secret", [_cmd("../../secret go")])
    write_snapshot(env.snap, [evil])
    v = _view(env)
    assert v["programs"][0]["commands"][0]["example"] is None
    assert v["unmatched_examples"] == []


def test_symlinked_examples_file_outside_root_is_refused(env, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("## smd check\n\nLEAK\n")
    (env.sig / "commands" / "smd.md").symlink_to(outside)
    write_snapshot(env.snap, [SMD])
    assert _cmds(_view(env), "smd")["smd check"]["example"] is None


def test_unknown_component_root_means_no_examples(env):
    write_snapshot(env.snap, [_prog("p", [_cmd("p go")], component="nowhere")])
    assert _view(env)["missing_examples"] == ["p go"]


def test_search_index_holds_commands_and_headings(env):
    from station_web.services import cli_reference
    write_snapshot(env.snap, [SMD])
    idx = cli_reference.search_index(env.settings)
    cmd = [e for e in idx if e["kind"] == "command" and e["text"] == "smd admin docs index"][0]
    assert cmd["href"] == "/commands#smd-admin-docs-index"
    assert cmd["context"] == "index the docs"
    head = [e for e in idx if e["kind"] == "heading" and e["text"] == "The ruler"][0]
    assert head["href"] == "/static/docs.html?root=hf-timestd&doc=design/MODEL.md#the-ruler"
    assert head["context"] == "hf-timestd/design/MODEL.md"


def test_cache_rebuilds_when_examples_change(env):
    write_snapshot(env.snap, [SMD])
    f = env.sig / "commands" / "smd.md"
    f.write_text("## smd check\n\none\n")
    assert _cmds(_view(env), "smd")["smd check"]["example"].strip() == "one"
    f.write_text("## smd check\n\ntwo!\n")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert _cmds(_view(env), "smd")["smd check"]["example"].strip() == "two!"


def test_cache_reused_when_nothing_changed(env, monkeypatch):
    from station_web.services import cli_reference
    write_snapshot(env.snap, [SMD])
    _view(env)
    calls = []
    monkeypatch.setattr(cli_reference, "_build", lambda *a, **k: calls.append(1))
    _view(env)
    assert calls == []


def test_search_index_command_entry_carries_its_flags(env):
    from station_web.services import cli_reference
    write_snapshot(env.snap, [SMD])
    idx = cli_reference.search_index(env.settings)
    cmd = [e for e in idx if e["kind"] == "command" and e["text"] == "smd check"][0]
    assert "--json" in cmd["flags"]
    plain = [e for e in idx if e["kind"] == "command" and e["text"] == "smd admin docs index"][0]
    assert plain["flags"] == ""


def test_aliases_are_read_and_default_to_empty(env):
    write_snapshot(env.snap, [_prog("hf-timestd", [_cmd("hf-timestd status")],
                                    component="hf-timestd", aliases=["timestd"]),
                              ALPHA])
    progs = {p["program"]: p for p in _view(env)["programs"]}
    assert progs["hf-timestd"]["aliases"] == ["timestd"]
    assert progs["alpha"]["aliases"] == []


def test_an_alias_is_not_listed_as_a_program_or_as_missing_examples(env):
    # an old snapshot may still hold the alias's own record
    write_snapshot(env.snap, [
        _prog("hf-timestd", [_cmd("hf-timestd status")], component="hf-timestd",
              aliases=["timestd"]),
        _prog("timestd", [_cmd("timestd status")], component="hf-timestd")])
    v = _view(env)
    assert [p["program"] for p in v["programs"]] == ["hf-timestd"]
    assert v["missing_examples"] == ["hf-timestd status"]
