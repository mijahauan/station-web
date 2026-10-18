/**
 * Common utilities for the station-web UI.
 *
 * Time: every printed time is UTC and says so ("Z" or "UTC").  Plotly draws
 * a JS Date in the browser's local zone, so plot x values go in as ISO
 * strings (plotTime), which plotly renders as written: UTC.
 */

// API client
class TimestdAPI {
    constructor(baseURL = '/api') {
        this.baseURL = baseURL;
    }
    
    async get(endpoint, params = {}) {
        // Ensure endpoint starts with /
        if (!endpoint.startsWith('/')) {
            endpoint = '/' + endpoint;
        }
        
        // Build full URL with base
        const fullPath = this.baseURL + endpoint;
        const url = new URL(fullPath, window.location.origin);
        
        Object.keys(params).forEach(key => 
            url.searchParams.append(key, params[key])
        );
        
        const response = await fetch(url);
        if (!response.ok) {
            // The server's JSON ``detail`` says why; statusText is empty
            // under HTTP/2, so never rely on it alone.
            let detail = '';
            try {
                const body = await response.json();
                detail = typeof body.detail === 'string' ? body.detail
                    : (body.detail ? JSON.stringify(body.detail) : (body.reason || ''));
            } catch (e) { /* not JSON */ }
            const err = new Error(`HTTP ${response.status}${detail ? ': ' + detail : (response.statusText ? ' ' + response.statusText : '')}`);
            err.status = response.status;
            err.detail = detail;
            throw err;
        }
        return response.json();
    }
}

// Global API instance
const api = new TimestdAPI();

// Time formatting — always UTC, always marked.
function _toDate(t) {
    if (t instanceof Date) return t;
    if (typeof t === 'number') return new Date(t < 1e11 ? t * 1000 : t);  // unix s or ms
    return new Date(t);
}

function _isoUTC(t) {
    const d = _toDate(t);
    return isNaN(d.getTime()) ? null : d.toISOString();
}

function formatTimestamp(iso8601) {
    if (!iso8601) return 'N/A';
    const s = _isoUTC(iso8601);
    return s ? s.slice(0, 19).replace('T', ' ') + ' UTC' : 'N/A';
}

function formatTime(iso8601) {
    if (!iso8601) return 'N/A';
    const s = _isoUTC(iso8601);
    return s ? s.slice(11, 19) + 'Z' : 'N/A';
}

function formatDate(iso8601) {
    if (!iso8601) return 'N/A';
    const s = _isoUTC(iso8601);
    return s ? s.slice(0, 10) : 'N/A';
}

/**
 * A plot x value as an ISO-8601 UTC string.  Accepts unix seconds, unix ms,
 * a Date or a date string.  Plotly drops the "Z" and draws the wall-clock
 * value as written, so the axis reads UTC whatever the browser's zone.
 */
function plotTime(t) {
    if (t === null || t === undefined) return null;
    return _isoUTC(t);
}

function timeAgo(iso8601) {
    if (!iso8601) return 'N/A';
    const date = new Date(iso8601);
    const seconds = Math.floor((new Date() - date) / 1000);
    
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * Escape a value for interpolation into innerHTML.  Every value that came
 * from an API (or an error message quoting one) passes through here.
 */
function escapeHtml(v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Coordinates, sign-aware: 38.92°N, 92.13°W
function formatLatLon(lat, lon, decimals = 2) {
    if (lat === null || lat === undefined || lon === null || lon === undefined) return 'N/A';
    const la = Number(lat), lo = Number(lon);
    return `${Math.abs(la).toFixed(decimals)}°${la >= 0 ? 'N' : 'S'}, ` +
           `${Math.abs(lo).toFixed(decimals)}°${lo >= 0 ? 'E' : 'W'}`;
}

// Number formatting
function formatNumber(value, decimals = 2) {
    if (value === null || value === undefined) return 'N/A';
    return Number(value).toFixed(decimals);
}

function formatFrequency(hz) {
    if (!hz) return 'N/A';
    const mhz = hz / 1e6;
    return `${mhz.toFixed(3)} MHz`;
}

// Status colors
const STATUS_COLORS = {
    'healthy': '#10b981',
    'degraded': '#f59e0b',
    'error': '#ef4444',
    'active': '#10b981',
    'inactive': '#94a3b8',
    'stale': '#f59e0b'
};

function getStatusColor(status) {
    return STATUS_COLORS[status] || '#94a3b8';
}

function getStatusClass(status) {
    return `status-${status.toLowerCase()}`;
}

// Error handling
function showError(message, containerId = 'error-container') {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = `
            <div class="error-message">
                <strong>Error:</strong> ${escapeHtml(message)}
            </div>
        `;
        container.style.display = 'block';
    } else {
        console.error(message);
    }
}

function clearError(containerId = 'error-container') {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '';
        container.style.display = 'none';
    }
}

