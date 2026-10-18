// Doppler and phase (the Ionosphere page's #phase section; once phase.html).
// Loads after common.js and physics-views.js.  One /api/phase/combined read
// draws Doppler, phase variability, S4, carrier phase and DC phase; the
// page hands over its TimePeriodSelector through PhaseView.init().
// Wrapped in a function so its names stay out of the page's global scope.
(function () {
    // State
    let tps = null;  // the page's TimePeriodSelector, handed over by init()
    let solarData = null;  // cached solar zenith data

    // Build night-shade shapes from solar elevation for a given station
    function makeNightShapes(station) {
        if (!solarData || !solarData.paths || !solarData.timestamps) return [];
        // Find any path for this station (all freqs share same midpoint)
        let elevs = null;
        for (const [pid, pdata] of Object.entries(solarData.paths)) {
            if (pdata.station === station) { elevs = pdata.elevation_deg; break; }
        }
        if (!elevs) return [];
        const ts = solarData.timestamps;
        const shapes = [];
        let inNight = false, nightStart = null;
        for (let i = 0; i < ts.length; i++) {
            if (elevs[i] < 0 && !inNight) { inNight = true; nightStart = ts[i]; }
            else if (elevs[i] >= 0 && inNight) {
                inNight = false;
                shapes.push({
                    type: 'rect', xref: 'x', yref: 'paper',
                    x0: nightStart, x1: ts[i], y0: 0, y1: 1,
                    fillcolor: 'rgba(15,23,42,0.4)', line: { width: 0 }, layer: 'below'
                });
            }
        }
        if (inNight && nightStart) {
            shapes.push({
                type: 'rect', xref: 'x', yref: 'paper',
                x0: nightStart, x1: ts[ts.length - 1], y0: 0, y1: 1,
                fillcolor: 'rgba(15,23,42,0.4)', line: { width: 0 }, layer: 'below'
            });
        }
        return shapes;
    }

    // Build solar elevation trace for a given station
    function makeSolarTrace(station) {
        if (!solarData || !solarData.paths || !solarData.timestamps) return null;
        for (const [pid, pdata] of Object.entries(solarData.paths)) {
            if (pdata.station === station) {
                return {
                    x: solarData.timestamps,
                    y: pdata.elevation_deg,
                    mode: 'lines',
                    line: { color: '#eab308', width: 2, shape: 'spline' },
                    name: `Solar elev. (${station} path)`,
                    yaxis: 'y3',
                    showlegend: true,
                    hovertemplate: 'Solar: %{y:.1f}\u00b0<extra></extra>',
                };
            }
        }
        return null;
    }

    // A trace's legend name: its channel and whose carrier it measures
    // (a SHARED channel: "composite: WWV + WWVH + BPM")
    function traceName(s) {
        return escapeHtml(`${s.channel} / ${s.label || s.station}`);
    }

    // Collect unique stations from plot series and build combined shapes.
    // A composite trace contributes every station heard on its channel.
    function getSolarOverlay(series) {
        const stations = [...new Set((series || []).flatMap(
            s => (s.stations_heard && s.stations_heard.length) ? s.stations_heard : [s.station]))];
        // Use first station for night shading (they mostly overlap)
        const shapes = stations.length > 0 ? makeNightShapes(stations[0]) : [];
        // Add solar trace for each unique station
        const traces = [];
        const seen = new Set();
        for (const st of stations) {
            if (seen.has(st)) continue;
            seen.add(st);
            const t = makeSolarTrace(st);
            if (t) traces.push(t);
        }
        return { shapes, traces };
    }

    // Insert nulls at time gaps > 3× median interval to break connecting lines
    function breakGaps(timestamps, values, factor) {
        if (!factor) factor = 3;
        if (!timestamps || timestamps.length < 3) return { x: timestamps, y: values };
        // Compute median dt in ms from ISO strings
        const dts = [];
        for (let i = 1; i < timestamps.length; i++) {
            dts.push(Date.parse(timestamps[i]) - Date.parse(timestamps[i - 1]));
        }
        dts.sort((a, b) => a - b);
        const medianDt = dts[Math.floor(dts.length / 2)];
        const threshold = medianDt * factor;
        const x = [], y = [];
        for (let i = 0; i < timestamps.length; i++) {
            if (i > 0 && (new Date(timestamps[i]) - new Date(timestamps[i - 1])) > threshold) {
                x.push(timestamps[i]); y.push(null);  // gap marker
            }
            x.push(timestamps[i]); y.push(values[i]);
        }
        return { x, y };
    }

    // Solar elevation y-axis (shared across plots)
    const solarY3Axis = {
        title: '',
        overlaying: 'y',
        side: 'right',
        range: [-90, 90],
        showgrid: false,
        zeroline: false,
        tickvals: [0, 30, 60, 90],
        ticktext: ['0\u00b0', '30\u00b0', '60\u00b0', '90\u00b0'],
        tickfont: { color: '#eab308', size: 9 },
        automargin: false,
    };

    // Plotly dark theme layout with uniform margins
    const darkLayout = {
        paper_bgcolor: '#1e293b',
        plot_bgcolor: '#0f172a',
        font: { color: '#e0e0e0', size: 12 },
        xaxis: {
            gridcolor: '#334155',
            linecolor: '#475569',
            tickformat: '%H:%M',
        },
        yaxis: {
            gridcolor: '#334155',
            linecolor: '#475569',
        },
        margin: { ...PLOT_MARGINS, r: 105 },
        legend: {
            bgcolor: 'rgba(30,41,59,0.8)',
            font: { color: '#e0e0e0' },
        },
        hovermode: 'x unified',
    };

    // Populate channel and station dropdowns from /phase/channels
    async function populateFilters() {
        try {
            const data = await api.get('/phase/channels');
            const chSelect = document.getElementById('channel-select');
            (data.channels || []).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.channel;
                const label = c.dc_meaningful
                    ? `${c.channel}  (DC, ${c.stations.join('/')})`
                    : `${c.channel}  (${c.stations.join('/')})`;
                opt.textContent = label;
                chSelect.appendChild(opt);
            });
            const stSelect = document.getElementById('station-select');
            (data.stations || []).forEach(st => {
                const opt = document.createElement('option');
                opt.value = st;
                opt.textContent = st;
                stSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Could not populate filters:', e);
        }
    }

    // Get current filter params (absolute ISO timestamps from TimePeriodSelector)
    function getParams() {
        const params = tps.getAPIParams();
        const ch = document.getElementById('channel-select').value;
        const stn = document.getElementById('station-select').value;
        if (ch) params.channel = ch;
        if (stn) params.station = stn;
        return params;
    }

    // Update summary cards
    async function updateSummary() {
        try {
            const data = await api.get('/phase/summary');
            const container = document.getElementById('summary-cards');

            if (!data.channels || data.channels.length === 0) {
                container.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 20px; grid-column: 1/-1;">No L2_tick_phase rows in the last 5 minutes.</div>';
                return;
            }

            const num = (v, d) => (v === null || v === undefined) ? '—' : Number(v).toFixed(d);
            container.innerHTML = data.channels.map(ch => `
                <div style="background: var(--bg-primary); border-radius: var(--radius-md); padding: var(--spacing-md); border-left: 3px solid ${escapeHtml(ch.color)};">
                    <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 4px;">
                        ${escapeHtml(ch.channel)} / ${escapeHtml(ch.label)}
                        ${ch.dc_meaningful ? '<span style="color: #10b981; font-size: 11px;" title="DC carrier phase available"> &#x2713; DC</span>' : ''}
                    </div>
                    <div style="font-size: 20px; font-weight: 600; color: ${escapeHtml(ch.color)}; margin-bottom: 8px;" title="Doppler over the last ${escapeHtml(data.window_minutes)} min, the chart's estimator">
                        ${escapeHtml(num(ch.doppler_hz, 4))} Hz
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 12px; color: var(--text-secondary);">
                        <span title="phase variability (60 s detrend), mean over the last ${escapeHtml(data.window_minutes)} min">phase var. ${escapeHtml(num(ch.sigma_phi_rad, 3))} rad</span>
                        <span>SNR ${escapeHtml(num(ch.mean_snr_db, 1))} dB</span>
                    </div>
                    <div style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">
                        ${escapeHtml(ch.n_points)} pts &middot; ${ch.last_update ? escapeHtml(timeAgo(ch.last_update)) : 'N/A'}
                    </div>
                </div>
            `).join('');
        } catch (e) {
            console.warn('Summary error:', e);
        }
    }

    // Say how many seconds each plotted point averages
    function showBucket(meta) {
        const el = document.getElementById('bucket-note');
        if (!el) return;
        const b = meta && meta.bucket_seconds;
        el.textContent = b && b > 1 ? ` Each point averages ${b} s of 1 Hz samples.` : '';
    }

    // Fetch the phase plot alone (phase-type change); the server's
    // cache answers it from the pass the combined call already made
    async function updatePhasePlot() {
        try {
            const params = getParams();
            params.phase_type = document.getElementById('phase-type-select').value;
            params.unwrap = true;
            renderPhasePlot(await api.get('/phase/timeseries', params));
        } catch (e) {
            console.error('Phase plot error:', e);
        }
    }

    // Draw the phase plot
    function renderPhasePlot(data) {
        try {
            showBucket(data.metadata);
            const traces = (data.series || []).map((s, i) => {
                const g = breakGaps(s.timestamps, s.phase_rad);
                return {
                    x: g.x, y: g.y,
                    name: traceName(s),
                    type: 'scattergl',
                    mode: 'lines',
                    line: { color: s.color, width: 1.5 },
                    hovertemplate: '%{y:.3f} rad<extra>%{fullData.name}</extra>',
                    visible: i === 0 ? true : 'legendonly',
                };
            });

            if (traces.length === 0) {
                showPlotMessage('phase-plot', 'No phase data in selected range');
                return;
            }

            const solar = getSolarOverlay(data.series);
            traces.push(...solar.traces);
            const layout = {
                ...darkLayout,
                xaxis: { ...darkLayout.xaxis, range: tps.getXRange(), title: '' },
                yaxis: { ...darkLayout.yaxis, title: 'Phase (rad)' },
                yaxis3: solarY3Axis,
                shapes: solar.shapes,
                showlegend: true,
            };

            Plotly.react('phase-plot', traces, layout, PLOTLY_CONFIG);
        } catch (e) {
            console.error('Phase plot error:', e);
        }
    }

    // Draw the Doppler plot
    function renderDopplerPlot(data) {
        try {
            const traces = (data.series || []).map((s, i) => {
                const g = breakGaps(s.timestamps, s.doppler_hz);
                return {
                    x: g.x, y: g.y,
                    name: traceName(s),
                    type: 'scattergl',
                    mode: 'lines',
                    line: { color: s.color, width: 1.5 },
                    hovertemplate: '%{y:.4f} Hz<extra>%{fullData.name}</extra>',
                    visible: i === 0 ? true : 'legendonly',
                };
            });

            loadRulerFloor('doppler-ruler', (data.series || []).map(s => channelFreqMHz(s.channel)));
            if (traces.length === 0) {
                showPlotMessage('doppler-plot', 'No Doppler data in selected range');
                return;
            }

            const solar = getSolarOverlay(data.series);
            traces.push(...solar.traces);
            const layout = {
                ...darkLayout,
                xaxis: { ...darkLayout.xaxis, range: tps.getXRange(), title: '' },
                yaxis: { ...darkLayout.yaxis, title: 'Doppler (Hz)', zeroline: true, zerolinecolor: '#475569' },
                yaxis3: solarY3Axis,
                shapes: solar.shapes,
                showlegend: true,
            };

            Plotly.react('doppler-plot', traces, layout, PLOTLY_CONFIG);
        } catch (e) {
            console.error('Doppler plot error:', e);
        }
    }

    // Draw phase variability, and S4 in its own panel
    function renderScintillationPlot(data) {
        try {
            const xRange = tps.getXRange();
            const series = data.series || [];
            const varTraces = series.map((s, i) => ({
                x: s.timestamps,
                y: s.sigma_phi_rad,
                name: traceName(s),
                type: 'scattergl',
                mode: 'lines',
                line: { color: s.color, width: 1.5 },
                hovertemplate: '%{y:.3f} rad<extra>%{fullData.name}</extra>',
                visible: i === 0 ? true : 'legendonly',
            }));
            const s4Traces = series.filter(s => s.s4 && s.s4.some(v => v !== null)).map((s, i) => ({
                x: s.timestamps,
                y: s.s4,
                name: traceName(s),
                type: 'scattergl',
                mode: 'lines',
                line: { color: s.color, width: 1.5 },
                hovertemplate: 'S4 %{y:.3f}<extra>%{fullData.name}</extra>',
                visible: i === 0 ? true : 'legendonly',
            }));
            const solar = getSolarOverlay(series);

            if (varTraces.length === 0) {
                showPlotMessage('scintillation-plot', 'No phase variability data in selected range');
            } else {
                Plotly.react('scintillation-plot', [...varTraces, ...solar.traces], {
                    ...darkLayout,
                    xaxis: { ...darkLayout.xaxis, range: xRange, title: '' },
                    yaxis: { ...darkLayout.yaxis, title: 'phase variability (rad)', rangemode: 'tozero' },
                    yaxis3: solarY3Axis,
                    shapes: solar.shapes,
                    showlegend: true,
                }, PLOTLY_CONFIG);
            }

            if (s4Traces.length === 0) {
                showPlotMessage('s4-plot', 'No S4 data in selected range');
            } else {
                Plotly.react('s4-plot', s4Traces, {
                    ...darkLayout,
                    margin: { ...PLOT_MARGINS },
                    xaxis: { ...darkLayout.xaxis, range: xRange, title: '' },
                    yaxis: { ...darkLayout.yaxis, title: 'S4 (uncorrected for noise)', rangemode: 'tozero' },
                    shapes: solar.shapes,
                    showlegend: true,
                }, PLOTLY_CONFIG);
            }
        } catch (e) {
            console.error('Phase variability plot error:', e);
        }
    }

    // Draw the DC carrier phase plot (single-station channels only;
    // the server marks each series dc_meaningful from its channel name)
    function renderDCPhasePlot(data) {
        try {
            const filtered = (data.series || []).filter(s => s.dc_meaningful);

            const traces = filtered.map((s, i) => {
                const g = breakGaps(s.timestamps, s.phase_rad);
                return {
                    x: g.x, y: g.y,
                    name: traceName(s),
                    type: 'scattergl',
                    mode: 'lines',
                    line: { color: s.color, width: 1.5 },
                    hovertemplate: '%{y:.3f} rad<extra>%{fullData.name}</extra>',
                    visible: i === 0 ? true : 'legendonly',
                };
            });

            if (traces.length === 0) {
                showPlotMessage('dc-phase-plot', 'No DC carrier phase data (requires a single-station channel, e.g. WWV 20/25 MHz)');
                return;
            }

            const solar = getSolarOverlay(filtered);
            traces.push(...solar.traces);
            const layout = {
                ...darkLayout,
                xaxis: { ...darkLayout.xaxis, range: tps.getXRange(), title: 'UTC Time' },
                yaxis: { ...darkLayout.yaxis, title: 'DC Carrier Phase (rad)' },
                yaxis3: solarY3Axis,
                shapes: solar.shapes,
                showlegend: true,
            };

            Plotly.react('dc-phase-plot', traces, layout, PLOTLY_CONFIG);
        } catch (e) {
            console.error('DC phase plot error:', e);
        }
    }

    // Fetch solar zenith data for the current date
    async function fetchSolarData() {
        try {
            const range = tps.getRange();
            const dateStr = range.start.slice(0, 10).replace(/-/g, '');
            solarData = await api.get('/dashboard/solar-zenith/24h', {
                date: dateStr, interval_minutes: 10
            });
        } catch (e) {
            console.warn('Solar zenith fetch failed:', e);
            solarData = null;
        }
    }

    // Fetch phase, DC phase, Doppler and sigma_phi in one request: the
    // server computes all four from one read of the tick-phase table
    async function updateCombinedPlots() {
        try {
            const params = getParams();
            params.phase_type = document.getElementById('phase-type-select').value;
            params.smoothing = 30;
            params.window = 60;
            const data = await api.get('/phase/combined', params);
            renderPhasePlot(data.phase || {});
            renderDopplerPlot(data.doppler || {});
            renderScintillationPlot(data.scintillation || {});
            renderDCPhasePlot(data.dc_phase || {});
        } catch (e) {
            console.error('Phase plots error:', e);
        }
    }

    // Refresh all plots
    async function refreshAll() {
        await fetchSolarData();
        await Promise.all([
            updateSummary(),
            updateCombinedPlots(),
        ]);
    }

    // Take the page's TimePeriodSelector, wire the filters, then load
    function init(pageTps) {
        tps = pageTps;
        document.getElementById('phase-type-select').addEventListener('change', () => {
            updatePhasePlot();
        });
        document.getElementById('channel-select').addEventListener('change', refreshAll);
        document.getElementById('station-select').addEventListener('change', refreshAll);
        populateFilters();
        return refreshAll();
    }

    window.PhaseView = { init, refresh: () => refreshAll() };
})();
