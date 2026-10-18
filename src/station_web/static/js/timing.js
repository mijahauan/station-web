// Timing page (metrology.html): the registration, its estimators, the T3
// detail and the host clock.  Loaded after common.js.
//
// The builders below take an API response and return HTML or a Plotly
// figure, and touch no DOM, so tests run them under node.  Every API value
// that lands in HTML passes through escapeHtml.  Time axes take the
// API's ISO-8601 UTC strings as they come; plotly draws them as written,
// so the axes read UTC whatever the browser's zone.

const TIMING_ESTIMATORS = ['T6', 'T5', 'T4', 'T3'];
// FUSE carries the T3 registration and HPPS the T6 one, so each takes
// its tier's colour; the NTP pool draws grey.
const WITNESS_TIER = { FUSE: 'T3', HPPS: 'T6' };
const NTP_GREY = '#8A93A3';
const INK = '#e0e0e0';
const GRID = '#334155';
const AXIS_INK = '#94a3b8';

function _num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
}

function _fmtMs(v, digits = 3) {
    const n = _num(v);
    return n === null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(digits)} ms`;
}

function timingBaseLayout(xRange, ytitle) {
    return {
        paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a',
        font: { color: INK, size: 12 },
        margin: { l: 80, r: 30, t: 10, b: 40 },
        hovermode: 'x unified',
        legend: { orientation: 'h', y: -0.18, bgcolor: 'rgba(0,0,0,0)' },
        xaxis: { type: 'date', title: 'UTC', gridcolor: GRID, color: AXIS_INK,
                 range: xRange || undefined },
        yaxis: { title: ytitle, gridcolor: GRID, color: AXIS_INK, zeroline: false },
    };
}

/** Decade ticks over the finite positive values: {tickvals, ticktext}. */
function decadeTicks(values, label) {
    const pos = (values || []).filter(v => _num(v) !== null && v > 0);
    let lo = 0, hi = 1;
    if (pos.length) {
        lo = Math.floor(Math.log10(Math.min(...pos)));
        hi = Math.ceil(Math.log10(Math.max(...pos)));
    }
    if (hi - lo < 1) { lo -= 1; hi += 1; }
    const tickvals = [], ticktext = [];
    for (let k = lo; k <= hi; k++) {
        tickvals.push(Math.pow(10, k));
        ticktext.push(label(k));
    }
    return { tickvals, ticktext };
}

/** A decade of nanoseconds as a duration: 3 -> "1 µs". */
function nsDecadeLabel(k) {
    const units = ['ns', 'µs', 'ms', 's'];
    const u = Math.min(Math.max(Math.floor(k / 3), 0), units.length - 1);
    return `${Math.pow(10, k - 3 * u)} ${units[u]}`;
}

/** A power of ten as plotly text: -9 -> "10<sup>-9</sup>". */
function powerLabel(k) {
    return k === 0 ? '1' : k === 1 ? '10' : `10<sup>${k}</sup>`;
}

/** Runs of one tier in force: [{tier, x0, x1}].  A run ends where the next starts. */
function tierRuns(times, tiers) {
    const runs = [];
    (times || []).forEach((t, i) => {
        const tier = (tiers || [])[i] || null;
        const last = runs[runs.length - 1];
        if (last && last.tier === tier) {
            last.x1 = t;
        } else {
            if (last) last.x1 = t;
            runs.push({ tier, x0: t, x1: t });
        }
    });
    return runs;
}

/** The 24 h registration strip: tier band on top, published sigma beneath. */
function registrationStripFigure(h, xRange) {
    const time = h.time || [];
    const tiers = h.tier_active || [];
    const sigma = (h.sigma_ns || []).map(v => (_num(v) !== null && v > 0) ? v : null);
    const shapes = tierRuns(time, tiers).map(r => ({
        type: 'rect', xref: 'x', yref: 'paper', x0: r.x0, x1: r.x1, y0: 0.86, y1: 1.0,
        fillcolor: tierColor(r.tier), line: { width: 0 }, layer: 'below',
    }));
    const trans = h.transitions || [];
    trans.forEach(tr => shapes.push({
        type: 'line', xref: 'x', yref: 'paper', x0: tr.time, x1: tr.time, y0: 0, y1: 1,
        line: { color: INK, width: 1, dash: 'dot' },
    }));
    const data = [{
        x: time, y: sigma, type: 'scatter', mode: 'lines', name: 'published sigma (sigma_ns)',
        line: { color: INK, width: 1.5 }, connectgaps: false,
        customdata: sigma.map(v => v === null ? 'none' : formatDuration(v * 1e-9)),
        hovertemplate: 'sigma %{customdata}<extra></extra>',
    }, {
        x: time, y: time.map(() => 0.5), yaxis: 'y2', type: 'scatter', mode: 'markers',
        marker: { opacity: 0 }, showlegend: false, name: 'tier in force',
        text: tiers.map(t => t || 'None'), hovertemplate: 'tier in force: %{text}<extra></extra>',
    }];
    if (trans.length) {
        data.push({
            x: trans.map(t => t.time), y: trans.map(() => 0.5), yaxis: 'y2', type: 'scatter',
            mode: 'markers', name: 'transition', marker: { symbol: 'diamond', size: 9, color: INK },
            text: trans.map(t => `${escapeHtml(t.from || 'None')} → ${escapeHtml(t.to || 'None')}`),
            hovertemplate: 'transition %{text}<extra></extra>',
        });
    }
    const present = [...new Set(tiers.map(t => t || null))];
    PALETTE.tierOrder.concat([null]).filter(t => present.includes(t)).forEach(t => data.push({
        x: [null], y: [null], yaxis: 'y2', type: 'scatter', mode: 'markers',
        marker: { symbol: 'square', size: 12, color: tierColor(t) },
        name: t ? `${t} in force` : 'no tier in force', hoverinfo: 'skip',
    }));
    const layout = timingBaseLayout(xRange, 'published sigma');
    const ticks = decadeTicks(sigma, nsDecadeLabel);
    layout.yaxis = Object.assign(layout.yaxis, { type: 'log', domain: [0, 0.8],
        tickvals: ticks.tickvals, ticktext: ticks.ticktext });
    layout.yaxis2 = { domain: [0.86, 1], range: [0, 1], visible: false, fixedrange: true };
    layout.shapes = shapes;
    layout.hovermode = 'closest';
    return { data, layout };
}

/** One offset line with its own sigma band (two band traces, then the line). */
function bandTraces(x, y, sig, color, name, group, yaxis) {
    const lo = y.map((v, i) => (_num(v) !== null && _num(sig[i]) !== null) ? v - sig[i] : null);
    const hi = y.map((v, i) => (_num(v) !== null && _num(sig[i]) !== null) ? v + sig[i] : null);
    const common = { x, type: 'scatter', mode: 'lines', legendgroup: group,
                     showlegend: false, hoverinfo: 'skip', line: { width: 0, color } };
    if (yaxis) common.yaxis = yaxis;
    const line = { x, y, type: 'scatter', mode: 'lines', name, legendgroup: group,
                   line: { color, width: 1.5 }, customdata: sig.map(v => _num(v)),
                   hovertemplate: `${name}: %{y:.3f} ms ± %{customdata:.3f}<extra></extra>` };
    if (yaxis) line.yaxis = yaxis;
    return [
        Object.assign({}, common, { y: lo }),
        Object.assign({}, common, { y: hi, fill: 'tonexty', fillcolor: withAlpha(color, 0.2) }),
        line,
    ];
}

/** Each estimator's offset (T6, T5, T4, T3), each with its own sigma band. */
function estimatorOverlayFigure(h, xRange) {
    const time = h.time || [];
    const est = h.estimators || {};
    let data = [];
    TIMING_ESTIMATORS.forEach(T => {
        const e = est[T.toLowerCase()] || {};
        const off = e.offset_ms || [];
        if (!off.some(v => _num(v) !== null)) return;
        data = data.concat(bandTraces(time, off, e.sigma_ms || [], tierColor(T), T, T));
    });
    return { data, layout: timingBaseLayout(xRange, 'estimator offset (ms)') };
}

/** The T3 estimate: calibrated median with its band above, raw median below,
 *  suspects marked apart on the calibrated axis. */
function t3Figure(t3, xRange) {
    const time = t3.time || [];
    const c = tierColor('T3');
    let data = bandTraces(time, t3.calibrated_ms || [], t3.sigma_ms || [], c,
                          'calibrated, median (d_clock_fused_ms)', 'cal');
    data.push({
        x: time, y: t3.raw_ms || [], yaxis: 'y3', type: 'scatter', mode: 'lines',
        name: 'raw, median (d_clock_raw_ms)', line: { color: NTP_GREY, width: 1.5 },
        hovertemplate: 'raw: %{y:.3f} ms<extra></extra>',
    });
    const s = t3.suspects || {};
    const kinds = s.kind || [];
    [['lattice', 'x', PALETTE.status.fault, 'suspect: on the 5 ms tick lattice'],
     ['outlier', 'circle-open', PALETTE.status.warn, 'suspect: beyond 5 sigma of the rolling median']]
        .forEach(([kind, symbol, color, name]) => {
            const idx = kinds.map((k, i) => k === kind ? i : -1).filter(i => i >= 0);
            if (!idx.length) return;
            data.push({
                x: idx.map(i => s.time[i]), y: idx.map(i => s.value_ms[i]), type: 'scatter',
                mode: 'markers', name: `${name} (${idx.length})`,
                marker: { symbol, size: 8, color, line: { color, width: 1.5 } },
                customdata: idx.map(i => s.residual_ms[i]),
                hovertemplate: `${kind}: %{y:.3f} ms, %{customdata:+.2f} ms from the median<extra></extra>`,
            });
        });
    const layout = timingBaseLayout(xRange, 'calibrated (ms)');
    layout.yaxis.domain = [0.42, 1];
    layout.xaxis.anchor = 'y3';         // time ticks under the lower panel, not between
    layout.yaxis3 = { domain: [0, 0.34], title: 'raw (ms)', gridcolor: GRID, color: AXIS_INK,
                      anchor: 'x' };
    return { data, layout };
}

/** The T3 facts row: latest values, the calibration offset, the suspects. */
function t3FactsHTML(latest, t3) {
    const f = (label, value) =>
        `<div class="reg-fact"><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
    const facts = [];
    const l = latest || {};
    facts.push(f('Latest, calibrated', `${escapeHtml(_fmtMs(l.d_clock_ms))} <code>d_clock_fused_ms</code>`));
    facts.push(f('Latest, raw', `${escapeHtml(_fmtMs(l.d_clock_raw_ms))} <code>d_clock_raw_ms</code>`));
    facts.push(f('Sigma', `${escapeHtml(_fmtMs(l.uncertainty_ms).replace('+', '± '))} <code>uncertainty_ms</code>`));
    const cal = (t3 && t3.calibration) || {};
    const spread = (_num(cal.min_ms) !== null && _num(cal.max_ms) !== null)
        ? ` (range ${escapeHtml(_fmtMs(cal.min_ms))} to ${escapeHtml(_fmtMs(cal.max_ms))})` : '';
    facts.push(f('Calibration offset', _num(cal.offset_ms) === null ? 'none in this window'
        : `${escapeHtml(_fmtMs(cal.offset_ms))}${spread}: raw minus calibrated, the median over ` +
          `${escapeHtml(cal.n)} rows`));
    const stations = Array.isArray(l.stations_used) && l.stations_used.length
        ? l.stations_used.map(s => `<span style="color:${stationColor(s)}">${escapeHtml(s)}</span>`).join(', ')
        : 'none';
    facts.push(f('Broadcasts', `${escapeHtml(l.n_broadcasts ?? '—')} from ${stations}`));
    if (t3) {
        facts.push(f('Suspect points', `${escapeHtml(t3.n_suspect)} of ${escapeHtml(t3.n_rows)} rows` +
            (t3.suspects_truncated ? ' (drawn: an even sample)' : '')));
    }
    facts.push(f('Latest row', escapeHtml(formatTimestamp(l.timestamp))));
    return `<dl class="reg-facts">${facts.join('')}</dl>`;
}

