"""A SHARED channel heard from three stations, as B4 hears SHARED_10000.

``conftest.seeded`` writes one channel (``WWV_20000``) with one station,
so no test ever saw rows from WWV, WWVH and BPM interleaved on one
channel.  The event detector compared consecutive rows regardless of
station and invented ~1000 "mode changes" in 6 h on B4 (D7); the
timeline and the phase views have the same exposure.  This module writes
that interleaving through hamsci-dsp's own writer.

Import the fixture into a test module to use it::

    from shared_fixture import shared_seeded, SHARED   # noqa: F401
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import PROC_VERSION, _iso, _minute_boundary, _write_rows

SHARED = "SHARED_10000"
FREQ_MHZ = 10.0
STATIONS = ("WWV", "WWVH", "BPM")
N_MINUTES = 40

#: per-station SNR (dB): far enough apart that comparing consecutive rows
#: ACROSS stations crosses the 10 dB event threshold on every row.
BASE_SNR = {"WWV": 22.0, "WWVH": 6.0, "BPM": 14.0}
#: WWV genuinely fades from minute 25 to 29 (a 15 dB drop and recovery).
WWV_FADE = range(25, 30)
#: per-station modes, also different, so a cross-station comparison sees a
#: "change" on nearly every row.  BPM: a ONE-minute blip to 1E at minute
#: 10 (noise, not a change) and a persistent switch to 2F2 from minute 20.
BASE_MODE = {"WWV": "1F2", "WWVH": "2F2", "BPM": "1F2"}
BPM_BLIP_MINUTE = 10
BPM_SWITCH_MINUTE = 20


def snr_at(station: str, i: int) -> float:
    if station == "WWV" and i in WWV_FADE:
        return BASE_SNR["WWV"] - 15.0
    return BASE_SNR[station]


def mode_at(station: str, i: int) -> str:
    if station == "BPM":
        if i == BPM_BLIP_MINUTE:
            return "1E"
        if i >= BPM_SWITCH_MINUTE:
            return "2F2"
    return BASE_MODE[station]


def _timing_row(t, station, i, now):
    return {
        "timestamp_utc": _iso(t), "minute_boundary_utc": _minute_boundary(t),
        "rtp_timestamp": 480000 + i, "station": station, "frequency_mhz": FREQ_MHZ,
        "discrimination_method": "TONE", "discrimination_confidence": 0.9,
        "tone_detected": True, "raw_arrival_time_ms": 8.0,
        "clock_offset_ms": 0.3, "uncertainty_ms": 0.25,
        "expanded_uncertainty_ms": 0.5, "coverage_factor": 2.0,
        "confidence_level": 0.95, "u_rtp_timestamp_ms": 0.02,
        "u_ionospheric_ms": 0.18, "u_multipath_ms": 0.12,
        "u_discrimination_ms": 0.05, "u_gpsdo_ms": 0.001,
        "u_propagation_model_ms": 0.09, "degrees_of_freedom": 12,
        "quality_grade": "A", "confidence": 0.9, "quality_flag": "GOOD",
        "traceability_chain": f"UTC->{station}->RTP->host",
        "processing_version": PROC_VERSION, "processed_at": _iso(now),
        "calibration_date": "2026-09-01", "gpsdo_locked": True,
        "snr_db": snr_at(station, i), "propagation_mode": mode_at(station, i),
        "doppler_hz": 0.01 * i,
    }


def _tick_timing_row(t, station, i, now):
    return {
        "timestamp_utc": _iso(t), "minute_boundary_utc": _minute_boundary(t),
        "channel": SHARED, "station": station, "frequency_mhz": FREQ_MHZ,
        "mean_snr_db": snr_at(station, i) - 10.0,   # the coarse estimator
        "valid_windows": 50, "total_windows": 59, "overall_confidence": 0.9,
        "expected_delay_ms": 5.0, "d_clock_ms": 0.1 * STATIONS.index(station),
        "d_clock_uncertainty_ms": 0.08, "d_clock_source": "tick_ensemble",
        "doppler_hz": 0.05, "doppler_uncertainty_hz": 0.02,
        "ensemble_n_edges": 50, "n_clean": 48,
        "processed_at": _iso(now), "processing_version": PROC_VERSION,
    }


def _tick_phase_rows(t, station, i, now):
    """Five windows a minute; their median is ``snr_at`` exactly, and one
    wild window shows a median, not a mean, is what comes back."""
    mb = _minute_boundary(t)
    out = []
    for k, off in enumerate((-1.0, 0.0, 1.0, 0.5, 30.0)):
        sec = 1 + k
        out.append({
            "timestamp_utc": _iso(datetime.fromtimestamp(mb + sec, timezone.utc)),
            "minute_boundary_utc": mb, "channel": SHARED, "station": station,
            "frequency_mhz": FREQ_MHZ, "window_start_second": sec,
            "window_end_second": sec + 1, "window_center_second": sec + 0.5,
            "phase_rad": 0.1 * k, "carrier_phase_rad": 0.1 * k,
            "dc_carrier_phase_rad": 0.1 * k, "timing_offset_ms": 0.3,
            "timing_uncertainty_ms": 0.05,
            "snr_db": snr_at(station, i) + (off if off != 0.5 else -0.5),
            "correlation_peak": 0.6, "coherence_quality": 0.9, "valid_ticks": 1,
            "processed_at": _iso(now), "processing_version": PROC_VERSION,
        })
    return out


def minute_times(now=None):
    """The N_MINUTES minute instants the fixture writes, oldest first."""
    now = now or datetime.now(timezone.utc)
    first = now.replace(second=30, microsecond=0) - timedelta(minutes=N_MINUTES + 1)
    return [first + timedelta(minutes=i) for i in range(N_MINUTES)]


def write_shared(settings, now=None):
    root, storage = settings.data_root, settings.storage
    now = now or datetime.now(timezone.utc)
    timing, ticks, phase = [], [], []
    for i, t in enumerate(minute_times(now)):
        for j, station in enumerate(STATIONS):
            ts = t + timedelta(seconds=j)          # interleaved, as on the box
            timing.append(_timing_row(ts, station, i, now))
            ticks.append(_tick_timing_row(ts, station, i, now))
            phase.extend(_tick_phase_rows(t, station, i, now))
    _write_rows(root, storage, "L2", "timing_measurements", SHARED,
                f"{SHARED}/clock_offset", timing)
    _write_rows(root, storage, "L2", "tick_timing", SHARED,
                f"{SHARED}/tick_timing", ticks)
    _write_rows(root, storage, "L2", "tick_phase", SHARED,
                f"{SHARED}/tick_phase", phase)
    return settings


@pytest.fixture
def shared_seeded(settings):
    """``settings`` plus one SHARED channel with WWV, WWVH and BPM interleaved."""
    return write_shared(settings)
