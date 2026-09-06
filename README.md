# station-web

Data reports and documentation for a sigmond station. Reads the data
products hf-timestd, hamsci-physics and gnss-vtec write under
`/var/lib/timestd`, and serves them as pages and JSON on port 8000.

It reads. It never runs a shell, never reaches the network, never
touches the host beyond the products directory. Sigmond owns station
management; station-web shows the science.

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
