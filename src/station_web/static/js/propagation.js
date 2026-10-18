// Propagation page (propagation.html): signal over the day, modes and MUF,
// the detection funnel, the path map, and time of flight (tof-view.js).
// Loads after common.js and physics-views.js.
//
// One station filter (All / WWV / WWVH / BPM) drives every section and
// lives in the URL as ?station=, so station.html?id=WWV lands here with
// WWV selected.  The prop* builders take API replies and return HTML or a
// Plotly figure without touching the DOM, so tests run them under node.
// Every API value inserted as HTML passes through escapeHtml; plotly draws
// the API's ISO-8601 UTC strings as written, so the axes read UTC.

const PROP_STATIONS = ['WWV', 'WWVH', 'BPM'];
const PROP_INK = '#e0e0e0';
const PROP_AXIS = '#94a3b8';
const PROP_GRID = '#334155';
// Propagation modes, one colour each; no station colour among them
const PROP_MODE_COLORS = {
    '1E': '#009E73', '1F': '#F0E442', '2F': '#D55E00', '3F': '#0072B2',
    'GW': '#8A93A3', 'unknown': '#4B5563',
    '1F2': '#F0E442', '2F2': '#D55E00', '3F2': '#0072B2', '4F2': '#1E3A8A',
    '2E': '#00684C', 'UNKNOWN': '#4B5563',
};

