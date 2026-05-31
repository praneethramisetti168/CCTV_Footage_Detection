"""Zone configuration REST endpoints."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.video_processor import video_processor
from pipeline.zone_manager import Zone

router = APIRouter(prefix="/api/v1/zones", tags=["Zones"])


class ZoneCreate(BaseModel):
    zone_id: str
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    max_capacity: int = 10
    restricted: bool = False


@router.get("", summary="List all configured zones")
async def get_zones():
    zm = video_processor.zone_manager
    return {
        "zones": [
            {
                "zone_id": zid,
                "name": z.name,
                "bounds": {"x1": z.x1, "y1": z.y1, "x2": z.x2, "y2": z.y2},
                "max_capacity": z.max_capacity,
                "restricted": z.restricted,
                "current_count": zm.zone_counts.get(zid, 0),
                "color_bgr": z.color,
            }
            for zid, z in zm.zones.items()
        ]
    }


@router.post("", summary="Add or update a zone")
async def upsert_zone(payload: ZoneCreate):
    zm = video_processor.zone_manager
    zone = Zone(
        zone_id=payload.zone_id,
        name=payload.name,
        x1=payload.x1,
        y1=payload.y1,
        x2=payload.x2,
        y2=payload.y2,
        max_capacity=payload.max_capacity,
        restricted=payload.restricted,
    )
    zm.zones[payload.zone_id] = zone
    zm.zone_counts.setdefault(payload.zone_id, 0)
    return {"message": "Zone saved", "zone_id": payload.zone_id}


@router.delete("/{zone_id}", summary="Delete a zone")
async def delete_zone(zone_id: str):
    zm = video_processor.zone_manager
    if zone_id not in zm.zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    del zm.zones[zone_id]
    zm.zone_counts.pop(zone_id, None)
    return {"message": "Zone deleted", "zone_id": zone_id}
