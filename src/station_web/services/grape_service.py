"""
GRAPE service - serves spectrograms, decimation status, and pipeline status.

The producer is hamsci-physics ``grape daily``.  It decimates each channel to
``products/<ch>/decimated/<date>.bin``, renders
``products/<ch>/spectrograms/<date>_spectrogram.png``, packages the day under
``upload/<date>/``, and records the run in ``upload/grape_status.json``.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GrapeService:
    """Service for GRAPE pipeline status, spectrograms, and upload history."""

    def __init__(self, data_root: str = "/var/lib/timestd"):
        self.data_root = Path(data_root)
        self.products_dir = self.data_root / "products"
        self.upload_dir = self.data_root / "upload"

    def get_channels(self) -> List[str]:
        """Channels with a decimated day or a rendered spectrogram.

        Either product counts: a station that prunes ``.bin`` files after
        packaging still keeps its spectrograms, and those must stay viewable.
        """
        channels = []
        if self.products_dir.exists():
            for d in sorted(self.products_dir.iterdir()):
                if not d.is_dir():
                    continue
                has_bin = any((d / "decimated").glob("????????.bin"))
                has_png = any((d / "spectrograms").glob("*_spectrogram.png"))
                if not (has_bin or has_png):
                    continue
                # Skip non-channel directories (date dirs, raw_buffer, etc.)
                name = d.name
                if name.isdigit() or name == "raw_buffer" or "_MHz" in name:
                    continue
                channels.append(name)
        return channels

    def get_spectrogram_dates(self, channel: str) -> List[str]:
        """Get available spectrogram dates for a channel."""
        spec_dir = self.products_dir / channel / "spectrograms"
        dates = []
        if spec_dir.exists():
            for f in sorted(spec_dir.glob("*_spectrogram.png")):
                date_str = f.stem.replace("_spectrogram", "")
                dates.append(date_str)
        return dates

    def get_spectrogram_path(self, channel: str, date_str: str) -> Optional[Path]:
        """Get path to a spectrogram PNG."""
        spec_path = self.products_dir / channel / "spectrograms" / f"{date_str}_spectrogram.png"
        if spec_path.exists():
            return spec_path
        return None

    def get_decimation_status(self) -> Dict:
        """Get decimation status for all channels."""
        status = {}
        for channel in self.get_channels():
            dec_dir = self.products_dir / channel / "decimated"
            if dec_dir.exists():
                dates = sorted([f.stem for f in dec_dir.glob("????????.bin")])
                meta_files = sorted(dec_dir.glob("*_meta.json"))

                latest_meta = None
                if meta_files:
                    try:
                        with open(meta_files[-1], "r") as f:
                            latest_meta = json.load(f)
                    except Exception:
                        pass

                status[channel] = {
                    "dates_available": len(dates),
                    "latest_date": dates[-1] if dates else None,
                    "oldest_date": dates[0] if dates else None,
                    "completeness_pct": (
                        latest_meta.get("summary", {}).get("completeness_pct", 0)
                        if latest_meta
                        else 0
                    ),
                    "valid_minutes": (
                        latest_meta.get("summary", {}).get("valid_minutes", 0)
                        if latest_meta
                        else 0
                    ),
                }
        return status

    def get_pipeline_status(self) -> Optional[Dict]:
        """The latest ``grape daily`` run, as hamsci-physics recorded it.

        ``upload/grape_status.json`` holds one run: date, status
        (running/completed/failed), channel counts per stage, upload_status
        and error.  ``None`` when the file is absent or unreadable.
        """
        status_file = self.upload_dir / "grape_status.json"
        if not status_file.exists():
            return None
        try:
            with open(status_file, "r") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading {status_file}: {e}")
            return None
        return data if isinstance(data, dict) else None

    def get_upload_dates(self) -> List[str]:
        """Get dates with packaged data ready for upload."""
        dates = []
        if self.upload_dir.exists():
            for d in sorted(self.upload_dir.iterdir()):
                if d.is_dir() and d.name.isdigit() and len(d.name) == 8:
                    dates.append(d.name)
        return dates

    def get_summary(self) -> Dict:
        """Get overall GRAPE pipeline summary."""
        channels = self.get_channels()
        upload_dates = self.get_upload_dates()
        return {
            "channels": len(channels),
            "channel_list": channels,
            "decimation": self.get_decimation_status(),
            "pipeline": self.get_pipeline_status(),
            "uploads": {
                "packaged_dates": len(upload_dates),
                "latest_packaged": upload_dates[-1] if upload_dates else None,
            },
        }
