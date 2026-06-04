"""
Store Intelligence System — FastAPI application entry point.

Startup order:
  1. Init SQLite database (create/migrate tables)
  2. Load POS transactions from CSV
  3. Subscribe event handler to event bus
  4. Launch event bus processor as asyncio background task
  5. Start video pipeline in background thread

All real-time data flows through the event bus:
  pipeline thread  →  event_bus (asyncio.Queue)  →  handle_event()
                                                    ├── update aggregator
                                                    ├── save to DB
                                                    ├── check anomalies
                                                    └── broadcast via WebSocket
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from analytics.aggregator import aggregator
from analytics.anomaly_detector import anomaly_detector
from analytics.pos_correlator import pos_correlator
from api.routes import analytics, anomalies, events, persons, zones
from api.routes.store import router as store_router
from api.websocket import ws_manager
from config import settings
from database import SessionLocal, init_db
from models.anomaly import Anomaly as AnomalyModel
from models.event import Event as EventModel
from models.person import PersonTrack
from pipeline.video_processor import video_processor
from streaming.event_bus import event_bus
from streaming.schemas import AnomalyEvent, EventType, StoreEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structured request logging middleware
# ---------------------------------------------------------------------------

class StructuredLoggingMiddleware:
    """Logs every request with trace_id, store_id, endpoint, latency_ms, status_code."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        trace_id = str(uuid.uuid4())[:8]

        # Extract store_id from URL path if present
        path = scope.get("path", "")
        store_id = "N/A"
        parts = path.split("/")
        if "stores" in parts:
            idx = parts.index("stores")
            if idx + 1 < len(parts):
                store_id = parts[idx + 1]

        status_code_holder = [200]

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code_holder[0] = message.get("status", 200)
            await send(message)

        await self.app(scope, receive, send_wrapper)

        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        method = scope.get("method", "")
        logger.info(
            "REQUEST trace_id=%s store_id=%s method=%s endpoint=%s "
            "latency_ms=%s status=%s",
            trace_id, store_id, method, path, latency_ms, status_code_holder[0],
        )


# ---------------------------------------------------------------------------
# Central event handler
# ---------------------------------------------------------------------------

async def handle_event(event: Any) -> None:
    """Fan-out handler subscribed to the event bus."""
    if not isinstance(event, StoreEvent):
        return

    db = SessionLocal()
    try:
        # 1. Update in-memory aggregator
        aggregator.update_from_event(event)

        # 2. Persist to DB (skip FRAME_PROCESSED to avoid DB bloat)
        if event.event_type != EventType.FRAME_PROCESSED:
            db_event = EventModel(
                event_id=event.event_id,
                timestamp=event.timestamp,
                store_id=settings.store_id,
                camera_id=event.camera_id,
                event_type=event.event_type,
                person_id=event.person_id,
                visitor_id=event.person_id,  # keep in sync
                zone_id=event.zone_id,
                confidence=event.confidence,
                is_staff=False,
                dwell_ms=int((event.metadata or {}).get("dwell_time_seconds", 0) * 1000),
                bounding_box=(
                    event.bounding_box.model_dump()
                    if event.bounding_box else None
                ),
                meta=event.metadata,
            )
            db.add(db_event)

            # Upsert PersonTrack
            if event.event_type == EventType.PERSON_ENTERED and event.person_id:
                existing = (
                    db.query(PersonTrack)
                    .filter(PersonTrack.track_id == event.person_id)
                    .first()
                )
                if not existing:
                    db.add(PersonTrack(
                        track_id=event.person_id,
                        entry_camera=event.camera_id,
                        zones_visited=[],
                    ))

            elif event.event_type == EventType.PERSON_EXITED and event.person_id:
                pt = (
                    db.query(PersonTrack)
                    .filter(PersonTrack.track_id == event.person_id)
                    .first()
                )
                if pt and event.metadata:
                    pt.total_dwell_seconds += event.metadata.get(
                        "total_dwell_seconds", 0
                    )
                    visited = event.metadata.get("zones_visited", [])
                    if visited:
                        existing_visited = pt.zones_visited or []
                        pt.zones_visited = list(
                            set(existing_visited + visited)
                        )

            db.commit()

        # 3. Anomaly detection on FRAME_PROCESSED events
        if event.event_type == EventType.FRAME_PROCESSED and event.metadata:
            zone_occ = event.metadata.get("zone_occupancy", {})
            active = event.metadata.get("active_persons", 0)
            found_anomalies = anomaly_detector.analyze_frame(zone_occ, active)
            for anom in found_anomalies:
                await _persist_and_broadcast_anomaly(anom, db)

        # Also check ZONE_EXITED for dwell / rapid-transition anomalies
        if event.event_type == EventType.ZONE_EXITED and event.zone_id and event.person_id:
            meta = event.metadata or {}
            dwell = meta.get("dwell_time_seconds", 0)
            transitions = meta.get("transitions", 0)
            zm = video_processor.zone_manager
            zone = zm.zones.get(event.zone_id)
            restricted = zone.restricted if zone else False
            anom_list = anomaly_detector.analyze_zone_event(
                event.person_id, event.zone_id, dwell, restricted, transitions
            )
            for anom in anom_list:
                await _persist_and_broadcast_anomaly(anom, db)

        # 4. Broadcast raw event via WebSocket (all except FRAME_PROCESSED)
        if event.event_type != EventType.FRAME_PROCESSED:
            await ws_manager.broadcast({
                "type": "event",
                "data": {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "person_id": event.person_id,
                    "zone_id": event.zone_id,
                    "timestamp": event.timestamp.isoformat(),
                    "metadata": event.metadata,
                },
            })

        # 5. Broadcast analytics snapshot on every FRAME_PROCESSED
        if event.event_type == EventType.FRAME_PROCESSED:
            await ws_manager.broadcast({
                "type": "analytics",
                "data": aggregator.get_summary(),
            })

    except Exception as exc:
        logger.error("handle_event error: %s", exc, exc_info=True)
        db.rollback()
    finally:
        db.close()