/** Allan deviation layout: decade ticks on both log axes. */
function adevLayout(data) {
    const yt = decadeTicks(data.adev, powerLabel);
    const xt = decadeTicks(data.tau_seconds, k => k <= 3 ? `${Math.pow(10, k)} s` : `10<sup>${k}</sup> s`);
    return {
        paper_bgcolor: 'transparent', plot_bgcolor: '#0f172a', font: { color: INK, size: 12 },
        margin: { l: 80, r: 30, t: 10, b: 50 }, hovermode: 'closest', showlegend: false,
        xaxis: { title: 'averaging time τ', type: 'log', gridcolor: GRID, color: AXIS_INK,
                 tickvals: xt.tickvals, ticktext: xt.ticktext },
        yaxis: { title: 'Allan deviation σ<sub>y</sub>(τ)', type: 'log', gridcolor: GRID,
                 color: AXIS_INK, tickvals: yt.tickvals, ticktext: yt.ticktext },
    };
}

/** The host-clock chart: FUSE and HPPS as witnesses, the NTP pool as one band. */
function chronyEnvelopeFigure(env, xRange) {
    const time = env.time || [];
    const ntp = env.ntp || {};
    const data = [];
    const members = (ntp.members || []).length;
    if ((ntp.median_ms || []).some(v => _num(v) !== null)) {
        const base = { x: time, type: 'scatter', mode: 'lines', legendgroup: 'ntp',
                       showlegend: false, hoverinfo: 'skip', line: { width: 0, color: NTP_GREY } };
        data.push(Object.assign({}, base, { y: ntp.min_ms }));
        data.push(Object.assign({}, base, { y: ntp.max_ms, fill: 'tonexty',
                                            fillcolor: withAlpha(NTP_GREY, 0.25) }));
        data.push({ x: time, y: ntp.median_ms, type: 'scatter', mode: 'lines', legendgroup: 'ntp',
                    name: `NTP pool: min, median, max of ${members} source${members === 1 ? '' : 's'}`,
                    line: { color: NTP_GREY, width: 1 }, customdata: ntp.n_sources,
                    hovertemplate: 'NTP median %{y:.3f} ms (%{customdata} sources)<extra></extra>' });
    }
    Object.entries(env.witnesses || {}).forEach(([name, w]) => {
        if (!(w.offset_ms || []).some(v => _num(v) !== null)) return;
        const tier = WITNESS_TIER[name];
        data.push({ x: time, y: w.offset_ms, type: 'scatter', mode: 'lines',
                    name: `${escapeHtml(name)} (${tier || 'refclock'}): host clock minus registration, ` +
                          `${escapeHtml(w.role || 'noselect witness')}`,
                    line: { color: tierColor(tier), width: 1.5 },
                    hovertemplate: `${escapeHtml(name)} %{y:.3f} ms<extra></extra>` });
    });
    Object.entries(env.other_refclocks || {}).forEach(([name, w]) => {
        data.push({ x: time, y: w.offset_ms, type: 'scatter', mode: 'lines',
                    name: `${escapeHtml(name)} (refclock)`,
                    line: { color: PALETTE.station.composite, width: 1, dash: 'dash' },
                    hovertemplate: `${escapeHtml(name)} %{y:.3f} ms<extra></extra>` });
    });
    if ((env.system_offset_ms || []).some(v => _num(v) !== null)) {
        data.push({ x: time, y: env.system_offset_ms, type: 'scatter', mode: 'lines',
                    name: "chrony system offset: host clock minus chrony's selected source",
                    line: { color: INK, width: 1, dash: 'dot' },
                    hovertemplate: 'system %{y:.3f} ms<extra></extra>' });
    }
    return { data, layout: timingBaseLayout(xRange, 'local clock minus source (ms)') };
}

