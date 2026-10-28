/**
 * The Station page ("/") and the Check page ("/check").
 *
 * Both draw what sigmond's snapshots say, through /api/status/*.  Every
 * server string reaches the page as text content, never as markup.
 * A missing, stale or failed snapshot shows as that, never as a pass.
 */
(function () {
    'use strict';

    const SHORT_WIDTH = 700;
    const POLL_MS = 5000;
    const POLL_LIMIT_MS = 120000;
    // Both pages re-read their data this often, so the ages shown move on.
    const REFRESH_MS = 60000;
    // True while a Check-now poll runs; the 60 s refresh then stands aside.
    let checkNowPolling = false;

    // ── small DOM helpers ───────────────────────────────────────────────
    function el(tag, opts, kids) {
        const n = document.createElement(tag);
        const o = opts || {};
        if (o.cls) n.className = o.cls;
        if (o.text !== undefined && o.text !== null) n.textContent = String(o.text);
        if (o.href) n.href = o.href;
        if (o.id) n.id = o.id;
        if (o.title) n.setAttribute('title', o.title);
        if (o.label) n.setAttribute('data-label', o.label);
        (kids || []).forEach(k => { if (k) n.appendChild(k); });
        return n;
    }

    function fill(id, ...nodes) {
        const host = document.getElementById(id);
        if (!host) return null;
        host.replaceChildren(...nodes.filter(Boolean));
        return host;
    }

    function fail(msg) {
        const box = document.getElementById('error-container');
        if (!box) return;
        box.textContent = msg;
        box.style.display = msg ? 'block' : 'none';
    }

    async function getJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
    }

    /** "5 s", "12 min", "3 h", "2 d": how long ago, from seconds. */
    function ageText(seconds) {
        if (seconds === null || seconds === undefined || !isFinite(seconds)) return 'unknown';
        const s = Math.max(0, Math.round(seconds));
        if (s < 90) return `${s} s`;
        if (s < 5400) return `${Math.round(s / 60)} min`;
        if (s < 129600) return `${Math.round(s / 3600)} h`;
        return `${Math.round(s / 86400)} d`;
    }

    function ageSince(iso) {
        const t = Date.parse(iso || '');
        return isNaN(t) ? null : (Date.now() - t) / 1000;
    }

    /** "2026-10-27 11:58Z": UTC, as written, no locale. */
    function utcText(iso) {
        const t = Date.parse(iso || '');
        if (isNaN(t)) return 'unknown time';
        return new Date(t).toISOString().slice(0, 16).replace('T', ' ') + 'Z';
    }

    // ── Station page ────────────────────────────────────────────────────
    function identityText(id) {
        const parts = [id.callsign, id.grid_square].filter(Boolean);
        const sm = id.sigmond || {};
        if (sm.local) {
            let s = `sigmond ${sm.local}`;
            if (!sm.fetched_at) s += ' (never fetched)';
            else if (sm.behind === null || sm.behind === undefined) s += ' (behind unknown)';
            else if (sm.behind > 0) s += ` (${sm.behind} behind)`;
            parts.push(s);
        }
        return parts.length ? parts.join(' · ') : 'Station';
    }

    function renderViewers(v) {
        if (!v) {
            fill('viewers', el('div', { cls: 'sp-note', text: 'viewer list not available' }));
            fill('viewer-conflicts');
            return;
        }
        const list = Array.isArray(v.viewers) ? v.viewers : [];
        const conflicted = new Set();
        (Array.isArray(v.conflicts) ? v.conflicts : []).forEach(c =>
            (c.names || []).forEach(n => conflicted.add(n)));
        if (!list.length) {
            fill('viewers', el('div', { cls: 'sp-note', text: 'no viewers on this station' }));
        } else {
            fill('viewers', ...list.map(w => {
                const port = Number(w.port);
                const linkable = Number.isInteger(port) && port > 0;
                const running = w.active === 'active';
                const name = w.label || w.name;
                // Front door: station-web's own /viewer/<name>/ (works through
                // any relay); the viewer's own port stays as a LAN-only link.
                // A name in a port conflict gets no link, only the warning below.
                const usable = running && linkable && !conflicted.has(w.name);
                const head = usable
                    ? el('a', { href: `/viewer/${encodeURIComponent(w.name)}/`, text: name })
                    : el('span', { text: name });
                const direct = usable
                    ? el('a', { cls: 'sp-muted', href: `http://${location.hostname}:${port}/`,
                                text: `direct :${port}`,
                                title: "LAN only: the viewer's own port" })
                    : null;
                const state = el('span', {
                    cls: running ? 'sp-green' : 'sp-amber',
                    text: running ? ' running' : ` ${w.active || 'state unknown'}`,
                });
                return el('div', { cls: 'sp-viewer sp-row' },
                          [head, direct && el('span', { text: ' ' }), direct, state,
                           el('span', { cls: 'sp-muted', text: w.description ? ` · ${w.description}` : '' })]);
            }));
        }
        const conflicts = Array.isArray(v.conflicts) ? v.conflicts : [];
        fill('viewer-conflicts', ...conflicts.map(c =>
            el('div', { cls: 'sp-note sp-warn',
                        text: `Port ${c.port} is claimed by ${(c.names || []).join(' and ')}` })));
    }

    function manifestCell(status) {
        if (status === null || status === undefined) return el('span', { cls: 'sp-amber', text: 'unknown' });
        if (status === 'match') return el('span', { cls: 'sp-green', text: 'match' });
        // superset: the checkout contains the pinned commit, a sanctioned case.
        if (status === 'superset') return el('span', { cls: 'sp-green', text: 'superset' });
        if (status === 'no_manifest') return el('span', { cls: 'sp-muted', text: 'no manifest' });
        // not_listed: the manifest does not pin this component; nothing to compare.
        if (status === 'not_listed') return el('span', { cls: 'sp-muted', text: 'not listed' });
        return el('span', { cls: 'sp-amber', text: String(status).replace(/_/g, ' ') });
    }

    /** Behind main: unknown when no components row backs it (a failed or
     *  missing snapshot), "never fetched" only when the row says so, and
     *  unknown again when the row was fetched but git could not count. */
    function behindText(row) {
        if (row.repo_known === false) return 'unknown';
        if (row.fetched_at === null || row.fetched_at === undefined) return 'never fetched';
        const ago = `fetched ${ageText(ageSince(row.fetched_at))} ago`;
        if (row.behind === null || row.behind === undefined) return `unknown, ${ago}`;
        return `${row.behind} behind, ${ago}`;
    }

    const NO_INSTANCES = 'no per-reporter instances';

    function instancesText(c) {
        if (!c) return 'unknown';
        if (c.rows === 0) return NO_INSTANCES;
        const n = v => (v === null || v === undefined ? 'unknown' : v);
        return `${n(c.configured)} configured · ${n(c.enabled)} enabled · ${n(c.active)} running`;
    }

    function instancesCell(c) {
        const text = instancesText(c);
        return el('span', { cls: text === NO_INSTANCES ? 'sp-muted' : '', text });
    }

    function checkCell(chk) {
        // null: the check snapshot holds no answer.  No line: nothing to show.
        if (!chk) return el('span', { cls: 'sp-amber', text: 'unknown' });
        if (!chk.id || !chk.mark) return el('span', { cls: 'sp-muted', text: '–' });
        const glyph = { ok: '✓ ok', wait: '… wait', FAIL: '✕ FAIL', '--': '– n/a' }[chk.mark] || chk.mark;
        const cls = { ok: 'sp-green', wait: 'sp-amber', FAIL: 'sp-red' }[chk.mark] || 'sp-muted';
        return el('a', { href: `/check#${encodeURIComponent(chk.id)}`, cls, text: glyph });
    }

    function renderClients(rows) {
        const body = document.querySelector('#clients tbody');
        if (!body) return;
        body.replaceChildren(...rows.map(r => {
            const name = el('td', { label: 'Client' }, [
                el('strong', { text: r.name }),
                r.description ? el('span', { cls: 'sp-desc', text: r.description }) : null]);
            const tr = el('tr', { cls: r.installed ? '' : 'sp-off' }, [
                name,
                el('td', { label: 'Installed', text: r.installed ? 'yes' : 'no' }),
                el('td', { label: 'Version', text: r.local || '' }),
                el('td', { label: 'Manifest' }, [manifestCell(r.manifest_status)]),
                el('td', { label: 'Behind main', text: r.installed ? behindText(r) : '' }),
                el('td', { label: 'Instances' }, [r.installed ? instancesCell(r.instances) : null]),
                el('td', { label: 'Check' }, [checkCell(r.check)]),
            ]);
            tr.setAttribute('data-installed', r.installed ? '1' : '0');
            return tr;
        }));
        applyInstalledOnly();
    }

    function applyInstalledOnly() {
        const box = document.getElementById('installed-only');
        const only = box && box.checked;
        document.querySelectorAll('#clients tbody tr').forEach(tr => {
            tr.style.display = only && tr.getAttribute('data-installed') === '0' ? 'none' : '';
        });
    }

    /** hs-uploader deletes each tick once it ships, so an empty spool is the
     *  usual healthy state; the page never calls it a stopped heartbeat. */
    function renderHeartbeat(hb) {
        const h = hb || { state: 'unavailable' };
        const stale = h.stale ? ' (snapshot out of date)' : '';
        let line;
        if (h.state === 'tick') {
            const when = h.emitted_at ? ` at ${utcText(h.emitted_at)}` : '';
            const reason = h.reason ? `: ${h.reason}` : '';
            line = el('div', { text: `Last heartbeat verdict ${h.verdict || 'unknown'}${reason}${when}${stale}` });
        } else if (h.state === 'no_tick') {
            line = el('div', { cls: 'sp-muted',
                text: `no heartbeat tick waiting (ticks leave the station once uploaded)${stale}` });
        } else if (h.state === 'failed') {
            line = el('div', { cls: 'sp-warn', text: `heartbeat snapshot failed: ${h.error || 'no reason given'}` });
        } else {
            line = el('div', { cls: 'sp-warn', text: 'heartbeat snapshot unavailable' });
        }
        fill('heartbeat', line);
    }

    /** A snapshot that ran but holds no data: the command failed.  The
     *  server's ``failed`` decides; an answer without it falls back to the
     *  error.  (An empty heartbeat spool carries an error, not a failure.) */
    function snapFailed(sn) {
        if (!sn || !sn.available) return false;
        return sn.failed === undefined ? !!sn.error : !!sn.failed;
    }

    function renderSnapshotFooter(snaps) {
        const names = Object.keys(snaps || {});
        const ages = names.filter(n => snaps[n].available).map(n =>
            `${n} ${ageText(snaps[n].age_s)}${snaps[n].stale ? ' (stale)' : ''}`
            + `${snapFailed(snaps[n]) ? ' (failed)' : ''}`);
        const lines = [];
        if (ages.length) lines.push(el('div', { text: `Snapshot ages: ${ages.join(' · ')}` }));
        names.filter(n => !snaps[n].available).forEach(n =>
            lines.push(el('div', { cls: 'sp-warn',
                                   text: `${n} snapshot unavailable; the command is ${snaps[n].command}` })));
        names.filter(n => snapFailed(snaps[n])).forEach(n => {
            const ex = snaps[n].exit === null || snaps[n].exit === undefined ? '' : `, exit ${snaps[n].exit}`;
            lines.push(el('div', { cls: 'sp-warn',
                text: `${n} snapshot failed${ex}: ${snaps[n].error || 'no data'} (command: ${snaps[n].command})` }));
        });
        fill('snapshot-footer', ...lines);
    }

    const TABLE_SNAPSHOTS = ['catalog', 'components', 'instances', 'manifest', 'check'];

    /** A warning above the client table when a snapshot it draws is stale or
     *  missing; the table dims.  Returns true when anything is out of date. */
    function renderTableWarning(snaps) {
        const lines = [];
        TABLE_SNAPSHOTS.forEach(n => {
            const sn = (snaps || {})[n];
            if (!sn || !sn.available) {
                lines.push(`${n} snapshot unavailable (command: ${sn ? sn.command : 'unknown'})`);
            } else if (snapFailed(sn)) {
                lines.push(`${n} snapshot failed: ${sn.error || 'no data'} (command: ${sn.command})`);
            } else if (sn.stale) {
                lines.push(`${n} snapshot is stale, ${ageText(sn.age_s)} old (command: ${sn.command})`);
            }
        });
        fill('clients-warning', ...lines.map(t => el('div', { text: t })));
        const box = document.getElementById('clients-warning');
        if (box) box.style.display = lines.length ? 'block' : 'none';
        const table = document.getElementById('clients');
        if (table) table.classList.toggle('sp-stale', lines.length > 0);
        return lines.length > 0;
    }

    /** Re-run `load` every REFRESH_MS, each run after the last one ends
     *  (recursive setTimeout, so runs never overlap); skip a turn while a
     *  Check-now poll is reading the same data. */
    function refreshEvery(load) {
        setTimeout(async () => {
            if (!checkNowPolling) await load();
            refreshEvery(load);
        }, REFRESH_MS);
    }

    function loadStationPage() {
        const box = document.getElementById('installed-only');
        if (box) {
            box.checked = window.innerWidth < SHORT_WIDTH;
            box.addEventListener('change', applyInstalledOnly);
        }
        loadStation().then(() => refreshEvery(loadStation));
    }

    async function loadStation() {
        try {
            const d = await getJSON('/api/status/station');
            fail('');
            fill('identity', document.createTextNode(identityText(d.identity || {})));
            renderViewers(d.viewers);
            renderTableWarning(d.snapshots);
            renderClients(d.rows || []);
            renderHeartbeat(d.heartbeat);
            renderSnapshotFooter(d.snapshots);
        } catch (e) {
            fail(`Could not load the station summary: ${e.message}`);
        }
    }

    // ── Check page ──────────────────────────────────────────────────────
    const VERDICTS = {
        PASS: ['ok', 'PASS'], WAIT: ['warn', 'WAIT'], FAIL: ['fault', 'FAIL'],
        STALE: ['warn', 'STALE'], NONE: ['unknown', 'no snapshot'],
    };
    const GLYPH = { ok: '✓', wait: '…', FAIL: '✕', '--': '–' };
    const MARK_CLASS = { ok: 'sp-green', wait: 'sp-amber', FAIL: 'sp-red', '--': 'sp-muted' };
    const FIX_SEP = '  Fix: ';

    function checkLine(ln) {
        const na = ln.mark === '--';
        let text = na ? 'not on this station' : String(ln.text || '');
        const at = text.indexOf(FIX_SEP);
        if (at >= 0) text = text.slice(0, at);
        const kids = [
            el('span', { cls: `sp-mark ${MARK_CLASS[ln.mark] || 'sp-muted'}`, text: GLYPH[ln.mark] || ln.mark }),
            el('strong', { text: ln.label || '' }),
            el('span', { text: ` ${text}` }),
        ];
        if (!na && ln.fix) kids.push(el('pre', {}, [el('code', { text: ln.fix })]));
        // The explanation page for this check id, in the "checks" docs root.
        // "other." ids name checks with no page of their own.
        if (ln.id && !String(ln.id).startsWith('other.')) {
            kids.push(el('div', {}, [el('a', {
                href: `/static/docs.html?root=checks&doc=${encodeURIComponent(ln.id + '.md')}`,
                text: 'What this means' })]));
        }
        const row = el('div', { cls: 'sp-line', id: ln.id || undefined }, kids);
        return row;
    }

    function renderCheck(d) {
        const [kind, word] = VERDICTS[d.verdict] || VERDICTS.NONE;
        const badge = document.getElementById('verdict');
        badge.className = `badge st-${kind}`;
        badge.textContent = word;
        const age = d.available ? ageText(d.age_s) : null;
        fill('check-age', document.createTextNode(age ? `checked ${age} ago` : 'no check has run'));
        fill('check-summary', document.createTextNode(
            d.error ? `snapshot error: ${d.error}` : (d.stale ? 'The last check is out of date' : 'Result of the last check')));

        if (!d.stages.length) {
            fill('stages', el('div', { cls: 'card' }, [el('div', { cls: 'sp-note',
                text: d.available ? 'the check reported no lines' : 'no check snapshot to show' })]));
        } else {
            fill('stages', ...d.stages.map(s => el('div', { cls: 'card' }, [
                el('div', { cls: 'card-header' }, [el('div', { cls: 'card-title', text: s.stage })]),
                ...s.lines.map(checkLine),
            ])));
        }

        const v = d.validate;
        if (v === null || v === undefined) {
            const vs = d.validate_snapshot || {};
            fill('validate', el('div', { cls: 'sp-note sp-warn',
                text: vs.stale ? 'the validation snapshot is out of date'
                    : (vs.error ? `validation snapshot error: ${vs.error}` : 'no validation snapshot') }));
        } else if (!v.length) {
            fill('validate', el('div', { cls: 'sp-note', text: 'no validation failures or warnings' }));
        } else {
            fill('validate', ...v.map(r => el('div', { cls: 'sp-line' }, [
                el('span', { cls: `sp-mark ${r.severity === 'fail' ? 'sp-red' : 'sp-amber'}`,
                             text: r.severity === 'fail' ? '✕' : '▲' }),
                el('strong', { text: r.rule || '' }),
                el('span', { text: ` ${r.message || ''}` }),
            ])));
        }
        // Only on the first draw: a refresh must not yank the reader back.
        if (!checkRendered) scrollToHash();
        checkRendered = true;
    }

    let checkRendered = false;

    function scrollToHash() {
        if (!location.hash) return;
        const t = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        if (t) t.scrollIntoView();
    }

    async function loadCheck() {
        try {
            const d = await getJSON('/api/status/check');
            fail('');
            renderCheck(d);
            return d;
        } catch (e) {
            fail(`Could not load the check: ${e.message}`);
            return null;
        }
    }

    async function checkNow(button) {
        const msg = document.getElementById('check-now-msg');
        button.disabled = true;
        checkNowPolling = true;
        const done = () => { button.disabled = false; checkNowPolling = false; };
        msg.textContent = 'asking for a fresh check...';
        // ``before`` comes only from a successful read; without it, success
        // needs a ran_at newer than the moment of the request.
        const before = await getJSON('/api/status/check').then(d => d.ran_at).catch(() => null);
        const requestedAt = Date.now();
        try {
            const r = await fetch('/api/status/check-now', { method: 'POST' });
            if (r.status !== 202) {
                let detail = '';
                try { detail = (await r.json()).detail || ''; } catch (e) { /* not JSON */ }
                msg.textContent = `could not request a check (HTTP ${r.status}${detail ? ': ' + detail : ''})`;
                done();
                return;
            }
        } catch (e) {
            msg.textContent = `could not request a check: ${e.message}`;
            done();
            return;
        }
        msg.textContent = before ? 'waiting for the new result...' : 'requested; waiting for a fresh result';
        const isNew = ran => {
            if (!ran) return false;
            if (before) return ran !== before;
            const t = Date.parse(ran);
            return !isNaN(t) && t > requestedAt;
        };
        const t0 = Date.now();
        const poll = async () => {
            const d = await loadCheck();
            if (d && isNew(d.ran_at)) {
                msg.textContent = 'updated';
                done();
            } else if (Date.now() - t0 > POLL_LIMIT_MS) {
                msg.textContent = 'no new result after 2 minutes; sigmond may be busy';
                done();
            } else {
                setTimeout(poll, POLL_MS);
            }
        };
        setTimeout(poll, POLL_MS);
    }

    async function loadCheckPage() {
        const btn = document.getElementById('check-now');
        if (btn) btn.addEventListener('click', () => checkNow(btn));
        await loadCheck();
        refreshEvery(loadCheck);
        try {
            const f = await getJSON('/api/features');
            if (f && f.science === true) {
                fill('science-link', el('a', { href: '/static/index.html', text: 'Open the Status page' }));
            }
        } catch (e) { /* no features answer: no science link */ }
    }

    function start() {
        const page = document.body.getAttribute('data-page');
        if (page === 'station') loadStationPage();
        else if (page === 'check') loadCheckPage();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
