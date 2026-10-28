# Upgrading a live station to the Station and Check pages

This runbook takes a station that already runs sigmond and station-web and
brings it to the new Station page (`/`) and the "Is it working?" Check page
(`/check`).  Each command runs on the station VM, as a user with sudo.

## How the pages get their facts

station-web runs unprivileged and runs no subprocess.  It cannot ask `smd`
anything.  Instead a root timer on the sigmond side runs `smd`'s read-only
commands every five minutes and writes each answer, as JSON, into
`/run/sigmond/snapshot/`.  station-web reads those files.  A system group,
`sigmond-status`, may read them.  station-web's user joins that group.

## Why `smd update` alone leaves the pages empty

`smd update` pulls sigmond's checkout, but sigmond's self-update stops there.
It never runs `install.sh`.  So after a pull the station still lacks four
things that only `install.sh` puts in place:

- the `sigmond-status` group;
- the tmpfiles entry that creates `/run/sigmond/snapshot/` and
  `/run/sigmond/snapshot-request/`;
- the six systemd units that write the snapshots and fetch upstream daily;
- the enabled timers and path watcher that start them.

Without those, nothing writes a snapshot, and every panel on both pages
reports its snapshot as unavailable.

## Step 1: the sigmond side

Pull sigmond first, with `smd update` or `smd component update sigmond`.
Then install the missing pieces by hand.  These commands touch nothing else
on the station:

```
cd /opt/git/sigmond/sigmond
sudo groupadd --system sigmond-status
sudo install -m 0644 systemd/sigmond-snapshot.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/sigmond-snapshot.conf
sudo install -m 0644 \
    systemd/sigmond-snapshot.service \
    systemd/sigmond-snapshot.timer \
    systemd/sigmond-snapshot-request.path \
    systemd/sigmond-snapshot-request.service \
    systemd/sigmond-component-fetch.service \
    systemd/sigmond-component-fetch.timer \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sigmond-snapshot.timer \
    sigmond-snapshot-request.path sigmond-component-fetch.timer
```

`groupadd` fails harmlessly if the group already exists.

Do not re-run sigmond's installer, `sudo ./install.sh`, for this step on a
station already in service.  It does all of the above, but it does much more.
It re-enables and starts every sigmond timer and watchdog (among them the T6
stuck watchdog, SDR recovery, storage trim, gap-hourly and timing reconcile),
starts the IRQ-affinity units, which move interrupts between CPUs, and
rewrites radiod's drop-in files.  On a station where someone stopped a timer
or a watchdog on purpose, the installer starts it again.  On AC0G-B4, read
`/etc/sigmond/CLAUDE-COORDINATION.md` first, and announce on the claude-bus
before running the installer for any reason.  The hand-install above touches
only the new group, directories and units.

The new units also belong in sigmond's CPU-affinity map, so the snapshot jobs
stay off radiod's CPUs.  The drop-ins for them arrive with the next
`smd admin diag cpu-affinity --apply`.  That step changes CPU affinity, so on
B4 it too needs a claude-bus announcement first.

## Step 2: the station-web side

Pull station-web, with `smd update` or `smd component update station-web`.
The pull changes `station-web@.service`, which now carries a new
`ExecStartPre` line.  That line runs `scripts/ensure-groups.sh` as root, and
the script adds the `stationweb` user to `sigmond-status`.  Reload systemd so
it sees the new unit, then restart the service:

```
sudo systemctl daemon-reload
sudo systemctl restart station-web@default
```

The restart matters for two reasons.  First, a process keeps the groups it
started with, so only a fresh start gains `sigmond-status`.  Second, the unit
grants write access to `/run/sigmond/snapshot-request/` only if that
directory exists when the service starts.  Step 1 created it, so restart
after Step 1, not before.

## What the pages show along the way

- Only station-web updated, sigmond not yet.  Both pages load.  The Station
  page lists each snapshot as unavailable and names the `smd` command it
  waits for, and the client table shows "unknown" in place of facts.
  The Check page shows "no snapshot".  "Check now" answers that check
  requests are not available on this station.
