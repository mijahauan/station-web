# Station Status, Plan 1 of 4: snapshots, the split, the Station and Check pages

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every sigmond station gets a station-web front page that summarises its clients against the sigmond catalog and the blessed manifest, plus an "Is it working?" page that walks the data path check by check, both fed only by JSON snapshots that sigmond writes.

**Architecture:** sigmond gains a few read-only `--json` outputs and a root timer that runs them every 5 min and writes envelopes to `/run/sigmond/snapshot/`.  station-web splits into a core that depends on nothing scientific (so it installs on every station) and optional science routers.  The core gains a snapshot reader, a `/api/status` router and two pages.  Plans 2–4 follow: the viewer proxy, the command reference, the documentation browser and content.

**Tech Stack:** sigmond: Python 3.11+, stdlib only in core, systemd units, bash `install.sh`, unittest/pytest.  station-web: FastAPI, static HTML + `common.js`, pytest with `TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-27-station-docs-and-status-design.md` (station-web)

Two repositories:
- **S** = `/home/mjh/hamsci/repos/sigmond`.  Tests: `.venv/bin/pytest <paths> -q -p no:cacheprovider --override-ini="addopts="` from S.
- **W** = `/home/mjh/hamsci/repos/station-web`.  Tests: `uv run pytest <paths> -q -p no:cacheprovider --override-ini="addopts="` from W.  Baseline: 875 passed.

## Global Constraints

- Facts about a station come only from sigmond snapshots.  station-web computes no station fact of its own and runs no subprocess (W `tests/test_hygiene.py` already forbids `subprocess`, `urllib.request`, `requests`, `httpx`, `aiohttp`, `hf_timestd`).
- station-web never changes the station.  Its one write: an empty request file asking sigmond to refresh the check snapshot.
- Green when broken is forbidden.  A missing, stale, failed or unreadable snapshot shows as such, never as a pass.  A check marked `--` shows "not on this station".
- sigmond core stays stdlib-only.  New verbs that only read never call `_need_root`.
- Snapshot directory: `/run/sigmond/snapshot/`, mode `0750`, owner `root:sigmond-status`; files `0640`, written atomically (temp file + `os.replace`).
- Request directory: `/run/sigmond/snapshot-request/`, mode `2770`, owner `root:sigmond-status`.
- Snapshot cadence: `OnCalendar=*:0/5`, `RandomizedDelaySec=30`.  A requested refresh runs at most once per 30 s.  A snapshot older than 900 s counts as stale.
- Daily fetch: once a day, as each checkout's owner (sigmond's `_git_fetch_as_owner` already drops to the owner when run as root).
- `smd check --json` output stays ASCII (`json.dumps` default), and its existing `mark`, `label`, `text` fields keep their meaning and wording.
- station-web routers: sync `def` handlers, opaque 500s via `station_web.errors.internal_error`, no import-time config reads, every parameterless GET returns non-500 with no data present (W `tests/test_routes.py`).
- Commit trailer on every commit:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3`

## Rulings made while planning

- **A sigmond-owned read group.**  The spec put the snapshots under "station-web's own group".  sigmond should not depend on a client's group existing, so sigmond creates a system group `sigmond-status`, and station-web's user joins it.  Any future reader joins the same group.
- **Check ids live beside the checks.**  `smd check`'s `Line` stays three fields (dozens of tests compare `Line(...)` values).  A table `CHECK_META` in `check.py`, keyed by the check's label, supplies `id`, `stage` and `client`; the JSON emitter adds them, plus `fix`, split from the text at `"  Fix: "`.
- **Manifest status is its own snapshot.**  `smd admin manifest status --json` reports the adopted manifest and its drift by reusing `heartbeat._read_manifest`.  `_component_statuses` gains only a `fetched_at` field (the mtime of the checkout's `FETCH_HEAD`, or `None`).
- **"Behind main" honesty.**  The Station page shows `behind` from the `--no-fetch` rows beside `fetched_at`.  `fetched_at` of `None` shows "never fetched".  The page never shows a bare zero.
- **Viewer links before the proxy.**  Plan 2 builds the front-port proxy.  Until then the Viewers panel links to `http://<page host>:<port>/`, which works on the LAN.
- **One parity rule for the web.**  `lib/sigmond/snapshot.py`'s `SNAPSHOT_COMMANDS` names every command station-web relies on; a sigmond test checks each one against the live `smd` argparse tree, reusing `tests/test_tui_parity.py`'s walker.
- **Core vs science in station-web.**  Core: `docs`, `grape`, `timing`, and the new `status` router.  Every other router loads only if its imports succeed.  `health` and `station` stay science routers (they reach `hamsci_dsp` through `services/channels.py`).
- **"/" becomes the Station page** (`static/home.html`).  The existing Status page keeps `/static/index.html`.  `static/station.html` stays the retired redirect stub its tests pin.

