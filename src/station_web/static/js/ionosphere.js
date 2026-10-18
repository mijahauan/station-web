// Ionosphere page (ionosphere.html): space weather, Doppler and phase
// (phase-view.js), dTEC (dtec-view.js), and TEC with GNSS VTEC as its
// reference.  Loads after common.js and physics-views.js.
//
// The iono* builders take API replies and return HTML or a Plotly figure
// without touching the DOM, so tests run them under node.  Every API value
// inserted as HTML passes through escapeHtml.  A series no product backs
// (the X-ray and Kp series today) hides its chart rather than drawing an
// empty frame; the Kp, F10.7 and Ap snapshot shows as tiles.

const IONO_INK = '#e0e0e0';
const IONO_AXIS = '#94a3b8';
const IONO_GRID = '#334155';
// The reference line: white, dashed, so no station colour can be mistaken for it
const IONO_REFERENCE = '#F5F5F5';

function _ionoNum(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
}

/** Tiles for the Kp, F10.7 and Ap snapshot from /api/space-weather/current. */
function ionoSpaceWeatherTilesHTML(cur) {
    if (!cur || cur.available === false) {
        return `<p class="muted">${escapeHtml((cur && cur.reason) ||
            'No space-weather snapshot on this station (hamsci-physics caches one).')}</p>`;
    }
    const tile = (label, value, digits, time, source) => {
        const v = _ionoNum(value);
        const body = v === null
            ? '<div class="iono-tile-value muted">not available</div>'
            : `<div class="iono-tile-value">${escapeHtml(v.toFixed(digits))}</div>`;
        const meta = [time ? escapeHtml(time) : null, source ? `from ${escapeHtml(source)}` : null]
            .filter(Boolean).join(' · ');
        return `<div class="iono-tile"><div class="iono-tile-label">${escapeHtml(label)}</div>` +
               `${body}<div class="iono-tile-meta">${meta || '&nbsp;'}</div></div>`;
    };
    const stale = cur.stale
        ? `<p class="iono-stale">${statusBadge('warn', 'STALE')} snapshot ${escapeHtml(formatDuration(cur.age_s))} old</p>`
        : '';
    return stale + '<div class="iono-tiles">' +
        tile('Kp', cur.kp, 1, cur.kp_time, cur.kp_source) +
        tile('F10.7', cur.f107, 0, cur.f107_time, cur.f107_source) +
        tile('Ap', cur.ap, 0, cur.ap_time, cur.ap_source) + '</div>';
}

function _ionoHasSeries(s, field) {
    return !!(s && s.available !== false && Array.isArray(s.timestamps) && s.timestamps.length &&
              Array.isArray(s[field]) && s[field].length);
}

/** True when either the X-ray or the Kp series has data to draw. */
function ionoSeriesAvailable(xray, kp) {
    return _ionoHasSeries(xray, 'flux') || _ionoHasSeries(kp, 'kp');
}

/**
 * HF slant TEC per station (L3_tec) with GNSS vertical TEC over the
 * receiver (L3_gnss_vtec) as the reference line.  The two measure different
 * things (slant along an oblique HF path, vertical over the receiver), so
 * the reference reads as context, not as the same quantity.
 */
function ionoTecFigure(tec, vtec, xRange) {
    const data = [];
    for (const [st, p] of Object.entries((tec && tec.paths) || {})) {
        if (!p.timestamps || !p.timestamps.length) continue;
        data.push({
            x: p.timestamps, y: p.tec_tecu, mode: 'markers', type: 'scatter', name: st,
            customdata: (p.residual_rms_ms || []).map(v => _ionoNum(v) === null ? 'n/a' : `${v.toFixed(2)} ms`),
            marker: { size: 4, color: stationColor(st), opacity: 0.75 },
            hovertemplate: '%{x}<br>HF slant TEC %{y:.1f} TECU<br>fit RMS residual %{customdata}<extra>' +
                           escapeHtml(st) + '</extra>',
        });
    }
    const annotations = [];
    if (vtec && vtec.available && vtec.time && vtec.time.length) {
        data.push({
            x: vtec.time, y: vtec.vtec_tecu, mode: 'lines', type: 'scatter',
            name: escapeHtml(vtec.label || 'GNSS VTEC (reference)'),
            line: { color: IONO_REFERENCE, width: 2, dash: 'dash' },
            hovertemplate: '%{x}<br>GNSS VTEC %{y:.1f} TECU<extra>reference</extra>',
        });
    } else {
        annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: 1.02, xanchor: 'left', showarrow: false,
                           text: escapeHtml(`GNSS VTEC reference: ${(vtec && vtec.reason) || 'not available'}`),
                           font: { color: IONO_AXIS, size: 11 } });
    }
    return {
        data,
        layout: {
            paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a', font: { color: IONO_INK, size: 12 },
            margin: { l: 70, r: 20, t: 24, b: 40 }, annotations,
            legend: { orientation: 'h', y: -0.2, bgcolor: 'rgba(0,0,0,0)' },
            xaxis: { type: 'date', title: 'UTC', gridcolor: IONO_GRID, color: IONO_AXIS, range: xRange || undefined },
            yaxis: { title: 'TEC (TECU)', gridcolor: IONO_GRID, color: IONO_AXIS, zeroline: true },
        },
    };
}

