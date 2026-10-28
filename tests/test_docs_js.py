"""docs-browser.js behaviour: the document list drawn in node from a list answer.

A small fake DOM records what the script builds.  Its ``innerHTML`` setter
throws, so any data that reached the page through markup fails the test.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

BROWSER = (Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"
           / "js" / "docs-browser.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = r"""
function mk(tag) {
  const n = { tag, children: [], textContent: '', className: '', style: {}, attrs: {},
    appendChild(k) { this.children.push(k); return k; },
    append(...k) { k.forEach(x => this.children.push(typeof x === 'string'
      ? { tag: '#text', textContent: x, children: [], className: '', attrs: {} } : x)); },
    replaceChildren(...k) { this.children = k; this.textContent = ''; },
    setAttribute(a, v) { this.attrs[a] = String(v); }, getAttribute(a) { return this.attrs[a]; },
    addEventListener(ev, fn) { this.attrs['on' + ev] = 'set'; (this.listeners = this.listeners || {})[ev] = fn; } };
  Object.defineProperty(n, 'innerHTML', { set() { throw new Error('innerHTML used'); } });
  return n;
}
globalThis.window = globalThis;
globalThis.document = {
  createElement: mk,
  createTextNode: t => ({ tag: '#text', textContent: String(t), children: [], className: '', attrs: {} }),
};
function text(n) { return (n.textContent || '') + n.children.map(text).join(''); }
function all(n, pred) {
  const out = [];
  (function walk(x) { if (pred(x)) out.push(x); (x.children || []).forEach(walk); })(n);
  return out;
}
function byClass(n, cls) {
  return all(n, x => (x.className || '').split(' ').includes(cls));
}
function entries(n) { return byClass(n, 'db-entry').map(e => [e.attrs['data-root'], e.attrs['data-doc']]); }
function texts(n, cls) { return byClass(n, cls).map(text); }
const box = mk('div');
"""


def _render(data, prefs, expr):
    script = (PRELUDE + BROWSER.read_text()
              + f"\nDocsBrowser.render(box, {json.dumps(data)}, {json.dumps(prefs)}, () => {{}});"
              + f"\nconsole.log(JSON.stringify({expr}));\n")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _run(body, expr):
    script = PRELUDE + BROWSER.read_text() + "\n" + body + f"\nconsole.log(JSON.stringify({expr}));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _doc(name, **over):
    d = {"name": name, "title": None, "audience": None, "status": None,
         "last_commit": "2026-10-01T00:00:00Z", "age_days": 27, "indexed": False,
         "canonical": False, "section": None}
    d.update(over)
    return d


def _root(name, docs, **over):
    r = {"name": name, "path": "/opt/git/sigmond/" + name, "present": True,
         "listed": True, "has_index": True, "documents": docs}
    r.update(over)
    return r


def _data(roots, **over):
    d = {"source": "snapshot", "reason": None, "stale_days": 90, "roots": roots}
    d.update(over)
    return d


DEFAULT = {"showAll": False, "groupBy": "component"}
EVERYTHING = {"showAll": True, "groupBy": "component"}

ROOT = _root("sigmond", [
    _doc("GUIDE.md", indexed=True, title="The guide"),
    _doc("INDEX.md", indexed=True, title="Index"),
    _doc("loose.md"),
    _doc("archive/old.md", section="archive"),
    _doc("superpowers/plans/p.md", section="superpowers"),
])


def test_default_view_hides_unindexed_and_puts_index_first():
    out = _render(_data([ROOT]), DEFAULT, "entries(box)")
    assert out == [["sigmond", "INDEX.md"], ["sigmond", "GUIDE.md"]]


def test_show_everything_adds_unindexed_then_sections():
    out = _render(_data([ROOT]), EVERYTHING,
                  "[entries(box), texts(box, 'db-section-head')]")
    assert out[0] == [["sigmond", "INDEX.md"], ["sigmond", "GUIDE.md"], ["sigmond", "loose.md"],
                      ["sigmond", "archive/old.md"], ["sigmond", "superpowers/plans/p.md"]]
    assert out[1] == ["archive", "superpowers"]


def test_group_by_audience_orders_the_groups():
    root = _root("hf-timestd", [
        _doc("INDEX.md", indexed=True),
        _doc("a.md", indexed=True, audience="scientist"),
        _doc("b.md", indexed=True, audience="operator"),
        _doc("c.md", indexed=True, audience="all"),
        _doc("d.md", indexed=True, audience="contributor"),
    ])
    out = _render(_data([root]), {"showAll": False, "groupBy": "audience"},
                  "[texts(box, 'db-group-head'), entries(box), texts(box, 'db-root')]")
    assert out[0] == ["operator", "scientist", "contributor", "all", "no audience given"]
    assert out[1] == [["hf-timestd", "b.md"], ["hf-timestd", "a.md"], ["hf-timestd", "d.md"],
                      ["hf-timestd", "c.md"], ["hf-timestd", "INDEX.md"]]
    # across components, each entry names the one it came from
    assert set(out[2]) == {"hf-timestd"}


def test_stale_marker_past_the_threshold_only():
    root = _root("r", [_doc("INDEX.md", indexed=True, age_days=91,
                            last_commit="2026-07-29T10:00:00Z"),
                       _doc("fresh.md", indexed=True, age_days=89)])
    out = _render(_data([root]), DEFAULT, "texts(box, 'db-age')")
    assert out == ["not edited since 2026-07-29"]


def test_unknown_age_says_so():
    root = _root("r", [_doc("INDEX.md", indexed=True, age_days=None, last_commit=None)])
    out = _render(_data([root]), DEFAULT, "texts(box, 'db-age')")
    assert out == ["age unknown"]


def test_root_without_index_shows_every_ordinary_page_with_a_note():
    root = _root("r", [_doc("a.md"), _doc("b.md"), _doc("archive/x.md", section="archive")],
                 has_index=False)
    out = _render(_data([root]), DEFAULT, "[entries(box), texts(box, 'db-note')]")
    assert out[0] == [["r", "a.md"], ["r", "b.md"]]
    assert out[1] == ["no INDEX.md: showing every page"]


def test_fallback_banner_names_the_reason():
    root = _root("sigmond", [_doc("INDEX.md"), _doc("x.md")], has_index=True)
    out = _render(_data([root], source="fallback", reason="docs.json is 3 h old"), DEFAULT,
                  "[texts(box, 'db-banner'), entries(box)]")
    assert out[0] == ["Page facts unavailable (docs.json is 3 h old): "
                      "showing the fixed folders without ages or index."]
    # no index facts: every ordinary page shows, INDEX.md still first
    assert out[1] == [["sigmond", "INDEX.md"], ["sigmond", "x.md"]]
    assert _render(_data([root]), DEFAULT, "texts(box, 'db-banner')") == []


def test_unlisted_root_never_renders():
    checks = _root("checks", [_doc("INDEX.md", indexed=True)], listed=False)
    for prefs in (DEFAULT, EVERYTHING, {"showAll": True, "groupBy": "audience"}):
        out = _render(_data([ROOT, checks]), prefs, "[entries(box), text(box)]")
        assert all(r != "checks" for r, _ in out[0])
        assert "checks" not in out[1]


def test_entry_shows_title_star_and_status():
    root = _root("r", [_doc("INDEX.md", indexed=True, canonical=True, title="Start here",
                            status="current"),
                       _doc("old.md", indexed=True, status="historical"),
                       _doc("p.md", indexed=True, status="pointer")])
    out = _render(_data([root]), DEFAULT,
                  "[texts(box, 'db-title'), texts(box, 'db-star'), texts(box, 'db-status'),"
                  " texts(box, 'db-muted')]")
    assert out[0] == ["Start here", "old.md", "p.md"]
    assert out[1] == ["★"]
    assert out[2] == ["current", "historical", "pointer"]
    assert out[3] == ["historical", "pointer"]


def test_titles_and_names_go_in_as_text():
    root = _root("<b>r</b>", [_doc("INDEX.md", indexed=True, title="<img src=x>"),
                              _doc("<i>.md", indexed=True, audience="<script>")])
    out = _render(_data([root]), {"showAll": True, "groupBy": "audience"},
                  "[texts(box, 'db-title'), texts(box, 'db-root'), texts(box, 'db-group-head')]")
    assert "<img src=x>" in out[0] and "<i>.md" in out[0]
    assert "<b>r</b>" in out[1]
    assert "<script>" in out[2]


def test_entry_link_is_a_deep_link_and_opens_the_page():
    out = _run("let got = [];\n"
               f"DocsBrowser.render(box, {json.dumps(_data([ROOT]))}, {json.dumps(DEFAULT)},"
               " (r, d) => { got.push([r, d]); });\n"
               "const a = byClass(box, 'db-title')[1];\n"
               "function click(over) { const ev = Object.assign({ button: 0, prevented: false,"
               " preventDefault() { this.prevented = true; } }, over);"
               " const before = got.length; a.listeners.click(ev);"
               " return [ev.prevented, got.slice(before)]; }",
               "[a.href, click({}), click({ctrlKey: true}), click({button: 1}), click({metaKey: true})]")
    assert out[0] == "?root=sigmond&doc=GUIDE.md"
    # a plain left click opens the page in place
    assert out[1] == [True, [["sigmond", "GUIDE.md"]]]
    # a modified or middle click follows the link: no open, no prevented default
    for res in out[2:]:
        assert res == [False, []]


def test_fallback_shows_no_age_marker_snapshot_still_says_unknown():
    root = _root("sigmond", [_doc("INDEX.md", age_days=None, last_commit=None),
                             _doc("x.md", age_days=None, last_commit=None)])
    out = _render(_data([root], source="fallback", reason="r"), DEFAULT, "texts(box, 'db-age')")
    assert out == []
    snap = _root("sigmond", [_doc("INDEX.md", indexed=True, age_days=None, last_commit=None)])
    assert _render(_data([snap]), DEFAULT, "texts(box, 'db-age')") == ["age unknown"]


def test_current_page_is_marked():
    out = _run(f"DocsBrowser.render(box, {json.dumps(_data([ROOT]))}, {json.dumps(DEFAULT)},"
               " () => {}, {root: 'sigmond', doc: 'GUIDE.md'});",
               "byClass(box, 'db-current').map(e => e.attrs['data-doc'])")
    assert out == ["GUIDE.md"]


def test_first_entry_follows_the_view():
    out = _run("", f"DocsBrowser.firstEntry({json.dumps(_data([ROOT]))}, {json.dumps(DEFAULT)})")
    assert out == {"root": "sigmond", "doc": "INDEX.md"}


def test_prefs_survive_a_storage_that_throws():
    body = ("globalThis.localStorage = { getItem() { throw new Error('x'); },"
            " setItem() { throw new Error('x'); } };\n"
            "DocsBrowser.savePrefs({showAll: true, groupBy: 'audience'});")
    assert _run(body, "DocsBrowser.loadPrefs()") == DEFAULT


def test_prefs_round_trip_under_their_keys():
    body = ("const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null),"
            " setItem: (k, v) => { store[k] = String(v); } };\n"
            "DocsBrowser.savePrefs({showAll: true, groupBy: 'audience'});")
    out = _run(body, "[DocsBrowser.loadPrefs(), Object.keys(store).sort()]")
    assert out == [{"showAll": True, "groupBy": "audience"}, ["docs.groupBy", "docs.showAll"]]
    # a value the page never wrote falls back to the default
    bad = ("globalThis.localStorage = { getItem: k => 'junk', setItem() {} };")
    assert _run(bad, "DocsBrowser.loadPrefs()") == DEFAULT


# --- Task 4b: landing page, sigmond first, links inside a page -------------

def test_landing_opens_sigmond_index_when_present():
    other = _root("callhash", [_doc("INDEX.md", indexed=True), _doc("spec.md", indexed=True)])
    sig = _root("sigmond", [_doc("GUIDE.md", indexed=True), _doc("INDEX.md", indexed=True)])
    out = _run("", f"DocsBrowser.firstEntry({json.dumps(_data([other, sig]))}, {json.dumps(DEFAULT)})")
    assert out == {"root": "sigmond", "doc": "INDEX.md"}


def test_landing_without_sigmond_opens_the_first_listed_root():
    hidden = _root("checks", [_doc("INDEX.md", indexed=True)], listed=False)
    other = _root("callhash", [_doc("spec.md", indexed=True), _doc("INDEX.md", indexed=True)])
    out = _run("", f"DocsBrowser.firstEntry({json.dumps(_data([hidden, other]))}, {json.dumps(DEFAULT)})")
    assert out == {"root": "callhash", "doc": "INDEX.md"}
    gone = _root("sigmond", [_doc("INDEX.md", indexed=True)], present=False)
    out = _run("", f"DocsBrowser.firstEntry({json.dumps(_data([other, gone]))}, {json.dumps(DEFAULT)})")
    assert out == {"root": "callhash", "doc": "INDEX.md"}


def test_component_view_puts_sigmond_first():
    roots = [_root(n, [_doc("INDEX.md", indexed=True)]) for n in ("callhash", "hf-timestd", "sigmond", "wspr")]
    out = _render(_data(roots), DEFAULT, "texts(box, 'db-group-head')")
    assert out == ["sigmond", "callhash", "hf-timestd", "wspr"]


@pytest.mark.parametrize("href,from_doc,want", [
    ("operator/README.md", "INDEX.md", {"doc": "operator/README.md", "hash": ""}),
    ("../INDEX.md", "operator/x.md", {"doc": "INDEX.md", "hash": ""}),
    ("glossary.md#terms", "operator/a.md", {"doc": "operator/glossary.md", "hash": "terms"}),
    ("./b.md", "operator/a.md", {"doc": "operator/b.md", "hash": ""}),
    ("../../x.md", "a.md", None),
    ("../../hf-timestd/docs/x.md", "operator/a.md", None),
    ("https://example.org/a.md", "INDEX.md", None),
    ("/abs.md", "INDEX.md", None),
    ("//host/a.md", "INDEX.md", None),
    ("mailto:x@y.md", "INDEX.md", None),
    ("#top", "INDEX.md", None),
    ("pic.png", "INDEX.md", None),
    ("operator/", "INDEX.md", None),
    ("?root=sigmond&doc=a.md", "INDEX.md", None),
])
def test_resolve_doc_link(href, from_doc, want):
    out = _run("", f"DocsBrowser.resolveDocLink({json.dumps(href)}, {json.dumps(from_doc)})")
    assert out == want


def test_rewrite_links_sets_href_by_attribute_and_opens_in_place():
    body = r"""