// chronyc source modes and states, in words
const CHRONY_MODES = { '#': 'refclock', '^': 'server', '=': 'peer' };
const CHRONY_STATES = { '*': ['ok', 'selected'], '+': ['ok', 'combined'], '-': ['unknown', 'not combined'],
                        'x': ['fault', 'falseticker'], '~': ['warn', 'too variable'], '?': ['unknown', 'unusable'] };

function _usAsMs(us, digits = 3) {
    const n = _num(us);
    return n === null ? '—' : `${n >= 0 ? '+' : ''}${(n / 1000).toFixed(digits)}`;
}

/** The live source table rows.  A witness that chrony selects breaks §7.1.1. */
function chronySourceRowsHTML(sources) {
    if (!sources || !sources.length) {
        return '<tr><td colspan="9" class="muted">No chrony sources in the latest rows</td></tr>';
    }
    return sources.map(s => {
        const st = CHRONY_STATES[s.state] || ['unknown', s.state || 'unknown'];
        const witness = Object.prototype.hasOwnProperty.call(WITNESS_TIER, s.name);
        let role;
        if (witness) {
            role = (s.state === '*' || s.state === '+')
                ? statusBadge('warn', 'witness, yet voting (§7.1.1 says noselect)')
                : statusBadge('ok', 'noselect witness');
        } else {
            role = escapeHtml(s.mode === '#' ? 'refclock' : 'NTP pool');
        }
        const freq = _num(s.frequency_ppm) === null ? '—'
            : `${s.frequency_ppm >= 0 ? '+' : ''}${s.frequency_ppm.toFixed(3)} ± ${escapeHtml(_num(s.freq_skew_ppm) === null ? '—' : s.freq_skew_ppm.toFixed(3))}`;
        return `<tr><td><strong>${escapeHtml(s.name)}</strong></td>` +
            `<td>${escapeHtml(CHRONY_MODES[s.mode] || s.mode || '')}</td>` +
            `<td>${role}</td>` +
            `<td>${statusBadge(st[0], st[1])}</td>` +
            `<td>${escapeHtml(s.stratum ?? '—')}</td>` +
            `<td class="num">${escapeHtml(_usAsMs(s.offset_us))}</td>` +
            `<td class="num">${escapeHtml(_usAsMs(s.error_us))} / ${escapeHtml(_usAsMs(s.std_dev_us).replace('+', ''))}</td>` +
            `<td class="num">${escapeHtml(freq)}</td>` +
            `<td>${escapeHtml(s.n_samples ?? '—')} · reach ${escapeHtml(_num(s.reach) === null ? '—' : s.reach.toString(8))}</td></tr>`;
    }).join('');
}

