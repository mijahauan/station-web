# Station documentation and status in station-web

Design, 2026-10-27.  Project A of two.  Project B, radiod control in the
browser, gets its own design later.

## Why

An operator finishes an install and wants to know that the station works.
That means the radio and the magnetometer deliver data, the decoders and the
analysis run, the results reach the local sinks, and the uploads run and
succeed.  A fleet manager sees a problem on the fleetboard and wants the
detail behind it.  An operator or administrator wants to look up a command,
`smd watch …` or `hf-timestd …`, and see its syntax and what good output looks
like.  Today the answers lie scattered across the READMEs and design notes of
some twenty repositories, the code, the TUI, and people's heads.

Michael's requests for this work, in his order of importance:

1. "Is it working?"  After an install, and at any time after, the station
   shows whether each stage of its data path works, from sensor to upload.
2. A summary of the sigmond environment against the local installation.
   Every sigmond client appears with what it does, whether this station has
   it installed, whether the installed version stands current, how many
   instances it has configured, and whether each instance is enabled and
   running.
3. A searchable command reference covering `smd` and each client's command,
   with the syntax and the expected output.
4. Links to the viewers the station already runs: ka9q-web (spectrum and
   waterfall), gmag-webui, and station-web's own live pages.

Later work in the same project gives the documentation two shapes.  One
follows the architecture: sigmond, hf-timestd, each client, each library.
The other follows the reader: site operator, scientist, developer, fleet
administrator.  The role guides hold task pages like the four above and
link down into the component pages.

## The governing idea: one source of facts, one source of words

Three front ends already show station state: the `smd` command, the sigmond
TUI, and station-web.  The heartbeat that Rob is building adds a fourth
reader upstream, the fleetboard on wd30.  Each new view that computes its own
facts adds one more copy to keep in step.  So this design holds to two rules.

- **Facts about a station come from sigmond.**  The client list, versions,
  instances, service state and health checks each come from one `smd`
  command's JSON.  station-web and the TUI render that JSON.  Neither
  computes a fact of its own.
- **Words come from Markdown in the repositories.**  Guides, client
  descriptions, check explanations and command examples live beside the code
  they describe.  station-web renders them.  The TUI can show the same files
  through Textual's Markdown viewer, or point to the web page.

Michael ruled on the division of labour (2026-10-27): keep both front ends.
The TUI does things and serves recovery.  It works on the console when the
network or the web server fails, and it owns every action that changes a
station.  station-web serves reading.  It reaches people without a terminal,
including remotely, and it places live evidence beside the explanation.
station-web never changes a station.

The path from an alarm to an answer then runs as follows.  The heartbeat
raises the flag upstream.  The fleetboard names the failing check and links
to the station.  The station's station-web page, reached through remote
access, shows the live state beside the words that say what healthy looks
like and what to do next.  The TUI shows the same facts on the console.

## Goals

1. A front page in station-web, **Station**, that summarises the station
   against the sigmond catalog, and carries the viewer links.
2. A page, **Is it working?**, that walks the data path in stages and shows a
   verdict, the evidence and the fix for each check.
3. A page, **Commands**, that lists every command of `smd` and of each
   installed client, with its arguments and help text, searchable, and with
   example output where someone has written one.
4. A search box that covers the commands and the headings of every served
   document.
5. Every fact on these pages traces to one named sigmond command.  A test
   fails when a page names a command that no longer exists.

## Non-goals

- Rewriting the existing documentation.  This project builds the frame and
  the four pages.  Moving and merging content (the six copies of the install
  steps, the loose files in `sigmond/docs/`) stays separate work, guided by
  sigmond's existing docs conventions and gap ledger.
- Radiod control in the browser (project B).  The Station page links to it
  once it exists.
- Changing the heartbeat.  Where this design suggests aligning names with
  the heartbeat, it offers that to Rob as a suggestion.
- A fleet-wide view.  The fleetboard owns that.  This project gives it
  stable deep links into each station.

## Anti-goals: what would make this a failure even if it worked

