#!/usr/bin/env bash
# deploy.toml [build] — create station-web's venv.
#
# The core (status, docs, grape, timing) needs nothing scientific, so it
# installs on every station, magnetometer-only and WSPR-only sites included.
# The science pages need hamsci-dsp and hamsci-physics, siblings not on PyPI;
# this script installs them, and the [science] extra, only where both
# checkouts already exist.  station-web loads the science routers only when
# their imports succeed (app.SCIENCE_ROUTERS).
#
# The interpreter path names the MINOR series (uv's stable symlink), never a
# patch release — see hamsci-physics/deploy.toml for the breakage that taught it.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-/opt/uv/python/cpython-3.11-linux-x86_64-gnu/bin/python3.11}
VENV=${VENV:-/opt/git/sigmond/station-web/venv}
SIBLINGS=${SIBLINGS:-/opt/git/sigmond}
PIP=$VENV/bin/pip

"$PYTHON" -m venv "$VENV"
"$PIP" install --upgrade pip setuptools wheel
# venv/bin/station-web (deploy.toml's `produces`) comes from installing this
# package, so that install runs LAST on either branch.  A science build that
# fails part-way then leaves no console script, and sigmond reports the build
# as failed instead of passing it as core-only.
if [[ -d $SIBLINGS/hamsci-dsp && -d $SIBLINGS/hamsci-physics ]]; then
    # hamsci-physics depends on hs-uploader, also not on PyPI, so that
    # sibling installs before hamsci-physics.
    "$PIP" install -e "$SIBLINGS/hamsci-dsp"
    if [[ -d $SIBLINGS/hs-uploader ]]; then
        "$PIP" install -e "$SIBLINGS/hs-uploader"
    fi
    "$PIP" install -e "$SIBLINGS/hamsci-physics"
    "$PIP" install -e "$REPO[science]"          # installs the core too
    echo "station-web: built with the science pages"
else
    "$PIP" install -e "$REPO"
    echo "station-web: built core only — $SIBLINGS/hamsci-dsp and hamsci-physics" \
         "not both present, so the science pages stay off"
fi
