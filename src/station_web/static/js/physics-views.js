/**
 * Helpers the ionosphere pages share: the ruler's Doppler floor and the
 * tick-lattice check on arrival residuals.  Loads after common.js.
 *
 * The ruler floor.  Doppler reads the sample clock (axis A of the
 * measurement model): a fractional frequency error u(f_s) on the ruler
 * shifts every carrier by f x u(f_s).  A disciplined ruler (A1) holds
 * u(f_s) near 4e-10, so the floor reads 0.004 Hz at 10 MHz and scales with
 * frequency.  An undisciplined ruler (A0) can sit ppm off, which at 10 MHz
 * means tens of Hz of fake Doppler: no floor worth printing.
 *
 * 4e-10 is a nominal figure: MEASUREMENT_MODEL §2 tables it from one B4
 * measurement (2026-08-16), not from this station.  When the registration
 * carries the station's own stability (stability_ns over tau_s), the floor
 * uses that instead; until then it says the figure is nominal.
 */

/** Fractional frequency floor of a disciplined ruler (0.004 Hz at 10 MHz). */
const RULER_FRACTIONAL_FLOOR = 0.0004e-6;
const RULER_NOMINAL_LABEL = 'nominal (MEASUREMENT_MODEL §2), station stability not published';

/**
 * The station's published fractional stability, stability_ns over tau_s,
 * from the registration's ``stability`` field; null when it carries none
 * or carries something unusable.
 */
function rulerPublishedFraction(stab) {
    if (!stab || typeof stab !== 'object') return null;
    const ns = Number(stab.value), tau = Number(stab.tau_s);
    if (!(isFinite(ns) && ns > 0 && isFinite(tau) && tau > 0)) return null;
    return { frac: ns * 1e-9 / tau, ns, tau };
}

/** The A-level from a /api/timing/registration reply, or null. */
function rulerAlevel(reg) {
    if (!reg || reg.available === false) return null;
    const a = (reg.ruler && reg.ruler.a_level !== undefined) ? reg.ruler.a_level : reg.a_level;
    return (a === undefined || a === '') ? null : a;
}

function _fmtHz(hz) {
    return hz >= 0.01 ? hz.toFixed(3) : hz.toPrecision(2).replace(/0+$/, '').replace(/\.$/, '');
}

function _fmtMHz(mhz) {
    return String(Number(mhz)).replace(/\.0+$/, '');
}

function _floorSource(pub) {
    return pub ? `from this station's published stability (${_fmtNum(pub.ns)} ns over ${_fmtNum(pub.tau)} s)`
               : RULER_NOMINAL_LABEL;
}

function _fmtNum(x) {
    return String(Number(x.toPrecision(4)));
}

/**
 * The Doppler floor the ruler sets at one carrier frequency.  ``stab`` is
 * the registration's ``stability`` field; a usable one replaces the nominal
 * fraction.  Returns {state: 'disciplined'|'undisciplined'|'unknown',
 * floor_hz, source: 'published'|'nominal'|null, text}.
 */
function rulerDopplerFloor(aLevel, freqMHz, stab) {
    if (aLevel === 'A1') {
        const pub = rulerPublishedFraction(stab);
        const hz = Number(freqMHz) * 1e6 * (pub ? pub.frac : RULER_FRACTIONAL_FLOOR);
        return { state: 'disciplined', floor_hz: hz, source: pub ? 'published' : 'nominal',
                 text: `ruler floor ±${_fmtHz(hz)} Hz at ${_fmtMHz(freqMHz)} MHz (A1, disciplined), `
                       + _floorSource(pub) };
    }
    if (aLevel === 'A0') {
        return { state: 'undisciplined', floor_hz: null, source: null,
                 text: 'ruler undisciplined: Doppler unreliable' };
    }
    return { state: 'unknown', floor_hz: null, source: null,
             text: 'ruler state not published: Doppler floor unknown' };
}

/** HTML for the floor beside a Doppler chart: one line per frequency. */
function rulerFloorHTML(reg, freqsMHz) {
    const a = rulerAlevel(reg);
    const freqs = [...new Set((freqsMHz || []).filter(f => f !== null && f !== undefined && isFinite(f))
                                              .map(Number))].sort((x, y) => x - y);
    const stab = reg ? reg.stability : null;
    const head = rulerDopplerFloor(a, freqs.length ? freqs[0] : 10, stab);
    const kind = head.state === 'disciplined' ? 'ok' : head.state === 'undisciplined' ? 'fault' : 'unknown';
    let body;
    if (head.state === 'disciplined' && freqs.length) {
        body = 'ruler floor (A1, disciplined): ' + freqs.map(f => {
            const r = rulerDopplerFloor('A1', f, stab);
            return `±${escapeHtml(_fmtHz(r.floor_hz))} Hz at ${escapeHtml(_fmtMHz(f))} MHz`;
        }).join(', ') + '; ' + escapeHtml(_floorSource(rulerPublishedFraction(stab)));
    } else {
        body = escapeHtml(head.text);
    }
    return `<div class="ruler-floor ruler-floor-${kind}">${statusBadge(kind, kind === 'ok' ? 'A1' : kind === 'fault' ? 'A0' : 'not published')} ${body}</div>`;
}

/**
 * Fetch the registration once per page load and render the floor into
 * ``elOrId`` for ``freqsMHz``.  Resolves to the registration (or null).
 */
let _rulerRegPromise = null;
async function loadRulerFloor(elOrId, freqsMHz) {
    if (!_rulerRegPromise) {
        _rulerRegPromise = api.get('/timing/registration').catch(() => null);
    }
    const reg = await _rulerRegPromise;
    const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
    if (el) el.innerHTML = rulerFloorHTML(reg, freqsMHz);
    return reg;
}

/** Carrier frequency (MHz) from a channel name such as SHARED_10000. */
function channelFreqMHz(channel) {
    const k = parseInt(String(channel || '').split('_').pop(), 10);
    return isFinite(k) ? k / 1000 : null;
}

/**
 * A per-broadcast arrival residual sitting on the tick lattice (within
 * ``tol`` ms of +-5, 10 or 15 ms) suggests a lock on the wrong tick or mode,
 * not an ionospheric delay.  The page draws such points as suspect.
 */
const LATTICE_MS = [5, 10, 15];
function latticeSuspect(ms, tol = 0.5) {
    if (ms === null || ms === undefined || !isFinite(ms)) return false;
    const a = Math.abs(ms);
    return LATTICE_MS.some(l => Math.abs(a - l) <= tol);
}

window.RULER_FRACTIONAL_FLOOR = RULER_FRACTIONAL_FLOOR;
window.rulerAlevel = rulerAlevel;
window.rulerDopplerFloor = rulerDopplerFloor;
window.rulerPublishedFraction = rulerPublishedFraction;
window.rulerFloorHTML = rulerFloorHTML;
window.loadRulerFloor = loadRulerFloor;
window.channelFreqMHz = channelFreqMHz;
window.latticeSuspect = latticeSuspect;