- **A third copy of the facts.**  A page that recomputes what `smd` already
  knows will drift from it within a release.
- **Green when broken.**  A check that cannot read its evidence must say so.
  It never shows a pass.  `smd check` already behaves this way ("FAIL cannot
  read … as <user>"); the pages must keep that honesty and must show a
  check absent from this station as "not on this station", never as a
  failure.
- **Load on the station.**  Station VMs run timing-critical work.  The pages
  must not run heavy diagnostics on each page load or on a short poll.
- **A web page that changes the station.**  station-web keeps its read-only
  stance and its systemd hardening (`NoNewPrivileges`, read-only paths).
- **Documentation that drifts from the code.**  The command reference comes
  from the programs themselves, never from hand-copied help text.

## What exists (survey, 2026-10-27)

- **station-web** serves documentation as a Markdown browser over four
  folders from other repositories (`routers/docs.py`, `static/docs.html`):
  hf-timestd's docs, hamsci-physics's, hamsci-dsp's, and sigmond's scientist
  guide.  A document can embed live widgets (`<!-- LIVE: … -->`) that
  refresh every five seconds.  It has no search, and it never calls `smd`.
- **sigmond** already sorts its documentation by audience (`docs/operator/`,
  `scientist/`, `contributor/`, `hardware/`), with an `Audience:` header on
  each page (`docs/contributor/docs-conventions.md`).  station-web exposes
  only the scientist folder.
- **`smd check --json`** already holds the "is it working?" checks: bring-up,
  PSK, WSPR, magnetometer, GRAPE, storage, uploads, meteor scatter, and the
  three local web pages.  Each line carries a mark (ok, wait, FAIL, or `--`
  for absent) and a free-text explanation with an embedded `Fix:` hint.  The
  whole run carries a PASS, WAIT or FAIL verdict.  It takes up to about 15 s.
  Some of its reads need the systemd journal or files that user
  `stationweb` cannot read.
- **`smd component list --catalog --json`** lists every client sigmond knows,
  with its description.  **`smd component list --json --no-fetch`** gives each
  installed component's version, its distance behind upstream (against the
  last fetch) and a lifecycle label.  Without `--no-fetch` it runs `git
  fetch` for every repository.
- **Instances** have no JSON surface in sigmond: `smd admin instance list`
  prints text.  Each client's `inventory --json` lists its own instances.
- **`smd admin validate --json`** reports named configuration rules with a
  severity and a message.
- **The heartbeat** (`smd admin heartbeat emit`, every 5 min when enabled)
  writes blocks named versions, manifest, timing, gaps, uploads, doctor and
  resources, each with a verdict and a reason.  It shares source functions
  with `smd`, but not field names.
- **The TUI** shows an Overview screen (the face of `smd status`) and a
  Software Versions screen.  `lib/sigmond/tui/parity.py` maps each screen to
  the `smd` command it stands for, and a test fails when the two drift.
- **No command reference exists.**  Nothing generates one.  sigmond's own
  test (`tests/test_tui_parity.py`) already walks the `smd` argparse tree
  without running a command.  Three clients expose a `build_parser()`
  function; four build their parser inside `main()`.
- **Viewers.**  ka9q-web serves on port 8081 for the first radiod and 8082,
  8083 and so on for further ones.  gmag-webui serves on 8082.  These ports
  appear hard-coded in sigmond, not in configuration.  *(Confirmed
  2026-10-28: `_ka9q_web_targets()` in `bin/smd` and `PORT` in
  `lib/sigmond/gmag_webui.py`.)*

## Design

### 0. Three rulings that shape everything below (Michael, 2026-10-27)

**Every station.**  The Station and Check pages serve every sigmond station,
including a magnetometer-only DASI site and a WSPR/PSK-only station.  Today
station-web cannot install there: its catalog entry requires hf-timestd,
hamsci-physics, hamsci-dsp and hs-uploader, it runs as group `timestd`, and
it reads `/var/lib/timestd`.  So the first piece of work splits station-web
into a core and an optional part:

