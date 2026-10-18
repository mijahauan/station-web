"""
Phase and Doppler analysis service.

Reads L2_tick_phase from the shared SQLite database and computes:
- Phase time series (unwrapped)
- Doppler shift from phase rate
- Phase scintillation index (sigma_phi) and an S4 proxy
- Summary across all channels

A station writes about a million tick_phase rows a day, 1 Hz per station per
channel.  So no request may hold a window of rows.  One SQL query per channel
projects six columns, cuts the window, and streams rows in chunks.  Each chunk
folds into fixed-size per-bucket sums, and only the sums stay in memory.  The
bucket size follows the window, so each (channel, station) trace stays at or
under MAX_POINTS_PER_TRACE points.  Phase, Doppler and sigma_phi all come out
of that one pass.  A short TTL cache lets the four views on the phase page
share it, and a semaphore lets only one pass run at a time.
"""

import logging
import math
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from station_web.config import config as _web_config

from hamsci_dsp.io.sqlite_writer import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

TABLE = 'L2_tick_phase'

# Station colors for consistent visualization
STATION_COLORS = {
    'WWV': '#3b82f6',    # blue
    'WWVH': '#f59e0b',   # amber
    'BPM': '#ef4444',    # red
}

#: Upper bound on points per (channel, station) trace, for every view.
MAX_POINTS_PER_TRACE = 1500
#: Longest window a request may ask for.  The sigma_phi sums grow with
#: window length / scintillation window, and a longer window reads more
#: rows, so the span needs a ceiling.
MAX_SPAN_DAYS = 7
#: Bucket sizes the aggregation may pick, smallest first.
_NICE_BUCKETS = (1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800, 3600)

# timestamp_utc carries the writer's processing time, which trails the data
# time (minute_boundary_utc + window_center_second) by a minute or so.  Only
# (channel, timestamp_utc) is indexed, so the query narrows on it with slack,
# then cuts the exact window on data time.
_TS_SLACK_BEFORE_S = 120
_TS_SLACK_AFTER_S = 3600

_FETCH_CHUNK = 20000
#: Phase differences across a longer gap say nothing about Doppler.
_MAX_PAIR_GAP_S = 10.0
#: A 60 s sigma_phi window needs this many samples to count.
_MIN_WINDOW_SAMPLES = 5

_PHASE_FIELDS = ('carrier_phase_rad', 'dc_carrier_phase_rad', 'phase_rad')

# One heavy pass at a time: four parallel day-long reads once multiplied
# memory past the unit's MemoryMax.
_HEAVY = threading.Semaphore(1)
_HEAVY_WAIT_S = 120.0

_CACHE_TTL_S = 60.0
_CACHE_MAX = 8
_cache: "OrderedDict[tuple, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_cache_lock = threading.Lock()


class PhaseBusy(RuntimeError):
    """Another heavy phase read held the semaphore past the wait limit."""


class PhaseRequestError(ValueError):
    """A request the service refuses.  The message is fixed text, never
    client input, so the router may return it as the 400 detail."""


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        expires, value = hit
        if expires < time.monotonic():
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return value


def _cache_put(key, value) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL_S, value)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def bucket_seconds_for(window_s: float) -> int:
    """Smallest nice bucket that keeps a window under MAX_POINTS_PER_TRACE."""
    need = window_s / MAX_POINTS_PER_TRACE
    for b in _NICE_BUCKETS:
        if b >= need:
            return b
    return int(math.ceil(need))


def dc_meaningful(channel: str) -> bool:
    """DC carrier phase means something only where one station transmits.

    Channels take their name from what they hear: ``SHARED_<kHz>`` carries
    several broadcasters on one frequency, ``<STATION>_<kHz>`` carries one.
    """
    return bool(channel) and not channel.startswith('SHARED_')


def _iso(epoch: float, fine: bool) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec='milliseconds' if fine else 'seconds')


def _round(arr, digits: int) -> list:
    return np.round(np.asarray(arr, dtype=float), digits).tolist()


