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

# CLIENT-CONTRACT §5.1: sigmond enumerates a client's instances from
# /etc/<client>/env/<instance>.env.  The templated unit only ever runs as
# station-web@default, so seed that instance file if it's missing — including
# on a re-run against an existing config — or sigmond shows the default
# instance as orphaned and invents an inactive station-web@station-web.
DEFAULT_ENV=$CONFIG_DIR/env/default.env
if [[ ! -f $DEFAULT_ENV ]]; then
    cat > "$DEFAULT_ENV" <<'EOF'
# station-web instance "default" — CLIENT-CONTRACT §5.1 instance enumerator.
# Sourced by station-web@default.service; keys here become environment
# variables for that instance.  Empty is fine.
EOF
    chmod 0644 "$DEFAULT_ENV"
    echo "station-web: wrote $CONFIG_DIR/env/default.env"
fi

# The one directory station-web needs to WRITE in: SQLite's -wal and -shm
# sidecars are created by whichever process opens the database, so a reader
# running as stationweb (group timestd) needs group write on the directory
# holding timestd.db.  hf-timestd creates that directory 0755 root:timestd,
# and `station-web validate` then fails it.  setgid so a sidecar created by
# either uid keeps group timestd.  Nothing else under the data root is
# touched: the root itself stays read-only to this client.
#
# Runs on BOTH paths below — a re-run against an existing config must still
# be able to repair the directory, which is the state a cutover meets.
grant_group_write_on_db_dir() {
    local seed=${1:-}
    local sqlite_path db_dir before
    sqlite_path=$(sed -n 's/^ *sqlite_path *= *"\([^"]*\)".*/\1/p' "$seed" 2>/dev/null | head -1)
    db_dir=${sqlite_path:+$(dirname "$sqlite_path")}
    db_dir=${db_dir:-/var/lib/timestd/phase2}
    if [[ $EUID -ne 0 ]]; then
        echo "station-web: not root — leaving $db_dir permissions alone"
        return 0
    fi
    if [[ ! -d $db_dir ]]; then
        echo "station-web: $db_dir absent — hf-timestd has not written yet; re-run after it has"
        return 0
    fi
    before=$(stat -c '%a %U:%G' "$db_dir")
    chmod g+ws "$db_dir"
    echo "station-web: chmod g+ws $db_dir  ($before -> $(stat -c '%a %U:%G' "$db_dir"))"
    echo "  (WAL sidecars: a reader in another uid must create timestd.db-wal/-shm)"
}

if [[ -f $CONFIG ]]; then
    echo "station-web: $CONFIG exists — leaving it alone (use 'smd config edit')."
    grant_group_write_on_db_dir "$CONFIG"
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

grant_group_write_on_db_dir "$CONFIG"
exit 0
