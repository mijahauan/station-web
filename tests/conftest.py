import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hamsci_dsp.io import make_data_product_writer

from station_web import config as cfgmod
from station_web.app import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    root = tmp_path / "timestd"
    (root / "phase2").mkdir(parents=True)
    cfg = tmp_path / "sw.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'latitude = 38.9\nlongitude = -92.3\n'
                   f'[data]\nroot = "{root}"\n'
                   f'[storage]\nsqlite_path = "{root / "phase2" / "timestd.db"}"\n')
    monkeypatch.setenv("STATION_WEB_CONFIG", str(cfg))
    s = cfgmod.load_settings()
    cfgmod.config.use(s)          # the moved routers import this singleton
    return s


@pytest.fixture
def client(settings):
    app = create_app(settings, control_socket=None)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# A data root holding one recent row of every product station-web reads.
#
# ``test_routes.py`` proves the routes survive an EMPTY root.  This fixture
# proves the other half: given real rows, the routes render them.  Every row
# goes in through ``make_data_product_writer``, so hamsci-dsp's own schema
# validates it — a field that drifts out of the schema fails here rather
# than on a station.
#
# One channel carries every per-receiver product.  The readers now discover
# their channels from the products themselves, so one channel, ``WWV_20000``,
# reaches every reader.  tests/shared_fixture.py adds a SHARED channel with
# three interleaved stations for the readers that must keep them apart.
# ---------------------------------------------------------------------------

CHANNEL = "WWV_20000"
STATION = "WWV"
FREQ_MHZ = 20.0
PROC_VERSION = "7.0.0"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _tick_phase(i: int) -> float:
    """Per-tick carrier phase: a Doppler ramp plus a small residual.

    A pure ramp detrends to sigma_phi == 0, which reads as a broken
    estimator rather than a quiet ionosphere.  The sine keeps the residual
    deterministic, so the scintillation figures are stable across runs.
    """
    return 0.05 * i - 0.75 + 0.04 * math.sin(1.7 * i)


def _minute_boundary(dt: datetime) -> int:
    """Unix seconds of the minute boundary containing ``dt`` (schema: integer)."""
    return int(dt.replace(second=0, microsecond=0).timestamp())


def _write_rows(root, storage, level, name, channel, subdir, rows):
    """Write ``rows`` into one product table.

    ``output_dir`` never holds the data — SQLite keeps every product in the
    single database named by ``storage["sqlite_path"]``.  It does get
    created, and several readers gate on that directory existing: the
    dashboard and PropagationService iterate ``phase2/*`` for channels,
    TECService checks ``phase2/science/tec``.  So each producer's own
    subdirectory is passed here deliberately.
    """
    writer = make_data_product_writer(
        output_dir=root / "phase2" / subdir,
        product_level=level,
        product_name=name,
        channel=channel,
        storage_config=storage,
    )
    try:
        for row in rows:
            writer.write_measurement(row)
    finally:
        writer.close()


