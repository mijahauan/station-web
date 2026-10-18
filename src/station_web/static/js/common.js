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

// Quality grade colors
const QUALITY_COLORS = {
    'A': '#10b981',  // green
    'B': '#3b82f6',  // blue
    'C': '#f59e0b',  // orange
    'D': '#ef4444'   // red
};

function getQualityColor(grade) {
    return QUALITY_COLORS[grade] || '#94a3b8';
}

function getQualityClass(grade) {
    return `grade-${grade.toLowerCase()}`;
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
window.getQualityColor = getQualityColor;
window.getQualityClass = getQualityClass;
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
