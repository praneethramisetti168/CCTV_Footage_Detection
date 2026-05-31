"""
Store-scoped Intelligence API endpoints.

All endpoints required by the challenge specification:
  POST /events/ingest
  GET  /stores/{store_id}/metrics
  GET  /stores/{store_id}/funnel
  GET  /stores/{store_id}/heatmap
  GET  /stores/{store_id}/anomalies
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from analytics.aggregator import aggregator
from analytics.pos_correlator import pos_correlator
from config import settings
from database import get_db, SessionLocal
from models.anomaly import Anomaly
from models.event import Event
from streaming.schemas import ChallengeEvent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Store Intelligence"])

# Valid challenge event types
CHALLENGE_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
}

# Anomaly severity mapping: internal → challenge spec
SEVERITY_MAP = {
    "LOW": "INFO",
    "MEDIUM": "INFO",
    "HIGH": "WARN",
    "CRITICAL": "CRITICAL",
}

SUGGESTED_ACTIONS: Dict[str, str] = {
    "CROWD_SURGE": "Dispatch staff to manage crowd in the affected zone",
    "BILLING_QUEUE_SPIKE": "Open an additional billing counter immediately",
    "BILLING_QUEUE_JOIN": "Monitor billing queue — consider opening extra counter",
    "CONVERSION_DROP": "Review staffing and product placement in high-dwell zones",
    "DEAD_ZONE": "Check if zone signage is visible; consider repositioning products",
    "UNUSUAL_CROWD_PATTERN": "Investigate unusual customer movement pattern",
    "LONG_DWELL": "Staff member should assist the customer in this zone",
    "AFTER_HOURS_PRESENCE": "Verify security — presence detected outside operating hours",
    "RAPID_ZONE_TRANSITION": "Review CCTV — potential security concern",
}


# ---------------------------------------------------------------------------
# Helper: get or 503
# ---------------------------------------------------------------------------

def safe_db() -> Session:
    """Return a DB session or raise 503 if unavailable."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        return db
    except Exception as exc:
        logger.error("DB unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "SERVICE_UNAVAILABLE",
                "message": "Database is temporarily unavailable",
                "retry_after": 30,
            },
        )


# ---------------------------------------------------------------------------
# POST /events/ingest
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    events: List[Dict[str, Any]]


