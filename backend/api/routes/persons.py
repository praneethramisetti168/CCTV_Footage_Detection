"""Person tracks REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models.person import PersonTrack

router = APIRouter(prefix="/api/v1/persons", tags=["Persons"])


@router.get("", summary="List tracked persons")
async def get_persons(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.query(PersonTrack).count()
    rows = (
        db.query(PersonTrack)
        .order_by(PersonTrack.last_seen.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "persons": [
            {
                "track_id": p.track_id,
                "first_seen": p.first_seen,
                "last_seen": p.last_seen,
                "zones_visited": p.zones_visited,
                "total_dwell_seconds": p.total_dwell_seconds,
                "entry_camera": p.entry_camera,
            }
            for p in rows
        ],
    }


@router.get("/{track_id}", summary="Get a single person track")
async def get_person(track_id: str, db: Session = Depends(get_db)):
    p = db.query(PersonTrack).filter(PersonTrack.track_id == track_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Person not found")
    return {
        "track_id": p.track_id,
        "first_seen": p.first_seen,
        "last_seen": p.last_seen,
        "zones_visited": p.zones_visited,
        "total_dwell_seconds": p.total_dwell_seconds,
        "entry_camera": p.entry_camera,
    }