function link(href) { const a = mk('a'); a.attrs.href = href; return a; }
const links = [link('operator/README.md'), link('glossary.md#terms'), link('https://x.org/a.md'),
               link('#top'), link('../../x.md')];
box.querySelectorAll = sel => (sel === 'a[href]' ? links : []);
const got = [];
DocsBrowser.rewriteLinks(box, 'sigmond', 'INDEX.md', (r, d, h) => got.push([r, d, h]));
function click(a, over) { const ev = Object.assign({ button: 0, prevented: false,
  preventDefault() { this.prevented = true; } }, over);
  const before = got.length; a.listeners.click(ev); return [ev.prevented, got.slice(before)]; }
"""
    out = _run(body, "[links.map(a => a.attrs.href), links.map(a => !!(a.listeners && a.listeners.click)),"
                     " click(links[1], {}), click(links[0], {ctrlKey: true}), click(links[0], {button: 1})]")
    hrefs, wired, plain, ctrl, middle = out
    assert hrefs == ["?root=sigmond&doc=operator%2FREADME.md", "?root=sigmond&doc=glossary.md#terms",
                     "https://x.org/a.md", "#top", "../../x.md"]
    assert wired == [True, True, False, False, False]
    assert plain == [True, [["sigmond", "glossary.md", "terms"]]]
    assert ctrl == [False, []] and middle == [False, []]
