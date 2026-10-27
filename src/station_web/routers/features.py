"""What this station's station-web serves: whether the science routers
loaded, and which ones the app skipped for want of their imports."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/features", tags=["features"])


@router.get("")
def features(request: Request):
    skipped = list(getattr(request.app.state, "skipped_routers", []))
    return {"science": not skipped, "skipped": skipped}