- **The core** (status, documentation, commands, viewers, the front-port
  proxy) depends on sigmond alone and installs on every station.  It runs
  under its own group, not `timestd`.
- **The timing and science pages** (timing, GRAPE, propagation and the
  rest) load only where hf-timestd and hamsci-physics run.  The navigation
  hides them elsewhere.

How the split gets packaged (one repository with an optional extra, or two
components in the catalog) stays with the builder.  Either way, a fresh
install of any kind of station gets the core.

**One front port.**  *(Corrected 2026-10-28, Plan 2 survey.  The first draft of
this paragraph said remote access forwards one web port, 8081.  That holds
only for stations that run sigmond without the appliance.)*  Each appliance
station already relays four channels from its host to the VM: ssh (22), web
(8081), station-web (8000) and gmag (8082), on gw2 bands 48800+N and 49800+N.
The relay copies raw TCP, so WebSockets pass.  Plan 2 therefore changes no
remote-access code.  A station that runs sigmond-rac forwards ssh and at most
one admin-assigned web port.  Giving it station-web's port needs a gw2 port
from Rob; Plan 2 asks him and does not build it.

station-web serves as the single front door.  It reverse-proxies each viewer
by path, for example `/viewer/ka9q-web/1/` and `/viewer/gmag/`, so every link
works the same on the LAN and through remote access.  The fleetboard's deep
links (`/check#<id>`) then reach any station.  Project B's radiod control
will sit behind the same door, which gives it one place for authentication.

The proxy has to carry WebSockets.  gmag-webui's page talks to mag-usb's
WebSocket on port 8765, and ka9q-web streams over a WebSocket too.  The
builder confirms each viewer works under a path prefix, and lists any that
cannot with the reason.  Changing the remote-access forward touches Rob's
gateway port registry, so the builder agrees that step with him.

**Blessed manifest first.**  "Is the installed version current?" means: does
each client match the blessed release this station adopted?  That signal
drives the colour.  A second, quieter column shows how far upstream main
has moved past the installed version, for information only.  A correctly
blessed station reads green, and the page never nudges anyone to update off
the manifest.  Where a station has adopted no blessed manifest, the page
says so and shows only the main column.

*My call:* the daily fetch runs git as each checkout's owner, as sigmond's
`_git_fetch_as_owner` already does, never as root.  A fetch that fails shows
"unknown since <time>", never "0 behind": a count of zero from an
unreachable remote would show green when the page knows nothing.

### 1. Snapshots: sigmond writes, station-web reads

A sigmond-owned timer runs as root and writes JSON snapshots to a directory
that station-web can read:

| File | Command | When |
|---|---|---|
| `check.json` | `smd check --json` | every 5 min, and on request |
| `components.json` | `smd component list --json --no-fetch` | every 5 min |
| `catalog.json` | `smd component list --catalog --json` | every 5 min |
| `instances.json` | `smd admin instance list --json` (new) | every 5 min |
| `validate.json` | `smd admin validate --json` | every 5 min |
| `viewers.json` | the viewer list (new, see §5) | every 5 min |
| `upstream.json` | `smd component list --json`, with fetch | daily |

Each snapshot carries the time it ran and the command that produced it.
station-web shows that age on every page.

Running as root removes every permission gap: `smd check` reads the journal
and the upload database as it does on a terminal.  station-web needs no new
groups, no sudo, and no subprocess.  The 5-minute interval matches the
heartbeat's.

*My call:* the directory sits at `/run/sigmond/snapshot/`, owned by root,
readable by station-web's own group (no longer `timestd`, per §0).  *My call:* the daily
fetch decides "latest version"; between fetches the page shows the fetch's
age beside each "behind" count.

**Check now.**  An operator who has just fixed something should not wait
five minutes.  The Check page offers a button that asks sigmond to refresh
`check.json`.  station-web writes an empty request file in a directory it
owns; a systemd path unit watches that directory and starts the snapshot
service.  The request carries no arguments, so it cannot ask for anything
but the fixed refresh.  *My call:* the service ignores requests that arrive
within 30 s of the last run, so a held-down button cannot load the station.

