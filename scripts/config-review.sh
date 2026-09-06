#!/usr/bin/env bash
# `smd config edit station-web` — show the config and self-validate.
set -euo pipefail
CONFIG=${CONFIG:-/etc/station-web/config.toml}
VENV_PY=${VENV_PY:-/opt/git/sigmond/station-web/venv/bin/python3}
[[ -x $VENV_PY ]] || VENV_PY=python3
if [[ ! -f $CONFIG ]]; then
    echo "station-web: $CONFIG not found — run 'smd config init station-web'." >&2
    exit 1
fi
echo "=== $CONFIG ==="; cat "$CONFIG"; echo; echo "=== validate ==="
"$VENV_PY" -m station_web.cli validate --json -c "$CONFIG"
