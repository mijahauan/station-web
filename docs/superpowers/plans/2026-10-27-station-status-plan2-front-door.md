# Station status, Plan 2: one front door for the viewers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** station-web reverse-proxies each viewer the station runs (ka9q-web, gmag-webui) under `/viewer/<name>/`, WebSockets included, so every viewer link on the Station page works on the LAN and through remote access alike.

**Architecture:** A new core router, `viewer`, mounted at `/viewer` (no `/api` prefix), resolves `<name>` to a localhost port through the `viewers` snapshot that Plan 1 already writes, then streams HTTP with httpx and relays WebSockets with the `websockets` client. Each viewer opens its WebSocket at a root-absolute path, so the proxy rewrites exactly one known line in one known asset per viewer kind, and serves the asset unchanged (with a warning) when that line is absent. The Station page links each viewer to its front-door path, with the direct port as a secondary LAN link.

**Tech Stack:** Python 3.11, FastAPI/Starlette, httpx (async, streaming), websockets ≥ 13 (`websockets.asyncio.client`), uvicorn; vanilla JS (`static/js/status.js`); pytest.

**Spec:** `station-web/docs/superpowers/specs/2026-10-27-station-docs-and-status-design.md` — §0 "One front port", §5 "Viewers", "Constraints", "Order of work" step 3.  Plan 1 (`docs/superpowers/plans/2026-10-27-station-status-plan1.md`) is built and deployed on AC0G-B4.

## What the survey found (2026-10-27), and what it changes