### 2. Small sigmond changes the pages need

- **`smd check` lines gain a stable `id`** (for example `sensor.magnetometer`,
  `decode.wspr`, `sink.grape`, `upload.psws`), and a `stage` (sensor,
  decode, sink, upload, platform).  Today the label string serves as the
  key, and any rewording would break a page link.  The existing `mark`,
  `label` and `text` fields stay as they are.
- **`smd check` lines split the fix hint into its own field** (`fix`), in
  place of the text after `Fix:`.  The text field keeps its current wording
  for the terminal.
- **`smd admin instance list --json`** emits, per client, each instance with
  its name, whether it is configured, enabled and active.
- **The viewer list** (see §5) comes from the code that already knows the
  ports (`_ka9q_web_targets()` and the gmag-webui settings).
- **A web parity map** joins the TUI's: each station-web page names the
  `smd` commands it renders, and the existing parity test checks that each
  still exists.

### 3. The Station page (front door)

One row per client in the sigmond catalog:

- name, and the catalog's one-line description;
- installed or not;
- installed version, and how far behind upstream, with the fetch's age;
- instances: how many are configured, how many enabled, how many running;
- the client's verdict from `check.json`, when a check covers it;
- links: the client's documentation, its rows on the Check page, its
  commands on the Commands page.

Clients the station lacks still appear, greyed, so the reader sees the whole
of sigmond and what this station uses of it.  *My call:* a filter shows
"installed only" by default on narrow screens.

Above the table sit the station's identity (callsign, grid, sigmond version)
and the **Viewers** panel (§5).  Below it, a link to the heartbeat's last
report when the heartbeat runs on this station, with its verdict and age.

### 4. The "Is it working?" page

The page walks the data path in stages, in this order: platform (bring-up,
storage, timing), sensors (radio, magnetometer), decoding and analysis
(WSPR, PSK, meteor scatter, GRAPE), local sinks, uploads.  Each check shows
its mark, the evidence text, and the fix.  A check absent from this station
shows "not on this station".  A check that could not read its evidence shows
that plainly, as `smd check` reports it.

Each check id links to a short Markdown page that explains what healthy
looks like, why the check might fail, and what to do.  Those pages live in
sigmond, at `docs/operator/checks/<id>.md`, beside the code that defines the
check.  Where a page wants live evidence, it can use station-web's existing
`<!-- LIVE: … -->` widgets.

The page's header gives the overall verdict, the snapshot's age and the
Check now button.  Configuration rule results from `validate.json` appear
in a second section, failures and warnings first.

*My call:* each check gets a stable URL, `/check#<id>`, so the fleetboard and
the heartbeat can link straight to it.

### 5. Viewers

The Viewers panel on the Station page lists, for each viewer installed and
running on this station:

- ka9q-web, one link per local radiod (spectrum and waterfall);
- gmag-webui (magnetometer);
- station-web's own live pages (timing, GRAPE, propagation, and the others
  in its navigation);
- radiod control (project B), once it exists.

sigmond supplies the list in `viewers.json`: name, port, unit, whether the
unit runs, and a one-line description.  Each link points to the viewer's
path on station-web's own front port (§0), so it works on the LAN and
through remote access alike.  A viewer that does not run shows as such,
with no link.

The survey found a port conflict: a second radiod's ka9q-web takes port
8082, which gmag-webui also uses.  The builder files that against sigmond
and does not work around it in station-web.  *(Filed 2026-10-28 as row 9 of
sigmond's `tasks/release-v383.md`.  The front door answers 409 for either
viewer while both claim the port.)*

### 6. The Commands page

**Generation.**  A sigmond command, `smd admin cli-reference build`, walks the
argparse tree of `smd` and of each installed client and writes one JSON file
per program to `/var/lib/sigmond/cli-reference/`.  It runs each client's walk
in that client's own venv.  Install and update run it, so the reference
always matches the code on the station.  For `smd` it reuses the technique
of `tests/test_tui_parity.py`.  For each command path it records the help
text, the arguments, the flags with their choices and defaults, and the
program's version.

