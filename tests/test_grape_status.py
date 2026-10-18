"""GRAPE: list what the pipeline left on disk, and read its own status file.

On B4 every ``products/<ch>/spectrograms`` directory held PNGs while no
``decimated/*.bin`` survived, and the page listed zero channels.  The upload
history read ``upload/queue.json``, a file nothing writes any more;
hamsci-physics ``grape daily`` writes ``upload/grape_status.json``.
"""
import json

from station_web.services.grape_service import GrapeService

STATUS = {
    "date": "20261017", "started_at": "2026-10-18T00:10:02+00:00",
    "completed_at": "2026-10-18T00:31:47+00:00", "status": "completed",
    "channels_expected": 9, "channels_decimated": 9, "channels_spectrogram": 8,
    "upload_status": "external", "upload_completed": 0, "upload_failed": 0,
    "error": None,
}


def _spectrogram_only(root, channel="SHARED_10000", day="20261017"):
    spec = root / "products" / channel / "spectrograms"
    spec.mkdir(parents=True)
    (spec / f"{day}_spectrogram.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def test_channel_with_only_spectrograms_is_listed(tmp_path):
    _spectrogram_only(tmp_path)
    (tmp_path / "products" / "raw_buffer" / "spectrograms").mkdir(parents=True)
    svc = GrapeService(data_root=str(tmp_path))
    assert svc.get_channels() == ["SHARED_10000"]
    assert svc.get_spectrogram_dates("SHARED_10000") == ["20261017"]


def test_pipeline_status_read_from_grape_status_json(tmp_path):
    (tmp_path / "upload").mkdir()
    (tmp_path / "upload" / "grape_status.json").write_text(json.dumps(STATUS))
    (tmp_path / "upload" / "queue.json").write_text(json.dumps(
        [{"status": "completed"}]))          # stale file: must be ignored
    svc = GrapeService(data_root=str(tmp_path))
    summary = svc.get_summary()
    assert summary["pipeline"]["status"] == "completed"
    assert summary["pipeline"]["channels_spectrogram"] == 8
    assert summary["pipeline"]["upload_status"] == "external"
    assert "history" not in summary["uploads"]


def test_pipeline_status_absent_or_corrupt_is_none(tmp_path):
    svc = GrapeService(data_root=str(tmp_path))
    assert svc.get_pipeline_status() is None
    (tmp_path / "upload").mkdir()
    (tmp_path / "upload" / "grape_status.json").write_text("{not json")
    assert svc.get_pipeline_status() is None


def test_summary_route_carries_pipeline(settings, client):
    root = settings.data_root
    _spectrogram_only(root)
    (root / "upload").mkdir(parents=True)
    (root / "upload" / "grape_status.json").write_text(json.dumps(STATUS))
    j = client.get("/api/grape/summary").json()
    assert j["channel_list"] == ["SHARED_10000"]
    assert j["pipeline"]["date"] == "20261017"
    u = client.get("/api/grape/uploads").json()
    assert u["pipeline"]["status"] == "completed"
