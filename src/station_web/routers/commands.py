"""The Commands page's data: the command reference and its search index.

Both answers come from ``services.cli_reference`` (sigmond's ``cli-reference``
snapshot plus the example files).  They answer 200 with a reason when the
snapshot is missing, stale or malformed.  The page itself is served by
``app.py``.
"""
import logging

from fastapi import APIRouter

from station_web.config import config
from station_web.errors import internal_error
from station_web.services import cli_reference

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/commands", tags=["commands"])


@router.get("/reference")
def get_reference():
    try:
        return cli_reference.reference_view(config)
    except Exception as e:
        raise internal_error(logger, "command reference", e)


@router.get("/search-index")
def get_search_index():
    try:
        return cli_reference.search_index(config)
    except Exception as e:
        raise internal_error(logger, "command search index", e)