/** The X-ray and Kp series, each on its own axis; only called with data. */
function ionoSpaceWeatherSeriesFigure(xray, kp, xRange) {
    const data = [];
    if (_ionoHasSeries(xray, 'flux')) {
        data.push({ x: xray.timestamps, y: xray.flux, mode: 'lines', name: 'GOES X-ray',
                    line: { color: '#D55E00', width: 1.5 }, yaxis: 'y' });
    }
    if (_ionoHasSeries(kp, 'kp')) {
        data.push({ x: kp.timestamps, y: kp.kp, type: 'bar', name: 'Kp', yaxis: 'y2',
                    marker: { color: '#8A93A3' }, width: 3 * 3600000 });
    }
    return {
        data,
        layout: {
            paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a', font: { color: IONO_INK, size: 12 },
            margin: { l: 70, r: 60, t: 10, b: 40 }, bargap: 0,
            legend: { orientation: 'h', y: -0.25, bgcolor: 'rgba(0,0,0,0)' },
            xaxis: { type: 'date', title: 'UTC', gridcolor: IONO_GRID, color: IONO_AXIS, range: xRange || undefined },
            yaxis: { title: 'X-ray (W/m²)', type: 'log', gridcolor: IONO_GRID, color: IONO_AXIS },
            yaxis2: { title: 'Kp', range: [0, 9], overlaying: 'y', side: 'right', showgrid: false, color: IONO_AXIS },
        },
    };
}

// ════════════════════════════════════════════════════════════════════
// Page wiring
// ════════════════════════════════════════════════════════════════════

const ionoState = { tps: null };

function ionoDraw(id, fig) {
    const el = document.getElementById(id);
    if (!el) return;
    // a live plot updates in place; wiping its DOM would leave react a husk
    if (!el.classList.contains('js-plotly-plot')) el.innerHTML = '';
    Plotly.react(el, fig.data, fig.layout, PLOTLY_CONFIG);
}

async function ionoLoadTiles() {
    const el = document.getElementById('sw-tiles');
    try {
        el.innerHTML = ionoSpaceWeatherTilesHTML(await api.get('/space-weather/current'));
    } catch (e) {
        el.innerHTML = `<p class="muted">Space weather unavailable: ${escapeHtml(e.message)}</p>`;
    }
}

async function ionoLoadSeries() {
    const xRange = ionoState.tps.getXRange();
    const [xray, kp] = await Promise.all([
        api.get('/space-weather/xray', { hours: 24 }).catch(() => null),
        api.get('/space-weather/kp', { hours: 72 }).catch(() => null),
    ]);
    const box = document.getElementById('sw-series-box');
    const note = document.getElementById('sw-series-note');
    if (!ionoSeriesAvailable(xray, kp)) {
        box.hidden = true;
        note.textContent = 'This station has no X-ray or Kp series product, so no series chart shows; ' +
                           'the tiles above hold the latest snapshot.';
        return;
    }
    box.hidden = false;
    note.textContent = '';
    ionoDraw('plot-space-weather', ionoSpaceWeatherSeriesFigure(xray, kp, xRange));
}

