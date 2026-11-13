# station-web

Data reports and documentation for a sigmond station. Reads the data
products hf-timestd, hamsci-physics and gnss-vtec write under
`/var/lib/timestd`, and serves them as pages and JSON on port 8000.

It reads. It never runs a shell and never reaches the network. Beyond
the products directory it reads only the snapshots sigmond publishes in
`/run/sigmond/snapshot`, and writes one request file in
`/run/sigmond/snapshot-request` to ask for a fresh one. Sigmond owns
station management; station-web shows the science.

The top bar on every page links to Documentation and to Commands. The
Commands page (`/commands`) shows the station's command reference, read
from sigmond's `cli-reference` snapshot, with examples people wrote in
`docs/commands/<program>.md` and a search box.

Phase 5 of the 2026-08-10 hf-timestd split. Design:
`hf-timestd/docs/superpowers/specs/2026-09-06-station-web-extraction-design.md`.

## Operate

    smd install station-web            # clone, venv, units
    smd config init station-web        # /etc/station-web/config.toml from the env bag
    systemctl status station-web@default
    station-web validate --json        # contract self-check

Browse `http://<station>:8000/`. API docs at `/api/docs`.

## Develop

    uv sync --extra dev
    uv run pytest tests/ -q --override-ini addopts=