/** The station filter from a query string: a catalogue station or 'ALL'. */
function propStationFromQuery(search) {
    const m = /[?&]station=([^&#]*)/.exec(search || '');
    if (!m) return 'ALL';
    let v;
    try { v = decodeURIComponent(m[1]).toUpperCase(); } catch (e) { return 'ALL'; }
    return PROP_STATIONS.includes(v) ? v : 'ALL';
}

function propBaseLayout(ytitle, xRange) {
    return {
        paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a',
        font: { color: PROP_INK, size: 12 },
        margin: { l: 70, r: 20, t: 10, b: 40 },
        legend: { orientation: 'h', y: -0.2, bgcolor: 'rgba(0,0,0,0)' },
        xaxis: { type: 'date', title: 'UTC', gridcolor: PROP_GRID, color: PROP_AXIS,
                 range: xRange || undefined },
        yaxis: { title: ytitle, gridcolor: PROP_GRID, color: PROP_AXIS, zeroline: false },
    };
}

function _propStations(stats, station) {
    const names = Object.keys((stats && stats.stations) || {}).sort();
    return station && station !== 'ALL' ? names.filter(n => n === station) : names;
}

/** Hourly MUF per station path (L3C_propagation_stats). */
function propMufFigure(stats, station, xRange) {
    const data = _propStations(stats, station).map(st => {
        const s = stats.stations[st];
        return {
            x: s.time, y: s.muf_mhz, customdata: s.muf_confidence,
            mode: 'lines+markers', type: 'scatter', name: escapeHtml(st), connectgaps: false,
            line: { color: stationColor(st), width: 2, shape: 'hv' },
            marker: { color: stationColor(st), size: 5 },
            hovertemplate: '%{x}<br>MUF %{y:.1f} MHz<br>confidence %{customdata:.2f}<extra>' +
                           escapeHtml(st) + '</extra>',
        };
    });
    const layout = propBaseLayout('estimated MUF (MHz)', xRange);
    layout.yaxis.rangemode = 'tozero';
    return { data, layout };
}

/** Hourly mode shares for one station (or all stations pooled by n), stacked to 1. */
function propModeFigure(stats, station, xRange) {
    const order = (stats && stats.modes_order) || ['1E', '1F', '2F', '3F', 'GW', 'unknown'];
    const names = _propStations(stats, station);
    // pool the chosen stations hour by hour, weighted by n_observations
    const byTime = new Map();
    for (const st of names) {
        const s = stats.stations[st];
        s.time.forEach((t, i) => {
            const n = s.n_observations[i] || 0;
            const acc = byTime.get(t) || { n: 0, p: Object.fromEntries(order.map(k => [k, 0])) };
            acc.n += n;
            for (const k of order) {
                const v = (s.mode_probability[k] || [])[i];
                if (v !== null && v !== undefined) acc.p[k] += v * n;
            }
            byTime.set(t, acc);
        });
    }
    const times = [...byTime.keys()].sort();
    const data = order.map(k => ({
        x: times,
        y: times.map(t => { const a = byTime.get(t); return a.n ? a.p[k] / a.n : null; }),
        name: escapeHtml(k), type: 'scatter', mode: 'lines', stackgroup: 'modes', line: { width: 0.5, shape: 'hv' },
        fillcolor: withAlpha(PROP_MODE_COLORS[k] || '#888888', 0.75),
        marker: { color: PROP_MODE_COLORS[k] || '#888888' },
        hovertemplate: '%{x}<br>' + escapeHtml(k) + ' %{y:.2f}<extra></extra>',
    }));
    const layout = propBaseLayout('share of the hour', xRange);
    layout.yaxis.range = [0, 1];
    return { data, layout };
}

function _propBroadcasts(data, station) {
    const all = (data && data.broadcasts) || [];
    return station && station !== 'ALL' ? all.filter(b => b.station === station) : all;
}

function _propBroadcastLabel(b) {
    const f = (b.frequency_mhz === null || b.frequency_mhz === undefined) ? '' : ` ${b.frequency_mhz} MHz`;
    return `${b.station}${f} (${b.channel})`;
}

/** Horizontal stacked bars per broadcast: detected, then each rejection reason. */
function propFunnelFigure(data, station) {
    const rows = _propBroadcasts(data, station);
    // plotly renders a subset of HTML in category labels and trace names
    const labels = rows.map(b => escapeHtml(_propBroadcastLabel(b)));
    const reasons = (data && data.reasons_order) || [];
    const greys = ['#B45309', '#9CA3AF', '#6B7280', '#78716C', '#A8A29E', '#57534E', '#D6D3D1'];
    const traces = [{
        type: 'bar', orientation: 'h', name: 'detected', y: labels,
        x: rows.map(b => b.detected), marker: { color: '#009E73' },
        hovertemplate: '%{y}<br>detected %{x}<extra></extra>',
    }];
    reasons.forEach((r, i) => traces.push({
        type: 'bar', orientation: 'h', name: escapeHtml(r), y: labels,
        x: rows.map(b => (b.reasons || {})[r] || 0),
        marker: { color: greys[i % greys.length] },
        hovertemplate: '%{y}<br>' + escapeHtml(r) + ' %{x}<extra></extra>',
    }));
    return {
        data: traces,
        layout: {
            barmode: 'stack', paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a',
            font: { color: PROP_INK, size: 12 },
            margin: { l: 10, r: 20, t: 50, b: 40 },
            height: Math.max(200, 110 + 28 * rows.length),
            legend: { orientation: 'h', x: 0, y: 1.02, yanchor: 'bottom', traceorder: 'normal', bgcolor: 'rgba(0,0,0,0)' },
            xaxis: { title: 'attempts', gridcolor: PROP_GRID, color: PROP_AXIS },
            yaxis: { automargin: true, color: PROP_AXIS, autorange: 'reversed' },
        },
    };
}

/** The funnel as a table: attempts, detected, rate, and each rejection reason. */
function propFunnelTableHTML(data, station) {
    const rows = _propBroadcasts(data, station);
    const reasons = (data && data.reasons_order) || [];
    if (!rows.length) {
        return `<p class="muted">${escapeHtml((data && data.reason) || 'No attempts for this selection.')}</p>`;
    }
    const pct = v => (v === null || v === undefined) ? '—' : `${(100 * v).toFixed(0)} %`;
    const head = ['Broadcast', 'Attempts', 'Detected', 'Rate', ...reasons]
        .map(h => `<th>${escapeHtml(h)}</th>`).join('');
    const body = rows.map(b => '<tr>' +
        `<td>${escapeHtml(_propBroadcastLabel(b))}</td>` +
        `<td class="num">${escapeHtml(b.attempts)}</td>` +
        `<td class="num">${escapeHtml(b.detected)}</td>` +
        `<td class="num">${escapeHtml(pct(b.detection_rate))}</td>` +
        reasons.map(r => `<td class="num">${escapeHtml((b.reasons || {})[r] || 0)}</td>`).join('') +
        '</tr>').join('');
    return `<div class="prop-table-wrap"><table class="prop-table"><thead><tr>${head}</tr></thead>` +
           `<tbody>${body}</tbody></table></div>`;
}

/**
 * Receiver and transmitters on an azimuthal-equidistant map centred on the
 * receiver: every straight line through the centre reads as a great
 * circle, so BPM's path over the pole draws as the signal travels it.
 * Plotly draws each two-point 'lines' trace on scattergeo as a great circle.
 */
function propPathMapFigure(rx, stations, selected) {
    const data = [];
    for (const [st, c] of Object.entries(stations || {})) {
        const on = !selected || selected === 'ALL' || selected === st;
        const colour = stationColor(st);
        data.push({
            type: 'scattergeo', mode: 'lines', name: `${st} path`,
            lat: [rx.lat, c.lat], lon: [rx.lon, c.lon],
            line: { width: on ? 3 : 1, color: on ? colour : withAlpha(colour, 0.35) },
            hoverinfo: 'skip', showlegend: false,
        });
        data.push({
            type: 'scattergeo', mode: 'markers+text', name: st,
            lat: [c.lat], lon: [c.lon], text: [st], textposition: 'top center',
            marker: { size: on ? 11 : 7, color: colour, symbol: 'star' },
            textfont: { color: colour, size: 12 },
            hovertext: [c.label || st], hoverinfo: 'text',
        });
    }
    data.push({
        type: 'scattergeo', mode: 'markers+text', name: 'Receiver',
        lat: [rx.lat], lon: [rx.lon], text: [rx.short || 'RX'], textposition: 'bottom center',
        marker: { size: 11, color: '#ffffff', symbol: 'diamond' },
        textfont: { color: '#ffffff', size: 11 },
        hovertext: [rx.label || 'Receiver'], hoverinfo: 'text',
    });
    return {
        data,
        layout: {
            geo: {
                projection: { type: 'azimuthal equidistant',
                              rotation: { lon: rx.lon, lat: rx.lat, roll: 0 } },
                showland: true, landcolor: '#1e293b', showocean: true, oceancolor: '#0f172a',
                showcoastlines: true, coastlinecolor: '#475569', showcountries: true,
                countrycolor: '#334155', showlakes: false, bgcolor: '#0f172a',
                lataxis: { showgrid: true, gridcolor: '#1f2937' },
                lonaxis: { showgrid: true, gridcolor: '#1f2937' },
            },
            paper_bgcolor: 'transparent', font: { color: PROP_AXIS },
            margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false,
        },
    };
}

// ════════════════════════════════════════════════════════════════════
// Page wiring
// ════════════════════════════════════════════════════════════════════

const propState = {
    tps: null, station: 'ALL', freq: 'ALL', snrMin: 10,
    timeline: null, solar: null, stats: null, funnel: null, rx: null, stations: {},
};

function propFreqKey(f) {
    const known = [2.5, 5.0, 10.0, 15.0, 20.0, 25.0];
    return known.reduce((best, k) => Math.abs(f - k) < Math.abs(f - best) ? k : best, known[0]);
}

// The path map's coastlines come from the vendored topojson, never cdn.plot.ly
const PROP_MAP_CONFIG = { ...PLOTLY_CONFIG, topojsonURL: '/static/vendor/topojson/' };

function propDraw(id, fig, config) {
    const el = document.getElementById(id);
    if (!el) return;
    // a live plot updates in place; wiping its DOM would leave react a husk
    if (!el.classList.contains('js-plotly-plot')) el.innerHTML = '';
    Plotly.react(el, fig.data, fig.layout, config || PLOTLY_CONFIG);
}

function propSolarTrace(station) {
    const s = propState.solar;
    if (!s || !s.paths || !s.timestamps) return null;
    for (const p of Object.values(s.paths)) {
        if (p.station === station) {
            return { x: s.timestamps, y: p.elevation_deg, mode: 'lines', yaxis: 'y2',
                     name: `solar elevation (${station} path midpoint)`,
                     line: { color: '#eab308', width: 2, dash: 'dot' },
                     hovertemplate: '%{x}<br>solar elevation %{y:.1f}°<extra></extra>' };
        }
    }
    return null;
}

const PROP_SZA_AXIS = {
    title: 'solar elevation (°)', overlaying: 'y', side: 'right', range: [-90, 90],
    showgrid: false, zeroline: false, color: '#eab308',
};

function propRebuildFreqs() {
    const sel = document.getElementById('prop-freq');
    const tl = propState.timeline;
    const keep = sel.value;
    sel.innerHTML = '<option value="ALL">All frequencies</option>';
    if (!tl || !tl.timestamps) return;
    const freqs = new Set();
    tl.timestamps.forEach((_, i) => {
        if (propState.station === 'ALL' || tl.stations[i] === propState.station) {
            freqs.add(propFreqKey(tl.frequencies[i]));
        }
    });
    [...freqs].sort((a, b) => a - b).forEach(f => {
        const o = document.createElement('option');
        o.value = String(f);
        o.textContent = `${f} MHz`;
        sel.appendChild(o);
    });
    sel.value = [...sel.options].some(o => o.value === keep) ? keep : 'ALL';
    propState.freq = sel.value;
}

function propRenderSignal() {
    const tl = propState.timeline;
    const xRange = propState.tps.getXRange();
    if (!tl || !tl.timestamps || !tl.timestamps.length) {
        showPlotMessage('plot-snr', noDataReason(tl, 'No timing measurements in this window'));
        showPlotMessage('plot-residual', noDataReason(tl, 'No timing measurements in this window'));
        return;
    }
    const groups = {};
    for (let i = 0; i < tl.timestamps.length; i++) {
        const st = tl.stations[i];
        const fk = propFreqKey(tl.frequencies[i]);
        if (propState.station !== 'ALL' && st !== propState.station) continue;
        if (propState.freq !== 'ALL' && fk !== parseFloat(propState.freq)) continue;
        const snr = tl.snr_db[i];
        if (propState.snrMin > 0 && (snr === null || snr < propState.snrMin)) continue;
        const key = `${st} ${fk} MHz`;
        const g = groups[key] || (groups[key] = { ts: [], snr: [], res: [], st, fk });
        const r = tl.d_clock_ms ? tl.d_clock_ms[i] : null;
        g.ts.push(tl.timestamps[i]);
        g.snr.push(snr);
        g.res.push(r !== null && Math.abs(r) < 19.5 ? r : null);
    }
    const sza = propState.station !== 'ALL' ? propSolarTrace(propState.station) : null;
    const snrTraces = Object.entries(groups).map(([key, g]) => ({
        x: g.ts, y: g.snr, mode: 'markers', type: 'scattergl', name: escapeHtml(key),
        marker: { size: 3, color: freqColor(g.fk), opacity: 0.8 },
        hovertemplate: '%{x}<br>SNR %{y:.1f} dB<extra>' + escapeHtml(key) + '</extra>',
    }));
    if (!snrTraces.length) {
        showPlotMessage('plot-snr', 'No rows match this station, frequency and SNR floor');
        showPlotMessage('plot-residual', 'No rows match this station, frequency and SNR floor');
        return;
    }
    if (sza) snrTraces.push(sza);
    const snrLayout = propBaseLayout('SNR (dB)', xRange);
    snrLayout.margin.r = 60;
    snrLayout.yaxis2 = PROP_SZA_AXIS;
    propDraw('plot-snr', { data: snrTraces, layout: snrLayout });

    const resTraces = [];
    const sx = [], sy = [], sn = [];
    for (const [key, g] of Object.entries(groups)) {
        if (g.res.every(v => v === null)) continue;
        g.res.forEach((v, i) => { if (latticeSuspect(v)) { sx.push(g.ts[i]); sy.push(v); sn.push(key); } });
        resTraces.push({
            x: g.ts, y: g.res.map(v => latticeSuspect(v) ? null : v), mode: 'markers', type: 'scattergl',
            name: escapeHtml(key), marker: { size: 3, color: freqColor(g.fk), opacity: 0.8 },
            hovertemplate: '%{x}<br>residual %{y:.2f} ms<extra>' + escapeHtml(key) + '</extra>',
        });
    }
    if (sx.length) {
        resTraces.push({
            x: sx, y: sy, text: sn, mode: 'markers', type: 'scattergl', name: 'lattice-aligned (suspect)',
            marker: { size: 5, symbol: 'x-thin-open', color: '#94a3b8', line: { width: 1 } },
            hovertemplate: '%{x}<br>residual %{y:.2f} ms (on the tick lattice: suspect)<extra>%{text}</extra>',
        });
    }
    if (!resTraces.length) {
        showPlotMessage('plot-residual', 'No arrival residuals for this selection (too few tick detections)');
        return;
    }
    propDraw('plot-residual', { data: resTraces, layout: propBaseLayout('arrival residual (ms)', xRange) });
}

function propRenderModes() {
    const tl = propState.timeline;
    if (!tl || !tl.timestamps || !tl.timestamps.length) {
        showPlotMessage('plot-modes', noDataReason(tl, 'No mode assignments in this window'));
        return;
    }
    const byMode = {};
    tl.timestamps.forEach((t, i) => {
        if (propState.station !== 'ALL' && tl.stations[i] !== propState.station) return;
        const m = tl.modes[i] || 'UNKNOWN';
        const g = byMode[m] || (byMode[m] = { x: [], y: [] });
        g.x.push(t);
        g.y.push(escapeHtml(`${tl.stations[i]} ${propFreqKey(tl.frequencies[i])} MHz`));
    });
    const data = Object.entries(byMode).map(([m, g]) => ({
        x: g.x, y: g.y, mode: 'markers', type: 'scattergl', name: escapeHtml(m),
        marker: { size: 4, color: PROP_MODE_COLORS[m] || '#888888', opacity: 0.85 },
        hovertemplate: '%{x}<br>%{y}<br>mode ' + escapeHtml(m) + '<extra></extra>',
    }));
    if (!data.length) {
        showPlotMessage('plot-modes', 'No mode assignments for this station in this window');
        return;
    }
    const layout = propBaseLayout('', propState.tps.getXRange());
    layout.yaxis = { automargin: true, categoryorder: 'category ascending', color: PROP_AXIS,
                     gridcolor: PROP_GRID };
    propDraw('plot-modes', { data, layout });
}

function propRenderStats() {
    const s = propState.stats;
    if (!s || !s.available) {
        const why = noDataReason(s, 'Hourly statistics unavailable');
        showPlotMessage('plot-muf', why);
        showPlotMessage('plot-mode-prob', why);
        return;
    }
    const names = _propStations(s, propState.station);
    if (!names.length) {
        const why = s.reason || 'No hourly statistics for this station in this window';
        showPlotMessage('plot-muf', why);
        showPlotMessage('plot-mode-prob', why);
        return;
    }
    const xRange = propState.tps.getXRange();
    const muf = propMufFigure(s, propState.station, xRange);
    if (muf.data.every(t => (t.y || []).every(v => v === null))) {
        showPlotMessage('plot-muf', 'hamsci-physics published no MUF estimate for these hours');
    } else {
        propDraw('plot-muf', muf);
    }
    propDraw('plot-mode-prob', propModeFigure(s, propState.station, xRange));
    const label = propState.station === 'ALL' ? 'all stations, pooled by observation count' : propState.station;
    document.getElementById('mode-prob-who').textContent = label;
}

function propRenderFunnel() {
    const f = propState.funnel;
    const table = document.getElementById('funnel-table');
    if (!f || !f.available) {
        showPlotMessage('plot-funnel', noDataReason(f, 'Detection attempts unavailable'));
        table.innerHTML = '';
        return;
    }
    table.innerHTML = propFunnelTableHTML(f, propState.station);
    if (!_propBroadcasts(f, propState.station).length) {
        showPlotMessage('plot-funnel', f.reason || 'No attempts for this station in this window');
        return;
    }
    const el = document.getElementById('plot-funnel');
    const fig = propFunnelFigure(f, propState.station);
    el.style.height = `${fig.layout.height}px`;
    propDraw('plot-funnel', fig);
}

function propRenderMap() {
    if (!propState.rx) {
        showPlotMessage('plot-map', 'Receiver location unavailable (/api/station/metadata)');
        return;
    }
    propDraw('plot-map', propPathMapFigure(propState.rx, propState.stations, propState.station), PROP_MAP_CONFIG);
}

function propRenderAll() {
    propRenderSignal();
    propRenderModes();
    propRenderStats();
    propRenderFunnel();
    propRenderMap();
}

function propSetStation(st) {
    propState.station = PROP_STATIONS.includes(st) ? st : 'ALL';
    document.querySelectorAll('.prop-station-btn').forEach(b => {
        const on = b.dataset.station === propState.station;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    try {
        const url = new URL(window.location.href);
        if (propState.station === 'ALL') url.searchParams.delete('station');
        else url.searchParams.set('station', propState.station);
        history.replaceState(null, '', url.pathname + url.search + url.hash);
    } catch (e) { /* the filter still applies; only the URL stays behind */ }
    propRebuildFreqs();
    propRenderAll();
    if (window.TofView) TofView.setStation(propState.station);
}

async function propLoadGeometry() {
    const [meta, reg] = await Promise.all([
        loadStationMeta(),
        api.get('/stations/').catch(() => null),
    ]);
    if (meta && meta.latitude !== undefined && meta.longitude !== undefined) {
        propState.rx = { lat: meta.latitude, lon: meta.longitude, short: meta.callsign || 'RX',
                         label: `Receiver ${meta.callsign || ''} (${meta.grid_square || formatLatLon(meta.latitude, meta.longitude)})` };
    }
    propState.stations = {};
    for (const st of (reg && reg.stations) || []) {
        propState.stations[st.station_id] = {
            lat: st.coordinates.lat, lon: st.coordinates.lon,
            label: `${st.station_id} (${st.location || ''}), ${Math.round(st.distance_km)} km, az ${Math.round(st.azimuth_deg)}°`,
        };
    }
}

async function propLoadWindow() {
    const r = propState.tps.getAPIParams();
    const day = propState.tps.getRange().start.slice(0, 10).replace(/-/g, '');
    const [tl, solar, stats, funnel] = await Promise.all([
        api.get('/propagation/timeline', { start: r.start, end: r.end })
            .catch(e => ({ reason: 'Timeline unavailable: ' + e.message })),
        api.get('/dashboard/solar-zenith/24h', { date: day, interval_minutes: 10 }).catch(() => null),
        api.get('/propagation/stats', { start: r.start, end: r.end })
            .catch(e => ({ available: false, reason: 'Hourly statistics unavailable: ' + e.message })),
        api.get('/propagation/detections', { start: r.start, end: r.end })
            .catch(e => ({ available: false, reason: 'Detection attempts unavailable: ' + e.message })),
    ]);
    propState.timeline = tl;
    propState.solar = solar;
    propState.stats = stats;
    propState.funnel = funnel;
    const n = (tl && tl.count) || 0;
    document.getElementById('prop-count').textContent = String(n);
    propRebuildFreqs();
    propRenderAll();
}

function propReloadAll() {
    const tasks = [propLoadWindow()];
    if (window.TofView) tasks.push(TofView.reload());
    return Promise.all(tasks);
}

async function initPropagationPage() {
    propState.station = propStationFromQuery(window.location.search);
    propState.tps = new TimePeriodSelector('time-period-selector', propReloadAll);
    document.querySelectorAll('.prop-station-btn').forEach(b =>
        b.addEventListener('click', () => propSetStation(b.dataset.station)));
    document.getElementById('prop-freq').addEventListener('change', e => {
        propState.freq = e.target.value;
        propRenderSignal();
    });
    const snr = document.getElementById('prop-snr');
    snr.addEventListener('input', () => {
        propState.snrMin = parseFloat(snr.value);
        document.getElementById('prop-snr-label').textContent = `${snr.value} dB`;
        propRenderSignal();
    });
    // mark the filter without re-rendering: nothing has loaded yet
    document.querySelectorAll('.prop-station-btn').forEach(b => {
        const on = b.dataset.station === propState.station;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    await propLoadGeometry().catch(() => null);
    propRenderMap();
    const loads = [propLoadWindow()];
    if (window.TofView) loads.push(TofView.init(propState.tps, propState.station));
    await Promise.all(loads);
    // refresh every 5 minutes while the window reaches the present
    setInterval(() => {
        if (Date.parse(propState.tps.getRange().end) >= Date.now() - 60000) propReloadAll();
    }, 300000);
}

if (typeof document !== 'undefined' && document.getElementById) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPropagationPage);
    else initPropagationPage();
}