## Review Focus

1. No snapshot directory at all (sigmond not yet updated, timer never run): every `/api/status/*` GET returns 200 with `available: false` and the command that would produce it; the pages say "no snapshot yet".  (Task 8 test.)
2. A stale snapshot (timer stopped): the API marks it `stale: true`, and the Check page shows the age and never a PASS banner.  (Task 8 test.)
3. One snapshot command fails or prints non-JSON: its envelope records `exit` and `error`, `data` stays `null`, and the other snapshots still get written.  (Task 5 test.)
4. A core-only station (no `hamsci_dsp`, `hamsci_physics`, `numpy`): the app starts, science routes are absent, `/api/status/features` reports `science: false`, and the nav hides the science pages.  (Task 6 test.)
5. A check added later without a `CHECK_META` entry: its JSON line still carries `id = "other." + slug(label)` and `stage = "platform"`, and a sigmond test fails until someone adds the entry.  (Task 1 test.)

---

### Task 1: `smd check --json` gains id, stage, client and fix

**Files:**
- Modify: `S/lib/sigmond/commands/check.py` (new `CHECK_META`, `STAGES`, `json_line`; `cmd_check` JSON branch, lines 171-190)
- Test: `S/tests/test_check_core.py` (update the JSON-shape test at line 91; add new tests)

**Interfaces:**
- Produces: `STAGES = ('platform', 'sensor', 'decode', 'sink', 'upload')`.  `CHECK_META: Dict[str, Tuple[str, str, Optional[str]]]` mapping label → `(id, stage, client)`.  `json_line(ln: Line) -> dict` with keys `mark, label, text, id, stage, client, fix`.
- The JSON document: `{"lines": [json_line(...)], "verdict", "exit"}`.

`CHECK_META` values (exact):

| label | id | stage | client |
|---|---|---|---|
| bring-up | platform.bringup | platform | None |
| storage | platform.storage | platform | None |
| magnetometer | sensor.magnetometer | sensor | mag-recorder |
| PSK | decode.psk | decode | psk-recorder |
| WSPR | decode.wspr | decode | wspr-recorder |
| meteor scatter | decode.meteor | decode | meteor-scatter |
| GRAPE | sink.grape | sink | hamsci-physics |
| uploads | upload.uploads | upload | hs-uploader |
| receiver page | platform.receiver-page | platform | ka9q-web |
| station pages | platform.station-pages | platform | station-web |
| magnetometer page | platform.magnetometer-page | platform | gmag-webui |

- [ ] **Step 1: Write the failing tests** in `test_check_core.py`:
  - `test_json_line_splits_the_fix`: `json_line(Line(FAIL, 'PSK', 'psk-recorder is failed.  Fix: smd start psk-recorder'))` → `text` unchanged, `fix == 'smd start psk-recorder'`, `id == 'decode.psk'`, `stage == 'decode'`, `client == 'psk-recorder'`.
  - `test_json_line_without_fix_has_none`: a line with no `"  Fix: "` → `fix is None`.
  - `test_unknown_label_gets_a_derived_id`: label `'new thing'` → `id == 'other.new-thing'`, `stage == 'platform'`, `client is None`.
  - `test_every_check_has_meta`: every label in `CHECKS` (first tuple element) appears in `CHECK_META`; every stage in `STAGES`; ids unique.
  - Update the existing shape test (line 91) to expect the line `{'mark': 'wait', 'label': 'GRAPE', 'text': 'soon', 'id': 'sink.grape', 'stage': 'sink', 'client': 'hamsci-physics', 'fix': None}`.
