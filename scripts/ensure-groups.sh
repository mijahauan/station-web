#!/usr/bin/env bash
# ExecStartPre=+ of station-web@.service, and called by setup-station.sh.
#
# Adds user stationweb to timestd (hf-timestd's products) and to
# sigmond-status (sigmond's snapshots), each only where that group exists
# and only when the user is not already a listed member.  It reads the
# group's member list, not `id -nG`: initgroups() grants listed memberships,
# never the passwd primary group, so an older station whose stationweb has
# timestd only as its primary group still needs the listed membership.
#
# It never fails the start: a step that fails gets logged and the script
# exits 0.  systemd resolves supplementary groups at each exec, so the main
# process picks up a membership added here on the same start.
USER_NAME=${STATIONWEB_USER:-stationweb}

if ! getent passwd "$USER_NAME" >/dev/null; then
    echo "station-web: user $USER_NAME absent — run 'smd config init station-web'" >&2
    exit 0
fi

for grp in timestd sigmond-status; do
    entry=$(getent group "$grp") || continue
    if tr ',' '\n' <<<"${entry##*:}" | grep -x "$USER_NAME" >/dev/null; then
        continue
    fi
    if usermod -aG "$grp" "$USER_NAME"; then
        echo "station-web: added $USER_NAME to group $grp"
    else
        echo "station-web: could not add $USER_NAME to group $grp — its pages may not read" >&2
    fi
done
exit 0
