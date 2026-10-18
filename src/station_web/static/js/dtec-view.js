// Phase-rate dTEC (the Ionosphere page's #dtec section; once dtec.html).
// Loads after common.js.  The page hands over its TimePeriodSelector
// through DtecView.init().  Wrapped in a function so its names stay out of
// the page's global scope.
(function () {
    // Station hue from the shared palette (common.js): a composite carrier takes
    // the neutral; frequency by dash pattern, so each path stays distinct.
    const FREQ_ORDER = [2.5, 5.0, 10.0, 15.0, 20.0, 25.0];
    const FREQ_DASH = ['solid', 'dot', 'dash', 'longdash', 'dashdot', 'longdashdot'];

    let _allSeries = null;
    let _refComparison = null;
    let _activeFreqFilter = 'all';
    let _timeRange = null;
    let _solarData = null;
    let _xrayData = null;
    let _tidData = null;

    // The page's TimePeriodSelector, handed over by init()
    let tps = null;

    // Path bearings, computed by the broadcast registry from this receiver
    async function loadBearings() {
        try {
            const reg = await api.get('/stations/');
            const parts = (reg.stations || []).map(st => `${st.station_id} (az ${Math.round(st.azimuth_deg)}°)`);
            if (parts.length > 0) document.getElementById('dtecPathBearings').textContent = parts.join(', ');
        } catch (e) { /* caption keeps its station names */ }
    }
    function toggleFreq(el) {
        document.querySelectorAll('#freqChips .freq-chip').forEach(c => c.classList.remove('active'));
        el.classList.add('active');
        _activeFreqFilter = el.dataset.freq;
        if (_allSeries) renderPlots(_allSeries);
    }

    function getSeriesDash(freqMhz) {
        const idx = FREQ_ORDER.findIndex(f => Math.abs(f - freqMhz) < 0.1);
        return FREQ_DASH[Math.max(0, idx) % FREQ_DASH.length];
    }

    function freqMatches(freqMhz) {
        if (_activeFreqFilter === 'all') return true;
        return Math.abs(freqMhz - parseFloat(_activeFreqFilter)) < 0.1;
    }

    async function getJSON(url) {
        try {
            const r = await fetch(url);
            return r.ok ? await r.json() : null;
        } catch (e) { return null; }
    }

    async function loadData() {
        const station = document.getElementById('dtecStationFilter').value;
        const minSnr = document.getElementById('dtecMinSnr').value;
        const ds = document.getElementById('dtecDownsample').value;

        const range = tps.getAPIParams();
        const startParam = range.start;
        const endParam = range.end;

        document.getElementById('dtecErrorMsg').style.display = 'none';
        document.getElementById('dtecStatsGrid').style.display = 'none';
        setLoading('dtecRatePlot');
        setLoading('refPlot');

        try {
            const params = { start: startParam, end: endParam, min_snr_db: minSnr, downsample: ds };
            if (station) params.station = station;
            const hours = Math.max(1, Math.ceil((new Date(endParam) - new Date(startParam)) / 3600000));
            const q = `start=${encodeURIComponent(startParam)}&end=${encodeURIComponent(endParam)}`;

            const [data, solar, xray, tid] = await Promise.all([
                api.get('/tec/dtec', params),
                getJSON(`/api/space-weather/solar/elevation?${q}&interval_minutes=5`),
                getJSON(`/api/space-weather/xray?hours=${hours}`),
                getJSON(`/api/tid/history?${q}`),
            ]);
            _solarData = solar;
            _xrayData = xray;
            _tidData = tid;

            if (data.status === 'no_data' || data.n_series === 0) {
                const why = noDataReason(data, 'No L3_dtec rows in this window');
                showPlotMessage('dtecRatePlot', why);
                showPlotMessage('refPlot', why);
                return;
            }

            _allSeries = data.series;
            _refComparison = data.reference_comparison;
            _timeRange = data.time_range;  // {start, end} ISO strings from API
            updateStats(data);
            renderPlots(data.series);

        } catch (e) {
            showPlotMessage('dtecRatePlot', 'Failed to load dTEC data: ' + e.message, 'error');
            showPlotMessage('refPlot', 'Failed to load dTEC data: ' + e.message, 'error');
        }
    }

    function setLoading(id) {
        document.getElementById(id).innerHTML = '<div class="loading-msg">Loading…</div>';
    }

    function showError(msg) {
        const el = document.getElementById('dtecErrorMsg');
        el.textContent = msg;
        el.style.display = 'block';
    }

    function updateStats(data) {
        document.getElementById('dtecStatsGrid').style.display = 'grid';
        document.getElementById('dtecStatPaths').textContent = data.n_series;
        document.getElementById('dtecStatRecords').textContent = String(data.n_points_total || 0);

        let nAnchored = 0, nTotal = 0;
        for (const s of Object.values(data.series)) {
            nAnchored += (s.is_anchored || []).filter(Boolean).length;
            nTotal += (s.is_anchored || []).length;
        }
        document.getElementById('dtecStatAnchored').textContent =
            nTotal > 0 ? Math.round(100 * nAnchored / nTotal) + '%' : '—';

        // the noise floor the server computed from this window, in the chart's unit
        const nf = data.noise_floor || {};
        const unit = nf.unit || 'TECU/s';
        document.getElementById('dtecStatNoise').textContent =
            (nf.value === null || nf.value === undefined) ? '—' : Number(nf.value).toExponential(1);
        document.getElementById('dtecStatNoiseSub').textContent = (nf.value === null || nf.value === undefined)
            ? `${unit}; ${nf.reason || 'not computed'}`
            : `${unit}, from ${nf.n_series} path${nf.n_series === 1 ? '' : 's'} (minute-to-minute differences)`;
    }

    function seriesStations(s) {
        return (s.stations_heard && s.stations_heard.length) ? s.stations_heard : [s.station];
    }

    function rateTrace(s, width, opacity) {
        return {
            x: s.timestamps.map(plotTime),
            y: s.dtec_rate_tecu_per_s,
            mode: 'lines',
            type: 'scattergl',
            name: s.label || `${s.station} ${s.frequency_mhz.toFixed(1)} MHz`,
            line: { color: stationColor(s.composite ? 'composite' : s.station), width,
                    dash: getSeriesDash(s.frequency_mhz) },
            opacity,
            hovertemplate: '%{y:.2e} TECU/s<extra>%{fullData.name}</extra>',
        };
    }

    function solarTraces(stationsNeeded, yaxis) {
        if (!_solarData || !_solarData.elevations || !_solarData.timestamps) return [];
        const xs = _solarData.timestamps.map(plotTime);
        const traces = [];
        for (const st of [...new Set(stationsNeeded)]) {
            if (!_solarData.elevations[st]) continue;
            traces.push({
                x: xs, y: _solarData.elevations[st],
                mode: 'lines', type: 'scatter',
                name: `Solar elev. ${st} path`,
                xaxis: 'x', yaxis,
                line: { color: withAlpha(stationColor(st), 0.8), width: 1.5 },
                hovertemplate: `%{y:.1f}°<extra>Solar ${st}</extra>`,
            });
        }
        return traces;
    }

    function xrayTrace(yaxis) {
        if (!_xrayData || !_xrayData.flux || _xrayData.flux.length === 0) return null;
        const xs = [], ys = [];
        const seen = new Set();
        for (let i = 0; i < _xrayData.flux.length; i++) {
            const f = _xrayData.flux[i];
            const t = _xrayData.timestamps[i];
            if (f && f > 0 && !seen.has(t)) {
                seen.add(t);
                xs.push(plotTime(t));
                ys.push(f);
            }
        }
        if (ys.length === 0) return null;
        return {
            x: xs, y: ys, mode: 'lines', type: 'scatter',
            name: 'GOES X-ray (0.1-0.8 nm)', xaxis: 'x', yaxis,
            line: { color: 'rgba(239, 68, 68, 0.8)', width: 1.5 },
            hovertemplate: '%{y:.2e} W/m²<extra>GOES X-ray</extra>',
        };
    }

    function tidTrace() {
        if (!_tidData || !_tidData.events || _tidData.events.length === 0) return null;
        const evs = _tidData.events;
        return {
            x: evs.map(e => plotTime(e.timestamp_utc)),   // L3_tid rows carry timestamp_utc
            y: evs.map(() => 0),
            mode: 'markers', type: 'scatter',
            name: 'TID detection',
            marker: { symbol: 'triangle-down', size: 10, color: 'rgba(168, 85, 247, 0.9)' },
            text: evs.map(e => `TID ${Number(e.velocity_m_s).toFixed(0)} m/s @ ${Number(e.direction_deg).toFixed(0)}°`),
            hoverinfo: 'text',
        };
    }

    const AXIS = { gridcolor: '#334155', tickfont: { size: 12 }, titlefont: { size: 13 } };

    function renderPlots(series) {
        const xRange = _timeRange ? [plotTime(_timeRange.start), plotTime(_timeRange.end)] : undefined;
        const baseLayout = {
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            font: { color: '#e2e8f0', size: 13 },
            legend: { bgcolor: 'transparent', font: { size: 12 }, orientation: 'v' },
            hovermode: 'x unified',
        };

        // Stacked panels sharing one time axis: dTEC rate, X-ray, solar elevation.
        const shown = Object.values(series).filter(s => freqMatches(s.frequency_mhz));
        const rateTraces = shown.map(s => rateTrace(s, 1.2, 0.7));
        if (rateTraces.length === 0) {
            showPlotMessage('dtecRatePlot', 'No series match the current frequency filter');
        } else {
            const extra = [];
            const tid = tidTrace();
            if (tid) extra.push(tid);
            // The X-ray panel shows only when a product backs it; with no
            // series the rate and solar panels take the height between them.
            const xr = xrayTrace('y2');
            if (xr) extra.push(xr);
            extra.push(...solarTraces(shown.flatMap(seriesStations), 'y3'));
            document.getElementById('dtecRatePlot').innerHTML = '';
            const note = document.getElementById('dtecXrayNote');
            if (note) note.textContent = xr ? '' : 'No GOES X-ray series product on this station, so the X-ray panel stays hidden.';
            Plotly.newPlot('dtecRatePlot', [...rateTraces, ...extra], {
                ...baseLayout,
                margin: { t: 10, b: 50, l: 90, r: 20 },
                xaxis: { ...AXIS, title: 'UTC', range: xRange, anchor: 'y3' },
                yaxis: { ...AXIS, domain: xr ? [0.44, 1] : [0.30, 1], title: 'dTEC/dt (TECU/s)', zeroline: true,
                         zerolinecolor: '#475569' },
                ...(xr ? { yaxis2: { ...AXIS, domain: [0.24, 0.40], type: 'log', range: [-9, -2],
                                     title: 'X-ray (W/m²)' } } : {}),
                yaxis3: { ...AXIS, domain: [0, xr ? 0.20 : 0.24], range: [-90, 90], title: 'Solar elev. (°)',
                          tickvals: [-90, -45, 0, 45, 90], zeroline: true,
                          zerolinecolor: 'rgba(255,220,80,0.35)' },
            }, { responsive: true, displayModeBar: false });
        }

        // Distinct-frequency comparison: only the paths the server picked
        const ref = _refComparison || { available: false, keys: [] };
        if (!ref.available) {
            showPlotMessage('refPlot', ref.reason || 'No distinct-frequency paths to compare on this station');
            return;
        }
        const refTraces = ref.keys.filter(k => series[k]).map(k => rateTrace(series[k], 2, 1));
        document.getElementById('refPlot').innerHTML = '';
        Plotly.newPlot('refPlot', refTraces, {
            ...baseLayout,
            margin: { t: 10, b: 50, l: 90, r: 20 },
            xaxis: { ...AXIS, title: 'UTC', range: xRange },
            yaxis: { ...AXIS, title: 'dTEC/dt (TECU/s)', zeroline: true, zerolinecolor: '#475569' },
        }, { responsive: true, displayModeBar: false });
    }

    // Take the page's TimePeriodSelector, wire the controls, then load
    function init(pageTps) {
        tps = pageTps;
        ['dtecStationFilter', 'dtecMinSnr', 'dtecDownsample'].forEach(id =>
            document.getElementById(id).addEventListener('change', loadData));
        document.querySelectorAll('#freqChips .freq-chip').forEach(el =>
            el.addEventListener('click', () => toggleFreq(el)));
        loadBearings();
        return loadData();
    }

    window.DtecView = { init, reload: () => loadData() };
})();