**Expected output.**  The code cannot say what good output looks like, so
people write that.  Each repository may hold `docs/commands/<program>.md`,
with one section per command path, showing an example run and what to look
for.  The Commands page shows the example beside the generated entry.  It
also lists the commands that have no example yet, so the gaps stay visible.

**Search.**  One search box filters commands by path, help text and flags,
and finds document headings across every served folder.  *My call:* the
server builds the index from the reference files and the document headings
when it starts and when files change, and the browser filters it.  The
station needs no search engine.

### 7. The documentation browser

The existing browser stays, with two changes:

- its folders come from the catalog's installed clients plus sigmond's
  audience folders, not from a fixed list of four;
- each page shows its `Audience:` header, and the folder list can group by
  audience as well as by component.

This begins the two-shaped organisation.  The role guides themselves,
written as Markdown in sigmond's audience folders, follow as content work.

## Constraints

- station-web stays read-only, keeps its systemd hardening, and runs no
  subprocess.  Every fact reaches it as a file.  Proxying a viewer passes
  that viewer's own traffic through; station-web adds no control of its own.
- sigmond's core stays standard-library only (its repository rule).
- The snapshot timer adds no measurable load: one `smd check` every 5 min,
  plus requests no closer than 30 s apart.
- The heartbeat stays Rob's.  Any field alignment goes to him as a proposal.
- Documentation follows sigmond's docs conventions (`Audience:` header,
  `docs/INDEX.md` per repository).
- Pages work on a phone and through remote access.

## Order of work

1. sigmond: check ids, stages and the `fix` field; `instance list --json`;
   the viewer list; the blessed-manifest comparison; the snapshot timer,
   service and path unit.
2. station-web: split into the core and the timing and science part, so
   the core installs on every station.
3. station-web: the front-port proxy for the viewers; then sigmond's
   remote-access forward for station-web's port, agreed with Rob.
4. station-web: the Station page with the Viewers panel; the Check page.
5. sigmond: `smd admin cli-reference build`, wired into install and update.
6. station-web: the Commands page and search.
7. station-web: the documentation browser changes.
8. Content: check pages in `sigmond/docs/operator/checks/`, then command
   examples, then the role guides.

Each step ships on its own.  The Station and Check pages come as early as
the split and the proxy allow, because they answer Michael's two most
pressing needs.

## Left to the builder

- Page layout and styling, within station-web's existing look and
  navigation.
- The exact check ids and stage names, provided they stay stable once
  published.
- The JSON layout of the new sigmond outputs.
- How the search index stores its entries.
- Whether the TUI gains a Markdown viewer for the check pages now or later.

## Calls I made (for Michael to check)

- Snapshots written by a root timer every 5 min, instead of station-web
  running `smd` itself.
- `/run/sigmond/snapshot/`, readable by group `timestd`.
- A daily fetch measures how far main has moved.  It runs as each checkout's
  owner, never root, and a failed fetch shows "unknown", never "0 behind".
- A Check now button through a request file and a path unit, limited to one
  run per 30 s.
- Check pages live in sigmond at `docs/operator/checks/<id>.md`.
- Stable check URLs for the fleetboard to link to.
- Command examples written by hand in `docs/commands/<program>.md`; the
  page lists the commands still lacking one.
- A search index built by station-web from files, no search engine.
- Clients the station lacks appear greyed on the Station page.
- The new Station page takes over `/`.  The existing Status (`index.html`)
  and System Health (`health.html`) pages stay: they report the freshness
  of station-web's own science products, which sigmond does not know about,
  so the one-source rule leaves them alone.  The navigation links them
  from the Check page.  The old `station.html` merges into the new Station
  page wherever the two overlap.
- The command generator reads each client's argparse tree from outside,
  intercepting the parser the way sigmond's parity test does.  It edits no
  client repository.  Programs without an argparse tree (radiod, ka9q-web
  and the other ka9q-radio tools) appear on the Commands page as links to
  the man pages ka9q-radio already ships.