/** chrony's tracking line, in words. */
function chronyTrackingHTML(c) {
    if (!c) return '';
    const age = _num(c.age_s) === null ? 'no recent rows'
        : `snapshot ${escapeHtml(formatTimestamp(c.timestamp_utc))}, ${escapeHtml(Math.round(c.age_s))} s old`;
    const t = c.tracking;
    const stale = c.stale ? ` ${statusBadge('warn', 'STALE')}` : '';
    if (!t) return `<p class="muted">No tracking fields in the latest rows; ${age}${stale}</p>`;
    return `<p>chrony follows <code>${escapeHtml(t.reference_id || '—')}</code>; system offset ` +
        `${escapeHtml(_num(t.sys_offset_us) === null ? '—' : t.sys_offset_us.toFixed(1))} µs, ` +
        `RMS ${escapeHtml(_num(t.rms_offset_us) === null ? '—' : t.rms_offset_us.toFixed(1))} µs ` +
        `<span class="muted">(${age})</span>${stale}</p>`;
}

// ── page wiring ─────────────────────────────────────────────────────────

/** Draw into a chart frame, clearing any message or spinner first: plotly
 *  appends to the frame and would leave a spinner sitting above the plot. */
function drawPlot(id, fig) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!el.classList.contains('js-plotly-plot')) el.innerHTML = '';
    Plotly.react(el, fig.data, fig.layout, PLOTLY_CONFIG);
}

