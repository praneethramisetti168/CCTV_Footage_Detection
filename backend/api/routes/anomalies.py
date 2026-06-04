"""Anomalies REST endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.anomaly import Anomaly

router = APIRouter(prefix="/api/v1/anomalies", tags=["Anomalies"])


@router.get("", summary="List detected anomalies")
async def get_anomalies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    severity: Optional[str] = Query(None, enum=["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
    anomaly_type: Optional[str] = None,
    resolved: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Anomaly)
    if severity:
        q = q.filter(Anomaly.severity == severity)
    if anomaly_type:
        q = q.filter(Anomaly.anomaly_type == anomaly_type)
    if resolved is not None:
        q = q.filter(Anomaly.resolved == resolved)

    total = q.count()
    rows = q.order_by(Anomaly.timestamp.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "anomalies": [
            {
                "id": a.id,
                "anomaly_id": a.anomaly_id,
                "timestamp": a.timestamp,
                "anomaly_type": a.anomaly_type,
                "severity": a.severity,
                "description": a.description,
                "zone_id": a.zone_id,
                "camera_id": a.camera_id,
                "resolved": a.resolved,
                "metadata": a.meta,
            }
            for a in rows
        ],
    }


@router.patch("/{anomaly_id}/resolve", summary="Mark anomaly as resolved")
async def resolve_anomaly(anomaly_id: str, db: Session = Depends(get_db)):
    a = db.query(Anomaly).filter(Anomaly.anomaly_id == anomaly_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    a.resolved = True
    db.commit()
    return {"message": "Anomaly resolved", "anomaly_id": anomaly_id}


@router.get("/stats/summary", summary="Anomaly counts by type and severity")
async def anomaly_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    by_severity = (
        db.query(Anomaly.severity, func.count(Anomaly.id))
        .group_by(Anomaly.severity)
        .all()
    )
    by_type = (
        db.query(Anomaly.anomaly_type, func.count(Anomaly.id))
        .group_by(Anomaly.anomaly_type)
        .all()
    )
    return {
        "by_severity": {r[0]: r[1] for r in by_severity},
        "by_type": {r[0]: r[1] for r in by_type},
        "total_unresolved": db.query(Anomaly).filter(Anomaly.resolved == False).count(),
    }