@router.post("/events/ingest", summary="Ingest batch of store events")
async def ingest_events(
    payload: IngestRequest = Body(...),
    db: Session = Depends(get_db),
):
    """
    Accepts batches of up to 500 ChallengeEvents.
    - Idempotent by event_id: duplicate event_ids are silently skipped.
    - Partial success: malformed events are rejected with per-item errors.
    - Returns structured error body on DB failure (503).
    """
    raw_events = payload.events
    if len(raw_events) > settings.max_ingest_batch_size:
        raise HTTPException(
            status_code=422,
            detail=f"Batch size {len(raw_events)} exceeds maximum {settings.max_ingest_batch_size}",
        )

    accepted = 0
    rejected = 0
    errors: List[Dict] = []
    duplicate_ids: List[str] = []

    # Pre-fetch all event_ids in this batch to check for duplicates in DB
    candidate_ids = []
    for ev in raw_events:
        if isinstance(ev, dict) and ev.get("event_id"):
            candidate_ids.append(ev["event_id"])

    try:
        existing_ids: set = set()
        if candidate_ids:
            rows = db.query(Event.event_id).filter(Event.event_id.in_(candidate_ids)).all()
            existing_ids = {r[0] for r in rows}
    except OperationalError as exc:
        logger.error("DB error on dedup check: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "message": str(exc)},
        )

    batch_seen: set = set()  # dedup within this batch itself

    for i, raw in enumerate(raw_events):
        try:
            event = ChallengeEvent.model_validate(raw)
        except (ValidationError, Exception) as exc:
            rejected += 1
            errors.append({
                "index": i,
                "event_id": raw.get("event_id") if isinstance(raw, dict) else None,
                "error": str(exc)[:200],
            })
            continue

        # Idempotency check
        if event.event_id in existing_ids or event.event_id in batch_seen:
            duplicate_ids.append(event.event_id)
            continue  # skip silently — idempotent
        batch_seen.add(event.event_id)

        # Persist
        try:
            ts = event.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            meta_dict = {}
            if event.metadata:
                meta_dict = event.metadata.model_dump(exclude_none=True)

            db_event = Event(
                event_id=event.event_id,
                timestamp=ts,
                store_id=event.store_id,
                camera_id=event.camera_id,
                event_type=event.event_type,
                visitor_id=event.visitor_id,
                person_id=event.visitor_id,  # keep person_id in sync
                zone_id=event.zone_id,
                confidence=event.confidence,
                is_staff=event.is_staff,
                dwell_ms=event.dwell_ms,
                session_seq=meta_dict.get("session_seq"),
                meta={**meta_dict, "ingested_via": "api"},
            )
            db.add(db_event)
            accepted += 1

        except Exception as exc:
            rejected += 1
            errors.append({
                "index": i,
                "event_id": event.event_id,
                "error": f"DB write failed: {str(exc)[:100]}",
            })

    try:
        db.commit()
    except OperationalError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "message": str(exc)},
        )

    return {
        "accepted": accepted,
        "rejected": rejected,
        "duplicates_skipped": len(duplicate_ids),
        "total_received": len(raw_events),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/metrics
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/metrics", summary="Real-time store metrics")
async def get_store_metrics(store_id: str, db: Session = Depends(get_db)):
    """
    Returns today's metrics for the store:
    - unique_visitors (excludes is_staff)
    - conversion_rate (POS-correlated)
    - avg_dwell_per_zone
    - queue_depth (current billing zone occupancy)
    - abandonment_rate
    """
    try:
        # Unique visitors today (ENTRY events, not staff)
        today = datetime.now(timezone.utc).date()
        today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

        unique_visitors: int = (
            db.query(func.count(func.distinct(Event.visitor_id)))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["ENTRY", "PERSON_ENTERED"]),
                Event.is_staff == False,
                Event.timestamp >= today_start,
            )
            .scalar() or 0
        )

        # Avg dwell per zone (from ZONE_DWELL / ZONE_EXIT events)
        dwell_rows = (
            db.query(Event.zone_id, func.avg(Event.dwell_ms))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["ZONE_DWELL", "ZONE_EXIT", "ZONE_EXITED"]),
                Event.is_staff == False,
                Event.zone_id.isnot(None),
                Event.dwell_ms > 0,
                Event.timestamp >= today_start,
            )
            .group_by(Event.zone_id)
            .all()
        )
        avg_dwell_per_zone = {
            row[0]: round(row[1] / 1000, 1) for row in dwell_rows if row[1]
        }

        # Supplement with in-memory aggregator dwell data
        summary = aggregator.get_summary()
        for zone_id, data in summary.get("avg_dwell_per_zone", {}).items():
            if zone_id not in avg_dwell_per_zone:
                avg_dwell_per_zone[zone_id] = data.get("avg", 0)

        # Queue depth — current billing zone occupancy from aggregator
        zone_occ = summary.get("zone_occupancy", {})
        billing_occ = zone_occ.get("checkout", zone_occ.get("BILLING", {}))
        queue_depth: int = billing_occ.get("count", 0) if isinstance(billing_occ, dict) else 0

        # Abandonment rate: visitors who joined billing queue but didn't purchase
        abandon_count: int = (
            db.query(func.count(func.distinct(Event.visitor_id)))
            .filter(
                Event.store_id == store_id,
                Event.event_type == "BILLING_QUEUE_ABANDON",
                Event.is_staff == False,
                Event.timestamp >= today_start,
            )
            .scalar() or 0
        )

        billing_joins: int = (
            db.query(func.count(func.distinct(Event.visitor_id)))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["BILLING_QUEUE_JOIN", "ZONE_ENTER"]),
                Event.zone_id.in_(["BILLING", "checkout"]),
                Event.is_staff == False,
                Event.timestamp >= today_start,
            )
            .scalar() or 0
        )

        abandonment_rate = round(abandon_count / billing_joins, 4) if billing_joins > 0 else 0.0

        # Conversion rate via POS correlator
        billing_sessions_rows = (
            db.query(Event.visitor_id, func.min(Event.timestamp).label("billing_entry"))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["BILLING_QUEUE_JOIN", "ZONE_ENTER"]),
                Event.zone_id.in_(["BILLING", "checkout"]),
                Event.is_staff == False,
                Event.timestamp >= today_start,
            )
            .group_by(Event.visitor_id)
            .all()
        )
        billing_sessions = [
            {"visitor_id": r[0], "billing_entry_time": r[1]}
            for r in billing_sessions_rows
        ]

        conversion_rate = pos_correlator.compute_conversion_rate(
            store_id=store_id,
            billing_sessions=billing_sessions,
            total_unique_visitors=unique_visitors,
        )

        return {
            "store_id": store_id,
            "window": "today",
            "unique_visitors": unique_visitors,
            "conversion_rate": conversion_rate,
            "avg_dwell_per_zone": avg_dwell_per_zone,
            "queue_depth": queue_depth,
            "abandonment_rate": abandonment_rate,
            "total_transactions": pos_correlator.get_transaction_count(store_id),
            "total_revenue_inr": pos_correlator.get_total_revenue(store_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/funnel
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/funnel", summary="Conversion funnel by session")
async def get_store_funnel(store_id: str, db: Session = Depends(get_db)):
    """
    Session-based conversion funnel:
    Entry → Zone Visit → Billing Queue → Purchase
    Re-entries do not double-count a visitor.
    """
    try:
        today = datetime.now(timezone.utc).date()
        today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

        # Stage 1: Unique visitors (first ENTRY per visitor, not staff)
        entry_visitors: set = set(
            r[0] for r in db.query(func.distinct(Event.visitor_id))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["ENTRY", "PERSON_ENTERED"]),
                Event.is_staff == False,
                Event.visitor_id.isnot(None),
                Event.timestamp >= today_start,
            ).all()
        )
        # Exclude re-entries — keep only first-time visitors
        reentry_visitors: set = set(
            r[0] for r in db.query(func.distinct(Event.visitor_id))
            .filter(
                Event.store_id == store_id,
                Event.event_type == "REENTRY",
                Event.timestamp >= today_start,
            ).all()
        )
        # Re-entrants are already counted once — don't double-count
        total_entries = len(entry_visitors)

        # Stage 2: Visitors who visited any zone (not just entry)
        zone_visitors: set = set(
            r[0] for r in db.query(func.distinct(Event.visitor_id))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["ZONE_ENTER", "ZONE_ENTERED"]),
                Event.is_staff == False,
                Event.visitor_id.isnot(None),
                Event.timestamp >= today_start,
            ).all()
        ) & entry_visitors  # must be within known visitors

        total_zone = len(zone_visitors)

        # Stage 3: Visitors who reached billing queue
        billing_visitors: set = set(
            r[0] for r in db.query(func.distinct(Event.visitor_id))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["BILLING_QUEUE_JOIN", "ZONE_ENTER"]),
                Event.zone_id.in_(["BILLING", "checkout"]),
                Event.is_staff == False,
                Event.visitor_id.isnot(None),
                Event.timestamp >= today_start,
            ).all()
        ) & entry_visitors

        total_billing = len(billing_visitors)

        # Stage 4: Purchased (POS-correlated)
        billing_sessions = [
            {"visitor_id": vid, "billing_entry_time": today_start}
            for vid in billing_visitors
        ]
        conversion_rate = pos_correlator.compute_conversion_rate(
            store_id=store_id,
            billing_sessions=billing_sessions,
            total_unique_visitors=max(total_entries, 1),
        )
        total_purchased = round(conversion_rate * total_entries)

        def drop_off(prev: int, curr: int) -> float:
            if prev == 0:
                return 0.0
            return round((prev - curr) / prev * 100, 1)

        funnel = [
            {"stage": "ENTRY",         "count": total_entries,  "drop_off_pct": 0.0},
            {"stage": "ZONE_VISIT",    "count": total_zone,     "drop_off_pct": drop_off(total_entries, total_zone)},
            {"stage": "BILLING_QUEUE", "count": total_billing,  "drop_off_pct": drop_off(total_zone, total_billing)},
            {"stage": "PURCHASE",      "count": total_purchased,"drop_off_pct": drop_off(total_billing, total_purchased)},
        ]

        return {
            "store_id": store_id,
            "funnel": funnel,
            "re_entry_sessions_excluded": len(reentry_visitors),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/heatmap
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/heatmap", summary="Zone visit frequency heatmap")
async def get_store_heatmap(store_id: str, db: Session = Depends(get_db)):
    """
    Zone visit frequency + avg dwell, normalised 0-100.
    data_confidence: LOW if < 20 sessions.
    """
    try:
        today = datetime.now(timezone.utc).date()
        today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

        rows = (
            db.query(
                Event.zone_id,
                func.count(func.distinct(Event.visitor_id)).label("visit_count"),
                func.avg(Event.dwell_ms).label("avg_dwell_ms"),
            )
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["ZONE_ENTER", "ZONE_ENTERED", "ZONE_DWELL"]),
                Event.is_staff == False,
                Event.zone_id.isnot(None),
                Event.timestamp >= today_start,
            )
            .group_by(Event.zone_id)
            .all()
        )

        # Supplement with live in-memory zone occupancy
        live_occ = aggregator.get_summary().get("zone_occupancy", {})

        zones_data = []
        for row in rows:
            zones_data.append({
                "zone_id": row[0],
                "visit_count": row[1] or 0,
                "avg_dwell_ms": round(row[2] or 0),
            })

        # Add live zones not yet in DB
        for zid, occ in live_occ.items():
            if not any(z["zone_id"] == zid for z in zones_data):
                zones_data.append({
                    "zone_id": zid,
                    "visit_count": occ.get("count", 0),
                    "avg_dwell_ms": 0,
                })

        # Normalise visit_count 0-100
        max_visits = max((z["visit_count"] for z in zones_data), default=1) or 1
        for z in zones_data:
            z["heat_score"] = round(z["visit_count"] / max_visits * 100)

        total_sessions = sum(z["visit_count"] for z in zones_data)
        data_confidence = "LOW" if total_sessions < 20 else "HIGH"

        return {
            "store_id": store_id,
            "zones": zones_data,
            "data_confidence": data_confidence,
            "total_zone_sessions": total_sessions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/anomalies
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/anomalies", summary="Active store anomalies")
async def get_store_anomalies(store_id: str, db: Session = Depends(get_db)):
    """
    Returns active (unresolved) anomalies with challenge-spec severity mapping
    and suggested_action strings.
    """
    try:
        rows = (
            db.query(Anomaly)
            .filter(Anomaly.resolved == False)
            .order_by(Anomaly.timestamp.desc())
            .limit(50)
            .all()
        )

        # Also check for DEAD_ZONE (no visits in last 30 min)
        thirty_min_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
        recent_zones = set(
            r[0] for r in
            db.query(func.distinct(Event.zone_id))
            .filter(
                Event.store_id == store_id,
                Event.event_type.in_(["ZONE_ENTER", "ZONE_ENTERED"]),
                Event.timestamp >= thirty_min_ago,
            ).all()
            if r[0]
        )

        anomaly_list = []
        for a in rows:
            atype = a.anomaly_type
            severity = SEVERITY_MAP.get(a.severity, "INFO")
            action = SUGGESTED_ACTIONS.get(atype, "Monitor the situation")
            anomaly_list.append({
                "anomaly_id": a.anomaly_id,
                "type": atype,
                "severity": severity,
                "description": a.description,
                "suggested_action": action,
                "detected_at": a.timestamp.isoformat() if a.timestamp else None,
                "zone_id": a.zone_id,
                "camera_id": a.camera_id,
            })

        # Inject DEAD_ZONE anomalies for known zones not seen recently
        live_zones = set(aggregator.get_summary().get("zone_occupancy", {}).keys())
        dead_zones = live_zones - recent_zones
        for zone_id in dead_zones:
            anomaly_list.append({
                "anomaly_id": str(uuid.uuid4()),
                "type": "DEAD_ZONE",
                "severity": "INFO",
                "description": f"No visitors in zone '{zone_id}' for 30+ minutes",
                "suggested_action": SUGGESTED_ACTIONS["DEAD_ZONE"],
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "zone_id": zone_id,
                "camera_id": None,
            })

        return {
            "store_id": store_id,
            "anomalies": anomaly_list,
            "total": len(anomaly_list),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "message": str(exc)},
        )