// Loading indicator
function showLoading(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '<div class="loading"></div>';
    }
}

function hideLoading(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '';
    }
}

/**
 * Put a message INSIDE a chart frame (or any container), replacing a
 * "Loading..." placeholder or a stale plot.  kind: 'empty' | 'error'.
 */
function showPlotMessage(containerId, message, kind = 'empty') {
    const el = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
    if (!el) return;
    if (window.Plotly && el.classList && el.classList.contains('js-plotly-plot')) {
        try { Plotly.purge(el); } catch (e) { /* not a plot */ }
    }
    const color = kind === 'error' ? 'var(--quality-d, #ef4444)' : 'var(--text-secondary, #94a3b8)';
    const box = document.createElement('div');
    box.className = 'plot-message plot-message-' + kind;
    box.style.cssText = 'display:flex;align-items:center;justify-content:center;text-align:center;' +
        `min-height:inherit;height:100%;padding:24px;color:${color};font-size:14px;`;
    box.textContent = message;
    el.innerHTML = '';
    el.appendChild(box);
}

/** The reason an API gave for having no data, or a fallback. */
function noDataReason(data, fallback = 'No data for this window') {
    if (!data) return fallback;
    return data.reason || data.message || data.detail || data.note || fallback;
}

// Station identity: one cached fetch of /api/station/metadata per page.
let _stationMetaPromise = null;
function loadStationMeta() {
    if (!_stationMetaPromise) {
        _stationMetaPromise = api.get('/station/metadata').catch(e => {
            console.error('station metadata unavailable:', e);
            _stationMetaPromise = null;
            return null;
        });
    }
    return _stationMetaPromise;
}

/** Put the station callsign into the page title and header. */
async function applyStationIdentity() {
    const meta = await loadStationMeta();
    const callsign = meta && meta.callsign;
    if (!callsign) return;
    if (document.title.includes('station-web') && !document.title.includes(callsign)) {
        document.title = document.title.replace('station-web', `station-web ${callsign}`);
    }
    document.querySelectorAll('.station-callsign').forEach(el => { el.textContent = callsign; });
    const h1 = document.querySelector('.header h1');
    if (h1 && !h1.querySelector('.station-callsign')) {
        const badge = document.createElement('span');
        badge.className = 'station-callsign station-badge';
        badge.textContent = callsign;
        h1.appendChild(badge);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyStationIdentity);
} else {
    applyStationIdentity();
}

// Auto-refresh helper
class AutoRefresh {
    constructor(callback, interval = 60000) {
        this.callback = callback;
        this.interval = interval;
        this.timerId = null;
    }
    
    start() {
        this.callback();
        this.timerId = setInterval(() => this.callback(), this.interval);
    }
    
    stop() {
        if (this.timerId) {
            clearInterval(this.timerId);
            this.timerId = null;
        }
    }
    
    setInterval(interval) {
        this.interval = interval;
        if (this.timerId) {
            this.stop();
            this.start();
        }
    }
}