@pytest.fixture
def seeded(settings):
    """A data root with one recent row of every product, under ``tmp_path``."""
    root, storage = settings.data_root, settings.storage
    now = datetime.now(timezone.utc)
    t = now - timedelta(seconds=90)
    ts, mb = _iso(t), _minute_boundary(t)

    # ── L1: every arrival peak the tone correlator found ───────────────
    # A tone_correlator row: arrival_ms counts from the start of the minute
    # buffer (hf-timestd metrology_service), and the ionogram router derives
    # time of flight from it.
    _write_rows(root, storage, "L1", "all_arrivals", CHANNEL,
                f"{CHANNEL}/all_arrivals", [
        {"timestamp_utc": ts, "minute_boundary_utc": mb, "channel": CHANNEL,
         "station": STATION, "frequency_mhz": FREQ_MHZ, "utc_second": mb,
         "peak_rank": 0, "arrival_ms": 8.42, "timing_error_ms": 0.31,
         "corr_snr_db": 21.5, "model_expected_ms": 8.11,
         "carrier_phase_rad": 0.42, "detection_method": "tone_correlator",
         "sec_in_minute": 0},
        {"timestamp_utc": ts, "minute_boundary_utc": mb, "channel": CHANNEL,
         "station": STATION, "frequency_mhz": FREQ_MHZ, "utc_second": mb,
         "peak_rank": 1, "arrival_ms": 12.07, "timing_error_ms": 3.96,
         "corr_snr_db": 14.2, "model_expected_ms": 8.11,
         "carrier_phase_rad": -1.13, "detection_method": "tone_correlator",
         "sec_in_minute": 0},
    ])

    # ── L1: broadcast-centric metrology (the dashboard's Doppler pane) ─
    _write_rows(root, storage, "L1", "broadcast_measurements", CHANNEL, CHANNEL, [
        {"broadcast_id": f"{STATION}_20000", "station": STATION,
         "frequency_khz": 20000, "timestamp_utc": ts, "minute_boundary_utc": mb,
         "rtp_timestamp": 480000, "tone_detected": True, "raw_toa_ms": 8.42,
         "toa_uncertainty_ms": 0.25, "snr_db": 21.5,
         "expected_duration_ms": 800.0, "duration_match": True,
         "attribution_method": "unique_freq", "attribution_confidence": 0.99,
         "attribution_ambiguous": False, "distance_km": 1108.0,
         "light_travel_time_ms": 3.70, "expected_delay_min_ms": 3.70,
         "expected_delay_max_ms": 12.0, "delay_plausible": True,
         "quality_flag": "GOOD", "processing_version": PROC_VERSION,
         "doppler_hz": 0.08},
    ])

    # ── L2: station-assigned timing — also the channel-discovery table ─
    _write_rows(root, storage, "L2", "timing_measurements", CHANNEL,
                f"{CHANNEL}/clock_offset", [
        {"timestamp_utc": ts, "minute_boundary_utc": mb, "rtp_timestamp": 480000,
         "station": STATION, "frequency_mhz": FREQ_MHZ,
         "discrimination_method": "TONE", "discrimination_confidence": 0.94,
         "tone_detected": True, "raw_arrival_time_ms": 8.42,
         "clock_offset_ms": 0.31, "uncertainty_ms": 0.25,
         "expanded_uncertainty_ms": 0.50, "coverage_factor": 2.0,
         "confidence_level": 0.95, "u_rtp_timestamp_ms": 0.02,
         "u_ionospheric_ms": 0.18, "u_multipath_ms": 0.12,
         "u_discrimination_ms": 0.05, "u_gpsdo_ms": 0.001,
         "u_propagation_model_ms": 0.09, "degrees_of_freedom": 12,
         "quality_grade": "A", "confidence": 0.94, "quality_flag": "GOOD",
         "traceability_chain": "UTC(NIST)->WWV->RTP->host",
         "processing_version": PROC_VERSION, "processed_at": _iso(now),
         "calibration_date": "2026-09-01", "gpsdo_locked": True,
         "snr_db": 21.5, "propagation_mode": "1F2"},
    ])

    # ── L2: per-tick carrier phase.  Two whole minutes of ticks: the σ_φ
    # estimator in PhaseService slides a 60-second window over the run and
    # emits nothing at all from a shorter one.
    tick_rows = []
    k = 0
    for minutes_ago in (2, 1):
        base = _minute_boundary(t) - 60 * minutes_ago
        for sec in range(1, 56):        # second 0 carries the minute marker
            tick_rows.append({
                "timestamp_utc": _iso(datetime.fromtimestamp(base + sec, timezone.utc)),
                "minute_boundary_utc": base, "channel": CHANNEL,
                "station": STATION, "frequency_mhz": FREQ_MHZ,
                "window_start_second": sec, "window_end_second": sec + 1,
                "window_center_second": sec + 0.5,
                "phase_rad": _tick_phase(k), "carrier_phase_rad": _tick_phase(k),
                "dc_carrier_phase_rad": _tick_phase(k) + 0.05,
                "timing_offset_ms": 0.31 + 0.002 * (k % 11),
                "timing_uncertainty_ms": 0.05,
                "snr_db": 20.0 + 0.05 * (k % 20), "correlation_peak": 0.62,
                "coherence_quality": 0.88, "valid_ticks": 1,
                "processed_at": _iso(now), "processing_version": PROC_VERSION,
            })
            k += 1
    _write_rows(root, storage, "L2", "tick_phase", CHANNEL,
                f"{CHANNEL}/tick_phase", tick_rows)

    # ── L2: per-minute tick timing summary ─────────────────────────────
    _write_rows(root, storage, "L2", "tick_timing", CHANNEL,
                f"{CHANNEL}/tick_timing", [
        {"timestamp_utc": ts, "minute_boundary_utc": mb, "channel": CHANNEL,
         "station": STATION, "frequency_mhz": FREQ_MHZ, "mean_snr_db": 20.4,
         "valid_windows": 55, "total_windows": 59, "overall_confidence": 0.91,
         "expected_delay_ms": 3.70, "d_clock_ms": 0.33,
         "d_clock_uncertainty_ms": 0.08, "d_clock_source": "tick_ensemble",
         "doppler_hz": 0.06, "doppler_uncertainty_hz": 0.02,
         "ensemble_n_edges": 55, "n_clean": 52,
         "processed_at": _iso(now), "processing_version": PROC_VERSION},
    ])

    # ── L2: the WWV minute-8 test signal (amplitude scintillation) ─────
    _write_rows(root, storage, "L2", "test_signal", CHANNEL,
                f"{CHANNEL}/test_signal", [
        {"timestamp_utc": ts, "minute_boundary_utc": mb, "minute_number": 8,
         "station": STATION, "frequency_mhz": FREQ_MHZ, "detected": True,
         "detection_confidence": 0.88, "snr_db": 19.8,
         "effective_snr_db": 18.9, "multitone_score": 0.81,
         "toa_offset_ms": 8.40, "toa_source": "multitone",
         "delay_spread_ms": 0.42, "coherence_time_sec": 6.2,
         "frequency_selectivity_db": 2.1,
         "tone_power_2khz_db": -18.2, "tone_power_3khz_db": -18.6,
         "tone_power_4khz_db": -19.1, "tone_power_5khz_db": -19.4,
         "fading_variance": 0.031, "scintillation_index": 0.27,
         "s4_2khz": 0.25, "s4_3khz": 0.27, "s4_4khz": 0.28, "s4_5khz": 0.30,
         "s4_frequency_slope": 0.017, "field_strength_db": -92.5,
         "field_strength_stability": 0.88, "multipath_detected": False,
         "channel_quality": "good", "quality_flag": "GOOD",
         "processing_version": PROC_VERSION, "processed_at": _iso(now)},
    ])

    # ── L3: the fusion estimate the metrology and stability pages read.
    # Three hours at one-minute cadence: StabilityService needs 60 points
    # before it will compute an Allan deviation.
    _write_rows(root, storage, "L3", "fusion_timing", "fusion", "fusion", [
        {"timestamp_utc": _iso(t - timedelta(seconds=60 * i)),
         "minute_boundary": mb - 60 * i,
         "d_clock_fused_ms": 0.31 + 0.004 * (i % 17),
         "d_clock_raw_ms": 0.35 + 0.004 * (i % 17),
         "uncertainty_ms": 0.22, "statistical_uncertainty_ms": 0.12,
         "systematic_uncertainty_ms": 0.15, "propagation_uncertainty_ms": 0.09,
         "n_broadcasts": 4, "n_stations": 2, "stations_used": "WWV,WWVH",
         "wwv_count": 3, "wwvh_count": 1, "bpm_count": 0,
         "consistency_flag": "OK", "global_solve_verified": True,
         "global_solve_n_obs": 4, "calibration_applied": True,
         "outliers_rejected": 0, "quality_grade": "A", "kalman_state": "LOCKED",
         "quality_flag": "GOOD", "processing_version": PROC_VERSION,
         "single_station_mode": False,
         "inter_station_spread_ms": 0.06, "wwv_mean_ms": 0.30,
         "wwvh_mean_ms": 0.36, "wwv_intra_std_ms": 0.05}
        for i in range(180)
    ])

    # ── L3: physics fusion — same directory, channel 'global' ──────────
    _write_rows(root, storage, "L3", "physics", "global", "fusion", [
        {"timestamp_utc": ts, "minute_boundary_utc": mb,
         "stations_used": "WWV,WWVH", "utc_offset_ms": 0.31,
         "utc_uncertainty_ms": 0.22, "utc_consistency_flag": True,
         "processing_version": PROC_VERSION, "processed_at": _iso(now)},
    ])

    # ── L3: absolute TEC ───────────────────────────────────────────────
    _write_rows(root, storage, "L3", "tec", "AGGREGATED", "science/tec", [
        {"timestamp_utc": ts, "minute_boundary": mb, "station": STATION,
         "tec_tecu": 14.7, "t_vacuum_error_ms": 0.31, "confidence": 0.81,
         "n_frequencies": 3, "residuals_ms": 0.14,
         "frequencies_mhz": "5.0,10.0,20.0", "quality_flag": "GOOD",
         "validation_flag": "UNVALIDATED", "propagation_mode": "1F2",
         "processing_version": PROC_VERSION},
    ])

    # ── L3: differential TEC (mean_snr_db gates the /tec/dtec series) ──
    _write_rows(root, storage, "L3", "dtec", "AGGREGATED", "science/dtec", [
        {"timestamp_utc": ts, "minute_boundary": mb, "station": STATION,
         "channel": "AGGREGATED", "frequency_mhz": FREQ_MHZ, "n_ticks": 55,
         "dtec_mean_tecu": 0.042, "dtec_std_tecu": 0.011,
         "dtec_rate_tecu_per_s": 0.0003, "anchor_tec_tecu": 14.7,
         "is_anchored": True, "anchor_status": "ANCHORED",
         "sigma_noise_tecu": 0.004, "mean_snr_db": 20.4,
         "unwrap_quality": 0.97, "n_phase_jumps": 0,
         "quality_flag": "GOOD", "processing_version": PROC_VERSION},
    ])

    # ── L3: travelling ionospheric disturbance ─────────────────────────
    _write_rows(root, storage, "L3", "tid", "AGGREGATED", "fusion/tid", [
        {"timestamp_utc": ts, "minute_boundary_utc": mb,
         "event_id": f"TID-{now.strftime('%Y%m%d')}-0001",
         "period_minutes": 24.0,
         "amplitude_ms": 0.18, "velocity_m_s": 210.0, "direction_deg": 137.0,
         "correlation_coefficient": 0.74, "significance_p": 0.01,
         "confidence": 0.72, "n_paths_correlated": 3,
         "leading_path": "WWV_20000", "lagging_path": "WWV_15000",
         "lag_minutes": 6.5, "processing_version": PROC_VERSION},
    ])

    # ── L3C: hourly propagation-mode statistics (the reanalysis MUF) ───
    _write_rows(root, storage, "L3C", "propagation_stats", "REANALYSIS",
                "science/propagation_stats", [
        {"timestamp_utc": ts, "period_start": _iso(t - timedelta(hours=1)),
         "aggregation_period": "HOURLY", "station": STATION,
         "frequency_mhz": FREQ_MHZ, "mode_1e_probability": 0.05,
         "mode_1f_probability": 0.70, "mode_2f_probability": 0.15,
         "mode_3f_probability": 0.05, "mode_gw_probability": 0.02,
         "mode_unknown_probability": 0.03, "n_observations": 55,
         "data_completeness": 0.92, "quality_flag": "GOOD",
         "processing_version": PROC_VERSION,
         "estimated_muf_mhz": 24.4, "muf_confidence": 0.71,
         "mean_snr_db": 20.4},
    ])

    # ── DIAG: the chrony source table the fusion service writes ────────
    _write_rows(root, storage, "DIAG", "chrony_stats", "fusion", "fusion", [
        {"timestamp_utc": ts, "unix_time": t.timestamp(),
         "source_name": "FUSE", "source_mode": "#", "source_state": "*",
         "stratum": 0, "reach": 377, "offset_us": 120.0, "error_us": 800.0,
         "n_samples": 8, "frequency_ppm": -1.42, "freq_skew_ppm": 0.03,
         "std_dev_us": 900.0, "sys_offset_s": 0.00012},
        {"timestamp_utc": ts, "unix_time": t.timestamp(),
         "source_name": "192.168.1.1", "source_mode": "^", "source_state": "+",
         "stratum": 2, "reach": 377, "offset_us": -450.0, "error_us": 2000.0,
         "n_samples": 8, "frequency_ppm": -1.42, "freq_skew_ppm": 0.03,
         "std_dev_us": 1500.0, "sys_offset_s": 0.00012},
    ])

    # ── Space weather: a cache file, never the network ─────────────────
    # Task 10 replaces the polling service with a reader of this file.
    sw = root / "iono_cache"
    sw.mkdir(parents=True, exist_ok=True)
    (sw / "space_weather.json").write_text(json.dumps({
        "f107": 142.0, "f107_time": ts, "f107_source": "swpc",
        "kp": 2.3, "kp_time": ts, "kp_source": "gfz",
        "ap": 9.0, "ap_time": ts, "ap_source": "gfz", "fetched_at": ts,
    }))

    # ── GRAPE: one decimated day and the spectrogram made from it ──────
    day = now.strftime("%Y%m%d")
    dec = root / "products" / CHANNEL / "decimated"
    dec.mkdir(parents=True, exist_ok=True)
    (dec / f"{day}.bin").write_bytes(b"\x00" * 32)
    (dec / f"{day}_meta.json").write_text(json.dumps(
        {"date": day, "channel": CHANNEL,
         "summary": {"completeness_pct": 99.4, "valid_minutes": 1431}}))
    spec = root / "products" / CHANNEL / "spectrograms"
    spec.mkdir(parents=True, exist_ok=True)
    (spec / f"{day}_spectrogram.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    (spec / f"{day}_meta.json").write_text(json.dumps(
        {"date": day, "channel": CHANNEL}))

    return settings


@pytest.fixture
def seeded_client(seeded):
    with TestClient(create_app(seeded, control_socket=None)) as c:
        yield c
