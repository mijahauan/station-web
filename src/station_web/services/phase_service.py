"""
Phase and Doppler analysis service.

Reads L2_tick_phase from the shared SQLite database and computes:
- Phase time series (unwrapped)
- Doppler shift from phase rate
- Phase variability (sigma_phi, std about a 60 s linear fit) and S4,
  uncorrected for noise
- One composite trace per SHARED channel (WWV + WWVH + BPM on one carrier)
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

# Station colours: the site palette (station_web/palette.py, common.js PALETTE)
from station_web.palette import COMPOSITE, STATION_COLORS  # noqa: E402

#: The one Doppler estimator on the site.  The phase page, its summary cards,
#: the Observatory and the ionogram's Doppler-delay view all draw it.  It
#: pools the 1 Hz carrier-phase steps inside each bucket: one least-squares
#: rate through every pair, so a bucket's value never rests on two or three
#: samples, and the 10 s gap limit keeps an outage from posing as a slope.
#: The ionogram's own per-minute polyfit and the L2 timing_measurements
#: doppler_hz each answered with different numbers, and with opposite signs.
DOPPLER_ESTIMATOR = (
    "f_D = -(1/2pi) * sum(dphi) / sum(dt) over consecutive 1 Hz carrier-phase "
    "samples (gap <= 10 s) in each bucket, from L2_tick_phase (phase_service)")

#: A SHARED_<kHz> channel hears one composite carrier: WWV, WWVH and BPM on
#: one frequency.  Its phase, Doppler and variability belong to that sum,
#: so the views return one trace per SHARED channel under this label.
COMPOSITE_STATION = 'composite'
COMPOSITE_LABEL = 'composite: WWV + WWVH + BPM'

#: Upper bound on points per (channel, station) trace, for every view.
MAX_POINTS_PER_TRACE = 1500
#: Longest window a request may ask for.  The sigma_phi sums grow with
#: window length / scintillation window, and a longer window reads more
#: rows, so the span needs a ceiling.
MAX_SPAN_DAYS = 7
#: The summary cards cover this many seconds, as one Doppler bucket, with
#: the chart's phase-variability window inside it.
SUMMARY_SECONDS = 300
SUMMARY_SIGMA_WINDOW_S = 60.0
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
# A queued request gives up quickly and answers 503, rather than parking one of
# the server's worker threads behind someone else's read.
_HEAVY_WAIT_S = 5.0
# The scintillation window comes from a short list: a free value would size the
# per-window accumulators (span / window) and mint cache keys at will.
ALLOWED_WINDOWS_S = (10, 30, 60, 120, 300, 600)

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

    def window_stats(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(valid, epochs, sigma_phi, s4) for every window on the grid.

        Full-length arrays, NaN where a window holds fewer than
        _MIN_WINDOW_SAMPLES, so traces on one grid line up window by window.
        """
        valid = self.w_n >= _MIN_WINDOW_SAMPLES
        wn = np.where(valid, self.w_n, 1.0)
        sx, sy = self.w_x, self.w_y
        sxx = self.w_xx - sx * sx / wn
        sxy = self.w_xy - sx * sy / wn
        syy = self.w_yy - sy * sy / wn
        slope = np.divide(sxy, sxx, out=np.zeros_like(sxy), where=sxx > 0)
        resid = np.maximum(syy - slope * sxy, 0.0) / wn
        sigma = np.where(valid, np.sqrt(resid), np.nan)
        mean_i = self.w_i / wn
        var_i = np.maximum(self.w_ii / wn - mean_i * mean_i, 0.0)
        s4 = np.divide(np.sqrt(var_i), mean_i, out=np.full_like(mean_i, np.nan),
                       where=valid & (mean_i > 1e-10))
        epochs = np.where(valid, self.w_t / wn, np.nan)
        return valid, epochs, sigma, s4

    def _fold_windows(self, valid, epochs, sigma, s4) -> Dict[str, Any]:
        """Keep the valid windows; fold them into output buckets when the
        bucket outgrows the window."""
        w_epochs, sigma, s4 = epochs[valid], sigma[valid], s4[valid]
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
        return {'sigma_epochs': w_epochs, 'sigma_phi_rad': sigma, 's4': s4,
                'sigma_bucket_seconds': sbucket}

    @staticmethod
    def _doppler(n, st, dphi, dt) -> Dict[str, Any]:
        dop = dt > 0
        return {'doppler_epochs': st[dop] / n[dop],
                'doppler_raw_hz': -(1.0 / (2 * np.pi)) * dphi[dop] / dt[dop]}

    def finish(self) -> Dict[str, Any]:
        has = self.n > 0
        n = self.n[has]
        out: Dict[str, Any] = {
            'epochs': self.st[has] / n,
            'snr_db': self.snr[has] / n,
            'phase': {f: self.phase[k][has] / n for k, f in enumerate(_PHASE_FIELDS)},
            'n_samples': self.n_samples,
            'last_epoch': self.prev_t,
        }
        out.update(self._doppler(self.n, self.st, self.dop_dphi, self.dop_dt))
        out.update(self._fold_windows(*self.window_stats()))
        return out