async function ionoLoadEvents() {
    const r = ionoState.tps.getAPIParams();
    const [tl, ev] = await Promise.all([
        api.get('/propagation/timeline', { start: r.start, end: r.end })
            .catch(e => ({ reason: 'Timeline unavailable: ' + e.message })),
        api.get('/physics/events/recent', { hours: 24 }).catch(() => null),
    ]);
    ionoState.timeline = tl;
    if (!tl || !tl.timestamps || !tl.timestamps.length) {
        showPlotMessage('plot-events-snr', noDataReason(tl, 'No timing measurements in this window'));
        return tl;
    }
    const by = {};
    tl.timestamps.forEach((t, i) => {
        const v = tl.snr_db[i];
        if (v === null) return;
        const g = by[tl.stations[i]] || (by[tl.stations[i]] = { x: [], y: [] });
        g.x.push(t);
        g.y.push(v);
    });
    const data = Object.entries(by).map(([st, g]) => ({
        x: g.x, y: g.y, mode: 'markers', type: 'scattergl', name: `${st} SNR`,
        marker: { size: 2.5, color: stationColor(st), opacity: 0.5 },
    }));
    const shapes = [];
    const events = ((ev && ev.events) || []).filter(e => e.type !== 'mode_change');
    for (const e of events.slice(0, 50)) {
        shapes.push({ type: 'line', x0: e.timestamp, x1: e.timestamp, y0: 0, y1: 1, yref: 'paper',
                      line: { color: '#D55E00', width: 1, dash: 'dot' } });
    }
    if (events.length) {
        data.push({ x: events.map(e => e.timestamp), y: events.map(() => null), mode: 'markers',
                    name: 'ionospheric events', text: events.map(e => escapeHtml(e.title || e.type)),
                    marker: { color: '#D55E00', symbol: 'triangle-down', size: 9 } });
    }
    ionoDraw('plot-events-snr', {
        data,
        layout: {
            paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a', font: { color: IONO_INK, size: 12 },
            margin: { l: 70, r: 20, t: 10, b: 40 }, shapes,
            legend: { orientation: 'h', y: -0.2, bgcolor: 'rgba(0,0,0,0)' },
            xaxis: { type: 'date', title: 'UTC', gridcolor: IONO_GRID, color: IONO_AXIS,
                     range: ionoState.tps.getXRange() },
            yaxis: { title: 'SNR (dB)', gridcolor: IONO_GRID, color: IONO_AXIS },
        },
    });
    return tl;
}

function _ionoLinReg(xs, ys) {
    const n = xs.length;
    const sx = xs.reduce((a, b) => a + b, 0), sy = ys.reduce((a, b) => a + b, 0);
    const sxy = xs.reduce((a, b, i) => a + b * ys[i], 0), sx2 = xs.reduce((a, b) => a + b * b, 0);
    const d = n * sx2 - sx * sx;
    if (d === 0) return null;
    const slope = (n * sxy - sx * sy) / d, intercept = (sy - slope * sx) / n;
    const mean = sy / n;
    const ssTot = ys.reduce((a, b) => a + (b - mean) ** 2, 0);
    const ssRes = xs.reduce((a, b, i) => a + (ys[i] - (slope * b + intercept)) ** 2, 0);
    return { slope, intercept, r2: ssTot > 0 ? 1 - ssRes / ssTot : 0, rms: Math.sqrt(ssRes / n) };
}