- [ ] **Step 2: Run, expect FAIL.**  `.venv/bin/pytest tests/test_check_core.py -q -p no:cacheprovider --override-ini="addopts="`
- [ ] **Step 3: Implement** `CHECK_META`, `STAGES`, `json_line`, and use `json_line` in `cmd_check`'s JSON branch.  `slug`: lowercase, runs of non-alphanumerics → `-`, stripped.  The terminal output stays byte-identical.
- [ ] **Step 4: Run** `tests/test_check_*.py`; expect PASS.
- [ ] **Step 5: Commit** (S) `check: stable ids, stages, clients and a fix field in --json`

---

### Task 2: `smd admin instance list --json` with enabled and active

**Files:**
- Create: `S/lib/sigmond/instance_report.py`
- Modify: `S/bin/smd` (`p_i_list` parser ~25888: add `--json`; `cmd_instance_list` ~23250: JSON branch)
- Test: `S/tests/test_instance_report.py`

**Interfaces:**
- Consumes: `sigmond.instance.list_instances(catalog_clients=None) -> list[Instance]`; `Instance.paths.unit_name`, `.client`, `.reporter_id`, `.has_config`, `.has_env`, `.has_sources`; `instance.display_reporter_id(rid)`.
- Produces: `instance_rows(instances: list, run: Callable = subprocess.run) -> list[dict]`, each `{client, reporter_id, display_id, unit, has_config, has_env, has_sources, enabled, active}`, where `enabled` holds `systemctl is-enabled <unit>`'s stdout word (e.g. `enabled`, `disabled`, `not-found`) and `active` holds `systemctl is-active`'s.  A failed `systemctl` call yields `"unknown"`, never `"active"`.
- `smd admin instance list --json [client]` prints `{"instances": [...]}`.

- [ ] **Step 1: Write the failing tests:** `test_rows_carry_unit_state` (fake `run` returning `enabled`/`active` per unit); `test_systemctl_failure_reads_unknown` (fake `run` raising `OSError` → both fields `"unknown"`); `test_cli_json` (load `bin/smd` the way `tests/test_smd_instance_display.py` does, patch `sigmond.instance.list_instances` and `subprocess.run`, run `cmd_instance_list(Namespace(client=None, json=True))`, parse stdout, assert the `instances` list).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**  The text table stays as it is when `--json` is absent.
- [ ] **Step 4: Run** the new file plus `tests/test_instance.py tests/test_smd_instance_display.py`; expect PASS.
- [ ] **Step 5: Commit** (S) `instance list: --json with enabled and active`

---

### Task 3: `smd admin viewers --json`

**Files:**
- Create: `S/lib/sigmond/viewers.py`
- Modify: `S/bin/smd` (new `admin viewers` parser and dispatch; a thin `cmd_admin_viewers(args)` that passes `_ka9q_web_targets()` in)
- Test: `S/tests/test_viewers.py`

**Interfaces:**
- Consumes: `_ka9q_web_targets() -> [(unit, status_dns, port)]` (S/bin/smd:11100); `sigmond.gmag_webui.SERVICE`, `.PORT`, `.COMPONENT`, `.CHECKOUT`.
- Produces: `list_viewers(ka9q_targets: list, gmag_installed: bool, is_active: Callable[[str], str]) -> list[dict]`, each `{name, label, port, unit, active, description}`:
  - ka9q-web, one per target: `name = "ka9q-web"` for the first, `"ka9q-web-<i>"` (i from 2) after; `label = "Spectrum and waterfall (<status_dns>)"`.
  - gmag-webui when installed: `label = "Magnetometer"`.
  - `active` holds `systemctl is-active <unit>`'s word, `"unknown"` on failure.
