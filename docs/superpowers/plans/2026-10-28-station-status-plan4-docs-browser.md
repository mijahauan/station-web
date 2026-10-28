# Station status, Plan 4: the documentation browser — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** station-web's documentation browser serves every installed component's docs plus all of sigmond's, shows by default only the pages each repo's `docs/INDEX.md` lists, and gives every page its audience, its status and the date of its last edit, with a marker after 90 days.

**Architecture:** sigmond gains `smd admin docs index --json`. It finds each installed component's `docs/` folder, reads every page's header block and title, reads each `docs/INDEX.md` for the pages it links, and asks git once per repo for each page's last commit. A HEAD-keyed cache spares git when nothing moved. The root snapshot timer writes the result as `docs.json`, as it already does for seven other commands. station-web reads that snapshot. It takes its folder list from it and enriches the listing. It still serves every page from disk under its existing path confinement, and it falls back to today's fixed folders when no snapshot exists. The page gains a "show everything" switch, a by-component or by-audience grouping, and the age marker.

**Tech Stack:** sigmond: Python 3.11 standard library only, git. station-web: FastAPI, vanilla JS (`static/docs.html` and its script), pytest.

**Spec:** `station-web/docs/superpowers/specs/2026-10-27-station-docs-and-status-design.md` §7 "The documentation browser", the governing idea ("Facts about a station come from sigmond … Words come from Markdown in the repositories"), and Constraints.  Michael's requests (2026-10-28): folders from the installed clients plus sigmond's audience folders; each repo's `docs/INDEX.md` decides the default view, archive behind a switch; a last-edit date per page with a 90-day marker.  Conventions the pages already follow: `sigmond/docs/contributor/docs-conventions.md` §1–§5 (INDEX.md per repo, ★ canonical, the header block, `archive/`).

## What the survey found (2026-10-28)

- The browser serves five fixed folders (`station_web.config.DEFAULT_DOCS_ROOTS`): hf-timestd/docs (285 pages, 232 untouched for 60+ days, the oldest from 2025-10-28), hamsci-physics, hamsci-dsp, sigmond/docs/scientist, and sigmond/docs/operator/checks.  It shows every page as an equal.  It shows no age.
- Ten repos keep a `docs/INDEX.md`: sigmond, hf-timestd, hamsci-dsp, hs-uploader, gpsdo-monitor, mag-recorder, meteor-scatter, psk-recorder, wspr-recorder and sigmond-appliance (not installed on stations).  hamsci-physics, ka9q-python, gmag-webui, station-web, codar-sounder, hf-tec and superdarn-sounder keep none.  INDEX.md rows link pages as `[NAME.md](NAME.md)` or `[x](design/X.md)`, mark the canonical page with a ★ after the link, and sometimes link a whole directory (`[design/](design/)`).
- Pages carry, under the H1, `> **Audience:** …`, `> **Status:** draft | current | shipped | historical | pointer`, `> **Verified against:** <repo> <sha> on <date> — <how>`.  Many older pages carry none.
- No station-web page links the root named `sigmond` (grep of `src/` and the docs trees); the Check page links `root=checks`; metrology.html links `root=hf-timestd&doc=design/MEASUREMENT_MODEL.md`.  Those two links must keep working.
- sigmond runs git as root over checkouts owned by `sigmond` with `git -c safe.directory=<repo>` (`bin/smd`, `_git_fetch_as_owner` and its neighbours).  The snapshot table lives in `lib/sigmond/snapshot.py` `SNAPSHOT_COMMANDS`; station-web mirrors the names in `services/snapshot.py` `COMMANDS`.

## Global Constraints

