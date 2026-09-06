"""Health = product freshness.  No processes, no disk, no upload queue."""
from fastapi import APIRouter

from station_web import app as appmod
from station_web.config import config
from station_web.services import freshness
from station_web.services.channels import discover_channels

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/system")
def get_system_health():
    t = freshness.table(config)
    # Only APPLICABLE products reach the control socket's /metrics: a product
    # whose table does not exist has no producing client here, and exporting a
    # permanent station_web_product_valid{...} 0 for it would alert forever.
    # An event product (INFORMATIONAL) has no VALID/INVALID verdict to export
    # either — it would just read as a permanently stuck gauge.
    appmod.STATUS["products"] = {k: v["verdict"] for k, v in t["products"].items()
                                 if v["applicable"] and v["verdict"] != "INFORMATIONAL"}
    appmod.STATUS["updated_at"] = t["timestamp"]
    return t


@router.get("/channels")
def get_channel_status():
    return {"channels": discover_channels(config)}
