# Station Status Plan 3 — the Command Reference — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every station serves a searchable reference of `smd` and each installed client's commands, generated from the code on that station, with hand-written examples beside each entry and the commands that lack one listed.

**Architecture:** A standalone, stdlib-only walker runs inside each program's own venv and prints the program's argparse tree as JSON. `smd admin cli-reference build` finds the programs (smd itself, plus every venv command an installed client's `deploy.toml` links into `/usr/local/bin`), runs the walker for each, and stores one file per program under `/var/lib/sigmond/cli-reference/`. Install, update and align run the build. The snapshot timer publishes the stored files as the `cli-reference` snapshot. station-web reads that snapshot, joins each command with the example sections in the owning repo's `docs/commands/<program>.md`, and serves a Commands page with one search box that filters commands and document headings in the browser.

**Tech Stack:** Python 3.11 stdlib (sigmond core, the walker), FastAPI + static HTML/JS (station-web), pytest.

**Spec:** `station-web/docs/superpowers/specs/2026-10-27-station-docs-and-status-design.md` — §6 "The Commands page", Order of work steps 5 and 6.  Also binding: `sigmond/docs/superpowers/specs/2026-10-29-station-layering-design.md` §3 (dependencies point down; sigmond reads what clients publish, with no special case per client name).

## Global Constraints

- sigmond's core stays stdlib only.  The walker (`lib/sigmond/cli_walk.py`) imports nothing outside the standard library and nothing from `sigmond`, because it runs by file path inside client venvs that do not hold sigmond.
- sigmond finds a client's programs only from that client's own declarations: the `[[install.steps]]` entries of `kind = "link"` in its `deploy.toml` whose `src` lies in a `venv/bin/` directory.  No list of client names, no per-client code.
- The walker never executes a command: it stops each program at its first `parse_args`/`parse_known_args` call.  As root, sigmond runs it as user `nobody` with `PYTHONDONTWRITEBYTECODE=1`, `cwd=/`, a 30 s timeout, and no inherited environment beyond `PATH` and `LANG`.
- A failed walk never fails install, update or align: it records the error in that program's file and prints one warning line.
- Reference files: `/var/lib/sigmond/cli-reference/<program>.json`, mode 0644, written atomically (temp file in the same directory, then `os.replace`).
- station-web stays unprivileged, runs no subprocess, and reads sigmond's facts only through `/run/sigmond/snapshot/` (the existing snapshot mechanism and group).
- Search runs in the browser over an index the server builds; no search engine (spec §6).
- Examples come only from people: `docs/commands/<program>.md`, one `## <command path>` section per command.  The page shows the commands without one.
- The sigmond suite never elevates (never set `SIGMOND_TEST_ALLOW_SUDO`).  Tests write under `TMPDIR=/home/mjh/t2tmp`.  Develop on main; push only with Michael's go; tell Rob on the claude-bus before pushing.  Deploys to B4 go through a staged `~/hf-deploy-*.sh` that Michael runs, announced on the bus first.
- Commit trailers: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3`.
- Known pre-existing failure: sigmond `tests/test_wizard_wifi.py::WiringTests::test_review_screen_lists_and_can_re_edit_it`.

## Review Focus

1. A client whose `main()` does work before parsing (opens a config, starts logging, touches a device): the walk must stop at parsing or time out, and the build must record the failure rather than hang or act.  Pinned by Task 1's `test_side_effect_before_parse_is_bounded` and Task 2's timeout test.
2. A program that does not use argparse, or whose entry point fails to import: its file records `error` and the page shows it as "reference unavailable", not as an empty command list.  Pinned by Task 1's `test_non_argparse_program_reports_error` and Task 4's view test.
3. A client removed since the last build: its stale file must go, so the page never lists a program the station no longer has.  Pinned by Task 2's `test_build_removes_files_of_vanished_programs`.
4. An examples file whose `##` headings do not match any command path (typo, renamed command): the page must say so rather than silently drop the example.  Pinned by Task 4's `test_unmatched_example_sections_are_reported`.
5. A reference built before the client moved: the page must mark it stale when the program's installed version differs from the recorded one.  Pinned by Task 2's `test_show_marks_stale_versions`.

---

### Task 1: The walker (sigmond)

**Files:**
- Create: `sigmond/lib/sigmond/cli_walk.py`
- Test: `sigmond/tests/test_cli_walk.py`

**Interfaces:**
- Produces: running `python -I cli_walk.py --entry MODULE:FUNC --program NAME [--dist DIST]` or `python -I cli_walk.py --script NAME` (looks the console script up with `importlib.metadata.entry_points(group="console_scripts")`) prints one JSON object to stdout and exits 0, even when the walk failed.
- Produces: `walk_parser(parser: argparse.ArgumentParser, program: str) -> list[dict]` and `capture_parser(func: Callable[[], Any]) -> argparse.ArgumentParser`, importable for smd's own in-process walk (Task 2).
- JSON schema 1: `{"schema": 1, "program": str, "version": str|null, "entry": str|null, "commands": [Command], "error": str|null}`.  `Command`: `{"path": "smd admin docs index", "help": str, "description": str, "arguments": [Arg]}`, one entry for the root and one per subcommand at every depth, in parser order.  `Arg`: `{"name": str, "flags": [str], "kind": "positional"|"option"|"flag", "help": str, "choices": [str]|null, "default": str|null, "required": bool, "nargs": str|null, "metavar": str|null}`.  Defaults and choices render with `str()`; `argparse.SUPPRESS` defaults render as null; arguments with `help=argparse.SUPPRESS` are left out; the `-h/--help` action is left out.

`capture_parser` patches `argparse.ArgumentParser.parse_args` and `parse_known_args` for the duration of the call so the first call raises a private exception carrying `self`; it calls `func` with `sys.argv = [program, "--help"]`, restores both methods in a `finally`, and returns the captured parser.  `SystemExit` before any parse, any other exception, or a return without parsing, becomes the `error` text ("no argparse parser reached", or the exception's type and message).

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_walk.py`, using small parsers built in the test module:
  - `test_walks_nested_subcommands_in_order`: root with subparsers `a` (with sub-subparsers `x`, `y`) and `b` → paths `["prog", "prog a", "prog a x", "prog a y", "prog b"]`.
  - `test_records_arguments`: one positional with choices, one `--opt` with default 5 and metavar, one `store_true` flag, one required option → each `Arg` has the stated `kind`, `choices`, `default` (`"5"`), `required`, `metavar`; `-h` absent; a `help=SUPPRESS` argument absent.
  - `test_capture_parser_stops_before_running`: a `main()` that builds a parser, calls `parse_args()`, then sets a module flag → captured parser returned, flag never set.
  - `test_non_argparse_program_reports_error`: run the file as a subprocess (`sys.executable -I cli_walk.py --entry tests.fixtures_cli:no_parser --program p`, with `PYTHONPATH` pointing at a temp dir holding a module whose function returns without parsing) → exit 0, JSON `commands == []`, `error` names "no argparse parser reached".
  - `test_side_effect_before_parse_is_bounded`: a function that raises `OSError` before parsing → `error` holds `OSError` and its message; exit 0.
  - `test_script_lookup_uses_console_scripts`: `--script pytest` in sigmond's own `.venv` → `entry` is `"pytest:console_main"` (or whatever the installed metadata says; assert it equals `importlib.metadata` lookup), `commands[0]["path"] == "pytest"`.
  - `test_walker_imports_nothing_from_sigmond`: parse `lib/sigmond/cli_walk.py` with `ast`; every import is a stdlib module (`sys.stdlib_module_names`).

- [ ] **Step 2: Run them to see them fail.** Run: `cd ~/hamsci/repos/sigmond && TMPDIR=/home/mjh/t2tmp .venv/bin/python -m pytest -q --override-ini addopts= tests/test_cli_walk.py`

- [ ] **Step 3: Implement `cli_walk.py`** to the interface above.  Walk subcommands through each `argparse._SubParsersAction`'s `choices`, keeping the help each `add_parser(help=...)` gave (from `_choices_actions`).  `version` comes from `importlib.metadata.version(dist)` when `--dist` is given or the console-script entry point names a distribution; null otherwise.

- [ ] **Step 4: Run the tests, then the full sigmond suite.**

- [ ] **Step 5: Commit** (`sigmond`): `cli-reference: a stdlib walker for argparse trees`.

### Task 2: `smd admin cli-reference build | show` (sigmond)

**Files:**
- Create: `sigmond/lib/sigmond/cli_reference.py`
- Modify: `sigmond/bin/smd` (the `admin` subparsers beside `docs`; a `cmd_admin_cli_reference` dispatcher)
- Modify: `sigmond/lib/sigmond/snapshot.py` (`SNAPSHOT_COMMANDS["cli-reference"] = ["admin", "cli-reference", "show", "--json"]`)
- Test: `sigmond/tests/test_cli_reference.py`; extend `tests/test_snapshot.py` only if its parser check needs the new name listed

**Interfaces:**
- Consumes: Task 1's `cli_walk.py` (run by path) and `walk_parser`/`capture_parser` (in-process for smd).
- Produces: `discover_programs(base: Path, catalog: dict) -> list[Program]`, where `Program = (name: str, component: str, python: Path | None, script: str)`; smd itself is `("smd", "sigmond", None, "smd")` and is walked in-process from `bin/smd`'s parser builder.
- Produces: `build(out_dir: Path = Path("/var/lib/sigmond/cli-reference"), *, run=subprocess.run, now=time.time) -> list[dict]` (one result per program, each with `program`, `ok`, `error`).
- Produces: `show(out_dir) -> dict`: `{"schema": 1, "programs": [ {...file contents..., "component": str, "built_at": iso, "stale": bool} ]}`.  `stale` is true when the program's installed version (read the same way the walker does, in its venv) differs from the recorded `version`.  `show` runs no walk.
- CLI: `smd admin cli-reference build [--out DIR]` (needs root unless `--out` names a writable dir) and `smd admin cli-reference show [--json] [--out DIR]` (no root needed).

Discovery: for each catalog component installed under `base` (`/opt/git/sigmond`), read its `deploy.toml`; each `[[install.steps]]` with `kind = "link"` and a `src` whose parent directory is named `bin` inside a directory named `venv` or `.venv` gives one program: name = the link's `dst` basename (else `src` basename), python = `<venv>/bin/python`.  Skip a program whose python does not exist.

Running a walk: `[python, "-I", str(CLI_WALK), "--script", name]`, under `runuser -u nobody --` when euid is 0, with the constraints' environment, cwd and timeout.  A timeout or non-JSON output becomes the program's `error`.

Writing: one file per program, atomic, 0644; the directory 0755; files for programs not discovered this run are removed.

- [ ] **Step 1: Write the failing tests** (all with temp dirs and a fake `run`):
  - `test_discovers_programs_from_deploy_links`: a fake base with two components whose `deploy.toml` link venv binaries (one under `venv/bin`, one under `.venv/bin`) plus a link to a non-venv path → exactly the two venv programs, plus smd.
  - `test_build_writes_one_file_per_program`: fake `run` returns walker JSON → files exist, mode 0644, content round-trips.
  - `test_timeout_is_recorded_not_raised`: fake `run` raises `subprocess.TimeoutExpired` → that program's file has `error` mentioning the timeout; build returns normally.
  - `test_build_removes_files_of_vanished_programs`: a pre-existing `ghost.json` in the out dir → removed after build.
  - `test_runs_as_nobody_when_root`: patch `os.geteuid` to 0 → the command starts with `runuser -u nobody --`; with euid 1000 it does not.
  - `test_show_marks_stale_versions`: stored version `1.0`, installed version (patched reader) `1.1` → `stale` true.
  - `test_smd_walks_itself_in_process`: build includes `smd` with commands containing `"smd admin cli-reference build"`.
  - `test_cli_reference_is_a_snapshot_command`: `"cli-reference"` in `SNAPSHOT_COMMANDS` with the `show --json` argv.

- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `cli_reference.py`, the two verbs, and the snapshot entry.
- [ ] **Step 4: Run the new tests and the full sigmond suite.**
- [ ] **Step 5: Commit** (`sigmond`): `cli-reference: build and show, from each client's own deploy links`.

### Task 3: Install, update and align build the reference (sigmond)

**Files:**
- Modify: `sigmond/bin/smd` — the end of a successful `smd install` (catalog walk), of `smd update --apply`, and of `smd align --apply` (after the restart stage)
- Test: `sigmond/tests/test_cli_reference_wiring.py`

**Interfaces:**
- Consumes: `cli_reference.build()` from Task 2.
- Produces: helper `_refresh_cli_reference() -> None` in `bin/smd`: calls `build()`, prints `  cli reference: N programs (M failed)`, and on any exception prints one `_warn` line and returns.  Never changes the caller's exit code.

- [ ] **Step 1: Write the failing tests**: for each of install, update `--apply`, align `--apply`, patch the operation's internals to succeed and patch `cli_reference.build` → called once; patch `build` to raise → the operation's exit code is unchanged and one warning printed; dry runs (no `--apply`) never call it.
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement** the helper and the three calls.
- [ ] **Step 4: Run the new tests and the full sigmond suite.**
- [ ] **Step 5: Commit** (`sigmond`): `cli-reference: install, update and align refresh it`.

### Task 4: The Commands page and search (station-web)

**Files:**
- Create: `station-web/src/station_web/services/cli_reference.py`
- Create: `station-web/src/station_web/routers/commands.py`
- Create: `station-web/src/station_web/static/commands.html`, `station-web/src/station_web/static/js/commands.js`
- Modify: `station-web/src/station_web/app.py` (register the router with the core routers every station serves; `GET /commands` serves the page), `station-web/src/station_web/services/snapshot.py` (the name→command map gains `cli-reference`), the shared navigation in `home.html` and `docs.html` (a "Commands" link)
- Test: `station-web/tests/test_cli_reference_service.py`, `station-web/tests/test_commands_routes.py`; extend `tests/test_pages_static.py` / `tests/test_routes.py` where they enumerate pages and routes

**Interfaces:**
- Consumes: the `cli-reference` snapshot (Task 2's `show --json` inside the snapshot envelope); the docs roots from `services/docs_index.docs_roots(config)`.
- Produces: `reference_view(settings) -> dict`: `{"freshness": {...as other snapshot views...}, "programs": [{"program", "component", "version", "stale", "error", "commands": [{"path", "help", "description", "arguments", "example": str|null}]}], "missing_examples": [path, ...], "unmatched_examples": [{"program", "heading"}]}`.
- Produces: `search_index(settings) -> list[dict]`, each `{"kind": "command"|"heading", "text": str, "context": str, "href": str}`.  Commands link to `/commands#<path with spaces as ->`; headings link into the docs browser (`/static/docs.html#<root>/<doc>/<anchor>`, matching how the browser already addresses sections).
- Routes: `GET /commands` (page), `GET /commands/reference` (JSON), `GET /commands/search-index` (JSON).

Examples: for a program owned by component C, read `docs/commands/<program>.md` from C's docs root (the root named C).  Split at `## ` headings; a heading equal to a command path attaches that section's markdown as the command's `example`.  A heading matching no path goes to `unmatched_examples`.  Every command without an example goes to `missing_examples`.  Cache both the parsed examples and the search index keyed by the snapshot file's mtime and the docs roots' directory signatures; rebuild only when one changes.

The page: one search box at the top filtering, as the user types, over `search-index` (case-insensitive substring on `text` and `context`); below it, programs in order (smd first, then A–Z), each command with its help, a table of arguments, and its example rendered as markdown by the renderer `docs.html` already uses; a "Reference unavailable" note with the error for programs whose walk failed; a "stale" badge where `stale`; a closing section "Commands without an example" listing `missing_examples`, and "Example sections that match no command" listing `unmatched_examples`.  The snapshot's age shows as on the Check page.

- [ ] **Step 1: Write the failing tests** with fixture snapshots and a temp docs root:
  - `test_examples_attach_by_heading`: `## smd check` section attaches to path `smd check`.
  - `test_missing_examples_listed`: commands without a section appear in `missing_examples`.
  - `test_unmatched_example_sections_are_reported`: `## smd chek` → in `unmatched_examples`.
  - `test_failed_walk_shows_error_not_empty`: a program with `error` and no commands → its entry carries `error`; the page's JS test (pattern in `tests/test_docs_js.py` or `test_common_js.py`) shows "Reference unavailable".
  - `test_search_index_holds_commands_and_headings`: entries of both kinds with the hrefs above.
  - `test_absent_snapshot_degrades`: no `cli-reference.json` → 200 with empty programs and freshness saying why, never 500.
  - `test_routes_registered`: `/commands`, `/commands/reference`, `/commands/search-index` answer 200.
- [ ] **Step 2: Run, see them fail.** Run: `cd ~/hamsci/repos/station-web && TMPDIR=/home/mjh/t2tmp uv run pytest tests/test_cli_reference_service.py tests/test_commands_routes.py -q`
- [ ] **Step 3: Implement** the service, router, page, script and navigation links.
- [ ] **Step 4: Run the new tests and the full station-web suite.**
- [ ] **Step 5: Commit** (`station-web`): `commands page: the station's command reference, with examples and search`.

### Task 5: First examples (content)

**Files:**
- Create: `sigmond/docs/commands/smd.md`
- Create: `hf-timestd/docs/commands/hf-timestd.md`

Each section: `## <command path>`, one line on when to use it, the command in a code block, real output in a code block, and two to four lines on what to look for.  Captured read-only from B4 through `ops/bin/fleet-ssh b4` as user hamsci (these commands change nothing; never use sudo with smd).  Commands for `smd.md`: `smd check`, `smd status`, `smd timing`, `smd component list`, `smd align` (dry run), `smd align --verify`, `smd admin viewers`, `smd admin cli-reference show`.  For `hf-timestd.md`: whichever of its read-only status commands the generated reference (Task 2, run locally against hf-timestd's dev venv) shows — at most five, chosen for an operator asking "is timing healthy?".  Prose follows Michael's style rules: short sentences, active voice, little shorthand.

- [ ] **Step 1: Capture the outputs**, trimming long lists with `…` and saying so.
- [ ] **Step 2: Write the two files.**
- [ ] **Step 3: Check every `##` heading matches a path in the generated reference** (run `smd admin cli-reference build --out /home/mjh/t2tmp/cliref` and `show --json --out ...` locally, then compare headings).
- [ ] **Step 4: Commit** in each repo: `docs: first command examples`.

### Task 6: Deploy to B4 (Michael runs it)

**Files:**
- Create: `~/hf-deploy-b4-cliref.sh` (devbox; not in any repo)

Steps the script takes, each printing what it does: `status` (read-only: current heads of sigmond and station-web on B4, `smd admin cli-reference show` exit code); `apply --go`: git fast-forward `/opt/git/sigmond/sigmond` and `/opt/git/sigmond/station-web` to origin/main as their owners (no `smd component update`, no install.sh), restart `sigmond-snapshot.timer` units' service once and `station-web@default`, run `smd admin cli-reference build` as root through the same route earlier B4 scripts used for root steps, then `smd admin snapshot write`, then curl `http://127.0.0.1:8000/commands/reference` and print the program count and failures; append a line to `/etc/sigmond/CLAUDE-COORDINATION.md`.

- [ ] **Step 1: Write the script** following `~/hf-deploy-b4-v383.sh`'s helpers (fleet-ssh `b4`, `need_go`, `coord`).
- [ ] **Step 2: Run `status`** (read-only) and record the output.
- [ ] **Step 3: After pushes (Michael's go) and a bus notice,** hand Michael `! ~/hf-deploy-b4-cliref.sh apply --go` and report what the page shows.