- sigmond done, station-web not yet restarted.  The old station-web process
  keeps serving the old pages.  If the new code already runs from an earlier
  restart, the pages call each snapshot unreadable, because the process lacks
  the new group, and "Check now" fails for want of the request directory.
  A restart clears both.
- Both done, before the first snapshot.  The timer fires on the next
  five-minute mark, with up to 30 seconds of random delay.  Until then the
  pages say the snapshots have not been written yet.  To write them at once,
  run `sudo systemctl start sigmond-snapshot.service`.
- Both done, before the first daily fetch.  The "Behind main" column reads
  "never fetched" for each checkout until the daily fetch runs.  The fetch
  timer fires within a day, with up to an hour of random delay.  To fetch at
  once, run `sudo systemctl start sigmond-component-fetch.service`; the next
  snapshot then carries the counts.
- The heartbeat line often reads "no heartbeat tick waiting".  hs-uploader
  deletes each tick once it ships, so an empty spool usually means the
  heartbeat works.

## Viewers through the front door

station-web serves each viewer on the station at `/viewer/<name>/`, for
example `/viewer/ka9q-web/` and `/viewer/gmag/`.  The Station page links each
viewer this way.  Because the viewer rides on station-web's own port, the link
works on the LAN and through remote access alike.

What the proxy does:

- It reads the viewer list and each viewer's port from the `viewers`
  snapshot.  It never probes ports, and it always connects to `127.0.0.1`.
- It carries HTTP and WebSockets.  A POST, PUT, PATCH or DELETE, and every
  WebSocket, must come from the same origin as station-web; the proxy
  refuses anything else.
- It adds no control of its own.  It passes the viewer's traffic through.
- It answers 404 for an unknown viewer, 503 when the snapshot is
  unavailable, and 409 when two viewers claim one port.

Two viewers open their WebSocket at the server root, which under a prefix
would reach station-web's own `/` or `/ws`.  The proxy rewrites one line in
each, as the file passes through:

| Viewer | File | Pinned upstream commit |
|---|---|---|
| ka9q-web | `html/radio.js` | 2c6303f |
| gmag-webui | `js/feed.js` | bc1328b |

The proxy replaces the line only when it appears exactly once.  If the line
is missing, because the viewer changed upstream, the file passes through
unchanged and station-web logs one WARNING per viewer and file:

```
viewer <name>: <path> lacks the line the front door rewrites; its live data will not work through /viewer/<name>/ — use the direct port
```

Find it with `journalctl -u station-web@default | grep 'lacks the line'`.
The page itself still loads; only its live data stops.

gmag-webui also asks for `/site.webmanifest` by an absolute path.  Under the
prefix that request reaches station-web and returns 404.  The effect is
cosmetic: the browser finds no web-app manifest.

The LAN fallback: each viewer row on the Station page carries a
"direct :<port>" link.  It goes straight to the viewer's own port and works
only on the station's LAN.  Use it when the WARNING appears, or when the
proxy answers 409.

One conflict stays open.  A second radiod's ka9q-web and gmag-webui both
claim port 8082, and sigmond hard-codes both.  The proxy refuses the shared
port with 409 until sigmond gives them distinct ports.  Row 9 of sigmond's
`tasks/release-v383.md` tracks it.

Remote access: appliance stations already forward station-web's port 8000
through the "station" relay, so they need nothing more.  A station that runs
sigmond-rac forwards ssh and at most one admin-assigned web port; it needs a
gw2 port for 8000 before the front door reaches it remotely.

## How to check the result

List the snapshot directory:

```
ls -l /run/sigmond/snapshot/
```

You should see eight files (`catalog`, `check`, `components`, `heartbeat`,
`instances`, `manifest`, `validate`, `viewers`, each with `.json`), owned by
`root:sigmond-status`, mode `0640`, and none older than five minutes.

Then open `http://<station>:<port>/` and `http://<station>:<port>/check` in a
browser.  The Station page lists each client with its version, manifest
status and check mark.  The footer gives each snapshot's age.  No panel
should report a snapshot as unavailable or failed.  On the Check page, press
"Check now"; within a minute or two the page should say "updated" and show a
newer check time.
