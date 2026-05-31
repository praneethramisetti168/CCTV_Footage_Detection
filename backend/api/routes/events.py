"""Events REST endpoints — paginated event log with filters."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from models.event import Event

router = APIRouter(prefix="/api/v1/events", tags=["Events"])


@router.get("", summary="Paginated event log")
async def get_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    camera_id: Optional[str] = Query(None),
    zone_id: Optional[str] = Query(None),
    person_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Event)
    if event_type:
        q = q.filter(Event.event_type == event_type)
    if camera_id:
        q = q.filter(Event.camera_id == camera_id)
    if zone_id:
        q = q.filter(Event.zone_id == zone_id)
    if person_id:
        q = q.filter(Event.person_id == person_id)

    total = q.count()
    rows = q.order_by(Event.timestamp.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "id": e.id,
                "event_id": e.event_id,
                "timestamp": e.timestamp,
                "camera_id": e.camera_id,
                "event_type": e.event_type,
                "person_id": e.person_id,
                "zone_id": e.zone_id,
                "confidence": e.confidence,
                "bounding_box": e.bounding_box,
                "metadata": e.meta,
            }
            for e in rows
        ],
    }


@router.get("/{event_id}", summary="Get a single event by UUID")
async def get_event(event_id: str, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    e = db.query(Event).filter(Event.event_id == event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "event_id": e.event_id,
        "timestamp": e.timestamp,
        "camera_id": e.camera_id,
        "event_type": e.event_type,
        "person_id": e.person_id,
        "zone_id": e.zone_id,
        "confidence": e.confidence,
        "bounding_box": e.bounding_box,
        "metadata": e.meta,
    }