- station-web stays read-only and runs no subprocess.  It never runs git.  Every fact about a page's age, index membership or header reaches it as `docs.json`.
- station-web still serves each page from disk through `routers/docs.py`'s confinement (`_valid_name`, `_confined`, `MAX_DEPTH = 3`, `MAX_LISTED = 500`).  The snapshot may only name folders; it never widens what can be served.
- A folder from the snapshot counts only if its resolved path lies under `/opt/git/sigmond/` (`ALLOWED_DOCS_BASE`).  Anything else gets dropped, with a log line.
- sigmond's core stays standard-library only.  git runs read-only, as `git -c safe.directory=<repo> -C <repo> …`, with a timeout.
- Load: the snapshot service runs every 5 minutes; the docs index reruns git for a repo only when that repo's HEAD moved since the cached run (cache at `/var/lib/sigmond/docs-index-cache.json`).
- Honesty: a page whose age git cannot give reads "age unknown", never a date.  A repo without INDEX.md says so on the page.  A missing or stale snapshot says so, and the browser falls back to `DEFAULT_DOCS_ROOTS`.
- Root names: each installed component's docs folder takes the component's name; `sigmond` becomes the whole of `sigmond/docs/` (it was `docs/scientist` alone); `checks` stays, pointing at `sigmond/docs/operator/checks`, served but not listed (its pages appear under `sigmond`).  `superpowers/` subtrees (plans and specs) and `archive/` subtrees appear only in the "everything" view.
- Indexed means: linked by an explicit `.md` link in that root's `docs/INDEX.md` (anchors and `./` stripped, normalised relative to `docs/`).  A directory link indexes nothing by itself.  `INDEX.md` itself always counts as indexed and sorts first.
- Age marker: 90 days since the page's last commit (`STALE_DAYS = 90`).  The marker text reads `not edited since <YYYY-MM-DD>`.
- JS: `textContent` only, never `innerHTML`; toggles may persist in `localStorage`, wrapped in try/catch.
- Tests: station-web `.venv/bin/pytest -q` (1095 passed at a81c634); sigmond `.venv/bin/pytest -q` (5752 passed, one known unrelated failure in `test_uploader_manifest_proxy`).  Watch each new test fail first.
- Develop on `main` in both repos; one commit per task per repo; never push without Michael's go; never `git commit --amend`.  Every commit ends with the executing session's two attribution lines.
- Before touching AC0G-B4: read and post on the claude-bus, and append to `/etc/sigmond/CLAUDE-COORDINATION.md`.  Never `install.sh`, `smd apply` or `smd component update` on a station for this.
- Prose in docs and messages: short sentences, active voice, few uses of "to be", times in UTC.

## Review Focus

1. **A snapshot that names a path outside `/opt/git/sigmond/`** (or a symlink that resolves outside it): station-web drops that folder and serves nothing from it.  Task 3 pins it.
2. **INDEX.md links in every shape real repos use** — `[X](X.md)`, `[X](./X.md#sec)`, `[X](design/X.md)`, `[d](design/)`, links into another repo (`../../sigmond/...`), and a ★ after the link — must yield exactly the explicit in-repo `.md` pages, ★ marked.  Task 1 pins it.
3. **git cannot answer** (not a repo, shallow clone without the history, git missing, timeout): ages read null, never a fabricated date, and the command still exits 0 with the rest of the index.  Task 1 pins it.
4. **An older sigmond with no `docs` snapshot**, or a stale one: the browser still lists today's fixed folders and says why.  Task 3 pins it.
5. **A repo with no INDEX.md:** its default view shows every page outside `archive/` and `superpowers/`, with the note "no INDEX.md: showing every page".  Task 4 pins it.

---

### Task 1: sigmond — build the docs index (pure library)

**Files:**
- Create: `sigmond/lib/sigmond/docs_index.py`
- Test: `sigmond/tests/test_docs_index.py`