- **Remote access already forwards station-web on every appliance station.**  Since the v3.53-era wizard, each appliance host relays four channels to the VM: ssh (22), web (8081), **station (8000)** and gmag (8082), on gw2 bands 48800+N and 49800+N (`sigmond/scripts/proxmox/sigmond-wizard.sh`, `RAC_BASE_VMSTN`; the relay `vm-port-relay.py` copies raw TCP, so WebSockets pass).  The spec's survey line "Remote access forwards one web port today, 8081" no longer holds.  So this plan changes **no** remote-access code.  Stations that run sigmond without the appliance (sigmond-rac, `lib/sigmond/rac_config.py`) forward ssh and at most one admin-assigned `-WEB` port; giving them station-web's port needs a gw2 port assignment from Rob, and Task 6 asks him rather than building it.
- **ka9q-web** (pinned 2c6303f) loads every asset by relative path, but `html/radio.js` opens its WebSocket at the server root: `new WebSocket(... + window.location.host)`.  Under a prefix that reaches station-web's own `/`.
- **gmag-webui** (pinned bc1328b) loads assets by relative path except `/site.webmanifest` (cosmetic), and `js/feed.js` `sameOriginFeed` returns `scheme + loc.host + "/ws"`.  Under a prefix that reaches station-web's `/ws`.
- Neither viewer belongs to us.  ka9q-web lives at wa2n-code/ka9q-web; gmag-webui at HamSCI/gmag_webui, maintained by the Frissell group under its own AI-governance rules.  So the fix that works today sits in the proxy, and the one-line upstream patches go to Michael as offers (Task 6).
- No row on the v3.83 list records the 8082 port conflict the spec told the builder to file (a second radiod's ka9q-web against gmag-webui).  Task 6 files it.

## Global Constraints

- station-web stays read-only, keeps its systemd hardening, and runs no subprocess.  "Proxying a viewer passes that viewer's own traffic through; station-web adds no control of its own." (spec, Constraints)
- The `viewer` router is a **core** router: it imports nothing from hamsci-dsp, hamsci-physics, numpy or scipy, and it appears in `CORE_ROUTERS`.
- Upstream host is always `127.0.0.1`.  The port comes only from the `viewers` snapshot.  A path or query from the browser never chooses a host or port.
- Every error the proxy returns to a browser carries a fixed message, never exception text (`tests/test_no_exception_text.py` must keep passing).
- Facts come from sigmond: the proxy reads `viewers.json` through `services/snapshot.read_snapshot("viewers", settings)`.  It never probes ports to discover viewers.
- Load: the proxy opens one upstream connection per browser request; it caches nothing and polls nothing.
- Tests: `cd /home/mjh/hamsci/repos/station-web && .venv/bin/pytest -q` (baseline 975 passed at 291b831).  Watch each new test fail before writing the code that passes it.
- Develop on `main`.  One commit per task.  Never push without Michael's go.  Every commit message ends with the two attribution lines the executing session's reminder supplies.
- Never run `install.sh`, `smd apply` or `smd component update` on a station for this work.  station-web deploys by git fast-forward plus `systemctl restart station-web@default`.  Before touching AC0G-B4, read and post on the claude-bus and append to `/etc/sigmond/CLAUDE-COORDINATION.md`.
- Prose (docs, bus notes): short sentences, active voice, few uses of "to be", times in UTC.

## Review Focus

1. **A WebSocket from another site.**  A page on evil.example opens `ws://station:8000/viewer/ka9q-web/`: the proxy must refuse it before connecting upstream (ka9q-web creates radiod channels on request).  Task 3 pins it.
2. **A viewer name that two viewers' port shares (8082).**  The proxy must refuse with 409 and name both, never guess.  Task 2 pins it.
3. **The rewrite line missing after a viewer update.**  The asset must pass through byte-identical, with one WARNING per viewer and path, never a 500.  Task 4 pins it.
4. **Upstream down or snapshot missing.**  502 "not answering" and 503 "viewer list unavailable", fixed text; the Station page keeps loading.  Task 2 pins both.
5. **Compressed upstream responses.**  A rewritten asset served gzip by upstream must reach the browser decoded and rewritten, with no stale `content-encoding` or `content-length`.  Task 4 pins it.

---

### Task 1: Resolve a viewer name to a localhost port

**Files:**
- Create: `src/station_web/services/viewer_proxy.py`
- Test: `tests/test_viewer_resolve.py`

**Interfaces:**
- Consumes: `station_web.services.snapshot.read_snapshot(name, settings, now=None) -> dict` (Plan 1).  The `viewers` envelope's `data` is `{"viewers": [ {name, label, port, unit, active, description}, … ], "conflicts": [ {port, names}, … ]}`.
- Produces:
  - `UPSTREAM_HOST = "127.0.0.1"`
  - `@dataclass(frozen=True) class Target: name: str; port: int; kind: str` — `kind` is `"ka9q-web"` for any name equal to `ka9q-web` or starting `ka9q-web-`, `"gmag"` for `gmag-webui`, else `"other"`.
  - `class Refusal(Exception): status: int; message: str` (fixed, user-facing text).
  - `resolve(name: str, settings: Settings) -> Target` — raises `Refusal`.

- [ ] **Step 1: Write the failing tests** (write `viewers.json` envelopes into a tmp `snapshot_dir`, as `tests/test_status_router.py` does)

```python
def test_resolve_known_viewer(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web", "port": 8081, "active": "active"}])
    t = resolve("ka9q-web", tmp_settings)
    assert (t.name, t.port, t.kind) == ("ka9q-web", 8081, "ka9q-web")

def test_resolve_second_ka9q_web_kind(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web-2", "port": 8083, "active": "active"}])
    assert resolve("ka9q-web-2", tmp_settings).kind == "ka9q-web"

def test_resolve_gmag_kind(tmp_settings, write_viewers):
    write_viewers([{"name": "gmag-webui", "port": 8082, "active": "active"}])
    assert resolve("gmag-webui", tmp_settings).kind == "gmag"

def test_unknown_name_is_404(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web", "port": 8081}])
    with pytest.raises(Refusal) as e: resolve("nope", tmp_settings)
    assert e.value.status == 404 and e.value.message == "no such viewer on this station"

def test_shared_port_is_409_naming_both(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web-2", "port": 8082}, {"name": "gmag-webui", "port": 8082}],
                  conflicts=[{"port": 8082, "names": ["ka9q-web-2", "gmag-webui"]}])
    with pytest.raises(Refusal) as e: resolve("gmag-webui", tmp_settings)
    assert e.value.status == 409
    assert e.value.message == "port 8082 is claimed by ka9q-web-2 and gmag-webui"

def test_missing_snapshot_is_503(tmp_settings):
    with pytest.raises(Refusal) as e: resolve("ka9q-web", tmp_settings)
    assert e.value.status == 503 and e.value.message == "viewer list unavailable"

@pytest.mark.parametrize("port", [0, -1, 65536, "8081", None, True])
def test_bad_port_is_502(tmp_settings, write_viewers, port):
    write_viewers([{"name": "ka9q-web", "port": port}])
    with pytest.raises(Refusal) as e: resolve("ka9q-web", tmp_settings)
    assert e.value.status == 502 and e.value.message == "viewer has no usable port"
```

A stale snapshot still resolves (ports rarely move; the proxy's own connect error reports a dead viewer).  `active` does not gate resolution, for the same reason.

- [ ] **Step 2: Run them and watch each fail** — `.venv/bin/pytest tests/test_viewer_resolve.py -q`: ImportError.
- [ ] **Step 3: Implement `resolve`** in `services/viewer_proxy.py`.  `bool` is not a port (`isinstance(port, bool)` first).
- [ ] **Step 4: Run them** — all pass; full suite still 975 + new.
- [ ] **Step 5: Commit** — `viewer proxy: resolve a viewer name to its localhost port from the viewers snapshot`.

### Task 2: The HTTP proxy at `/viewer/<name>/`

**Files:**
- Create: `src/station_web/routers/viewer.py`
- Modify: `src/station_web/services/viewer_proxy.py`, `src/station_web/app.py` (`CORE_ROUTERS`, and mount `viewer` with no `/api` prefix, as `docs` is special-cased), `pyproject.toml` (`httpx>=0.27`, `websockets>=13` in `dependencies`; refresh `uv.lock` if the repo carries one)
- Test: `tests/test_viewer_http.py`, plus the router list in `tests/test_core_split.py`

**Interfaces:**
- Consumes: Task 1's `resolve`, `Target`, `Refusal`.
- Produces:
  - `router = APIRouter(prefix="/viewer")`
  - `GET /viewer/{name}` → 308 to `/viewer/{name}/`, query kept.
  - `GET|HEAD|POST /viewer/{name}/{path:path}` → upstream `http://127.0.0.1:<port>/<path>?<query>`.
  - `viewer_proxy.request_headers(incoming: Mapping[str,str], target: Target, client_host: str, scheme: str) -> dict`
  - `viewer_proxy.response_headers(upstream: Mapping[str,str], prefix: str) -> list[tuple[str,str]]`
  - `viewer_proxy.HOP_BY_HOP = frozenset({"connection","keep-alive","proxy-authenticate","proxy-authorization","te","trailer","transfer-encoding","upgrade"})`
  - `viewer_proxy.MAX_REQUEST_BODY = 1 << 20`

Decisions the implementer cannot make alone:
- Request headers: drop hop-by-hop, `host`, `cookie`, `origin` and `referer`; set `Host: 127.0.0.1:<port>`; add `X-Forwarded-Prefix: /viewer/<name>`, `X-Forwarded-For`, `X-Forwarded-Proto`.  Cookies stay out so a future front-door login (project B) never reaches a viewer.
- Response headers: drop hop-by-hop; rewrite a `Location` that starts with `/` (not `//`) to `/viewer/<name>` + it; drop any `Set-Cookie` whose `Path` would escape the prefix (keep the rest, with `Path=/viewer/<name>/...`).
- Streaming: one `httpx.AsyncClient` per app, created in the app lifespan, `timeout=httpx.Timeout(connect=3.0, read=60.0, write=10.0, pool=3.0)`, `follow_redirects=False`; stream with `aiter_raw()` so upstream `content-encoding` passes through untouched.  Close the upstream response when the client disconnects.
- Errors (fixed text, `text/plain`): connect refused or timed out → 502 `viewer not answering on port <port>`; request body over `MAX_REQUEST_BODY` → 413 `request too large`; `Refusal` → its status and message.
- The GZip middleware must not double-compress: exclude `/viewer/` responses from it (pass upstream encoding through).

- [ ] **Step 1: Write the failing tests.**  Start a real upstream in a fixture: a small Starlette app served by uvicorn on a free port in a background thread (`uvicorn.Server(Config(..., port=0))`, read the bound port), recording each request it saw.  Write a `viewers.json` naming that port.  Tests:
  - `test_redirects_bare_name_to_slash` — `GET /viewer/up?x=1` → 308, `location == "/viewer/up/?x=1"`.
  - `test_proxies_path_and_query` — `GET /viewer/up/a/b.js?q=2` → 200, upstream saw path `/a/b.js`, query `q=2`, body identical.
  - `test_upstream_sees_localhost_host_and_no_cookie` — sent `Cookie: s=1`; upstream saw `host == f"127.0.0.1:{port}"`, no `cookie`, `x-forwarded-prefix == "/viewer/up"`.
  - `test_location_is_rewritten_under_prefix` — upstream answers 302 `Location: /login` → client sees `/viewer/up/login`; `Location: http://elsewhere/` passes unchanged.
  - `test_gzip_passes_through_untouched` — upstream sends gzip bytes with `content-encoding: gzip`; client gets the same bytes and header.
  - `test_upstream_down_is_502_fixed_text` — viewers.json names a closed port → 502, body `viewer not answering on port <p>`.
  - `test_snapshot_missing_is_503` and `test_shared_port_is_409` through the route.
  - `test_body_over_limit_is_413` — POST of `MAX_REQUEST_BODY + 1` bytes.
  - `test_viewer_is_a_core_router` — `"viewer" in CORE_ROUTERS`, and the router imports with `hamsci_dsp`, `hamsci_physics`, `numpy`, `scipy` blocked (reuse `tests/test_core_split.py`'s blocker).
- [ ] **Step 2: Run them; watch each fail for the right reason.**
- [ ] **Step 3: Implement** the router, the two header functions, the lifespan client, the GZip exclusion and the registration.
- [ ] **Step 4: Run** the new file, `tests/test_no_exception_text.py`, `tests/test_core_split.py`, then the whole suite.
- [ ] **Step 5: Commit** — `viewer proxy: serve each viewer under /viewer/<name>/ on station-web's own port`.

### Task 3: WebSockets through the front door, same origin only

**Files:**
- Create: `src/station_web/security.py` — move Plan 1's `_same_origin` out of `routers/status.py` into `same_origin(headers: Mapping[str, str]) -> bool`, unchanged in behaviour; `status.py` calls it.
- Modify: `src/station_web/routers/viewer.py`, `src/station_web/services/viewer_proxy.py`
- Test: `tests/test_viewer_ws.py`; `tests/test_status_router.py` keeps passing untouched.

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `@router.websocket("/{name}/{path:path}")`; `viewer_proxy.WS_MAX_MESSAGE = 16 << 20`; `viewer_proxy.ws_upstream_url(target: Target, path: str, query: str) -> str` (`ws://127.0.0.1:<port>/<path>?<query>`).

Decisions:
- Refuse before any upstream connect when `same_origin(websocket.headers)` is false: close with code 1008 and no accept.  `Refusal` → close 1008 (resolution) or 1011 (connect failure).  Fixed reasons only.
- Subprotocols: pass the browser's `Sec-WebSocket-Protocol` list to `websockets.asyncio.client.connect(subprotocols=…)`; accept the browser with the one upstream chose (or none).
- Relay both directions concurrently; keep text as text and binary as binary; `max_size=WS_MAX_MESSAGE`; when either side closes, close the other with the same code (1000 if none) and cancel the other pump.  `open_timeout=3`, `ping_interval=None` (the viewers run their own keep-alives).
- Send upstream `Host: 127.0.0.1:<port>` and no `Origin` (`origin=None`), so the viewer's own origin check, if any, sees a local client.

- [ ] **Step 1: Write the failing tests** against a fixture upstream built with `websockets.asyncio.server.serve` on a free port (echo; records the request path and subprotocol), using Starlette's `TestClient.websocket_connect`:
  - `test_ws_text_and_binary_echo` — send `"hi"` and `b"\x00\x01"`; receive them back with types kept.
  - `test_ws_path_reaches_upstream` — connect `/viewer/up/ws?a=1`; upstream saw `/ws?a=1`.  Connect `/viewer/up/`; upstream saw `/`.
  - `test_ws_cross_origin_refused_before_upstream` — headers `{"origin": "http://evil.example"}` → connection refused; upstream recorded **no** connection.
  - `test_ws_sec_fetch_cross_site_refused` — `sec-fetch-site: cross-site`, no upstream connection.
  - `test_ws_same_origin_allowed` — `origin` equal to `http://testserver` → echo works.
  - `test_ws_subprotocol_negotiated` — browser offers `["a","b"]`, upstream picks `"b"`; client sees `"b"`.
  - `test_ws_upstream_close_code_propagates` — upstream closes 4001 → client sees 4001.
  - `test_ws_upstream_down_closes_1011`.
- [ ] **Step 2: Run; watch each fail.**
- [ ] **Step 3: Implement** `security.same_origin`, the move in `status.py`, and the websocket route.
- [ ] **Step 4: Run** the new file, `tests/test_status_router.py`, then the whole suite.
- [ ] **Step 5: Commit** — `viewer proxy: relay WebSockets, same-origin only; share the origin check with check-now`.

### Task 4: Teach each viewer's WebSocket the prefix — one pinned rewrite per viewer kind

**Files:**
- Modify: `src/station_web/services/viewer_proxy.py`, `src/station_web/routers/viewer.py`
- Create: `tests/fixtures/viewers/ka9q-web-2c6303f-radio.js` and `tests/fixtures/viewers/gmag-webui-bc1328b-feed.js` — verbatim copies of the two upstream files at their v3.82 pins (the ka9q-web clone: `git clone https://github.com/wa2n-code/ka9q-web`, `git show 2c6303f:html/radio.js`; gmag: `git -C ../gmag-webui show bc1328b:js/feed.js`).  Each file's first line, a comment, names its repo and commit.
- Test: `tests/test_viewer_rewrite.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Rewrite: kind: str; path: str; old: str; new: str`
  - `REWRITES: tuple[Rewrite, ...]` — exactly these two:

```python
Rewrite("ka9q-web", "radio.js",
        "ws = new WebSocket((window.location.protocol == 'https:' ? 'wss://' : 'ws://') + window.location.host);",
        "ws = new WebSocket((window.location.protocol == 'https:' ? 'wss://' : 'ws://') + window.location.host + window.location.pathname.replace(/[^/]*$/, ''));"),
Rewrite("gmag", "js/feed.js",
        'return scheme + loc.host + "/ws";',
        'return scheme + loc.host + (loc.pathname || "/").replace(/[^/]*$/, "") + "ws";'),
```

  - `apply_rewrite(kind: str, path: str, body: bytes) -> tuple[bytes, str]` — returns `(body, "rewritten" | "missed" | "none")`.  `"none"` when no rewrite names that kind and path.  `"missed"` when the `old` text occurs zero times or more than once; the body then comes back unchanged.
  - Both `new` lines resolve to the old URL when the page sits at `/` (direct port), so the rewrite also holds for a viewer visited without the proxy — but the proxy applies it only on proxied responses.

Decisions:
- Rewrite only a 200 response whose request path (after the prefix) equals `Rewrite.path` exactly.  For such a path request upstream with `Accept-Encoding: identity`, read at most 8 MiB, and send the result without `content-encoding` and with a fresh `content-length`.  Every other path keeps Task 2's raw streaming.
- A miss logs one WARNING per `(viewer name, path)` for the process lifetime: `viewer <name>: <path> lacks the line the front door rewrites; its live data will not work through /viewer/<name>/ — use the direct port`.  Never a 500.
- Upstream offers of the same fix: Task 6.  When upstream adopts one, the rewrite misses harmlessly and its WARNING tells the maintainer to drop the row.

- [ ] **Step 1: Write the failing tests:**
  - `test_each_rewrite_matches_its_pinned_fixture_exactly_once` — for each `Rewrite`, the fixture file contains `old` exactly once.
  - `test_ka9q_rewrite_applied` / `test_gmag_rewrite_applied` — `apply_rewrite` on the fixture returns `"rewritten"` and the body contains `new`, not `old`.
  - `test_miss_returns_body_unchanged` — `apply_rewrite("gmag", "js/feed.js", b"no such line")` → same bytes, `"missed"`.
  - `test_twice_is_a_miss` — `old` twice → unchanged, `"missed"`.
  - `test_other_paths_untouched` — `("ka9q-web", "spectrum.js", …)` → `"none"`.
  - Through the route (Task 2's fixture upstream serving the ka9q fixture at `/radio.js` gzip-encoded): `test_rewritten_asset_decoded_and_relength` — client body contains `new`, has no `content-encoding`, and `content-length` equals the body length.
  - `test_miss_through_route_is_200_and_warns_once` — two requests, one WARNING (`caplog`).
  - A JS check of the two `new` expressions with node if the repo's JS tests already use node (`tests/test_status_js.py` shows how); otherwise assert the strings.  At `/viewer/ka9q-web/` the ka9q URL ends `/viewer/ka9q-web/`; at `/` it ends `/`.  For gmag at `/viewer/gmag-webui/` it ends `/viewer/gmag-webui/ws`; at `/` it ends `/ws`.
- [ ] **Step 2: Run; watch each fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file, then the suite.
- [ ] **Step 5: Commit** — `viewer proxy: rewrite each viewer's WebSocket URL to keep the prefix (ka9q-web 2c6303f, gmag-webui bc1328b)`.

### Task 5: The Station page links through the front door

**Files:**
- Modify: `src/station_web/static/js/status.js` (`renderViewers`), `src/station_web/static/home.html` only if a class is needed
- Test: `tests/test_status_js.py` (and `tests/test_status_pages.py` if it pins the markup)

**Interfaces:**
- Consumes: the route shape `/viewer/<encodeURIComponent(name)>/`.

Decisions:
- A running viewer with a usable port renders its label as a link to `/viewer/<name>/` (relative to the page's own origin, so it works through any relay), then a small secondary link `direct :<port>` to `http://${location.hostname}:${port}/`, labelled so a remote reader understands it works on the LAN only (title text: `LAN only: the viewer's own port`).
- A viewer whose name sits in `conflicts` gets no front-door link, only the existing conflict warning.
- A viewer not running keeps today's unlinked label and state.
- `textContent` only, as Plan 1 requires.

- [ ] **Step 1: Write the failing JS tests** in the style of `tests/test_status_js.py`: front-door href for `ka9q-web` is `/viewer/ka9q-web/`; for `ka9q-web-2` `/viewer/ka9q-web-2/`; the direct link carries `:8081`; a conflicted name gets no `/viewer/` link; a name with a `/` or `?` is encoded.
- [ ] **Step 2: Run; watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_status_js.py tests/test_status_pages.py`, then the suite.
- [ ] **Step 5: Commit** — `station page: link each viewer through the front door, direct port as the LAN fallback`.

### Task 6: Docs, the filed conflict, and the offers to the people who own the rest

**Files:**
- Modify: `docs/DEPLOY-STATUS-PAGES.md` (a short "Viewers through the front door" section: what `/viewer/<name>/` serves, the two rewrites and their WARNING, the LAN fallback, and that appliance stations already forward port 8000 through the "station" relay)
- Modify: `docs/superpowers/specs/2026-10-27-station-docs-and-status-design.md` — correct the survey's remote-access sentence and §0's forward paragraph to the finding above, marked "corrected 2026-10-27 (Plan 2 survey)".
- Modify: `../sigmond/tasks/release-v383.md` — add row 9: **a second radiod's ka9q-web and gmag-webui both claim port 8082**; sigmond hard-codes both (`_ka9q_web_targets()`, `gmag_webui.PORT`); the front door refuses the shared port with 409 until sigmond gives them distinct ports.  Commit in sigmond, separately.
- Create: `docs/superpowers/plans/plan2-upstream-offers.md` — the two one-line upstream patches as unified diffs (ka9q-web `html/radio.js`; gmag-webui `js/feed.js`), each with a two-sentence reason, for Michael to send or not.  No pull request gets opened by this plan.

No tests beyond the suite staying green and `tests/test_docs_roots.py` passing.

- [ ] **Step 1: Write the four documents.**
- [ ] **Step 2: Run** the whole suite in station-web, and `cd ../sigmond && .venv/bin/pytest tests/test_tui_parity.py -q` (sanity; nothing there changes).
- [ ] **Step 3: Commit** station-web (`docs: viewers through the front door; spec survey corrected; upstream offers`) and sigmond (`v3.83 row 9: ka9q-web-2 and gmag-webui share port 8082`).
- [ ] **Step 4: Draft, do not post,** a claude-bus note to Rob, to the controller's scratchpad: what the front door does; that appliance stations need nothing from him; and one question: should sigmond-rac stations get a gw2 port for station-web (8000), and from which band?  The controller posts it after Michael's go to push.

### Task 7: Deploy to AC0G-B4 and prove it on the LAN and through gw2

Every step reaches outside the devbox: each waits for Michael's go.  The auto-mode classifier blocks remote root writes, so the controller stages `~/hf-deploy-front-door-b4.sh` (dry run by default, `--go` acts) for Michael to run with `!`.

- [ ] **Step 1:** Michael's go to push station-web and sigmond (`git log origin/main..main` in both first).
- [ ] **Step 2:** Bus: read, then post the deploy announcement.  Append the coordination-doc line.
- [ ] **Step 3: The script** (B4 via `ops/bin/fleet-ssh b4`): fast-forward `/opt/git/sigmond/station-web` to the pushed HEAD; check that the venv already imports `httpx` and `websockets.asyncio.client` — if either fails, stop and report (a dependency install needs its own ruling, never `install.sh`); `sudo -n systemctl restart station-web@default`; then, on B4: `curl -s -o /dev/null -w '%{http_code}'` for `/viewer/ka9q-web/`, `/viewer/ka9q-web/radio.js` (and grep the rewritten line), `/viewer/gmag-webui/`, `/viewer/gmag-webui/js/feed.js` (grep), and a WebSocket handshake for each (`curl -i -N --max-time 5 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==' …` expecting `HTTP/1.1 101`); and the journal for any front-door WARNING.
- [ ] **Step 4: Through gw2.**  B4's "station" channel sits at gw2 port 48800+N (`RAC_BASE_VMSTN`; read N from fleet.toml or the frps registry, never guess).  From wd30, repeat the HTTP checks and the 101 handshake against `http://gw2.wsprdaemon.org:<port>/viewer/…`.
- [ ] **Step 5: In a browser** (Michael): open the Station page through gw2, click each viewer, and confirm the waterfall moves and the magnetometer traces update.  Only a person can confirm that.
- [ ] **Step 6:** Bus note with the results; post Task 6's drafted note to Rob; update `project_station_status_docs_program` in memory.