const timingState = { tps: null, adevRange: '-100000s' };

async function loadTimingRegistration() {
    try {
        const data = await api.get('/timing/registration');
        renderRegistrationPanel('registration-panel', data, 'full');
    } catch (e) {
        document.getElementById('registration-panel').innerHTML =
            `${statusBadge('fault', 'ERROR')} ${escapeHtml(e.message)}`;
    }
}

async function loadTimingStrip() {
    const range = timingState.tps.getAPIParams();
    const xRange = timingState.tps.getXRange();
    try {
        const h = await api.get('/timing/registration/history', { start: range.start, end: range.end });
        if (!h.available || !(h.time || []).length) {
            const msg = noDataReason(h, 'No registration history in this window');
            showPlotMessage('strip-plot', msg);
            showPlotMessage('estimator-plot', msg);
            return;
        }
        drawPlot('strip-plot', registrationStripFigure(h, xRange));
        const est = estimatorOverlayFigure(h, xRange);
        if (est.data.length) drawPlot('estimator-plot', est);
        else showPlotMessage('estimator-plot', 'No estimator published an offset in this window');
        document.getElementById('strip-note').textContent =
            `${h.n_rows} snapshots` + (h.bucket_s ? `, drawn as ${h.n_points} medians of ${Math.round(h.bucket_s)} s` : '') +
            `; ${(h.transitions || []).length} transition${(h.transitions || []).length === 1 ? '' : 's'}` +
            (h.transitions_truncated ? ' (first 500 marked)' : '');
    } catch (e) {
        showPlotMessage('strip-plot', `Error: ${e.message}`, 'error');
        showPlotMessage('estimator-plot', `Error: ${e.message}`, 'error');
    }
}