// Shared Plotly config — hide modebar to avoid overlapping time controls
const PLOTLY_CONFIG = { responsive: true, displayModeBar: false };

// Uniform plot margins so stacked plots align their x-axes
const PLOT_MARGINS = { l: 70, r: 70, t: 10, b: 40 };

/**
 * TimePeriodSelector — shared time-period navigation for stacked-plot pages.
 *
 * Default view: today 00:00-23:59 UTC.
 * Period buttons: 6h, 12h, 1d (default).
 * Step back / forward by the selected period.
 * Date picker for jumping to any day.
 *
 * Usage:
 *   const tps = new TimePeriodSelector('container-id', () => refreshAll());
 *   // In your plot code:
 *   const range = tps.getRange();       // { start: '...Z', end: '...Z' }
 *   const xRange = tps.getXRange();     // [startISO, endISO] for Plotly xaxis.range
 *   const apiParams = tps.getAPIParams(); // { start: 'ISO', end: 'ISO' }
 */
class TimePeriodSelector {
    constructor(containerId, onChange, options = {}) {
        this.container = document.getElementById(containerId);
        this.onChange = onChange;
        this.periods = options.periods || [
            { label: '6h', hours: 6 },
            { label: '12h', hours: 12 },
            { label: '1d', hours: 24 },
        ];
        this.defaultPeriodLabel = options.defaultPeriod || '1d';

        // State: anchor is the START of the window (UTC midnight by default)
        const now = new Date();
        this.anchorUTC = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
        this.periodHours = 24;

        // Set initial period
        const match = this.periods.find(p => p.label === this.defaultPeriodLabel);
        if (match) this.periodHours = match.hours;

        this._render();
    }

    _render() {
        if (!this.container) return;
        const c = this.container;
        c.innerHTML = '';
        c.style.cssText = 'display:flex; gap:8px; align-items:center; flex-wrap:wrap;';

        // Step back button
        const backBtn = document.createElement('button');
        backBtn.className = 'time-range-btn';
        backBtn.innerHTML = '&#9664;';
        backBtn.title = 'Step back';
        backBtn.addEventListener('click', () => this.step(-1));
        c.appendChild(backBtn);

        // Period buttons
        this.periods.forEach(p => {
            const btn = document.createElement('button');
            btn.className = 'time-range-btn';
            if (p.hours === this.periodHours) btn.classList.add('active');
            btn.textContent = p.label;
            btn.addEventListener('click', () => {
                this.periodHours = p.hours;
                // Re-anchor to day boundary when switching to 1d
                if (p.hours === 24) {
                    this.anchorUTC = new Date(Date.UTC(
                        this.anchorUTC.getUTCFullYear(),
                        this.anchorUTC.getUTCMonth(),
                        this.anchorUTC.getUTCDate()
                    ));
                }
                this._render();
                this.onChange();
            });
            c.appendChild(btn);
        });

        // Step forward button
        const fwdBtn = document.createElement('button');
        fwdBtn.className = 'time-range-btn';
        fwdBtn.innerHTML = '&#9654;';
        fwdBtn.title = 'Step forward';
        fwdBtn.addEventListener('click', () => this.step(1));
        c.appendChild(fwdBtn);

        // Separator
        const sep = document.createElement('span');
        sep.style.cssText = 'color:#475569; margin:0 4px;';
        sep.textContent = '|';
        c.appendChild(sep);

        // Date picker
        const dateInput = document.createElement('input');
        dateInput.type = 'date';
        dateInput.style.cssText = 'padding:4px 8px; background:var(--bg-surface,#1e293b); border:1px solid rgba(59,130,246,0.3); border-radius:6px; color:#e2e8f0; font-size:12px;';
        dateInput.value = this._dateStr(this.anchorUTC);
        dateInput.addEventListener('change', () => {
            const parts = dateInput.value.split('-');
            this.anchorUTC = new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2]));
            this.onChange();
        });
        c.appendChild(dateInput);
        this._dateInput = dateInput;

        // "Today" button
        const todayBtn = document.createElement('button');
        todayBtn.className = 'time-range-btn';
        todayBtn.textContent = 'Today';
        todayBtn.addEventListener('click', () => {
            const now = new Date();
            this.anchorUTC = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
            this.periodHours = 24;
            this._render();
            this.onChange();
        });
        c.appendChild(todayBtn);

        // Range label
        const label = document.createElement('span');
        label.style.cssText = 'color:#94a3b8; font-size:12px; margin-left:8px;';
        const r = this.getRange();
        const startD = new Date(r.start);
        const endD = new Date(r.end);
        const fmt = (d) => d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
        label.textContent = fmt(startD) + '  →  ' + fmt(endD);
        c.appendChild(label);
    }

    _dateStr(d) {
        return d.toISOString().slice(0, 10);
    }

    step(direction) {
        const ms = direction * this.periodHours * 3600000;
        this.anchorUTC = new Date(this.anchorUTC.getTime() + ms);
        this._render();
        this.onChange();
    }

    getRange() {
        const start = new Date(this.anchorUTC);
        const end = new Date(start.getTime() + this.periodHours * 3600000 - 1000); // -1s
        return {
            start: start.toISOString().replace('.000Z', 'Z'),
            end: end.toISOString().replace('.000Z', 'Z'),
        };
    }

    getXRange() {
        const r = this.getRange();
        return [r.start, r.end];
    }

    getAPIParams() {
        return this.getRange();
    }
}


