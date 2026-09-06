"""station-web settings: one TOML file, never fatal when absent.

Resolution order mirrors hamsci-physics: $STATION_WEB_CONFIG, then
/etc/station-web/station-web.toml.  A missing file yields defaults and
``loaded = False``; ``validate`` reports it, the app still serves the
pages so an operator can read the documentation.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/etc/station-web/station-web.toml")
DEFAULT_DATA_ROOT = Path("/var/lib/timestd")
DEFAULT_DOCS_ROOTS = [
    {"name": "hf-timestd",     "path": "/opt/git/sigmond/hf-timestd/docs"},
    {"name": "hamsci-physics", "path": "/opt/git/sigmond/hamsci-physics/docs"},
    {"name": "hamsci-dsp",     "path": "/opt/git/sigmond/hamsci-dsp/docs"},
    {"name": "sigmond",        "path": "/opt/git/sigmond/sigmond/docs/scientist"},
]


def resolve_config_path() -> Path:
    env = os.environ.get("STATION_WEB_CONFIG")
    return Path(env).resolve() if env else DEFAULT_CONFIG_PATH


@dataclass
class Settings:
    config_path: Path
    loaded: bool
    raw: dict = field(default_factory=dict)

    # ── derived views the routers read ─────────────────────────────
    @property
    def station(self) -> dict:
        return dict(self.raw.get("station", {}) or {})

    @property
    def station_metadata(self) -> dict:
        s = self.station
        return {
            "callsign": s.get("callsign", ""),
            "grid_square": s.get("grid_square", ""),
            "latitude": float(s.get("latitude", 0.0) or 0.0),
            "longitude": float(s.get("longitude", 0.0) or 0.0),
            "description": s.get("description", ""),
        }

    @property
    def data_root(self) -> Path:
        return Path((self.raw.get("data", {}) or {}).get("root", DEFAULT_DATA_ROOT))

    @property
    def fusion_dir(self) -> Path:
        return self.data_root / "phase2" / "fusion"

    @property
    def storage(self) -> dict:
        st = dict(self.raw.get("storage", {}) or {})
        if not st.get("sqlite_path"):
            st.pop("sqlite_path", None)
        return st

    @property
    def web_bind(self) -> str:
        return str((self.raw.get("web", {}) or {}).get("bind", "0.0.0.0"))

    @property
    def web_port(self) -> int:
        return int((self.raw.get("web", {}) or {}).get("port", 8000))

    @property
    def docs_roots(self) -> list[dict[str, Any]]:
        roots = (self.raw.get("docs", {}) or {}).get("roots")
        return [dict(r) for r in roots] if roots else [dict(r) for r in DEFAULT_DOCS_ROOTS]

    @property
    def log_level(self) -> str:
        return str((self.raw.get("logging", {}) or {}).get("level", "INFO"))


def load_settings(path: Path | None = None) -> Settings:
    # §12.3: config_path is always absolute, whether it came from -c/--config
    # (possibly a relative flag), STATION_WEB_CONFIG, or the compiled default.
    p = (Path(path) if path is not None else resolve_config_path()).resolve()
    if not p.exists():
        logger.warning("station-web: no config at %s — serving with defaults", p)
        return Settings(config_path=p, loaded=False, raw={})
    with open(p, "rb") as fh:
        return Settings(config_path=p, loaded=True, raw=tomllib.load(fh))


class _ConfigProxy:
    """Forwards attribute access to the current Settings; tests swap it with use()."""
    def __init__(self, settings: Settings):
        object.__setattr__(self, "_current", settings)

    def use(self, settings: Settings) -> None:
        object.__setattr__(self, "_current", settings)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_current"), name)


#: Module singleton the moved routers import as ``from station_web.config import config``.
config = _ConfigProxy(load_settings())
