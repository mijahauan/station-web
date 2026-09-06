"""
Station metadata API endpoints.
"""

from fastapi import APIRouter, HTTPException

from station_web.models.station import StationMetadata
from station_web.services.channels import discover_channel_infos
from station_web.config import config

router = APIRouter(prefix="/station", tags=["station"])


@router.get("/metadata", response_model=StationMetadata)
def get_station_metadata():
    """
    Get station metadata and configuration.
    
    Returns:
    - Station identification (callsign, grid square, IDs)
    - Geographic location
    - Operating mode
    - Configured channels
    - Data paths
    """
    try:
        station = config.station
        metadata = dict(config.station_metadata)
        # station_web.config carries the fields every page needs; these three
        # come straight off the [station] table when the operator set them.
        metadata.setdefault('station_id', station.get('id', ''))
        metadata.setdefault('instrument_id', station.get('instrument_id', ''))
        metadata.setdefault('mode', station.get('mode', 'production'))
        metadata['channels'] = discover_channel_infos(config)
        metadata['data_root'] = str(config.data_root)
        
        return metadata
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