/** Arrival residual against 1/f² over the window's last hour, same-mode points only. */
function ionoRenderTecFit(tl) {
    if (!tl || !tl.timestamps || !tl.timestamps.length) {
        showPlotMessage('plot-tec-fit', noDataReason(tl, 'No timing measurements in this window'));
        return;
    }
    const last = Date.parse(tl.timestamps[tl.timestamps.length - 1]);
    const known = [2.5, 5, 10, 15, 20, 25];
    const fk = f => known.reduce((b, k) => Math.abs(f - k) < Math.abs(f - b) ? k : b, known[0]);
    const per = {};
    tl.timestamps.forEach((t, i) => {
        if (Date.parse(t) < last - 3600000) return;
        const r = tl.d_clock_ms ? tl.d_clock_ms[i] : null;
        if (r === null || Math.abs(r) >= 19.5) return;
        const st = tl.stations[i], mode = (tl.modes && tl.modes[i]) || 'UNKNOWN';
        ((per[st] = per[st] || {})[mode] = per[st][mode] || {});
        (per[st][mode][fk(tl.frequencies[i])] = per[st][mode][fk(tl.frequencies[i])] || []).push(r);
    });
    const data = [], annotations = [];
    for (const [st, modes] of Object.entries(per)) {
        let best = null, nBest = 0;
        for (const [m, fm] of Object.entries(modes)) {
            if (m !== 'UNKNOWN' && Object.keys(fm).length > nBest) { best = m; nBest = Object.keys(fm).length; }
        }
        const fm = modes[best || 'UNKNOWN'];
        if (!fm || Object.keys(fm).length < 2) continue;
        const xs = [], ys = [], labels = [];
        for (const [f, rs] of Object.entries(fm)) {
            const s = [...rs].sort((a, b) => a - b);
            xs.push(1 / (Number(f) ** 2));
            ys.push(s[Math.floor(s.length / 2)]);
            labels.push(`${f} MHz (n=${rs.length})`);
        }
        const fit = _ionoLinReg(xs, ys);
        if (!fit) continue;
        const tec = fit.slope / 1.344;
        data.push({ x: xs, y: ys, text: labels, mode: 'markers+text', textposition: 'top center',
                    name: escapeHtml(`${st} (mode ${best || 'UNKNOWN'})`), marker: { size: 9, color: stationColor(st) },
                    textfont: { size: 10, color: stationColor(st) } });
        const x0 = Math.min(...xs) * 0.8, x1 = Math.max(...xs) * 1.2;
        data.push({ x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
                    mode: 'lines', line: { color: stationColor(st), dash: 'dash', width: 1.5 },
                    name: `${st}: ${tec.toFixed(1)} TECU${tec < 0 ? ' (unphysical)' : ''}` });
        annotations.push({ x: (x0 + x1) / 2, y: fit.slope * (x0 + x1) / 2 + fit.intercept, yshift: 16,
                           showarrow: false, font: { color: IONO_AXIS, size: 10 },
                           text: `R²=${fit.r2.toFixed(2)}, RMS=${fit.rms.toFixed(2)} ms` });
    }
    if (!data.length) {
        showPlotMessage('plot-tec-fit', 'Too few same-mode, multi-frequency arrivals in the last hour');
        return;
    }
    ionoDraw('plot-tec-fit', {
        data,
        layout: {
            paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a', font: { color: IONO_INK, size: 12 },
            margin: { l: 70, r: 20, t: 10, b: 40 }, annotations,
            legend: { orientation: 'h', y: -0.25, bgcolor: 'rgba(0,0,0,0)' },
            xaxis: { title: '1/f² (MHz⁻²)', gridcolor: IONO_GRID, color: IONO_AXIS },
            yaxis: { title: 'arrival residual (ms)', gridcolor: IONO_GRID, color: IONO_AXIS },
        },
    });
}

async function ionoLoadTec() {
    const r = ionoState.tps.getAPIParams();
    const [tec, vtec] = await Promise.all([
        api.get('/propagation/tec', { start: r.start, end: r.end }).catch(() => null),
        api.get('/tec/gnss-vtec', { start: r.start, end: r.end })
            .catch(e => ({ available: false, reason: e.message })),
    ]);
    const fig = ionoTecFigure(tec, vtec, ionoState.tps.getXRange());
    const note = document.getElementById('vtec-note');
    note.textContent = (vtec && vtec.available)
        ? `GNSS VTEC: ${vtec.n_points} points from ${vtec.n_rows} rows` +
          (vtec.bucket_s ? `, medians over ${vtec.bucket_s} s` : '') +
          `; ${vtec.n_excluded_bad} rows flagged BAD left out.`
        : `GNSS VTEC reference: ${(vtec && vtec.reason) || 'not available'}`;
    if (!fig.data.length) {
        showPlotMessage('plot-tec-ts', 'No HF TEC and no GNSS VTEC in this window');
        return;
    }
    ionoDraw('plot-tec-ts', fig);
}

function ionoReloadAll() {
    const tasks = [ionoLoadSeries(), ionoLoadEvents().then(ionoRenderTecFit), ionoLoadTec()];
    if (window.PhaseView) tasks.push(PhaseView.refresh());
    if (window.DtecView) tasks.push(DtecView.reload());
    return Promise.all(tasks);
}

async function initIonospherePage() {
    ionoState.tps = new TimePeriodSelector('time-period-selector', ionoReloadAll);
    const tasks = [ionoLoadTiles(), ionoLoadSeries(), ionoLoadEvents().then(ionoRenderTecFit), ionoLoadTec()];
    if (window.PhaseView) tasks.push(PhaseView.init(ionoState.tps));
    if (window.DtecView) tasks.push(DtecView.init(ionoState.tps));
    await Promise.all(tasks);
    // refresh every 2 minutes while the window reaches the present
    setInterval(() => {
        ionoLoadTiles();
        if (Date.parse(ionoState.tps.getRange().end) >= Date.now() - 60000) ionoReloadAll();
    }, 120000);
}

if (typeof document !== 'undefined' && document.getElementById) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initIonospherePage);
    else initIonospherePage();
}
