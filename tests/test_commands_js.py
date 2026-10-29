"""commands.js behaviour in node: the page drawn from a reference answer.

The fake DOM throws on ``innerHTML``, so server data that reached markup
fails the test; examples render through an injected markdown function.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "src" / "station_web" / "static" / "js"
      / "commands.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = r"""
function mk(tag) {
  const n = { tag, children: [], textContent: '', className: '', attrs: {},
    appendChild(k) { this.children.push(k); return k; },
    replaceChildren(...k) { this.children = k; this.textContent = ''; },
    setAttribute(a, v) { this.attrs[a] = String(v); }, addEventListener() {} };
  Object.defineProperty(n, 'innerHTML', { set(v) { this.html = v; }, get() { return this.html; } });
  return n;
}
globalThis.window = globalThis;
globalThis.document = { createElement: mk,
  createTextNode: t => ({ tag: '#text', textContent: String(t), children: [], className: '', attrs: {} }) };
function text(n) { return (n.textContent || '') + (n.children || []).map(text).join(''); }
function all(n, pred) { const out = []; (function w(x) { if (pred(x)) out.push(x); (x.children || []).forEach(w); })(n); return out; }
function byClass(n, c) { return all(n, x => (x.className || '').split(' ').includes(c)); }
const box = mk('div');
"""


def _run(body, expr):
    script = PRELUDE + JS.read_text() + "\n" + body + f"\nconsole.log(JSON.stringify({expr}));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _cmd(path, **over):
    c = {"path": path, "help": "h", "description": "", "arguments": [], "example": None}
    c.update(over)
    return c


def _prog(name, commands, **over):
    p = {"program": name, "component": "sigmond", "version": "1", "stale": False,
         "error": None, "commands": commands}
    p.update(over)
    return p


def _ref(programs, **over):
    r = {"freshness": {"available": True, "stale": False, "age_s": 30, "error": None},
         "programs": programs, "missing_examples": [], "unmatched_examples": []}
    r.update(over)
    return r


def _render(ref, expr, opts="{}"):
    return _run(f"CommandsPage.renderReference(box, {json.dumps(ref)}, {opts});", expr)


def test_failed_walk_shows_reference_unavailable_with_the_error():
    ref = _ref([_prog("bad", [], error="entry point failed to import")])
    out = _render(ref, "byClass(box, 'cmd-unavailable').map(text)")
    assert out == ["Reference unavailable: entry point failed to import"]


def test_stale_badge_only_where_stale():
    ref = _ref([_prog("a", [_cmd("a go")], stale=True), _prog("b", [_cmd("b go")])])
    out = _render(ref, "byClass(box, 'cmd-stale').map(text)")
    assert out == ["stale"]


def test_closing_sections_list_missing_and_unmatched():
    ref = _ref([_prog("smd", [_cmd("smd check")])], missing_examples=["smd check"],
               unmatched_examples=[{"program": "smd", "heading": "smd chek"}])
    out = _render(ref, "byClass(box, 'cmd-closing').map(text)")
    assert "Commands without an example" in out[0] and "smd check" in out[0]
    assert "Example sections that match no command" in out[1] and "smd: smd chek" in out[1]


def test_command_anchor_and_arguments_table():
    arg = {"name": "x", "flags": ["--x"], "kind": "option", "help": "an x", "choices": ["a", "b"],
           "default": "a", "required": True, "nargs": None, "metavar": "X"}
    ref = _ref([_prog("smd", [_cmd("smd admin docs", arguments=[arg])])])
    out = _render(ref, "[all(box, n => n.attrs && n.attrs.id === 'smd-admin-docs').length,"
                       " byClass(box, 'cmd-arg-name').map(text), all(box, n => n.tag === 'tr').map(text)]")
    assert out[0] == 1 and out[1] == ["--x X"]
    assert "choices: a, b" in out[2][1] and "required" in out[2][1]


def test_example_goes_through_the_markdown_renderer():
    ref = _ref([_prog("smd", [_cmd("smd check", example="*hi*")])])
    out = _render(ref, "byClass(box, 'cmd-example').map(n => n.html)",
                  "{markdown: md => '<em>' + md + '</em>'}")
    assert out == ["<em>*hi*</em>"]


def test_example_without_renderer_is_plain_text():
    ref = _ref([_prog("smd", [_cmd("smd check", example="<b>x</b>")])])
    assert _render(ref, "byClass(box, 'cmd-example').map(text)") == ["<b>x</b>"]


def test_server_strings_never_reach_innerhtml():
    ref = _ref([_prog("<img>", [_cmd("<img> go", help="<script>")], error=None)])
    out = _render(ref, "text(box)")
    assert "<script>" in out


def test_filter_is_case_insensitive_over_text_and_context():
    idx = [{"kind": "command", "text": "smd Check", "context": "run", "href": "/a"},
           {"kind": "heading", "text": "Other", "context": "sigmond/CHECK.md", "href": "/b"},
           {"kind": "heading", "text": "nope", "context": "x", "href": "/c"}]
    out = _run(f"var i = {json.dumps(idx)};", "[CommandsPage.filter(i, 'check').map(e => e.href),"
                                              " CommandsPage.filter(i, '  ').length]")
    assert out == [["/a", "/b"], 0]


def test_freshness_says_why_when_unavailable():
    out = _run("CommandsPage.renderFreshness(box, {available: false, error: 'snapshot not written yet'});",
               "text(box)")
    assert "snapshot not written yet" in out


def test_filter_matches_a_commands_flags():
    idx = [{"kind": "command", "text": "smd check", "context": "run", "flags": "--json --quiet",
            "href": "/a"},
           {"kind": "command", "text": "smd list", "context": "list", "flags": "", "href": "/b"}]
    out = _run(f"var i = {json.dumps(idx)};",
               "[CommandsPage.filter(i, '--quiet').map(e => e.href), CommandsPage.filter(i, '--nope').length]")
    assert out == [["/a"], 0]


def test_empty_reference_says_no_program_is_recorded_and_names_the_build():
    out = _run("CommandsPage.renderFreshness(box, {available: true, stale: false, age_s: 30, error: null}, 0);",
               "text(box)")
    assert "No program is recorded yet" in out
    assert "smd admin cli-reference build" in out
    assert "Reference built" not in out


def test_nonempty_reference_still_says_how_old_it_is():
    out = _run("CommandsPage.renderFreshness(box, {available: true, stale: false, age_s: 30, error: null}, 3);",
               "text(box)")
    assert "Reference built 30 s ago" in out


def test_aliases_show_under_the_program_title():
    ref = _ref([_prog("hf-timestd", [_cmd("hf-timestd status")], aliases=["timestd", "ts"]),
                _prog("alpha", [_cmd("alpha go")], aliases=[])])
    out = _render(ref, "byClass(box, 'cmd-aliases').map(text)")
    assert out == ["also: timestd, ts"]