def _composite_finish(accs: Dict[str, '_TraceAcc']) -> Dict[str, Any]:
    """One trace for the composite carrier of a SHARED channel.

    Every station's rows on a SHARED channel sample the same summed carrier,
    so the estimates pool:
    - Doppler: the phase steps and their spans sum across stations, one
      pooled rate per bucket, the same estimator as a single-station trace;
    - phase variability and S4: the mean over stations, window by window;
    - phase and SNR: the trace with the most samples (the unwrapped phase
      of several stations' rows carries a different offset in each, so
      averaging them would invent jumps).  ``phase_reference_station``
      names it.
    """
    names = sorted(accs)
    lead_name = max(names, key=lambda k: accs[k].n_samples)
    lead = accs[lead_name]
    out = lead.finish()
    n = sum(a.n for a in accs.values())
    st = sum(a.st for a in accs.values())
    out.update(_TraceAcc._doppler(n, st, sum(a.dop_dphi for a in accs.values()),
                                  sum(a.dop_dt for a in accs.values())))
    stats = [a.window_stats() for a in accs.values()]
    valid = np.any([s[0] for s in stats], axis=0)
    cnt = np.sum([s[0] for s in stats], axis=0)
    safe = np.where(cnt > 0, cnt, 1)

    def mean_of(i):
        return np.where(valid, np.nansum([np.where(s[0], s[i], 0.0) for s in stats], axis=0)
                        / safe, np.nan)
    s4_ok = np.sum([s[0] & ~np.isnan(s[3]) for s in stats], axis=0)
    s4_sum = np.nansum([np.where(s[0] & ~np.isnan(s[3]), s[3], 0.0) for s in stats], axis=0)
    s4 = np.divide(s4_sum, s4_ok, out=np.full(s4_sum.shape, np.nan), where=s4_ok > 0)
    out.update(lead._fold_windows(valid, mean_of(1), mean_of(2), s4))
    out['n_samples'] = int(sum(a.n_samples for a in accs.values()))
    out['last_epoch'] = max(a.prev_t for a in accs.values() if a.prev_t is not None)
    out['phase_reference_station'] = lead_name
    out['stations_heard'] = [s for s in names if s]
    return out