// ════════════════════════════════════════════════════════════════════
// Palette: the one source of colour for every page.
//
// Stations keep a fixed colour everywhere (Okabe-Ito hues, safe for the
// common colour-vision deficiencies); a SHARED channel, where WWV, WWVH
// and BPM arrive on one carrier, draws as a neutral "composite".
// Frequencies and tiers each take one ordered ramp whose steps grow
// lighter in order, so the order survives in grey.  Status never rides
// on colour alone: statusBadge() always prints a glyph and a word.
// The backend's per-series colours (phase_service, routers/ionogram)
// copy PALETTE.station; tests/test_stage2_frontend.py holds them equal.
// ════════════════════════════════════════════════════════════════════
const PALETTE = Object.freeze({
    station: { WWV: '#56B4E9', WWVH: '#E69F00', BPM: '#CC79A7', composite: '#A0A7B4' },
    freqOrderMHz: [2.5, 5, 10, 15, 20, 25],
    // plasma, from its mid-dark end to its light end
    freqRamp: ['#B12A90', '#CC4778', '#E16462', '#F2844B', '#FCA636', '#F0F921'],
    tierOrder: ['T6', 'T5', 'T4', 'T3', 'T2', 'T1', 'T0'],
    // viridis, reversed: the best tier reads lightest
    tier: { T6: '#FDE725', T5: '#A0DA39', T4: '#4AC16D', T3: '#1FA187',
            T2: '#277F8E', T1: '#365C8D', T0: '#46327E' },
    tierNone: '#5B6472',
    status: { ok: '#009E73', warn: '#F0E442', fault: '#D55E00', unknown: '#8A93A3' },
    glyph: { ok: '✓', warn: '▲', fault: '✕', unknown: '○' },
});

/** A station's colour.  Takes 'WWV', a channel name ('WWV_10000'), or
 *  'SHARED_*' / 'composite' / anything else, which read as the neutral. */
function stationColor(name) {
    if (!name) return PALETTE.station.composite;
    const key = String(name).toUpperCase().split('_')[0];
    return PALETTE.station[key] || PALETTE.station.composite;
}