async function loadTimingT3() {
    const range = timingState.tps.getAPIParams();
    let latest = null;
    try { latest = await api.get('/metrology/fusion/latest'); } catch (e) { latest = null; }
    try {
        const t3 = await api.get('/metrology/t3/history', { start: range.start, end: range.end });
        document.getElementById('t3-facts').innerHTML = t3FactsHTML(latest, t3);
        if (!t3.available) {
            showPlotMessage('t3-plot', noDataReason(t3, 'No T3 rows in this window'));
            return;
        }
        drawPlot('t3-plot', t3Figure(t3, timingState.tps.getXRange()));
    } catch (e) {
        document.getElementById('t3-facts').innerHTML = t3FactsHTML(latest, null);
        showPlotMessage('t3-plot', `Error: ${e.message}`, 'error');
    }
}

async function loadTimingAdev(range) {
    const metrics = document.getElementById('adev-metrics');
    showPlotMessage('adev-plot', 'Computing the Allan deviation…');
    try {
        const data = await api.get('/stability/adev', { start: range, end: 'now' });
        drawPlot('adev-plot', {
            data: [{ x: data.tau_seconds, y: data.adev, type: 'scatter', mode: 'lines+markers',
                     line: { color: tierColor('T3'), width: 2 }, marker: { size: 6 },
                     hovertemplate: 'τ = %{x:.0f} s<br>σ<sub>y</sub> = %{y:.2e}<extra></extra>' }],
            layout: adevLayout(data),
        });
        const e = (v) => (_num(v) === null ? '—' : v.toExponential(2));
        metrics.innerHTML =
            `<dl class="reg-facts">` +
            `<div class="reg-fact"><dt>Dominant noise</dt><dd>${escapeHtml(data.dominant_noise || '—')}</dd></div>` +
            `<div class="reg-fact"><dt>σ<sub>y</sub> at τ = 60 s</dt><dd>${escapeHtml(e(data.adev_60s))}</dd></div>` +
            `<div class="reg-fact"><dt>σ<sub>y</sub> at τ = 1000 s</dt><dd>${escapeHtml(e(data.adev_1000s))}</dd></div>` +
            `<div class="reg-fact"><dt>Rows</dt><dd>${escapeHtml(data.n_points ?? '—')}</dd></div></dl>`;
    } catch (err) {
        metrics.innerHTML = '';
        showPlotMessage('adev-plot', `No Allan deviation for this window: ${err.message}`);
    }
}

async function loadTimingChrony() {
    const range = timingState.tps.getAPIParams();
    try {
        const c = await api.get('/chrony/comparison');
        document.getElementById('chrony-tracking').innerHTML = chronyTrackingHTML(c);
        document.getElementById('chrony-sources').innerHTML = chronySourceRowsHTML(c.sources);
    } catch (e) {
        document.getElementById('chrony-tracking').innerHTML =
            `${statusBadge('fault', 'ERROR')} ${escapeHtml(e.message)}`;
        document.getElementById('chrony-sources').innerHTML = '';
    }
    try {
        const env = await api.get('/chrony/envelope', { start: range.start, end: range.end });
        if (!env.available) {
            showPlotMessage('chrony-plot', noDataReason(env, 'No chrony rows in this window'));
            return;
        }
        drawPlot('chrony-plot', chronyEnvelopeFigure(env, timingState.tps.getXRange()));
    } catch (e) {
        showPlotMessage('chrony-plot', `Error: ${e.message}`, 'error');
    }
}