def is_shared(channel: Optional[str]) -> bool:
    """A ``SHARED_<kHz>`` channel carries several broadcasters on one frequency."""
    return bool(channel) and channel.startswith('SHARED_')


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

    def _aggregate(self, conn, channels: List[str], start_epoch: float, end_epoch: float,
                   station: Optional[str], bucket: float, window_s: float
                   ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """One pass per channel; one trace per (channel, station), and one
        ``composite`` trace per SHARED channel.

        A SHARED channel reads every station's rows, whatever the station
        filter: the carrier it measures belongs to no single station.  With
        a station filter, the composite appears when that station was heard.
        """
        traces: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for ch in channels:
            shared = is_shared(ch)
            accs: Dict[str, _TraceAcc] = {}
            try:
                for stn, t, phases, snr in self._iter_chunks(
                        conn, ch, start_epoch, end_epoch, None if shared else station):
                    acc = accs.get(stn)
                    if acc is None:
                        acc = accs[stn] = _TraceAcc(start_epoch, end_epoch, bucket, window_s)
                    acc.add(t, phases, snr)
            except sqlite3.Error as e:
                logger.warning(f"{TABLE} read failed for {ch}: {e}")
            if not accs:
                continue
            if shared:
                if station and station not in accs:
                    continue
                traces[(ch, COMPOSITE_STATION)] = _composite_finish(accs)
            else:
                for stn, acc in accs.items():
                    traces[(ch, stn or '')] = acc.finish()
        return traces

    def _read_and_aggregate(self, start_epoch: float, end_epoch: float,
                            channel: Optional[str], station: Optional[str],
                            window_s: float, bucket: Optional[float] = None) -> Dict[str, Any]:
        bucket = bucket or bucket_seconds_for(end_epoch - start_epoch)
        traces: Dict[Tuple[str, str], Dict[str, Any]] = {}
        conn = self._connect()
        if conn is not None:
            try:
                channels = [channel] if channel else self._distinct_channels(conn)
                traces = self._aggregate(conn, channels, start_epoch, end_epoch,
                                         station, bucket, window_s)
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
              station: Optional[str], window_s: float = 60.0,
              min_bucket: int = 0) -> Dict[str, Any]:
        start_epoch, end_epoch = start.timestamp(), end.timestamp()
        if end_epoch <= start_epoch:
            raise PhaseRequestError("end must follow start")
        if end_epoch - start_epoch > MAX_SPAN_DAYS * 86400:
            raise PhaseRequestError(f"window longer than {MAX_SPAN_DAYS} days")
        if window_s not in ALLOWED_WINDOWS_S:
            raise PhaseRequestError(
                "scintillation window must be one of "
                + ", ".join(str(w) for w in ALLOWED_WINDOWS_S) + " s")
        window_s = float(window_s)
        # Snap the window outward to bucket boundaries, so requests a few
        # seconds apart share one cache entry instead of one full read each.
        # ``min_bucket`` (internal callers only) floors the bucket: the
        # ionogram asks for whole minutes.  Under a day the span's own bucket
        # never exceeds 60 s, so a day-long phase page shares the entry.
        b = max(bucket_seconds_for(end_epoch - start_epoch), int(min_bucket))
        start_epoch = math.floor(start_epoch / b) * b
        end_epoch = math.ceil(end_epoch / b) * b
        # Only names the database holds may reach the cache key, so a client
        # cannot mint keys, or reads, with arbitrary strings.
        if channel or station:
            known = self.get_available_channels()
            if channel and channel not in {c['channel'] for c in known['channels']}:
                raise PhaseRequestError("unknown channel")
            if station and station not in known['stations']:
                raise PhaseRequestError("unknown station")
        key = (start_epoch, end_epoch, channel or None,
               station or None, window_s, b, str(self._db_path()))
        hit = _cache_get(key)
        if hit is not None:
            return hit
        if not _HEAVY.acquire(timeout=_HEAVY_WAIT_S):
            raise PhaseBusy("phase data busy; try again")
        try:
            hit = _cache_get(key)       # a twin request may have filled it meanwhile
            if hit is not None:
                return hit
            core = self._read_and_aggregate(start_epoch, end_epoch, channel, station,
                                            window_s, bucket=b)
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

    @staticmethod
    def _identity(ch: str, stn: str, tr: Dict[str, Any]) -> Dict[str, Any]:
        """The fields that say whose carrier a trace measures."""
        composite = stn == COMPOSITE_STATION
        out = {
            'channel': ch,
            'station': stn,
            'composite': composite,
            'label': COMPOSITE_LABEL if composite else stn,
            'stations_heard': list(tr.get('stations_heard', [stn] if stn else [])),
            'color': COMPOSITE if composite else STATION_COLORS.get(stn, COMPOSITE),
        }
        if composite:
            out['phase_reference_station'] = tr.get('phase_reference_station')
        return out

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
                **self._identity(ch, stn, tr),
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
                **self._identity(ch, stn, tr),
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
                **self._identity(ch, stn, tr),
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
                     'doppler_estimator': DOPPLER_ESTIMATOR,
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
        Phase variability (sigma_phi) over consecutive windows.

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
                                     'effective_smoothing_seconds': eff,
                                     'doppler_estimator': DOPPLER_ESTIMATOR}},
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
        Current phase/Doppler state: one card per trace over the last 5 minutes.

        The cards use the charts' estimators, not their own: Doppler pools the
        1 Hz phase steps over the whole 5 minutes (DOPPLER_ESTIMATOR), and
        phase variability is the mean of 60 s detrended windows, the chart's
        window.  A SHARED channel gives one composite card.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=SUMMARY_SECONDS)
        s_ep, e_ep = start.timestamp(), end.timestamp()

        traces: Dict[Tuple[str, str], Dict[str, Any]] = {}
        conn = self._connect()
        if conn is not None:
            try:
                traces = self._aggregate(conn, self._distinct_channels(conn), s_ep, e_ep,
                                         None, SUMMARY_SECONDS, SUMMARY_SIGMA_WINDOW_S)
            except sqlite3.Error as e:
                logger.warning(f"{TABLE} summary read failed: {e}")
            finally:
                conn.close()

        channels = []
        for (ch, stn), tr in sorted(traces.items()):
            if tr['n_samples'] < 2 or tr['last_epoch'] is None:
                continue
            dop = tr['doppler_raw_hz']
            sig = tr['sigma_phi_rad']
            snr = tr['snr_db']
            channels.append({
                **self._identity(ch, stn, tr),
                'dc_meaningful': dc_meaningful(ch),
                'doppler_hz': float(dop[-1]) if dop.size else None,
                'sigma_phi_rad': float(np.mean(sig)) if sig.size else None,
                'mean_snr_db': float(np.mean(snr)) if snr.size else None,
                'n_points': int(tr['n_samples']),
                'last_update': datetime.fromtimestamp(
                    float(tr['last_epoch']), tz=timezone.utc).isoformat(),
            })

        return {
            'channels': channels,
            'timestamp': end.isoformat(),
            'window_minutes': SUMMARY_SECONDS // 60,
            'sigma_window_seconds': SUMMARY_SIGMA_WINDOW_S,
            'doppler_estimator': DOPPLER_ESTIMATOR,
        }

    def get_minute_doppler(self, start: datetime, end: datetime,
                           channel: str) -> Dict[str, Any]:
        """Per-minute Doppler (Hz) for one channel, for the ionogram.

        The phase page's own pass with the bucket floored at 60 s, so the
        numbers equal the phase page's at a day's window.  A SHARED channel
        answers with its composite carrier.  Raises PhaseRequestError for a
        channel with no tick_phase rows, PhaseBusy when another pass holds
        the read.
        """
        core = self._core(start, end, channel, None, min_bucket=60)
        for (ch, stn), tr in sorted(core['traces'].items()):
            ep = tr['doppler_epochs']
            return {
                'minute_boundary': (np.floor(ep / 60.0) * 60.0).astype(np.int64),
                'doppler_hz': tr['doppler_raw_hz'],
                'composite': stn == COMPOSITE_STATION,
                'label': COMPOSITE_LABEL if stn == COMPOSITE_STATION else stn,
                'bucket_seconds': core['bucket_seconds'],
            }
        return {'minute_boundary': np.zeros(0, dtype=np.int64), 'doppler_hz': np.zeros(0),
                'composite': is_shared(channel),
                'label': COMPOSITE_LABEL if is_shared(channel) else None,
                'bucket_seconds': core['bucket_seconds']}
