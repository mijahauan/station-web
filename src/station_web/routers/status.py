"""Station status router: the Station page's data, read from sigmond
snapshots.  A placeholder until Task 8 fills it."""
from fastapi import APIRouter

router = APIRouter(prefix="/status", tags=["status"])
