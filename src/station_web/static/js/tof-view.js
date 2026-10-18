// Time of flight (the Propagation page's #time-of-flight section; once ionogram.html).
// Loads after common.js and physics-views.js.  The section keeps its own
// channel, station, SNR and downsample controls; the page hands it the one
// TimePeriodSelector and its station filter through TofView.init().
// Wrapped in a function so its names stay out of the page's global scope.
(function () {
    let _tsData = null;
    let _scatterData = null;
    let _dopplerData = null;

    function showTab(name) {
        document.querySelectorAll('.tof-tab-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === name);
        });
        document.querySelectorAll('.tof-tab-panel').forEach(p => {
            p.classList.toggle('active', p.id === 'tof-tab-' + name);
        });
        if (name === 'cluster' && _scatterData) renderCluster(_scatterData);
        if (name === 'doppler' && _dopplerData) renderDopplerDelay(_dopplerData);
    }

    // The page's TimePeriodSelector, handed over by init()
    let tps = null;
    // The page's station filter ('ALL' or a station); the station select follows it
    let _pageStation = 'ALL';

    // Channels and the stations heard on each, from /api/ionogram/channels
    let _channels = [];

    function fillStations() {
        const ch = _channels.find(c => c.channel === document.getElementById('channelSelect').value);
        const sel = document.getElementById('stationSelect');
        const stations = ch ? ch.stations : [];
        sel.innerHTML = '';
        if (stations.length !== 1) {
            const all = document.createElement('option');
            all.value = '';
            all.textContent = 'All on channel';
            sel.appendChild(all);
        }
        stations.forEach(st => {
            const opt = document.createElement('option');
            opt.value = st;
            opt.textContent = st;
            sel.appendChild(opt);
        });
        // Follow the page's station filter where the channel hears that station;
        // otherwise a SHARED channel (three stations mixed) defaults to WWV
        if (_pageStation !== 'ALL' && stations.includes(_pageStation)) sel.value = _pageStation;
        else if (stations.length > 1 && stations.includes('WWV')) sel.value = 'WWV';
    }

    function onChannelChange() {
        fillStations();
        loadData();
    }

    const PLOT_IDS = ['tofPlot', 'snrPlot', 'clusterPlot', 'dopplerDelayPlot', 'dopplerTsPlot'];

    async function loadData() {
        const channel = document.getElementById('channelSelect').value;
        const station = document.getElementById('stationSelect').value;
        const minSnr = document.getElementById('minSnr').value;
        const ds = document.getElementById('downsampleSelect').value;

        document.getElementById('errorMsg').style.display = 'none';
        document.getElementById('statsGrid').style.display = 'none';
        if (!channel) {
            PLOT_IDS.forEach(id => showPlotMessage(id, 'No channel has rows in L1_all_arrivals'));
            return;
        }

        const range = tps.getAPIParams();
        const common = { channel, start: range.start, end: range.end };
        if (station) common.station = station;
        ['tofPlot', 'snrPlot', 'clusterPlot'].forEach(setLoading);
        loadRulerFloor('dopplerRuler', [channelFreqMHz(channel)]);

        try {
            // One read serves all three views: three parallel day-long reads
            // once pushed station-web past its memory limit.
            const all = await api.get('/ionogram/combined', {
                ...common, min_snr_db: minSnr, downsample: ds,
                doppler_min_snr_db: 3, include_kde: true,
            });
            const ts = all.timeseries;
            const scatter = all.arrivals;
            _tsData = ts;
            _scatterData = scatter;
            _dopplerData = all.doppler_delay;

            if (ts.status === 'no_data' && scatter.status === 'no_data') {
                const why = noDataReason(ts, 'No data for this channel and window');
                ['tofPlot', 'snrPlot', 'clusterPlot'].forEach(id => showPlotMessage(id, why));
                renderDopplerDelay(_dopplerData);
                return;
            }

            updateStats(_tsData, _scatterData);
            renderTimeSeries(_tsData);
            renderCluster(_scatterData);
            renderDopplerDelay(_dopplerData);

        } catch (e) {
            PLOT_IDS.forEach(id => showPlotMessage(id, 'Failed to load: ' + e.message, 'error'));
        }
    }

    // Plotly draws beside a placeholder child rather than replacing it, so the
    // "Loading…" text would sit on top of the finished chart.
    function clearMsg(id) {
        document.getElementById(id)
            .querySelectorAll('.loading-msg, .plot-message')
            .forEach(el => el.remove());
    }

    function setLoading(id) {
        document.getElementById(id).innerHTML = '<div class="loading-msg">Loading…</div>';
    }

    function updateStats(ts, scatter) {
        document.getElementById('statsGrid').style.display = 'grid';
        document.getElementById('statTotal').textContent = String(scatter.n_points || 0);

        const r0 = scatter.rank_stats && scatter.rank_stats['0'];
        const r1n = Object.entries(scatter.rank_stats || {})
            .filter(([k]) => parseInt(k) >= 1)
            .reduce((s, [, v]) => s + v.n, 0);

        document.getElementById('statRank0').textContent = r0 ? String(r0.n) : '—';
        document.getElementById('statRank0Tof').textContent = r0 ? `ToF ${r0.tof_mean_ms.toFixed(1)} ± ${r0.tof_std_ms.toFixed(1)} ms` : '—';
        document.getElementById('statRankN').textContent = String(r1n);
    }

    function renderTimeSeries(data) {
        const dom = data.dominant;
        const sec = data.secondary;
        const solar = data.solar;
        const color = data.color || '#7B68EE';

        if (!dom || !dom.n) {
            const why = data.status === 'no_data' ? noDataReason(data) : 'No plausible dominant arrivals in this window';
            ['tofPlot', 'snrPlot'].forEach(id => showPlotMessage(id, why));
            return;
        }

        const toDate = ts => new Date(ts * 1000).toISOString();

        // Compute shared x-axis range from all timestamps so panels align
        let allTs = [...dom.timestamps];
        if (sec && sec.n > 0) allTs = allTs.concat(sec.timestamps);
        if (solar && solar.timestamps) allTs = allTs.concat(solar.timestamps);
        const xMin = toDate(Math.min(...allTs));
        const xMax = toDate(Math.max(...allTs));
        const sharedXaxis = { gridcolor: '#334155', title: 'UTC', range: [xMin, xMax] };

        // Solar zenith overlay — elevation curve only (no shading)
        function makeSolarTrace(solar, yaxisId) {
            if (!solar || !solar.timestamps) return null;
            return {
                x: solar.timestamps.map(toDate),
                y: solar.elevation_deg,
                name: 'Solar elev. (midpoint)',
                yaxis: yaxisId || 'y2',
                type: 'scatter', mode: 'lines',
                line: { color: 'rgba(255,220,80,0.55)', width: 1.5, dash: 'dot' },
                hovertemplate: 'Solar: %{y:.1f}°<extra></extra>',
                showlegend: true,
            };
        }

        const solarY2 = {
            title: 'Solar Elev. (°)', overlaying: 'y', side: 'right',
            gridcolor: 'rgba(148,163,184,0.05)', zeroline: true,
            zerolinecolor: 'rgba(255,220,80,0.25)', zerolinewidth: 1,
            range: [-90, 90], showgrid: false,
            tickvals: [-90, -45, 0, 45, 90],
            ticktext: ['-90°', '-45°', '0°', '45°', '90°'],
            titlefont: { size: 11, color: 'rgba(255,220,80,0.7)' },
            tickfont: { size: 10, color: 'rgba(255,220,80,0.7)' },
        };

        const sharedMargin = { t: 10, b: 40, l: 60, r: 130 };
        const sharedLegend = { bgcolor: 'transparent', font: { size: 11 }, x: 1.02, xanchor: 'left', y: 1, yanchor: 'top' };

        // ToF plot
        const tofTraces = [
            {
                x: dom.timestamps.map(toDate),
                y: dom.tof_ms,
                mode: 'markers',
                type: 'scatter',
                name: 'Rank-0 (dominant)',
                marker: { color, size: 4, symbol: 'circle' },
            }
        ];
        if (sec && sec.n > 0) {
            tofTraces.push({
                x: sec.timestamps.map(toDate),
                y: sec.tof_ms,
                mode: 'markers',
                type: 'scatter',
                name: 'Rank≥1 (secondary)',
                marker: { color: '#cbd5e1', size: 2, symbol: 'circle-open', opacity: 0.4 },
            });
        }
        const solarTof = makeSolarTrace(solar, 'y2');
        if (solarTof) tofTraces.push(solarTof);

        clearMsg('tofPlot'); Plotly.newPlot('tofPlot', tofTraces, {
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: '#e2e8f0', size: 11 },
            margin: sharedMargin,
            xaxis: sharedXaxis,
            yaxis: { gridcolor: '#334155', title: 'Propagation Delay (ms)', rangemode: 'tozero' },
            yaxis2: solarY2,
            legend: sharedLegend,
            showlegend: true,
        }, { responsive: true, displayModeBar: false });

        // SNR plot
        const snrTraces = [
            {
                x: dom.timestamps.map(toDate),
                y: dom.snr_db,
                mode: 'markers',
                type: 'scatter',
                name: 'Rank-0 SNR',
                marker: { color, size: 3 },
            }
        ];
        if (sec && sec.n > 0) {
            snrTraces.push({
                x: sec.timestamps.map(toDate),
                y: sec.snr_db,
                mode: 'markers',
                type: 'scatter',
                name: 'Rank≥1 SNR',
                marker: { color: '#cbd5e1', size: 2, symbol: 'circle-open', opacity: 0.4 },
            });
        }
        const solarSnr = makeSolarTrace(solar, 'y2');
        if (solarSnr) { solarSnr.showlegend = false; snrTraces.push(solarSnr); }

        clearMsg('snrPlot'); Plotly.newPlot('snrPlot', snrTraces, {
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: '#e2e8f0', size: 11 },
            margin: sharedMargin,
            xaxis: sharedXaxis,
            yaxis: { gridcolor: '#334155', title: 'Corr SNR (dB)', range: [0, 80] },
            yaxis2: solarY2,
            legend: sharedLegend,
            showlegend: true,
        }, { responsive: true, displayModeBar: false });
    }

    function renderCluster(data) {
        if (!data || data.status === 'no_data' || !data.scatter) {
            showPlotMessage('clusterPlot', noDataReason(data));
            return;
        }

        const scatter = data.scatter;
        const kde = data.kde;
        const color = data.color || '#7B68EE';
        const rank = scatter.peak_rank || [];
        const tof = scatter.tof_ms || [];
        const snr = scatter.corr_snr_db || [];

        // Cap SNR at 40 dB — values above this are correlation artefacts
        // (e.g. DC leakage, self-interference) that compress the real cluster.
        const SNR_MAX = 40;
        const valid = rank.map((_, i) => snr[i] <= SNR_MAX && isFinite(tof[i]) && tof[i] >= 0);

        const dom_mask = rank.map((r, i) => r === 0 && valid[i]);
        const sec_mask = rank.map((r, i) => r > 0  && valid[i]);

        const traces = [];

        // KDE contours (background density)
        if (kde && kde.x && kde.y && kde.z) {
            traces.push({
                type: 'contour',
                x: kde.x,
                y: kde.y,
                z: kde.z,
                colorscale: [
                    [0, 'rgba(30,58,138,0)'],
                    [0.2, 'rgba(30,58,138,0.3)'],
                    [0.5, 'rgba(59,130,246,0.5)'],
                    [0.8, 'rgba(99,179,237,0.7)'],
                    [1.0, 'rgba(196,230,255,0.9)'],
                ],
                showscale: false,
                contours: { coloring: 'fill', showlines: true },
                line: { color: 'rgba(148,163,184,0.4)', width: 0.5 },
                name: 'KDE density',
                hoverinfo: 'skip',
            });
        }

        // Scatter: secondary arrivals (behind)
        if (sec_mask.some(Boolean)) {
            traces.push({
                x: snr.filter((_, i) => sec_mask[i]),
                y: tof.filter((_, i) => sec_mask[i]),
                mode: 'markers',
                type: 'scatter',
                name: 'Rank≥1 (secondary)',
                marker: { color: '#475569', size: 3, symbol: 'circle-open', opacity: 0.6 },
            });
        }

        // Scatter: dominant arrivals (front)
        if (dom_mask.some(Boolean)) {
            traces.push({
                x: snr.filter((_, i) => dom_mask[i]),
                y: tof.filter((_, i) => dom_mask[i]),
                mode: 'markers',
                type: 'scatter',
                name: 'Rank-0 (dominant)',
                marker: { color, size: 4, opacity: 0.8 },
            });
        }

        clearMsg('clusterPlot'); Plotly.newPlot('clusterPlot', traces, {
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: '#e2e8f0', size: 11 },
            margin: { t: 10, b: 50, l: 70, r: 10 },
            xaxis: {
                gridcolor: '#334155',
                title: 'Signal + Noise level (dB)',
                range: [SNR_MAX, 0],
            },
            yaxis: {
                gridcolor: '#334155',
                title: 'Propagation Delay (ms)',
                // WWV paths sit inside 30 ms; WWVH, BPM or a mixed channel autoscale
                ...(data.station === 'WWV' ? { range: [0, 30] } : { rangemode: 'tozero' }),
            },
            legend: { bgcolor: 'transparent', font: { size: 11 } },
            showlegend: true,
        }, { responsive: true, displayModeBar: false });
    }

    function renderDopplerDelay(data) {
        const grid = document.getElementById('dopplerStatsGrid');
        const ts = (data && data.doppler_timeseries) || { minute_boundary: [], doppler_hz: [] };
        const s = (data && data.scatter) || {};
        const delay = s.delay_ms || [];
        const dop = s.doppler_hz || [];
        const why = (data && (data.doppler_reason || data.reason)) || 'No minute holds both a delay and a Doppler estimate';
        const toDate = t => new Date(t * 1000).toISOString();

        // the Doppler time series stands on its own: it needs no arrivals
        if (ts.minute_boundary.length >= 3) {
            clearMsg('dopplerTsPlot'); Plotly.newPlot('dopplerTsPlot', [{
                x: ts.minute_boundary.map(toDate),
                y: ts.doppler_hz,
                mode: 'markers+lines',
                type: 'scatter',
                name: data.doppler_label || 'Doppler',
                marker: { color: data.doppler_composite ? stationColor('composite') : (data.color || stationColor('composite')), size: 3 },
                line: { width: 1 },
                hovertemplate: '%{y:.4f} Hz<extra></extra>',
            }], {
                paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
                font: { color: '#e2e8f0', size: 11 },
                margin: { t: 10, b: 40, l: 60, r: 10 },
                xaxis: { gridcolor: '#334155', title: 'UTC' },
                yaxis: { gridcolor: '#334155', title: 'Doppler (Hz)', zeroline: true, zerolinecolor: '#475569' },
                showlegend: false,
            }, { responsive: true, displayModeBar: false });
        } else {
            showPlotMessage('dopplerTsPlot', (data && data.doppler_reason) || 'Too few minutes with a Doppler estimate');
        }

        if (!data || delay.length === 0) {
            showPlotMessage('dopplerDelayPlot', why);
            grid.style.display = 'none';
            return;
        }

        const summary = data.summary || {};
        const fmt = (v, d) => (v === null || v === undefined) ? '--' : Number(v).toFixed(d);
        grid.style.display = 'grid';
        document.getElementById('statDopplerMins').textContent = String(data.n_points || 0);
        document.getElementById('statDopplerMinsSub').textContent =
            `of ${data.n_minutes_delay || 0} minute-station delays`;
        document.getElementById('statDelaySpread').textContent = fmt(summary.delay_std_ms, 2);
        document.getElementById('statDopplerSpread').textContent = fmt(summary.doppler_std_hz, 4);
        document.getElementById('statDopplerMedian').textContent = fmt(summary.doppler_median_hz, 4);

        const traces = [];
        const kde = data.kde;
        if (kde && kde.x && kde.y && kde.z) {
            traces.push({
                type: 'contour', x: kde.x, y: kde.y, z: kde.z,
                colorscale: [
                    [0, 'rgba(30,58,138,0)'],
                    [0.2, 'rgba(30,58,138,0.3)'],
                    [0.5, 'rgba(59,130,246,0.5)'],
                    [0.8, 'rgba(99,179,237,0.7)'],
                    [1.0, 'rgba(196,230,255,0.9)'],
                ],
                showscale: false,
                contours: { coloring: 'fill', showlines: true },
                line: { color: 'rgba(148,163,184,0.4)', width: 0.5 },
                name: 'KDE density', hoverinfo: 'skip',
            });
        }
        // one trace per station: its own delay, the channel's Doppler
        const stations = [...new Set(s.station || [])];
        for (const st of stations) {
            const idx = s.station.map((v, i) => v === st ? i : -1).filter(i => i >= 0);
            traces.push({
                x: idx.map(i => delay[i]),
                y: idx.map(i => dop[i]),
                mode: 'markers', type: 'scatter',
                name: `${st} delay`,
                marker: { color: stationColor(st), size: 5, opacity: 0.8 },
                customdata: idx.map(i => [toDate(s.minute_boundary[i]), s.corr_snr_db[i]]),
                hovertemplate: '%{customdata[0]}<br>delay %{x:.2f} ms<br>Doppler %{y:.4f} Hz<br>SNR %{customdata[1]:.1f} dB<extra>' + escapeHtml(st) + '</extra>',
            });
        }
        document.getElementById('dopplerDelaySub').textContent = data.doppler_composite
            ? 'Per-minute Doppler of the composite carrier (WWV + WWVH + BPM) against each station\'s per-minute delay.'
            : 'Per-minute Doppler (Hz) against per-minute delay (ms). KDE contours outline clusters.';

        clearMsg('dopplerDelayPlot'); Plotly.newPlot('dopplerDelayPlot', traces, {
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: '#e2e8f0', size: 11 },
            margin: { t: 10, b: 50, l: 70, r: 10 },
            xaxis: { gridcolor: '#334155', title: 'Delay (ms)' },
            yaxis: { gridcolor: '#334155', title: 'Doppler (Hz)', zeroline: true, zerolinecolor: '#475569' },
            legend: { bgcolor: 'transparent', font: { size: 11 } },
            showlegend: true,
        }, { responsive: true, displayModeBar: false });
    }

    // Take the page's TimePeriodSelector, discover channels, then load
    async function init(pageTps, station) {
        tps = pageTps;
        _pageStation = station || 'ALL';
        document.getElementById('channelSelect').addEventListener('change', onChannelChange);
        ['stationSelect', 'minSnr', 'downsampleSelect'].forEach(id =>
            document.getElementById(id).addEventListener('change', loadData));
        document.querySelectorAll('.tof-tab-btn').forEach(b =>
            b.addEventListener('click', () => showTab(b.dataset.tab)));
        const sel = document.getElementById('channelSelect');
        try {
            const d = await api.get('/ionogram/channels');
            _channels = d.channels || [];
        } catch (e) {
            _channels = [];
            console.error('ionogram channels unavailable:', e);
        }
        sel.innerHTML = '';
        if (_channels.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No channels';
            sel.appendChild(opt);
        }
        _channels.forEach(ch => {
            const opt = document.createElement('option');
            opt.value = ch.channel;
            const freqKhz = parseInt(ch.channel.split('_').pop());
            const freqMhz = (freqKhz / 1000).toFixed(2).replace(/\.?0+$/, '');
            const label = ch.channel.startsWith('SHARED') ? 'Shared' : ch.stations.join('/');
            opt.textContent = `${label} ${freqMhz} MHz`;
            sel.appendChild(opt);
        });
        // Default: the channel with the newest row
        if (_channels.length > 0) {
            const newest = _channels.reduce((a, b) => ((b.latest || '') > (a.latest || '') ? b : a));
            sel.value = newest.channel;
        }
        // Prefer a channel that hears the filtered station
        if (_pageStation !== 'ALL') {
            const hears = _channels.filter(c => (c.stations || []).includes(_pageStation));
            if (hears.length && !hears.some(c => c.channel === sel.value)) sel.value = hears[0].channel;
        }
        fillStations();
        return loadData();
    }

    /** The page's station filter changed: follow it where this channel hears the station. */
    function setStation(station) {
        _pageStation = station || 'ALL';
        const opts = [...document.getElementById('stationSelect').options].map(o => o.value);
        const want = _pageStation === 'ALL' ? (opts.includes('') ? '' : opts[0]) : _pageStation;
        if (opts.includes(want) && document.getElementById('stationSelect').value !== want) {
            document.getElementById('stationSelect').value = want;
            loadData();
        }
    }

    window.TofView = { init, reload: () => loadData(), setStation };
})();