**Interfaces:**
- Produces:
  - `STALE_DAYS = 90`; `SKIP_DIRS = ("archive", "superpowers")`
  - `parse_header(text: str) -> dict` — keys `title` (first `# ` line, else None), `audience`, `status`, `verified` (raw strings after the bold label, else None).  Reads only the first 40 lines.
  - `index_links(index_text: str) -> dict[str, bool]` — page path relative to `docs/` → `True` when the row carries ★ after that link.
  - `git_last_commits(repo: Path, sub: str = "docs", timeout: float = 20.0) -> dict[str, int] | None` — path relative to `sub` → unix time of the last commit touching it; `None` when git cannot answer.  One `git -c safe.directory=<repo> -C <repo> log --format=%x00%ct --name-only --no-renames -- <sub>` pass; the first time seen per path wins.
  - `head_sha(repo: Path) -> str | None`
  - `build_index(components: list[tuple[str, Path]], now: float, cache: dict) -> tuple[dict, dict]` — returns `(index, new_cache)`.  `components` holds (name, checkout path) pairs.  `index = {"generated_at": iso, "stale_days": 90, "roots": [{"name", "path", "has_index": bool, "documents": [{"name", "title", "audience", "status", "last_commit": iso|None, "age_days": int|None, "indexed": bool, "canonical": bool, "section": "archive"|"superpowers"|None}]}]}`.  The `sigmond` root uses `<checkout>/docs` whole; a `checks` root (`sigmond/docs/operator/checks`) is added with `"listed": false`.  Cache maps repo path → `{"head": sha, "commits": {...}}`; a repo whose HEAD equals the cached head reuses its commits.
