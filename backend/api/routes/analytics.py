"""Analytics REST endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.analytics.aggregator import aggregator
from backend.database import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


@router.get("/summary", summary="Real-time analytics snapshot")
async def get_summary():
    """Returns the current in-memory analytics snapshot (live, no DB query)."""
    return aggregator.get_summary()


@router.get("/footfall", summary="Footfall counts by hour or day")
async def get_footfall(
    period: str = Query("hourly", enum=["hourly", "daily"]),
    db: Session = Depends(get_db),
):
    if period == "hourly":
        return {
            "period": "hourly",
            "data": aggregator.get_hourly_footfall(),
        }
    # Daily — query DB
    rows = db.execute(text("""
        SELECT strftime('%Y-%m-%d', timestamp) AS day, COUNT(*) AS count
        FROM events
        WHERE event_type = 'PERSON_ENTERED'
        GROUP BY day
        ORDER BY day DESC
        LIMIT 30
    """)).fetchall()
    return {
        "period": "daily",
        "data": [{"date": r[0], "count": r[1]} for r in rows],
    }


@router.get("/heatmap", summary="Zone-level occupancy heatmap")
async def get_heatmap():
    return {
        "zones": aggregator.zone_occupancy,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/dwell-time", summary="Average dwell time per zone")
async def get_dwell_time():
    return {"dwell_time_by_zone": aggregator.get_summary()["avg_dwell_per_zone"]}


@router.get("/timeseries", summary="Footfall timeseries for chart")
async def get_timeseries(limit: int = Query(60, ge=10, le=300)):
    ts = aggregator.footfall_timeseries
    return {"data": list(ts)[-limit:]}
