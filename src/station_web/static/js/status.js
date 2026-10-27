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

    // ── small DOM helpers ───────────────────────────────────────────────
    function el(tag, opts, kids) {
        const n = document.createElement(tag);
        const o = opts || {};
        if (o.cls) n.className = o.cls;
        if (o.text !== undefined && o.text !== null) n.textContent = String(o.text);
        if (o.href) n.href = o.href;
        if (o.id) n.id = o.id;
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
            if (sm.behind === null || sm.behind === undefined) s += ' (never fetched)';
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
        if (!list.length) {
            fill('viewers', el('div', { cls: 'sp-note', text: 'no viewers on this station' }));
        } else {
            fill('viewers', ...list.map(w => {
                const running = w.active === 'active';
                const name = w.label || w.name;
                const head = running
                    ? el('a', { href: `http://${location.hostname}:${w.port}/`, text: name })
                    : el('span', { text: name });
                const state = el('span', {
                    cls: running ? 'sp-green' : 'sp-amber',
                    text: running ? ' running' : ` ${w.active || 'state unknown'}`,
                });
                return el('div', { cls: 'sp-viewer sp-row' },
                          [head, state, el('span', { cls: 'sp-muted', text: w.description ? ` · ${w.description}` : '' })]);
            }));
        }
        const conflicts = Array.isArray(v.conflicts) ? v.conflicts : [];
        fill('viewer-conflicts', ...conflicts.map(c =>
            el('div', { cls: 'sp-note sp-warn',
                        text: `Port ${c.port} is claimed by ${(c.names || []).join(' and ')}` })));
    }

    function manifestCell(status) {
        if (status === null || status === undefined) return el('span', { cls: 'sp-muted', text: '' });
        if (status === 'match') return el('span', { cls: 'sp-green', text: 'match' });
        if (status === 'no_manifest') return el('span', { cls: 'sp-muted', text: 'no manifest' });
        return el('span', { cls: 'sp-amber', text: String(status).replace(/_/g, ' ') });
    }

    function behindText(row) {
        if (row.fetched_at === null || row.fetched_at === undefined || row.behind === null
            || row.behind === undefined) return 'never fetched';
        return `${row.behind} behind, fetched ${ageText(ageSince(row.fetched_at))} ago`;
    }

    function instancesText(c) {
        if (!c) return 'unknown';
        return `${c.configured} configured · ${c.enabled} enabled · ${c.active} running`;
    }

    function checkCell(chk) {
        if (!chk || !chk.id) return el('span', { cls: 'sp-muted', text: '' });
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
                el('td', { label: 'Instances', text: r.installed ? instancesText(r.instances) : '' }),
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

    function renderHeartbeat(hb) {
        if (!hb) {
            fill('heartbeat', el('div', { text: 'heartbeat not running on this station' }));
            return;
        }
        const when = hb.emitted_at ? ` at ${utcText(hb.emitted_at)}` : '';
        const reason = hb.reason ? `: ${hb.reason}` : '';
        fill('heartbeat', el('div', { text: `Last heartbeat verdict ${hb.verdict || 'unknown'}${reason}${when}` }));
    }

    function renderSnapshotFooter(snaps) {
        const names = Object.keys(snaps || {});
        const ages = names.filter(n => snaps[n].available).map(n =>
            `${n} ${ageText(snaps[n].age_s)}${snaps[n].stale ? ' (stale)' : ''}`);
        const lines = [];
        if (ages.length) lines.push(el('div', { text: `Snapshot ages: ${ages.join(' · ')}` }));
        names.filter(n => !snaps[n].available).forEach(n =>
            lines.push(el('div', { cls: 'sp-warn',
                                   text: `${n} snapshot unavailable; the command is ${snaps[n].command}` })));
        fill('snapshot-footer', ...lines);
    }

    async function loadStation() {
        const box = document.getElementById('installed-only');
        if (box) {
            box.checked = window.innerWidth < SHORT_WIDTH;
            box.addEventListener('change', applyInstalledOnly);
        }
        try {
            const d = await getJSON('/api/status/station');
            fail('');
            fill('identity', document.createTextNode(identityText(d.identity || {})));
            renderViewers(d.viewers);
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
        scrollToHash();
    }

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
        msg.textContent = 'asking for a fresh check...';
        const before = await getJSON('/api/status/check').then(d => d.ran_at).catch(() => null);
        try {
            const r = await fetch('/api/status/check-now', { method: 'POST' });
            if (r.status !== 202) {
                let detail = '';
                try { detail = (await r.json()).detail || ''; } catch (e) { /* not JSON */ }
                msg.textContent = `could not request a check (HTTP ${r.status}${detail ? ': ' + detail : ''})`;
                button.disabled = false;
                return;
            }
        } catch (e) {
            msg.textContent = `could not request a check: ${e.message}`;
            button.disabled = false;
            return;
        }
        msg.textContent = 'waiting for the new result...';
        const t0 = Date.now();
        const timer = setInterval(async () => {
            const d = await loadCheck();
            if (d && d.ran_at !== before) {
                clearInterval(timer);
                msg.textContent = 'updated';
                button.disabled = false;
            } else if (Date.now() - t0 > POLL_LIMIT_MS) {
                clearInterval(timer);
                msg.textContent = 'no new result after 2 minutes; sigmond may be busy';
                button.disabled = false;
            }
        }, POLL_MS);
    }

    async function loadCheckPage() {
        const btn = document.getElementById('check-now');
        if (btn) btn.addEventListener('click', () => checkNow(btn));
        await loadCheck();
        try {
            const f = await getJSON('/api/features');
            if (f && f.science === true) {
                fill('science-link', el('a', { href: '/static/index.html', text: 'Open the Status page' }));
            }
        } catch (e) { /* no features answer: no science link */ }
    }

    function start() {
        const page = document.body.getAttribute('data-page');
        if (page === 'station') loadStation();
        else if (page === 'check') loadCheckPage();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
