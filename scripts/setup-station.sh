#!/usr/bin/env bash
# `smd config init station-web` — render /etc/station-web/station-web.toml.
#
# CONTRACT §14.3: sigmond exports STATION_* so the operator answers nothing
# twice.  STATION_CALL is canonical; STATION_CALLSIGN is the compat alias.
# Also seeds [storage] sqlite_path from hf-timestd's config when present, and
# creates the stationweb user (group timestd) because only link-kind install
# steps execute in sigmond today.
set -euo pipefail

CONFIG_DIR=${CONFIG_DIR:-/etc/station-web}
CONFIG=${CONFIG:-$CONFIG_DIR/station-web.toml}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEMPLATE=$REPO/config/station-web.toml.template
TIMESTD_CONFIG=${TIMESTD_CONFIG:-/etc/hf-timestd/timestd-config.toml}

if [[ $EUID -eq 0 ]] && ! id -u stationweb >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin -g timestd stationweb
    echo "station-web: created user stationweb (group timestd)"
fi

install -d -m 0755 "$CONFIG_DIR" "$CONFIG_DIR/env"

if [[ -f $CONFIG ]]; then
    echo "station-web: $CONFIG exists — leaving it alone (use 'smd config edit')."
    exit 0
fi

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
cp "$TEMPLATE" "$tmp"

set_str() {  # set_str <key> <value>   fills an empty "" slot
    local key=$1 val=$2
    [[ -z $val ]] && return 0
    sed -i "s|^\( *${key} *= *\)\"\"|\1\"${val}\"|" "$tmp"
}
set_num() {  # set_num <key> <value>   replaces a 0.0 slot
    local key=$1 val=$2
    [[ -z $val ]] && return 0
    sed -i "s|^\( *${key} *= *\)0\.0|\1${val}|" "$tmp"
}

set_str callsign    "${STATION_CALL:-${STATION_CALLSIGN:-}}"
set_str grid_square "${STATION_GRID:-}"
set_num latitude    "${STATION_LAT:-}"
set_num longitude   "${STATION_LON:-}"

if [[ -r $TIMESTD_CONFIG ]]; then
    sq=$(sed -n 's/^ *sqlite_path *= *"\([^"]*\)".*/\1/p' "$TIMESTD_CONFIG" | head -1)
    set_str sqlite_path "$sq"
fi

install -m 0644 "$tmp" "$CONFIG"
echo "station-web: wrote $CONFIG"
echo "  station: ${STATION_CALL:-${STATION_CALLSIGN:-<unset>}} / ${STATION_GRID:-<unset>}"
exit 0