- Pages: `*.md` under the root, depth ≤ 3 directories, at most 500 per root (match station-web's bounds).

- [ ] **Step 1: Write the failing tests** with real temporary git repos (`git init`, commit files with `GIT_COMMITTER_DATE`/`GIT_AUTHOR_DATE` set):
  - `test_parse_header_reads_the_block` — the docs-conventions example yields audience `operator`, status `current`; a page without a block yields all None but its title.
  - `test_index_links_every_shape` — the five shapes in Review Focus 2 give exactly `{"OVERVIEW.md": False, "ARCHITECTURE.md": True, "design/X.md": False}` (anchor and `./` stripped; directory and cross-repo links ignored).
  - `test_git_last_commits_newest_per_path` — two commits touching `a.md` at different dates; the newer one wins; a path renamed by a later commit keeps its own history.
  - `test_git_unavailable_gives_none` — a plain directory (not a repo) → None; `build_index` then gives `last_commit: None, age_days: None` and still lists the pages.
  - `test_build_index_marks_indexed_archive_superpowers` — INDEX.md, an indexed page, an unindexed page, `archive/old.md`, `superpowers/plans/p.md`: flags and `section` as specified; INDEX.md first.
  - `test_cache_skips_git_when_head_unchanged` — second call with the returned cache and an unchanged HEAD makes no git call (inject a counting runner or patch `git_last_commits`).
  - `test_sigmond_root_is_whole_docs_and_checks_unlisted`.
  - `test_age_days_and_stale` — `now` 100 days after the commit gives `age_days == 100`.
- [ ] **Step 2: Run them; watch each fail** — `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_docs_index.py -q`.
- [ ] **Step 3: Implement** `docs_index.py`.
- [ ] **Step 4: Run** the file, then the full sigmond suite once.
- [ ] **Step 5: Commit** (sigmond) — `docs index: pages, headers, INDEX.md membership and last commits per installed component`.

### Task 2: sigmond — `smd admin docs index --json`, and the `docs` snapshot

**Files:**
- Modify: `sigmond/bin/smd` (a `docs index` subcommand under `admin`), `sigmond/lib/sigmond/snapshot.py` (`SNAPSHOT_COMMANDS["docs"] = ["admin", "docs", "index", "--json"]`)
- Test: `sigmond/tests/test_docs_index_cmd.py`, and whatever existing test pins the snapshot table or the TUI/CLI parity (`tests/test_tui_parity.py`, the snapshot tests)

**Interfaces:**
- Consumes: Task 1's `build_index`.
- Produces: `smd admin docs index --json` prints Task 1's `index` dict; exit 0 even when some repos give no git history.  Components: every catalog component whose checkout `/opt/git/sigmond/<name>` holds a `docs/` directory (use the code `smd component list` uses for checkout paths).  Cache: read and write `/var/lib/sigmond/docs-index-cache.json` atomically (temp + rename), only when running as root; a non-root run computes without the cache and writes nothing.
- The snapshot service already runs every entry in `SNAPSHOT_COMMANDS`; no unit file changes.

- [ ] **Step 1: Failing tests:** the command's JSON for a fixture tree of two components; `"docs"` present in `SNAPSHOT_COMMANDS` with that argv; the parity test still passes (add the command where the parity map expects it); a non-root run writes no cache file.
- [ ] **Step 2: Run; watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, the snapshot and parity tests, then the full suite once.  Time one run against this devbox's `/home/mjh/hamsci/repos` copy (pass the base by the existing test hook, never by changing the default) and note the seconds in the report; it must stay well under the snapshot's 60 s timeout on a cold cache.
- [ ] **Step 5: Commit** (sigmond) — `smd admin docs index, written as the docs snapshot`.

### Task 3: station-web — folders and metadata from the snapshot

**Files:**
- Create: `station-web/src/station_web/services/docs_index.py`
- Modify: `station-web/src/station_web/services/snapshot.py` (`COMMANDS["docs"] = "smd admin docs index --json"`), `station-web/src/station_web/routers/docs.py` (`_roots()` and `list_docs()`), `station-web/src/station_web/config.py` only if `docs_roots` needs a hook
- Test: `station-web/tests/test_docs_index_service.py`, plus the existing `tests/test_docs_roots.py` and `tests/test_docs_subdirs.py` kept green

**Interfaces:**
- Consumes: the `docs` snapshot (Task 2's dict as `data`), `read_snapshot("docs", settings)`.
- Produces:
  - `ALLOWED_DOCS_BASE = Path("/opt/git/sigmond")` (overridable in tests through settings)
  - `docs_roots(settings) -> tuple[list[dict], dict]` — `(roots, meta)`.  `roots` holds `{"name", "path", "listed"}` from the snapshot when it reads available and fresh, each path resolved and checked under `ALLOWED_DOCS_BASE`; otherwise `config.docs_roots` with `listed: True`.  `meta = {"source": "snapshot"|"fallback", "reason": str|None, "generated_at": str|None}`.
  - `doc_meta(settings) -> dict[(root, name), dict]` — the per-page fields from the snapshot.
  - `GET /api/living-docs/list` returns `{"source", "reason", "stale_days", "roots": [{"name", "path", "present", "listed", "has_index", "documents": [{"name", "title", "audience", "status", "last_commit", "age_days", "indexed", "canonical", "section"}]}]}`.  The disk walk stays authoritative for which documents exist and may be served; snapshot fields join by name, and a page on disk the snapshot lacks gets nulls and `indexed: false`.
  - Serving (`get_doc`, `get_section`) resolves roots through `docs_roots` too, so `root=sigmond&doc=operator/install.md` works, and `root=checks` keeps working.

- [ ] **Step 1: Failing tests:**
  - `test_roots_come_from_snapshot` — two components and sigmond in the snapshot become roots; `checks` present with `listed: False`.
  - `test_root_outside_allowed_base_dropped` — `/etc`, `/opt/git/sigmond/../../etc`, and a symlink under the base pointing out: all dropped; a log line names each.
  - `test_fallback_when_snapshot_missing_or_stale` — `source == "fallback"`, `reason` set, roots equal `DEFAULT_DOCS_ROOTS`.
  - `test_list_joins_metadata_by_name` — fields copied; a disk-only page gets nulls.
  - `test_snapshot_cannot_widen_serving` — a snapshot document name with `..` or depth 4 never serves (still 400/404 via `_valid_name`).
  - `test_existing_links_still_resolve` — `root=checks&doc=platform.bringup.md` and `root=hf-timestd&doc=design/MEASUREMENT_MODEL.md` (fixture files).
- [ ] **Step 2: Run; watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new file, the two existing docs test files, `tests/test_no_exception_text.py`, then the full suite once.
- [ ] **Step 5: Commit** (station-web) — `docs browser: folders and page facts from sigmond's docs snapshot`.

### Task 4: station-web — the browser page: default view, switch, grouping, age

**Files:**
- Modify: `station-web/src/station_web/static/docs.html` and the script it loads (find it from the page; create `static/js/docs.js` only if the page holds its script inline and the change would make it unwieldy)
- Test: the JS-test file pattern of `tests/test_status_js.py` (node, skipping when node is absent), as `tests/test_docs_js.py`; `tests/test_pages_static.py` kept green

**Interfaces:**
- Consumes: Task 3's list response.
- Decisions:
  - Default view per root: `indexed` pages only, INDEX.md first.  A root with `has_index: false` shows every page whose `section` is null, under the note `no INDEX.md: showing every page`.
  - A "Show everything" switch adds the unindexed pages, then the `archive` and `superpowers` sections under their own subheadings.
  - A "Group by" toggle: component (default) or audience.  Audience groups: operator, scientist, contributor, all, then `no audience given`.  Roots with `listed: false` never appear in the list.
  - Each entry shows its title (else its file name); a ★ for `canonical`; its status when present (`historical` and `pointer` muted); and, when `age_days > stale_days`, the marker `not edited since <YYYY-MM-DD>` from `last_commit`.  `age_days == null` shows `age unknown`.
  - A banner when `source == "fallback"`: `Page facts unavailable (<reason>): showing the fixed folders without ages or index.`
  - Deep links (`?root=…&doc=…#…`) keep working and open the page whatever the switch says.
  - Toggles persist per viewer in `localStorage` under `docs.showAll` and `docs.groupBy`, each access in try/catch.
- [ ] **Step 1: Failing JS tests:** default view hides an unindexed page and shows INDEX.md first; the switch shows it and the archive section; audience grouping; the stale marker at 91 days and not at 89; `age unknown` for null; the no-INDEX note; the fallback banner; an unlisted root never renders; titles and names set through `textContent` (a title holding `<img src=x>` renders as text).
- [ ] **Step 2: Run; watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the JS tests and `tests/test_pages_static.py`, then the full suite once.
- [ ] **Step 5: Commit** (station-web) — `docs browser: INDEX.md decides the default view; show everything; group by audience; age marker`.

### Task 5: Docs and the spec

**Files:**
- Modify: `station-web/docs/DEPLOY-STATUS-PAGES.md` — a short section "The documentation browser": where its folders come from, what "indexed" means, the 90-day marker, the fallback, and that a station needs only the sigmond pull (the snapshot service already runs every snapshot command) plus a station-web restart.
- Modify: the spec §7 — mark it built, and record the rulings this plan made (indexed = explicit links only; `sigmond` root widened to all of `docs/`; `checks` unlisted), each marked "decided 2026-10-28 (Plan 4)".
- Modify: `sigmond/docs/contributor/docs-conventions.md` §1 — one paragraph: station-web's browser shows by default only the pages INDEX.md links, so a page missing from INDEX.md drops out of an operator's view.
- [ ] **Step 1:** Write the three edits.
- [ ] **Step 2:** Run the station-web suite once and `cd ../sigmond && .venv/bin/pytest tests/test_tui_parity.py -q`.
- [ ] **Step 3:** Commit in each repo (`docs: the documentation browser's folders, index and ages`).

### Task 6: Deploy to AC0G-B4 and check it

Every step reaches outside the devbox and waits for Michael's go.  The controller stages `~/hf-deploy-docs-browser-b4.sh` (dry run by default, `--go` acts) for Michael to run with `!`.

- [ ] **Step 1:** Michael's go to push both repos (`git log origin/main..main` in each first).
- [ ] **Step 2:** Bus: read, then post the announcement.
- [ ] **Step 3: The script** (`ops/bin/fleet-ssh b4`): fast-forward `/opt/git/sigmond/sigmond` and `/opt/git/sigmond/station-web`; run `sudo -n systemctl start sigmond-snapshot.service` and confirm `/run/sigmond/snapshot/docs.json` exists, reads `exit: 0`, and took under 60 s (journal); `sudo -n systemctl restart station-web@default`; then `curl` `/api/living-docs/list` and print, per root, its `source`, `has_index`, indexed count, total count and stale count; and fetch `/static/docs.html?root=checks&doc=platform.bringup.md` and the metrology link.  Append the coordination-doc line.
- [ ] **Step 4:** Michael opens the Docs page through gw2 and judges it.
- [ ] **Step 5:** Bus note with the numbers; update the `project_station_status_docs_program` memory.