async def _persist_and_broadcast_anomaly(anom: AnomalyEvent, db: Any) -> None:
    try:
        db.add(AnomalyModel(
            anomaly_id=anom.anomaly_id,
            timestamp=anom.timestamp,
            anomaly_type=anom.anomaly_type,
            severity=anom.severity,
            description=anom.description,
            zone_id=anom.zone_id,
            camera_id=anom.camera_id,
            meta=anom.metadata,
        ))
        db.commit()
    except Exception:
        db.rollback()

    await ws_manager.broadcast({
        "type": "anomaly",
        "data": {
            "anomaly_id": anom.anomaly_id,
            "anomaly_type": anom.anomaly_type,
            "severity": anom.severity,
            "description": anom.description,
            "zone_id": anom.zone_id,
            "timestamp": anom.timestamp.isoformat(),
        },
    })


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=== Store Intelligence System starting up ===")
    logger.info("Store ID: %s | Store: %s", settings.store_id, settings.store_name)

    # 1. Init DB
    init_db()

    # 2. Load POS transactions
    pos_correlator.load(settings.pos_csv_path)

    # 3. Event bus
    loop = asyncio.get_event_loop()
    event_bus.subscribe(handle_event)
    asyncio.create_task(event_bus.process())

    # 4. Start video pipeline
    source = settings.video_source
    video_processor.start(source, loop)

    logger.info("System ready. Dashboard → http://localhost:8000")
    logger.info("API docs    → http://localhost:8000/docs")
    yield

    logger.info("Shutting down pipeline…")
    video_processor.stop()
    logger.info("=== Store Intelligence System stopped ===")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "End-to-end Store Intelligence System: "
        "CCTV → YOLOv8+ByteTrack → Events → REST APIs + WebSocket → Live Dashboard. "
        "Store: Brigade Road Bangalore (Purplle) · Challenge submission."
    ),
    lifespan=lifespan,
)

# Structured logging middleware (must be added before CORS)
app.add_middleware(StructuredLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(store_router)          # /events/ingest, /stores/{id}/*
app.include_router(analytics.router)     # /api/v1/analytics/*
app.include_router(events.router)        # /api/v1/events
app.include_router(anomalies.router)     # /api/v1/anomalies
app.include_router(persons.router)       # /api/v1/persons
app.include_router(zones.router)         # /api/v1/zones

# Static dashboard
import os
_dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.isdir(_dashboard_dir):
    app.mount("/assets", StaticFiles(directory=_dashboard_dir), name="dashboard_static")


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    dashboard_path = os.path.join(
        os.path.dirname(__file__), "..", "dashboard", "index.html"
    )
    if os.path.isfile(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)


@app.get("/health", tags=["System"], summary="Service health check")
async def health():
    """
    Returns service status with per-store last event timestamp
    and STALE_FEED warning if no events received in 10+ minutes.
    """
    db = SessionLocal()
    try:
        last_event_per_store: dict = {}
        stale_feeds: list = []

        from sqlalchemy import text as sql_text
        rows = db.execute(sql_text(
            "SELECT store_id, MAX(timestamp) as last_ts FROM events GROUP BY store_id"
        )).fetchall()

        from datetime import timedelta
        stale_threshold = timedelta(seconds=settings.stale_feed_threshold_seconds)
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

        for row in rows:
            sid = row[0] or settings.store_id
            ts = row[1]
            if ts:
                if hasattr(ts, "isoformat"):
                    last_event_per_store[sid] = ts.isoformat()
                    # Check staleness
                    if ts.tzinfo is None:
                        from datetime import timezone as tz
                        ts = ts.replace(tzinfo=tz.utc)
                    if (now - ts) > stale_threshold:
                        stale_feeds.append(sid)
                else:
                    last_event_per_store[sid] = str(ts)

        # Add current store if no events yet
        if settings.store_id not in last_event_per_store:
            last_event_per_store[settings.store_id] = None
            stale_feeds.append(settings.store_id)

        db.close()

        return {
            "status": "healthy",
            "version": settings.app_version,
            "store_id": settings.store_id,
            "pipeline_running": video_processor.running,
            "active_ws_connections": ws_manager.connection_count,
            "last_event_per_store": last_event_per_store,
            "stale_feeds": stale_feeds,
            "stale_feed_threshold_seconds": settings.stale_feed_threshold_seconds,
            "pos_transactions_loaded": pos_correlator.get_transaction_count(settings.store_id),
        }
    except Exception as exc:
        logger.error("Health check DB error: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "error": str(exc),
                "pipeline_running": video_processor.running,
            },
        )


@app.get("/api/v1/cameras", tags=["System"])
async def get_cameras():
    return {
        "cameras": [
            {
                "camera_id": settings.camera_id,
                "name": settings.camera_name,
                "status": "active" if video_processor.running else "inactive",
                "source": settings.video_source,
                "fps": video_processor.current_stats.get("fps", 0),
                "active_persons": video_processor.current_stats.get(
                    "active_persons", 0
                ),
            }
        ]
    }

@app.websocket("/ws/live-events")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send initial snapshot immediately
        await websocket.send_text(
            json.dumps(
                {"type": "analytics", "data": aggregator.get_summary()},
                default=str,
            )
        )
        # Keep alive: echo pings
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=25)
                if msg == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