class _TraceAcc:
    """Fixed-size running sums for one (channel, station) trace."""

    def __init__(self, start: float, end: float, bucket: float, window_s: float):
        self.start, self.bucket, self.window_s = start, bucket, window_s
        span = max(end - start, 1e-9)
        nb = max(1, int(math.ceil(span / bucket)))
        nw = max(1, int(math.ceil(span / window_s)))
        self.nb, self.nw = nb, nw
        z = lambda n: np.zeros(n)
        self.n, self.st, self.snr = z(nb), z(nb), z(nb)
        self.phase = [z(nb) for _ in _PHASE_FIELDS]
        self.dop_dphi, self.dop_dt = z(nb), z(nb)
        # sigma_phi / S4 windows: sums of t and phase relative to a per-window
        # reference, so long unwrapped ramps do not cancel catastrophically
        self.w_n, self.w_t = z(nw), z(nw)
        self.w_x, self.w_y, self.w_xx, self.w_xy, self.w_yy = z(nw), z(nw), z(nw), z(nw), z(nw)
        self.w_i, self.w_ii = z(nw), z(nw)
        self.w_ref = np.full(nw, np.nan)
        self.prev_t: Optional[float] = None
        self.prev_raw = [0.0] * len(_PHASE_FIELDS)
        self.prev_unw = [0.0] * len(_PHASE_FIELDS)
        self.n_samples = 0

    def _unwrap(self, k: int, raw: np.ndarray) -> np.ndarray:
        if self.prev_t is None:
            unw = np.unwrap(raw)
        else:
            unw = np.unwrap(np.concatenate(([self.prev_raw[k]], raw)))[1:]
            unw += self.prev_unw[k] - self.prev_raw[k]
        self.prev_raw[k] = float(raw[-1])
        self.prev_unw[k] = float(unw[-1])
        return unw

    def add(self, t: np.ndarray, phases: List[np.ndarray], snr: np.ndarray) -> None:
        if t.size == 0:
            return
        prev_t, last_carrier = self.prev_t, self.prev_unw[0]
        unw = [self._unwrap(k, p) for k, p in enumerate(phases)]
        self.prev_t = float(t[-1])
        self.n_samples += int(t.size)

        nb = self.nb
        b = np.clip(((t - self.start) // self.bucket).astype(np.int64), 0, nb - 1)
        self.n += np.bincount(b, minlength=nb)
        self.st += np.bincount(b, weights=t, minlength=nb)
        self.snr += np.bincount(b, weights=snr, minlength=nb)
        for k in range(len(_PHASE_FIELDS)):
            self.phase[k] += np.bincount(b, weights=unw[k], minlength=nb)

        # Doppler: 1 s phase differences, summed into the later sample's bucket
        carrier = unw[0]
        if prev_t is None:
            dt, dphi, bb = np.diff(t), np.diff(carrier), b[1:]
        else:
            dt = np.diff(np.concatenate(([prev_t], t)))
            dphi = np.diff(np.concatenate(([last_carrier], carrier)))
            bb = b
        ok = (dt > 0) & (dt <= _MAX_PAIR_GAP_S)
        if ok.any():
            self.dop_dphi += np.bincount(bb[ok], weights=dphi[ok], minlength=nb)
            self.dop_dt += np.bincount(bb[ok], weights=dt[ok], minlength=nb)

        # sigma_phi windows
        nw = self.nw
        w = np.clip(((t - self.start) // self.window_s).astype(np.int64), 0, nw - 1)
        first_w, first_i = np.unique(w, return_index=True)
        fresh = np.isnan(self.w_ref[first_w])
        self.w_ref[first_w[fresh]] = carrier[first_i[fresh]]
        x = t - (self.start + w * self.window_s)
        y = carrier - self.w_ref[w]
        inten = 10.0 ** (snr / 10.0)
        for acc, wt in ((self.w_n, None), (self.w_t, t), (self.w_x, x), (self.w_y, y),
                        (self.w_xx, x * x), (self.w_xy, x * y), (self.w_yy, y * y),
                        (self.w_i, inten), (self.w_ii, inten * inten)):
            acc += np.bincount(w, weights=wt, minlength=nw)

    def finish(self) -> Dict[str, Any]:
        has = self.n > 0
        n = self.n[has]
        out: Dict[str, Any] = {
            'epochs': self.st[has] / n,
            'snr_db': self.snr[has] / n,
            'phase': {f: self.phase[k][has] / n for k, f in enumerate(_PHASE_FIELDS)},
            'n_samples': self.n_samples,
        }
        dop = self.dop_dt > 0
        out['doppler_epochs'] = self.st[dop] / self.n[dop]
        out['doppler_raw_hz'] = -(1.0 / (2 * np.pi)) * self.dop_dphi[dop] / self.dop_dt[dop]

        wv = self.w_n >= _MIN_WINDOW_SAMPLES
        wn = self.w_n[wv]
        sx, sy = self.w_x[wv], self.w_y[wv]
        sxx = self.w_xx[wv] - sx * sx / wn
        sxy = self.w_xy[wv] - sx * sy / wn
        syy = self.w_yy[wv] - sy * sy / wn
        slope = np.divide(sxy, sxx, out=np.zeros_like(sxy), where=sxx > 0)
        resid = np.maximum(syy - slope * sxy, 0.0) / wn
        sigma = np.sqrt(resid)
        mean_i = self.w_i[wv] / wn
        var_i = np.maximum(self.w_ii[wv] / wn - mean_i * mean_i, 0.0)
        s4 = np.divide(np.sqrt(var_i), mean_i, out=np.full_like(mean_i, np.nan),
                       where=mean_i > 1e-10)
        w_epochs = self.w_t[wv] / wn

        # Fold windows into output buckets when the bucket outgrows the window.
        sbucket = max(self.bucket, self.window_s)
        if sbucket > self.window_s and w_epochs.size:
            g = ((w_epochs - self.start) // sbucket).astype(np.int64)
            uniq, inv = np.unique(g, return_inverse=True)
            cnt = np.bincount(inv)
            w_epochs = np.bincount(inv, weights=w_epochs) / cnt
            sigma = np.bincount(inv, weights=sigma) / cnt
            s4_ok = ~np.isnan(s4)
            s4_cnt = np.bincount(inv, weights=s4_ok.astype(float), minlength=uniq.size)
            s4_sum = np.bincount(inv, weights=np.where(s4_ok, s4, 0.0), minlength=uniq.size)
            s4 = np.divide(s4_sum, s4_cnt, out=np.full(uniq.size, np.nan), where=s4_cnt > 0)
        out['sigma_epochs'] = w_epochs
        out['sigma_phi_rad'] = sigma
        out['s4'] = s4
        out['sigma_bucket_seconds'] = sbucket
        return out


class PhaseService:
    """
    Service for phase and Doppler analysis from L2_tick_phase.
    """

    VALID_STATIONS = {'WWV', 'WWVH', 'BPM'}

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)

    # ── database access ─────────────────────────────────────────────────

    def _db_path(self) -> Path:
        storage = getattr(_web_config, 'storage', {}) if _web_config else {}
        return Path(storage.get('sqlite_path', DEFAULT_DB_PATH))

    def _connect(self) -> Optional[sqlite3.Connection]:
        db_path = self._db_path()
        if not db_path.exists():
            return None
        try:
            return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error as e:
            logger.warning(f"Could not open {db_path} read-only: {e}")
            return None

    @staticmethod
    def _distinct_channels(conn: sqlite3.Connection) -> List[str]:
        """Channels by skip-scan of the (channel, timestamp_utc) index.

        One index seek per channel, so the cost tracks the channel count,
        never the row count.
        """
        out: List[str] = []
        last = ''
        while True:
            row = conn.execute(
                f"SELECT channel FROM {TABLE} WHERE channel > ? ORDER BY channel LIMIT 1",
                (last,),
            ).fetchone()
            if row is None:
                return out
            last = row[0]
            out.append(last)

    def _list_channels(self) -> List[str]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            return self._distinct_channels(conn)
        except sqlite3.Error as e:
            logger.warning(f"{TABLE} channel listing failed: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def _iter_chunks(conn, channel: str, start_epoch: float, end_epoch: float,
                     station: Optional[str]):
        """Yield (station, t, [carrier, dc, audio], snr) runs, ordered by station, t.

        Each run holds at most _FETCH_CHUNK rows; a station's rows may span
        several runs.  NULL phase or SNR reads as 0.0, as before.
        """
        ts_lo = datetime.fromtimestamp(start_epoch - _TS_SLACK_BEFORE_S, tz=timezone.utc).isoformat()
        ts_hi = datetime.fromtimestamp(end_epoch + _TS_SLACK_AFTER_S, tz=timezone.utc).isoformat()
        sql = (
            "SELECT station, minute_boundary_utc + window_center_second AS t, "
            "COALESCE(carrier_phase_rad, 0.0), COALESCE(dc_carrier_phase_rad, 0.0), "
            "COALESCE(phase_rad, 0.0), COALESCE(snr_db, 0.0) "
            f"FROM {TABLE} "
            "WHERE channel = ? AND timestamp_utc BETWEEN ? AND ? "
            "AND minute_boundary_utc IS NOT NULL AND window_center_second IS NOT NULL "
            "AND minute_boundary_utc + window_center_second BETWEEN ? AND ?"
        )
        params: list = [channel, ts_lo, ts_hi, start_epoch, end_epoch]
        if station:
            sql += " AND station = ?"
            params.append(station)
        sql += " ORDER BY station, t"
        cur = conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(_FETCH_CHUNK)
            if not rows:
                return
            stations = [r[0] for r in rows]
            arr = np.array([r[1:] for r in rows], dtype=float)
            del rows
            i = 0
            n = len(stations)
            while i < n:
                j = i + 1
                stn = stations[i]
                while j < n and stations[j] == stn:
                    j += 1
                seg = arr[i:j]
                yield stn, seg[:, 0], [seg[:, 1], seg[:, 2], seg[:, 3]], seg[:, 4]
                i = j

    # ── the shared pass ─────────────────────────────────────────────────

    def _read_and_aggregate(self, start_epoch: float, end_epoch: float,
                            channel: Optional[str], station: Optional[str],
                            window_s: float) -> Dict[str, Any]:
        bucket = bucket_seconds_for(end_epoch - start_epoch)
        traces: Dict[Tuple[str, str], Dict[str, Any]] = {}
        conn = self._connect()
        if conn is not None:
            try:
                channels = [channel] if channel else self._distinct_channels(conn)
                for ch in channels:
                    accs: Dict[str, _TraceAcc] = {}
                    try:
                        for stn, t, phases, snr in self._iter_chunks(
                                conn, ch, start_epoch, end_epoch, station):
                            acc = accs.get(stn)
                            if acc is None:
                                acc = accs[stn] = _TraceAcc(start_epoch, end_epoch, bucket, window_s)
                            acc.add(t, phases, snr)
                    except sqlite3.Error as e:
                        logger.warning(f"{TABLE} read failed for {ch}: {e}")
                    for stn, acc in accs.items():
                        traces[(ch, stn or '')] = acc.finish()
            except sqlite3.Error as e:
                logger.warning(f"{TABLE} channel listing failed: {e}")
            finally:
                conn.close()

        truncated = []
        for key, tr in traces.items():
            if tr['epochs'].size > MAX_POINTS_PER_TRACE:
                # cannot happen with bucket_seconds_for, but never hide it if it does
                keep = slice(-MAX_POINTS_PER_TRACE, None)
                tr['epochs'] = tr['epochs'][keep]
                tr['snr_db'] = tr['snr_db'][keep]
                tr['phase'] = {f: v[keep] for f, v in tr['phase'].items()}
                truncated.append(f"{key[0]}/{key[1]}")
        return {'traces': traces, 'bucket_seconds': bucket, 'truncated': truncated,
                'window_seconds': window_s}

    def _core(self, start: datetime, end: datetime, channel: Optional[str],
              station: Optional[str], window_s: float = 60.0) -> Dict[str, Any]:
        start_epoch, end_epoch = start.timestamp(), end.timestamp()
        if end_epoch <= start_epoch:
            raise PhaseRequestError("end must follow start")
        if end_epoch - start_epoch > MAX_SPAN_DAYS * 86400:
            raise PhaseRequestError(f"window longer than {MAX_SPAN_DAYS} days")
        if not window_s >= 1.0:
            raise PhaseRequestError("scintillation window must be at least 1 s")
        # Only names the database holds may reach the cache key, so a client
        # cannot mint keys, or reads, with arbitrary strings.
        if channel or station:
            known = self.get_available_channels()
            if channel and channel not in {c['channel'] for c in known['channels']}:
                raise PhaseRequestError("unknown channel")
            if station and station not in known['stations']:
                raise PhaseRequestError("unknown station")
        key =(round(start_epoch, 3), round(end_epoch, 3), channel or None,
               station or None, float(window_s), str(self._db_path()))
        hit = _cache_get(key)
        if hit is not None:
            return hit
        if not _HEAVY.acquire(timeout=_HEAVY_WAIT_S):
            raise PhaseBusy("phase data busy; try again")
        try:
            hit = _cache_get(key)       # a twin request may have filled it meanwhile
            if hit is not None:
                return hit
            core = self._read_and_aggregate(start_epoch, end_epoch, channel, station, window_s)
            _cache_put(key, core)
            return core
        finally:
            _HEAVY.release()

    @staticmethod
    def _meta(core: Dict[str, Any], start: datetime, end: datetime) -> Dict[str, Any]:
        return {
            'start': start.isoformat(),
            'end': end.isoformat(),
            'bucket_seconds': core['bucket_seconds'],
            'max_points_per_trace': MAX_POINTS_PER_TRACE,
            'truncated': list(core['truncated']),
        }

    # ── views ───────────────────────────────────────────────────────────

    def _phase_series(self, core, phase_type: str, unwrap: bool,
                      only_dc: bool = False) -> List[Dict[str, Any]]:
        fine = core['bucket_seconds'] < 5
        series = []
        for (ch, stn), tr in sorted(core['traces'].items()):
            if only_dc and not dc_meaningful(ch):
                continue
            ep = tr['epochs']
            if ep.size == 0:
                continue
            ph = tr['phase'][phase_type]
            if not unwrap:
                ph = (ph + np.pi) % (2 * np.pi) - np.pi
            series.append({
                'channel': ch,
                'station': stn,
                'color': STATION_COLORS.get(stn, '#94a3b8'),
                'timestamps': [_iso(t, fine) for t in ep],
                'epochs': _round(ep, 3),
                'phase_rad': _round(ph, 4),
                'snr_db': _round(tr['snr_db'], 2),
                'n_points': int(ep.size),
                'n_samples': tr['n_samples'],
                'dc_meaningful': dc_meaningful(ch),
            })
        return series

    def _doppler_series(self, core, smoothing_seconds: float) -> Tuple[List[Dict[str, Any]], int]:
        bucket = core['bucket_seconds']
        k = int(round(smoothing_seconds / bucket)) if smoothing_seconds > 0 else 0
        if k >= 2 and k % 2 == 0:
            k += 1
        fine = bucket < 5
        series = []
        for (ch, stn), tr in sorted(core['traces'].items()):
            ep, raw = tr['doppler_epochs'], tr['doppler_raw_hz']
            if ep.size < 2:
                continue
            smooth = raw
            if k >= 3 and raw.size >= k:
                kernel = np.ones(k)
                # divide by the samples actually under the kernel, so the ends
                # do not sag toward zero
                smooth = (np.convolve(raw, kernel, mode='same')
                          / np.convolve(np.ones(raw.size), kernel, mode='same'))
            series.append({
                'channel': ch,
                'station': stn,
                'color': STATION_COLORS.get(stn, '#94a3b8'),
                'timestamps': [_iso(t, fine) for t in ep],
                'epochs': _round(ep, 3),
                'doppler_hz': _round(smooth, 5),
                'doppler_raw_hz': _round(raw, 5),
                'n_points': int(ep.size),
            })
        return series, max(k, 1) * bucket

    def _scintillation_series(self, core) -> List[Dict[str, Any]]:
        series = []
        for (ch, stn), tr in sorted(core['traces'].items()):
            ep = tr['sigma_epochs']
            if ep.size == 0:
                continue
            s4 = [None if math.isnan(v) else round(float(v), 4) for v in tr['s4']]
            series.append({
                'channel': ch,
                'station': stn,
                'color': STATION_COLORS.get(stn, '#94a3b8'),
                'timestamps': [_iso(t, False) for t in ep],
                'epochs': _round(ep, 3),
                'sigma_phi_rad': _round(tr['sigma_phi_rad'], 4),
                's4': s4,
                'n_points': int(ep.size),
            })
        return series

    def get_phase_timeseries(
        self,
        start: datetime,
        end: datetime,
        channel: Optional[str] = None,
        station: Optional[str] = None,
        phase_type: str = 'carrier_phase_rad',
        unwrap: bool = True,
    ) -> Dict[str, Any]:
        """
        Get phase time series, optionally unwrapped, one point per bucket.

        Each point is the mean of the samples in its bucket, unwrapped at the
        native 1 Hz before averaging.  ``metadata.bucket_seconds`` names the
        bucket.
        """
        core = self._core(start, end, channel, station)
        series = self._phase_series(core, phase_type, unwrap)
        meta = self._meta(core, start, end)
        meta.update({
            'phase_type': phase_type,
            'unwrapped': unwrap,
            'n_points': sum(s['n_points'] for s in series),
            'n_samples': sum(s['n_samples'] for s in series),
            'n_traces': len(series),
        })
        return {'series': series, 'metadata': meta}

    def get_doppler(
        self,
        start: datetime,
        end: datetime,
        channel: Optional[str] = None,
        station: Optional[str] = None,
        smoothing_seconds: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Doppler shift from phase rate: f_D = -(1/2pi) * dphi/dt.

        Each bucket's raw value sums the 1 s phase steps in it over their
        time span.  A moving average over ``smoothing_seconds`` follows, when
        that spans three or more buckets; the bucket itself smooths otherwise.
        """
        core = self._core(start, end, channel, station)
        series, eff = self._doppler_series(core, smoothing_seconds)
        meta = self._meta(core, start, end)
        meta.update({'smoothing_seconds': smoothing_seconds,
                     'effective_smoothing_seconds': eff,
                     'n_traces': len(series)})
        return {'series': series, 'metadata': meta}

    def get_scintillation(
        self,
        start: datetime,
        end: datetime,
        channel: Optional[str] = None,
        station: Optional[str] = None,
        window_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """
        Phase scintillation index (sigma_phi) over consecutive windows.

        sigma_phi = std(linearly detrended carrier phase) in each
        ``window_seconds`` window.  When the bucket outgrows the window, a
        point gives the mean sigma_phi of the windows inside it.
        """
        core = self._core(start, end, channel, station, window_seconds)
        series = self._scintillation_series(core)
        meta = self._meta(core, start, end)
        sb = max(core['bucket_seconds'], window_seconds)
        meta.update({'window_seconds': window_seconds,
                     'sigma_bucket_seconds': sb,
                     'n_traces': len(series)})
        return {'series': series, 'metadata': meta}

    def get_combined(
        self,
        start: datetime,
        end: datetime,
        channel: Optional[str] = None,
        station: Optional[str] = None,
        phase_type: str = 'carrier_phase_rad',
        smoothing_seconds: float = 30.0,
        window_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """Phase, DC phase, Doppler and sigma_phi from one pass, in one reply."""
        core = self._core(start, end, channel, station, window_seconds)
        meta = self._meta(core, start, end)
        doppler, eff = self._doppler_series(core, smoothing_seconds)
        return {
            'phase': {'series': self._phase_series(core, phase_type, True),
                      'metadata': {**meta, 'phase_type': phase_type, 'unwrapped': True}},
            'dc_phase': {'series': self._phase_series(core, 'dc_carrier_phase_rad', True,
                                                      only_dc=True),
                         'metadata': {**meta, 'phase_type': 'dc_carrier_phase_rad',
                                      'unwrapped': True}},
            'doppler': {'series': doppler,
                        'metadata': {**meta, 'smoothing_seconds': smoothing_seconds,
                                     'effective_smoothing_seconds': eff}},
            'scintillation': {'series': self._scintillation_series(core),
                              'metadata': {**meta, 'window_seconds': window_seconds,
                                           'sigma_bucket_seconds':
                                               max(core['bucket_seconds'], window_seconds)}},
            'metadata': meta,
        }

    def get_available_channels(self) -> Dict[str, Any]:
        """Channels with tick_phase data, and the stations each heard lately.

        Channels come from a skip-scan of the index.  Stations come from the
        last hour before each channel's newest row, so a stale channel still
        lists who it heard.
        """
        conn = self._connect()
        if conn is None:
            return {'channels': [], 'stations': []}
        channels = []
        stations: set = set()
        try:
            for ch in self._distinct_channels(conn):
                if ch.startswith('SHARED_'):
                    unique_st = sorted(
                        s for s in self._recent_stations(conn, ch) if s in self.VALID_STATIONS
                    )
                else:
                    s = ch.split('_')[0]
                    unique_st = [s] if s in self.VALID_STATIONS else []
                stations.update(unique_st)
                channels.append({
                    'channel': ch,
                    'stations': unique_st,
                    'dc_meaningful': dc_meaningful(ch),
                })
        except sqlite3.Error as e:
            logger.warning(f"{TABLE} listing failed: {e}")
        finally:
            conn.close()

        return {
            'channels': sorted(channels, key=lambda c: c['channel']),
            'stations': sorted(stations),
        }

    @staticmethod
    def _recent_stations(conn, channel: str) -> List[str]:
        row = conn.execute(
            f"SELECT timestamp_utc FROM {TABLE} WHERE channel = ? "
            "ORDER BY timestamp_utc DESC LIMIT 1",
            (channel,),
        ).fetchone()
        if row is None or not row[0]:
            return []
        try:
            latest = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00'))
        except ValueError:
            return []
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        lo = (latest - timedelta(hours=1)).isoformat()
        rows = conn.execute(
            f"SELECT DISTINCT station FROM {TABLE} WHERE channel = ? AND timestamp_utc >= ?",
            (channel, lo),
        ).fetchall()
        return [r[0] for r in rows if r[0]]

    def get_phase_summary(self) -> Dict[str, Any]:
        """
        Get current phase/Doppler state across all channels.
        Returns the latest ~5 minutes of data summarized per channel/station.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=5)
        s_ep, e_ep = start.timestamp(), end.timestamp()

        raw: Dict[Tuple[str, str], List] = {}
        conn = self._connect()
        if conn is not None:
            try:
                for ch in self._distinct_channels(conn):
                    for stn, t, phases, snr in self._iter_chunks(conn, ch, s_ep, e_ep, None):
                        raw.setdefault((ch, stn or ''), []).append((t, phases[0], snr))
            except sqlite3.Error as e:
                logger.warning(f"{TABLE} summary read failed: {e}")
            finally:
                conn.close()

        channels = []
        for (ch, stn), parts in sorted(raw.items()):
            epochs = np.concatenate([p[0] for p in parts])
            phases = np.unwrap(np.concatenate([p[1] for p in parts]))
            snrs = np.concatenate([p[2] for p in parts])

            if len(epochs) < 2:
                continue

            # Compute instantaneous Doppler from last few points
            doppler_hz = 0.0
            if len(epochs) >= 3:
                dt = epochs[-1] - epochs[-3]
                if dt > 0:
                    doppler_hz = -(1.0 / (2 * np.pi)) * (phases[-1] - phases[-3]) / dt

            # Phase scintillation over the window
            if len(phases) >= 5:
                coeffs = np.polyfit(epochs - epochs[0], phases, 1)
                trend = np.polyval(coeffs, epochs - epochs[0])
                sigma_phi = float(np.std(phases - trend))
            else:
                sigma_phi = 0.0

            channels.append({
                'channel': ch,
                'station': stn,
                'color': STATION_COLORS.get(stn, '#94a3b8'),
                'dc_meaningful': dc_meaningful(ch),
                'latest_phase_rad': float(phases[-1]),
                'doppler_hz': float(doppler_hz),
                'sigma_phi_rad': sigma_phi,
                'mean_snr_db': float(np.mean(snrs)),
                'n_points': len(epochs),
                'last_update': datetime.fromtimestamp(
                    float(epochs[-1]), tz=timezone.utc).isoformat(),
            })

        return {
            'channels': channels,
            'timestamp': end.isoformat(),
            'window_minutes': 5,
        }