/** A frequency's colour on the ordered ramp: the nearest of 2.5…25 MHz. */
function freqColor(mhz) {
    const f = Number(mhz);
    if (mhz === null || mhz === undefined || !isFinite(f)) return PALETTE.station.composite;
    let best = 0;
    PALETTE.freqOrderMHz.forEach((g, i) => {
        if (Math.abs(g - f) < Math.abs(PALETTE.freqOrderMHz[best] - f)) best = i;
    });
    return PALETTE.freqRamp[best];
}

/** A tier's colour; null (no tier in force) gets its own grey. */
function tierColor(tier) {
    return (tier && PALETTE.tier[tier]) || PALETTE.tierNone;
}

/** '#rrggbb' as 'rgba(r,g,b,a)'. */
function withAlpha(hex, alpha) {
    const h = String(hex).replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** Map a verdict, status or state word to ok | warn | fault | unknown. */
function statusKind(word) {
    const w = String(word || '').toLowerCase();
    if (['valid', 'healthy', 'ok', 'active', 'authoritative', 'info'].includes(w)) return 'ok';
    if (['degraded', 'warn', 'warning', 'stale', 'unwitnessed', 'acquiring', 'silent',
         'candidate', 'witness'].includes(w)) return 'warn';
    if (['invalid', 'error', 'fault', 'critical', 'alarm', 'unlocked', 'disagree',
         'exceeded'].includes(w)) return 'fault';
    return 'unknown';
}

/** A status badge: glyph + word, so it never depends on colour alone. */
function statusBadge(kind, text) {
    const k = PALETTE.glyph[kind] ? kind : 'unknown';
    return `<span class="badge st-${k}"><span class="st-glyph" aria-hidden="true">` +
           `${PALETTE.glyph[k]}</span> ${escapeHtml(text)}</span>`;
}

// ════════════════════════════════════════════════════════════════════
// Site navigation: one list, rendered into <nav data-site-nav> on every
// page.  Add or rename a page here, nowhere else.  Under 768 px the nav
// folds behind a menu button (styles.css), so a phone's first screen
// shows data.  ``also`` lists other paths that light up the same entry.
// ════════════════════════════════════════════════════════════════════
const NAV_ITEMS = [
    { href: '/', label: 'Status', also: ['/index.html', '/static/index.html', '/static/health.html'] },
    { href: '/static/metrology.html', label: 'Timing', also: ['/static/chrony.html'] },
    { href: '/static/phase.html', label: 'Phase', also: ['/phase'] },
    { href: '/static/ionosphere.html', label: 'Observatory', also: ['/static/station.html'] },
    { href: '/static/ionogram.html', label: 'Ionogram' },
    { href: '/static/dtec.html', label: 'dTEC' },
    { href: '/static/test_signal.html', label: 'Test signal' },
    { href: '/static/grape.html', label: 'GRAPE', also: ['/grape'] },
    { href: '/static/docs.html', label: 'Docs' },
];

/** The nav links as HTML, the entry for ``pathname`` marked current. */
function siteNavHTML(pathname) {
    const path = pathname || '/';
    return NAV_ITEMS.map(item => {
        const current = item.href === path || (item.also || []).includes(path);
        return `<a href="${escapeHtml(item.href)}" class="nav-link${current ? ' active' : ''}"` +
               `${current ? ' aria-current="page"' : ''}>${escapeHtml(item.label)}</a>`;
    }).join('');
}

/** Fill <nav data-site-nav> and give it a menu button for narrow screens. */
function renderSiteNav() {
    const nav = document.querySelector('nav[data-site-nav]');
    if (!nav) return;
    nav.id = nav.id || 'site-nav';
    nav.innerHTML = siteNavHTML(window.location.pathname);
    const header = nav.closest('.header') || nav.parentElement;
    if (header && !header.querySelector('.nav-toggle')) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'nav-toggle';
        btn.setAttribute('aria-controls', nav.id);
        btn.setAttribute('aria-expanded', 'false');
        btn.textContent = '☰ Menu';
        btn.addEventListener('click', () => {
            const open = header.classList.toggle('nav-open');
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        nav.parentElement.insertBefore(btn, nav);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderSiteNav);
} else {
    renderSiteNav();
}

// ════════════════════════════════════════════════════════════════════
// Registration panel: draws GET /api/timing/registration.
//
//   registrationPanelHTML(data, 'compact' | 'full')  -> HTML string
//   renderRegistrationPanel(elOrId, data, variant)   -> sets innerHTML
//   loadRegistrationPanel(elOrId, variant)           -> fetch + render;
//                                                       resolves to data
//
// Every API value passes through escapeHtml.  A value hf-timestd does not
// publish prints "not published", never a number.
// ════════════════════════════════════════════════════════════════════
const NOT_PUBLISHED = 'not published';

/** A duration in seconds, in the unit that reads best (ns, µs, ms, s). */
function formatDuration(seconds, digits = 2) {
    const s = Number(seconds);
    if (seconds === null || seconds === undefined || !isFinite(s)) return NOT_PUBLISHED;
    const a = Math.abs(s);
    if (a === 0) return '0 s';
    if (a < 1e-6) return `${(s * 1e9).toFixed(0)} ns`;
    if (a < 1e-3) return `${(s * 1e6).toFixed(digits)} µs`;
    if (a < 1) return `${(s * 1e3).toFixed(digits)} ms`;
    return `${s.toFixed(digits)} s`;
}

const _UNIT_S = { ns: 1e-9, us: 1e-6, 'µs': 1e-6, ms: 1e-3, s: 1 };

/** {value, unit, field} -> "2.30 µs"; a missing value -> "not published". */
function formatMeasure(m) {
    if (!m || typeof m !== 'object' || m.value === null || m.value === undefined) return NOT_PUBLISHED;
    const scale = _UNIT_S[m.unit];
    const v = Number(m.value);
    if (!scale || !isFinite(v)) return `${escapeHtml(m.value)} ${escapeHtml(m.unit || '')}`.trim();
    return formatDuration(v * scale);
}

function _np(v) {
    return (v === null || v === undefined || v === '') ? NOT_PUBLISHED : v;
}

function _list(v) {
    return Array.isArray(v) && v.length ? v.map(escapeHtml).join(', ') : 'none';
}

function _fact(label, valueHtml) {
    return `<div class="reg-fact"><dt>${escapeHtml(label)}</dt><dd>${valueHtml}</dd></div>`;
}

function _ageText(age) {
    const a = Number(age);
    if (age === null || age === undefined || !isFinite(a)) return 'age unknown';
    return a < 120 ? `${a.toFixed(0)} s old` : `${(a / 60).toFixed(0)} min old`;
}

function registrationPanelHTML(data, variant = 'compact') {
    const full = variant === 'full';
    if (!data || data.available === false) {
        return `<div class="reg-panel reg-unavailable">` +
               `<div class="reg-tier-value">—</div>` +
               `<p>Timing authority not available on this station.</p>` +
               `<p class="reg-note">${escapeHtml((data && data.reason) || '')}</p></div>`;
    }
    const parts = [];
    if (data.stale) {
        parts.push(`<div class="reg-stale">${statusBadge('warn', 'STALE')} snapshot ` +
                   `${escapeHtml(_ageText(data.age_s))}; the values below may no longer hold</div>`);
    }

    // Tier in force, large; or None with the tiers available beneath.
    const tier = data.tier_active;
    parts.push(`<div class="reg-tier" style="border-left-color:${tierColor(tier)}">` +
        `<div class="reg-tier-label">Tier in force</div>` +
        `<div class="reg-tier-value">${tier ? escapeHtml(tier) : 'None'}</div>` +
        `<div class="reg-tier-sub">available: ${_list(data.tiers_available)}` +
        `${full ? ` · witnesses: ${_list(data.witnesses)}` : ''}</div></div>`);

    const facts = [];
    const sig = data.sigma || {};
    facts.push(_fact('Published sigma', `${escapeHtml(formatMeasure(sig))}` +
        (sig.field ? ` <span class="reg-src">published by <code>${escapeHtml(sig.field)}</code></span>` : '')));

    const reg = data.registration;
    if (reg) {
        facts.push(_fact('Registration',
            `${statusBadge(statusKind(reg.state), _np(reg.state))} source ` +
            `<code>${escapeHtml(_np(reg.source))}</code> · ${escapeHtml(_ageText(reg.age_s))}`));
    } else {
        facts.push(_fact('Registration', NOT_PUBLISHED));
    }

    const ruler = data.ruler || {};
    const disc = ruler.disciplined === true ? 'disciplined' : ruler.disciplined === false ? 'not disciplined' : '';
    facts.push(_fact('Ruler', `${escapeHtml(_np(ruler.a_level))}${disc ? ' ' + disc : ''} · ` +
        `provenance ${escapeHtml(_np(ruler.provenance))}`));

    const ce = data.counter_epoch_changes_24h || {};
    const ceText = (ce.count === null || ce.count === undefined)
        ? `changes in 24 h: ${escapeHtml(ce.reason || NOT_PUBLISHED)}`
        : `${escapeHtml(ce.count)} change${Number(ce.count) === 1 ? '' : 's'} in 24 h` +
          `${Number(ce.count) > 0 ? ' (a radiod restart renumbered the samples)' : ''}`;
    facts.push(_fact('Counter epoch', `<code>${escapeHtml(_np(reg && reg.counter_epoch_id))}</code> · ${ceText}`));

    const hc = data.host_clock;
    facts.push(_fact('Host clock', hc
        ? `${statusBadge(statusKind(hc.verdict), _np(hc.verdict))} ${escapeHtml(hc.reason || '')}`
        : NOT_PUBLISHED));

    const flags = data.disagreement_flags || [];
    facts.push(_fact('Disagreements', flags.length
        ? `<ul class="reg-flags">${flags.map(f =>
            `<li>${statusBadge('warn', 'flag')} ${escapeHtml(f.text || f.flag)}` +
            `${full && f.text !== f.flag ? ` <code>${escapeHtml(f.flag)}</code>` : ''}</li>`).join('')}</ul>`
        : 'none'));

    if (full) {
        const ue = data.u_epoch;
        facts.push(_fact('Epoch uncertainty', (ue && typeof ue === 'object')
            ? `${escapeHtml(formatMeasure(ue))} (k=${escapeHtml(_np(ue.k))}, p=${escapeHtml(_np(ue.p))})`
            : NOT_PUBLISHED));
        const st = data.stability;
        facts.push(_fact('Stability', (st && typeof st === 'object')
            ? `${escapeHtml(formatMeasure(st))} over τ = ${escapeHtml(_np(st.tau_s))} s`
            : NOT_PUBLISHED));
        if (reg) {
            facts.push(_fact('Registration detail',
                `sigma ${escapeHtml(formatMeasure({ value: reg.sigma_ms, unit: 'ms' }))}` +
                (reg.residual_vs_t6_ms !== undefined
                    ? ` · residual vs T6 ${escapeHtml(formatMeasure({ value: reg.residual_vs_t6_ms, unit: 'ms' }))}` : '') +
                ` · channels: ${_list(reg.contributing)} · stations: ${_list(reg.stations)}`));
        }
        const t6 = data.t6;
        facts.push(_fact('T6 anchor', t6
            ? `${statusBadge(statusKind(t6.authority_state), _np(t6.authority_state))}` +
              ` violations: ${_list(t6.violations)}` +
              (t6.suspect_criteria ? ` · suspect: ${escapeHtml(t6.suspect_criteria)}` : '')
            : NOT_PUBLISHED));
        // An unparseable time prints as sent, escaped, rather than "N/A".
        const lt = data.last_transition_utc;
        const ltText = !lt ? NOT_PUBLISHED : (formatTimestamp(lt) === 'N/A' ? lt : formatTimestamp(lt));
        facts.push(_fact('Last transition', escapeHtml(ltText)));
        facts.push(_fact('Governing radiod', escapeHtml(_np(data.governor_radiod))));
        facts.push(_fact('Snapshot', `${escapeHtml(formatTimestamp(data.utc_published))} · ${escapeHtml(_ageText(data.age_s))}`));
    }
    parts.push(`<dl class="reg-facts">${facts.join('')}</dl>`);

    if (full && hc && Array.isArray(hc.witnesses) && hc.witnesses.length) {
        parts.push(`<table class="table reg-witnesses"><thead><tr><th>Witness</th><th>Value</th>` +
            `<th>Bound</th><th>Verdict</th></tr></thead><tbody>` +
            hc.witnesses.map(w => {
                const over = w.exceeded === true;
                const num = (v) => (typeof v === 'number' && isFinite(v)) ? v.toFixed(3) : escapeHtml(_np(v));
                return `<tr><td>${escapeHtml(w.name)}</td><td>${num(w.value)} ${escapeHtml(w.unit || '')}</td>` +
                       `<td>${num(w.bound)} ${escapeHtml(w.unit || '')}</td>` +
                       `<td>${statusBadge(over ? 'fault' : (w.exceeded === false ? 'ok' : 'unknown'),
                                          over ? 'exceeded' : (w.exceeded === false ? 'within' : 'unknown'))}</td></tr>`;
            }).join('') + `</tbody></table>`);
    }
    return `<div class="reg-panel reg-${full ? 'full' : 'compact'}">${parts.join('')}</div>`;
}

function renderRegistrationPanel(elOrId, data, variant = 'compact') {
    const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
    if (el) el.innerHTML = registrationPanelHTML(data, variant);
}

async function loadRegistrationPanel(elOrId, variant = 'compact') {
    try {
        const data = await api.get('/timing/registration');
        renderRegistrationPanel(elOrId, data, variant);
        return data;
    } catch (e) {
        const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
        if (el) el.innerHTML = `<div class="reg-panel reg-unavailable">` +
            `${statusBadge('fault', 'ERROR')} ${escapeHtml(e.message)}</div>`;
        return null;
    }
}

// Export for use in other scripts
window.TimestdAPI = TimestdAPI;
window.api = api;
window.formatTimestamp = formatTimestamp;
window.formatTime = formatTime;
window.formatDate = formatDate;
window.plotTime = plotTime;
window.escapeHtml = escapeHtml;
window.formatLatLon = formatLatLon;
window.showPlotMessage = showPlotMessage;
window.noDataReason = noDataReason;
window.loadStationMeta = loadStationMeta;
window.applyStationIdentity = applyStationIdentity;
window.timeAgo = timeAgo;
window.formatNumber = formatNumber;
window.formatFrequency = formatFrequency;
window.getStatusColor = getStatusColor;
window.getStatusClass = getStatusClass;
window.showError = showError;
window.clearError = clearError;
window.showLoading = showLoading;
window.hideLoading = hideLoading;
window.AutoRefresh = AutoRefresh;
window.PLOTLY_CONFIG = PLOTLY_CONFIG;
window.PLOT_MARGINS = PLOT_MARGINS;
window.TimePeriodSelector = TimePeriodSelector;
window.PALETTE = PALETTE;
window.stationColor = stationColor;
window.freqColor = freqColor;
window.tierColor = tierColor;
window.withAlpha = withAlpha;
window.statusKind = statusKind;
window.statusBadge = statusBadge;
window.NAV_ITEMS = NAV_ITEMS;
window.siteNavHTML = siteNavHTML;
window.renderSiteNav = renderSiteNav;
window.formatDuration = formatDuration;
window.formatMeasure = formatMeasure;
window.registrationPanelHTML = registrationPanelHTML;
window.renderRegistrationPanel = renderRegistrationPanel;
window.loadRegistrationPanel = loadRegistrationPanel;
