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

You can instead re-run sigmond's installer, `sudo ./install.sh`, which does
all of the above.  It also re-enables and starts the IRQ-affinity units
(`sigmond-rx888-irq-affinity.service` and `sigmond-guest-irq-affinity.service`),
which move interrupts between CPUs.  On AC0G-B4, announce that on the
claude-bus before you run it, because it changes IRQ placement on a station
that hosts live experiments.  The hand-install above avoids that.

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