// ── WWVB: which minute WWVB named (GET /api/timing/wwvb) ─────────────

function _wwvbNum(v, digits) {
    const n = _num(v);
    return n === null ? '—' : n.toFixed(digits);
}

/** The WWVB ledger as HTML: its role, the last pass and frame, recent frames. */
function wwvbPanelHTML(data) {
    if (!data || data.available === false) {
        return `<p class="timing-note">${escapeHtml((data && data.reason) || 'No WWVB ledger on this station.')}</p>`;
    }
    const p = data.passes || {}, f = data.frames || {};
    const lp = p.last || {}, lf = f.last_plausible || null;
    const facts = [
        ['Last decoded minute', lf ? escapeHtml(lf.minute || '—') : 'none in this file'],
        ['Decoded minute minus host clock', lf ? `${escapeHtml(_wwvbNum(lf.vs_wallclock_s, 2))} s (<code>vs_wallclock_s</code>)` : '—'],
        ['Frames (plausible / all)', `${escapeHtml(f.plausible ?? 0)} / ${escapeHtml(f.n ?? 0)}`],
        ['Passes with a frame / all', `${escapeHtml(p.with_frames ?? 0)} / ${escapeHtml(p.n ?? 0)}`],
        ['Last pass SNR', `${escapeHtml(_wwvbNum(lp.snr_db, 1))} dB at ${escapeHtml(lp.ts || '—')}`],
        ['Ledger', `${escapeHtml(data.file || '—')}, newest line ${escapeHtml(data.age_s === null || data.age_s === undefined ? '—' : formatDuration(data.age_s))} old`],
    ].map(([k, v]) => `<div class="reg-fact"><dt>${escapeHtml(k)}</dt><dd>${v}</dd></div>`).join('');
    const rows = (data.recent_frames || []).map(fr => '<tr>' +
        `<td>${escapeHtml(fr.ts || '—')}</td><td>${escapeHtml(fr.minute || '—')}</td>` +
        `<td>${fr.plausible === true ? statusBadge('ok', 'plausible') : statusBadge('warn', 'implausible')}</td>` +
        `<td class="num">${escapeHtml(_wwvbNum(fr.vs_wallclock_s, 2))}</td>` +
        `<td class="num">${escapeHtml(fr.parity_errors ?? '—')} / ${escapeHtml(fr.sync_errors ?? '—')}</td></tr>`).join('');
    const table = rows
        ? `<div class="chrony-table-wrap"><table class="chrony-table"><thead><tr><th>Decoded at</th>` +
          `<th>Minute named</th><th>Frame</th><th>Minus host clock (s)</th><th>Parity / sync errors</th>` +
          `</tr></thead><tbody>${rows}</tbody></table></div>`
        : '<p class="timing-note">No frames in the newest ledger file.</p>';
    return `<p class="timing-note">${escapeHtml(data.role || '')}</p><dl class="reg-facts">${facts}</dl>${table}`;
}

async function loadTimingWwvb() {
    const el = document.getElementById('wwvb-panel');
    if (!el) return;
    try {
        el.innerHTML = wwvbPanelHTML(await api.get('/timing/wwvb', { n: 10 }));
    } catch (e) {
        el.innerHTML = `${statusBadge('fault', 'ERROR')} ${escapeHtml(e.message)}`;
    }
}

function loadTimingWindow() {
    return Promise.all([loadTimingStrip(), loadTimingT3(), loadTimingChrony()]);
}

function initTimingPage() {
    timingState.tps = new TimePeriodSelector('time-period-selector', loadTimingWindow);
    document.querySelectorAll('.adev-range-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.adev-range-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            timingState.adevRange = btn.dataset.range;
            loadTimingAdev(timingState.adevRange);
        });
    });
    // start() runs each loader once now, then on its interval.  The Allan
    // deviation costs a long read, so it reloads only on demand.
    new AutoRefresh(loadTimingRegistration, 30000).start();
    new AutoRefresh(loadTimingWindow, 120000).start();
    new AutoRefresh(loadTimingWwvb, 120000).start();
    loadTimingAdev(timingState.adevRange);
}

if (typeof document !== 'undefined' && document.getElementById) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initTimingPage);
    else initTimingPage();
}