- `smd admin viewers --json` prints `{"viewers": [...], "conflicts": [...]}`, where `conflicts` lists any port claimed by two viewers (today: a second radiod's ka9q-web and gmag-webui both on 8082) as `{port, names}`.

- [ ] **Step 1: Write the failing tests:** one radiod and gmag installed → two viewers; two radiods and gmag → `conflicts == [{'port': 8082, 'names': ['ka9q-web-2', 'gmag-webui']}]`; no targets and no gmag → empty list; `is_active` raising → `"unknown"`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** (S) `admin viewers: list the station's web viewers and port conflicts`

---

### Task 4: Manifest status and `fetched_at`

**Files:**
- Create: `S/lib/sigmond/manifest_status.py`
- Modify: `S/bin/smd` (`admin manifest` gains `status [--json]`; `_component_statuses` ~15076 adds `fetched_at` to every installed row)
- Test: `S/tests/test_manifest_status.py`, and extend the component-list tests that pin row keys (find with `grep -rn "behind_exact" tests`)

**Interfaces:**
- Consumes: `sigmond.heartbeat._read_manifest(paths) -> {present, blessed_source, drift}`; `sigmond.align.recorded_release(path)`; `sigmond.provenance.IMAGE_VERSION_FILE`.
- Produces: `manifest_status(paths=None) -> dict` = `{"present": bool, "release": str|None, "source": str|None, "components": {name: status}}`, where `status` is one of `match`, `moved`, `superset`, `live_only`, `manifest_only`.  A live component absent from `drift` reads `match`.  With no usable manifest: `present: false` and `components: {}`.
- `smd admin manifest status --json` prints that dict.
- Every installed row of `_component_statuses` gains `fetched_at`: ISO-8601 UTC of `<repo>/.git/FETCH_HEAD`'s mtime, or `None` when the file is missing.

- [ ] **Step 1: Write the failing tests:** a manifest matching all live components → every status `match`; one moved → `moved`; manifest file missing → `present: false`; `fetched_at` present and ISO when `FETCH_HEAD` exists, `None` when it doesn't (temp git dirs).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests and the component-list tests; expect PASS.
- [ ] **Step 5: Commit** (S) `manifest status --json; component rows carry fetched_at`

---

### Task 5: The snapshot writer and its units

**Files:**
- Create: `S/lib/sigmond/snapshot.py`
- Create: `S/systemd/sigmond-snapshot.service`, `sigmond-snapshot.timer`, `sigmond-snapshot-request.path`, `sigmond-snapshot-request.service`, `sigmond-component-fetch.service`, `sigmond-component-fetch.timer`, `sigmond-snapshot.conf` (tmpfiles)
- Modify: `S/bin/smd` (`admin snapshot write [--only NAME] [--request]`), `S/install.sh` (group, `.path` install, tmpfiles, enables)
- Test: `S/tests/test_snapshot.py`, `S/tests/test_snapshot_units.py`

**Interfaces:**
- Produces:
  - `SNAPSHOT_COMMANDS: Dict[str, List[str]]`, exactly: `check: ['check','--json']`, `components: ['component','list','--json','--no-fetch']`, `catalog: ['component','list','--catalog','--json']`, `instances: ['admin','instance','list','--json']`, `validate: ['admin','validate','--json']`, `viewers: ['admin','viewers','--json']`, `manifest: ['admin','manifest','status','--json']`, `heartbeat: ['admin','heartbeat','show','--json']` (exits 1 with no ticks; that envelope then carries `data: null` and the error, which the page reads as "heartbeat not running here").
  - `write_snapshots(names: Iterable[str], out_dir: Path, run: Callable = subprocess.run, now: Callable = time.time, smd: str = '/usr/local/bin/smd') -> Dict[str, dict]`.  For each name it writes `<out_dir>/<name>.json`, the envelope `{"name", "command": "smd " + " ".join(args), "ran_at": ISO-8601 UTC, "exit": int, "data": <parsed stdout>|null, "error": str|null}`, mode `0640`, atomically.  Non-JSON stdout → `data: null`, `error: "output was not JSON"`.  A nonzero exit with valid JSON keeps the data (`smd check` exits 1 or 2 by design).  A command that times out (60 s) → `exit: -1`, `error: "timed out"`.  Each name runs independently.
  - `take_requests(req_dir: Path, last_run: float, now: float, min_interval_s: float = 30.0) -> bool`: deletes every file in `req_dir` and returns whether a refresh should run (`now - last_run >= min_interval_s`).  It keeps `last_run` in `<out_dir>/.last-request`.
  - `smd admin snapshot write` writes every name; `--only check` writes one; `--request` consumes requests first and writes `check` only if `take_requests` says so.

Unit contents:
- `sigmond-snapshot.service`: `Type=oneshot`, `User=root`, `Nice=19`, `IOWeight=10`, `ExecStart=/usr/local/bin/smd admin snapshot write`, `SyslogIdentifier=sigmond-snapshot`.
- `sigmond-snapshot.timer`: `OnCalendar=*:0/5`, `RandomizedDelaySec=30`, `Persistent=false`, `WantedBy=timers.target`.
- `sigmond-snapshot-request.path`: `PathExistsGlob=/run/sigmond/snapshot-request/*`, `Unit=sigmond-snapshot-request.service`.
- `sigmond-snapshot-request.service`: as the snapshot service, `ExecStart=/usr/local/bin/smd admin snapshot write --request`.
- `sigmond-component-fetch.service`: `Type=oneshot`, `User=root`, `Nice=19`, `ExecStart=/usr/local/bin/smd component list --json`, `StandardOutput=null`.
- `sigmond-component-fetch.timer`: `OnCalendar=daily`, `RandomizedDelaySec=3600`, `Persistent=true`.
- `sigmond-snapshot.conf`: `d /run/sigmond/snapshot 0750 root sigmond-status -` and `d /run/sigmond/snapshot-request 2770 root sigmond-status -`.

`install.sh`: create the system group `sigmond-status` if absent (`groupadd --system`); install `systemd/sigmond-*.path` alongside the existing `.service`/`.timer` loop (~line 822); install the tmpfiles conf and run `systemd-tmpfiles --create` for it (pattern at ~838); `systemctl enable --now` the snapshot timer, the request path and the fetch timer (pattern at ~1185).

- [ ] **Step 1: Write the failing tests** (`test_snapshot.py`):
  - `test_each_snapshot_gets_an_envelope` (fake `run` returning JSON per command; eight files, each envelope's keys and `data`).
  - `test_bad_output_is_recorded_and_others_still_written` (one command prints `"oops"`; its envelope has `data: None` and the error; the other seven written).
  - `test_check_exit_two_keeps_its_data`.
  - `test_timeout_is_recorded`.
  - `test_files_are_0640_and_written_atomically` (no temp file left).
  - `test_requests_are_consumed_and_rate_limited` (two requests 10 s apart → one refresh; a third 40 s later → another).
  - `test_every_snapshot_command_exists_in_smd`: walk the live `smd` argparse tree with the helper in `tests/test_tui_parity.py` (`_live_verb_paths`, or factor it into a shared helper) and assert every `SNAPSHOT_COMMANDS` path exists.
- [ ] **Step 2: Write the failing unit tests** (`test_snapshot_units.py`): each unit file exists with the exact keys above; `install.sh` installs `.path` units, the tmpfiles conf, the `sigmond-status` group, and enables the three units.
- [ ] **Step 3: Run, expect FAIL.**
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** both files plus `tests/test_tui_parity.py`; expect PASS.  Then run the whole S suite once and record the count.
- [ ] **Step 6: Commit** (S) `snapshot: a root timer writes smd's JSON for station-web to read`

---

### Task 6: Split station-web into a core and optional science routers

**Files:**
- Modify: `W/src/station_web/app.py` (`_register_routers`), `W/pyproject.toml`, `W/deploy.toml`, `W/systemd/station-web@.service`, `W/scripts/setup-station.sh`, `W/src/station_web/contract.py`
- Create: `W/scripts/build-venv.sh`, `W/src/station_web/routers/features.py`
- Test: `W/tests/test_core_split.py`; update `W/tests/test_deploy_contract.py`, `W/tests/test_routes.py` as needed

**Interfaces:**
- Produces: `CORE_ROUTERS = ("docs", "grape", "timing", "status", "features")` and `SCIENCE_ROUTERS = ("station", "stations", "health", "metrology", "stability", "propagation", "space_weather", "correlations", "physics", "tec", "tid", "dashboard", "phase", "ionogram", "chrony")` in `app.py`.  `_register_routers(app) -> list[str]` returns the science routers it skipped.  A science router whose import raises `ImportError` gets skipped with one WARNING naming it; a core router import failure stays fatal.  (Task 8 creates `status`; until then the list may name it and the loader skips a missing core module only for `status` — or add a placeholder module in this task.  Prefer the placeholder: `routers/status.py` with an empty `APIRouter(prefix="/status")`.)
- Produces: `GET /api/features` → `{"science": bool, "skipped": [names]}` (`routers/features.py`).
- `pyproject.toml`: core `dependencies` = fastapi, uvicorn[standard], pydantic, systemd-python; extra `science` = numpy, scipy, hamsci-dsp, hamsci-physics.
- `scripts/build-venv.sh`: creates the venv, installs `-e .`, and installs the science siblings and `-e .[science]` only when `/opt/git/sigmond/hamsci-dsp` and `/opt/git/sigmond/hamsci-physics` both exist.  `deploy.toml [build]` calls it.  `[[deps.git]]` keeps only what a core install needs (none of the science siblings).
- Unit: `Group=stationweb` (no `Group=timestd`); `ReadOnlyPaths=-/var/lib/timestd /opt/git/sigmond`; `ReadWritePaths=-/var/lib/timestd/phase2 -/run/sigmond/snapshot-request`.  Supplementary groups come from the user's own memberships (systemd applies them for `User=`).
- `setup-station.sh`: create group `stationweb` if absent; create user `stationweb` with primary group `stationweb`; add it to `timestd` when that group exists and to `sigmond-status` when that group exists.
- `contract.py validate`: a missing `hamsci_dsp`/`hamsci_physics` reports `info` ("science pages not installed"), not a failure.

- [ ] **Step 1: Write the failing tests** (`test_core_split.py`): with `sys.modules['hamsci_dsp'] = None`, `sys.modules['hamsci_physics'] = None` and `sys.modules['numpy'] = None` (monkeypatched, and the science router modules evicted from `sys.modules`), `create_app` succeeds; `/api/living-docs/list` answers; `/api/features` says `science: false` and lists the skipped routers; no `/api/stations/` route.  With everything importable, `/api/features` says `science: true`, `skipped: []`.  `deploy.toml`'s build runs `scripts/build-venv.sh`; the unit has no `Group=timestd` and uses the `-` prefixes.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole W suite**; expect 875 + new, 0 failures.
- [ ] **Step 5: Commit** (W) `split: a core that installs on every station, science routers optional`

---

### Task 7: sigmond's catalog installs station-web everywhere

**Files:**
- Modify: `S/etc/catalog.toml` (`[client.station-web]` ~96-115; profiles `base` ~355 and `client` ~360)
- Test: `S/tests/test_catalog.py` (98-111 and the profile tests), `S/tests/test_bringup.py` (302-312) as needed

**Interfaces:**
- `[client.station-web]`: `requires = []`; `description = "Station status, documentation and data reports; timing and science pages where hf-timestd runs"`.  Kind, contract and start_priority unchanged.
- Profiles: `station-web` appears last in `dasi2`, `base` and `client`.

- [ ] **Step 1: Update the tests first** to the new `requires` and profile membership; run, expect FAIL.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run** `tests/test_catalog.py tests/test_bringup.py`, then the whole S suite; expect PASS.
- [ ] **Step 4: Commit** (S) `catalog: station-web on every station, no science requires`

---

### Task 8: Snapshot reader and the `/api/status` router

**Files:**
- Create: `W/src/station_web/services/snapshot.py`, `W/src/station_web/services/station_summary.py`
- Modify: `W/src/station_web/routers/status.py` (replace the Task 6 placeholder), `W/src/station_web/config.py` (`snapshot_dir`, `snapshot_request_dir`), `W/config/station-web.toml.template` (`[status]` section, commented defaults)
- Test: `W/tests/test_status_router.py`, `W/tests/test_station_summary.py`

**Interfaces:**
- Settings: `snapshot_dir` (default `/run/sigmond/snapshot`), `snapshot_request_dir` (default `/run/sigmond/snapshot-request`), from `[status]`.
- `read_snapshot(name: str, settings, now: float | None = None) -> dict` → `{"available": bool, "name", "command", "ran_at", "age_s", "stale": bool, "exit", "data", "error"}`.  Missing or unreadable file → `available: false`, `command` from a fixed table matching sigmond's `SNAPSHOT_COMMANDS`, `error` saying why.  `stale` when `age_s > 900`.
- `build_rows(catalog, components, instances, manifest, check) -> list[dict]`, one row per catalog entry (installed components missing from the catalog appended), each `{name, description, kind, installed, local, manifest_status, behind, fetched_at, instances: {configured, enabled, active}, check: {id, mark}|None}`.  `manifest_status` is the manifest snapshot's per-component status, `"no_manifest"` when it is not present, `None` for an uninstalled client.  `behind` is `None` whenever `fetched_at` is `None`.  `instances` counts rows with `has_config`, `enabled == "enabled"`, `active == "active"`.  `check` takes the worst mark among check lines whose `client` names this row.
- Routes (prefix `/status`):
  - `GET /status/station` → `{"identity": {...from components' sigmond row and settings.station}, "rows": [...], "viewers": viewers snapshot data, "heartbeat": {"verdict", "reason", "emitted_at"} from the heartbeat envelope's `rollup` or `null`, "snapshots": {name: {available, ran_at, age_s, stale, error}}}`.
  - `GET /status/check` → `{"verdict", "stale", "ran_at", "age_s", "stages": [{"stage", "lines": [...]}], "validate": [...failures then warnings], "available"}`.  Stages in sigmond's order (`platform, sensor, decode, sink, upload`); empty stages omitted.  `verdict` becomes `"STALE"` when the check snapshot is stale and `"NONE"` when unavailable.
  - `POST /status/check-now` → writes an empty file named `check-<ns timestamp>` in `snapshot_request_dir`; `202 {"requested": true}`; `503` with fixed text when the directory is missing or not writable.

- [ ] **Step 1: Write the failing tests:**
  - `test_no_snapshot_dir_is_not_an_error` (Review Focus 1): both GETs return 200, `available: false`, each named snapshot reports its command.
  - `test_stale_check_never_passes` (Review Focus 2): a PASS snapshot 20 min old → `verdict == "STALE"`.
  - `test_failed_snapshot_shows_error`.
  - `test_rows_from_fixture_snapshots`: fixture JSON for all eight snapshots (write them under `tests/fixtures/snapshot/`, copied from a real `smd` run's shapes as defined in Tasks 1–5) → psk-recorder row installed, `manifest_status: "match"`, `instances: {configured: 1, enabled: 1, active: 1}`, `check: {"id": "decode.psk", "mark": "ok"}`; an uninstalled catalog client → `installed: false`, `manifest_status: None`.
  - `test_never_fetched_shows_no_count`: `fetched_at: None` → `behind: None`.
  - `test_check_now_writes_one_request` and `test_check_now_without_dir_is_503`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**, following `services/timing_authority.py`'s file-reading pattern.
- [ ] **Step 4: Run the whole W suite**; expect PASS (including `test_parameterless_gets_never_500_on_empty_root`).
- [ ] **Step 5: Commit** (W) `status: read sigmond's snapshots; station and check endpoints`

---

### Task 9: The Station and Check pages

**Files:**
- Create: `W/src/station_web/static/home.html`, `W/src/station_web/static/check.html`, `W/src/station_web/static/js/status.js`
- Modify: `W/src/station_web/app.py` (`GET /` → `home.html`; `GET /check` → `check.html`), `W/src/station_web/static/js/common.js` (`NAV_ITEMS`)
- Test: `W/tests/test_status_pages.py`; existing `tests/test_pages_static.py` and `tests/test_common_js.py` must keep passing

**Interfaces:**
- Consumes: `/api/status/station`, `/api/status/check`, `POST /api/status/check-now`, `/api/features`.
- Nav: core items `Station` (`/`), `Check` (`/check`), then the science items (`Status` → `/static/index.html`, `Timing`, `Propagation`, `Ionosphere`, `Test signal`, `GRAPE`) shown only when `/api/features` reports `science: true`.  `renderSiteNav()` renders the core items at once, then adds the science items after the features call.  The `Docs` footer link stays.
- **Station page:** identity line; the Viewers panel (each active viewer links to `http://${location.hostname}:${port}/`; an inactive one shows its state without a link; port conflicts show as a warning line); the client table with columns name + description, installed, version (`local`), manifest (`match` green, other statuses amber, `no_manifest` grey), behind main ("N behind, fetched <age> ago", or "never fetched"), instances ("c configured · e enabled · a running"), check mark linking to `/check#<id>`.  Uninstalled clients greyed; a toggle "installed only" defaults on below 700 px width.  Below the table: the heartbeat's last verdict, reason and time when it runs here, else "heartbeat not running on this station".  Footer: each snapshot's age; any unavailable snapshot named with its command.
- **Check page:** a header with the verdict (PASS / WAIT / FAIL / STALE / no snapshot), the age, and a **Check now** button (POST, then poll `/api/status/check` every 5 s for up to 2 min until `ran_at` changes).  One section per stage; each line shows mark, label, text without the fix, the fix in a code block, and an anchor `id="<check id>"` so `/check#decode.psk` scrolls to it.  `--` lines read "not on this station".  A second section lists validate failures then warnings.  A link to the Status page (`/static/index.html`) when science is installed.
- Both pages load `common.js` once, use no `toLocale*`, mention no CHU (the static-page tests enforce these).

- [ ] **Step 1: Write the failing tests** (`test_status_pages.py`): `GET /` serves `home.html`; `GET /check` serves `check.html`; both include `data-site-nav` and `status.js`; `check.html` contains the Check now button; `common.js` keeps `Station` and `Check` as the first two nav items and gates the science items on `/api/features`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole W suite**; expect PASS.
- [ ] **Step 5: Check by eye:** run `uv run station-web serve` against the fixture snapshot directory (`STATION_WEB_CONFIG` pointing at a temp config with `[status] snapshot_dir`), open `/` and `/check` at desktop and phone widths, and record what you saw in the report.
- [ ] **Step 6: Commit** (W) `pages: Station front page and Is-it-working check page`

---

### Task 10: Check explanations

**Files:**
- Create: `S/docs/operator/checks/<id>.md` for each of the eleven ids in Task 1, and `S/docs/operator/checks/README.md` (index)
- Modify: `W/src/station_web/config.py` (`DEFAULT_DOCS_ROOTS` gains `{"name": "checks", "path": "/opt/git/sigmond/sigmond/docs/operator/checks"}`), `W/src/station_web/static/check.html` (each line links "What this means" to `/static/docs.html?root=checks&doc=<id>.md`)
- Test: `S/tests/test_check_docs.py`; W `tests/test_docs_roots.py`

**Content of each check page** (sigmond docs conventions: `> **Audience:** operator` header): what the check looks at; what healthy looks like (quote the OK text the check prints); each WAIT and FAIL text it can print, with the meaning and what to do; how to look closer (the `smd watch` or TUI screen).  Derive the texts from the check functions in `check.py`.  Plain, short sentences.

- [ ] **Step 1: Write the failing test** (`test_check_docs.py`): every id in `CHECK_META` has `docs/operator/checks/<id>.md` with the Audience header.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Write the pages and the W changes.**
- [ ] **Step 4: Run** both repos' affected tests; expect PASS.
- [ ] **Step 5: Commit** (S) `docs: an explanation page for every smd check` and (W) `check page: link each line to its explanation`

---

## After the build (not tasks for the implementer)

- Announce on the claude-bus before any B4 install; Rob owns the heartbeat and agrees the remote-access change in Plan 2.
- Deploy order on a station: sigmond first (snapshots start), then station-web.
- Plans 2–4: the front-port proxy and remote access; the command reference and search; the documentation browser and content.
