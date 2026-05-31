"""
Pydantic schemas for the event streaming layer.

Two schema families:
  1. Internal pipeline schemas (StoreEvent, AnomalyEvent) — used by VideoProcessor,
     EventBus, WebSocket broadcasts. Kept for backward compatibility.
  2. Challenge-spec schemas (ChallengeEvent) — used by POST /events/ingest
     and the detection pipeline output.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Internal event types (pipeline → EventBus → WebSocket)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    # Internal pipeline events
    PERSON_ENTERED = "PERSON_ENTERED"
    PERSON_EXITED = "PERSON_EXITED"
    ZONE_ENTERED = "ZONE_ENTERED"
    ZONE_EXITED = "ZONE_EXITED"
    FRAME_PROCESSED = "FRAME_PROCESSED"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"

    # Challenge-spec event types (also stored in DB via ingest endpoint)
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class AnomalyType(str, Enum):
    CROWD_SURGE = "CROWD_SURGE"
    UNUSUAL_CROWD_PATTERN = "UNUSUAL_CROWD_PATTERN"
    LONG_DWELL = "LONG_DWELL"
    AFTER_HOURS_PRESENCE = "AFTER_HOURS_PRESENCE"
    RAPID_ZONE_TRANSITION = "RAPID_ZONE_TRANSITION"
    BILLING_QUEUE_SPIKE = "BILLING_QUEUE_SPIKE"
    CONVERSION_DROP = "CONVERSION_DROP"
    DEAD_ZONE = "DEAD_ZONE"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Internal core schemas
# ---------------------------------------------------------------------------

class BoundingBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    camera_id: str = "cam_01"
    event_type: EventType
    person_id: Optional[str] = None
    zone_id: Optional[str] = None
    confidence: Optional[float] = None
    bounding_box: Optional[BoundingBox] = None
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(use_enum_values=True)


class AnomalyEvent(BaseModel):
    anomaly_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    anomaly_type: AnomalyType
    severity: Severity
    description: str
    zone_id: Optional[str] = None
    camera_id: str = "cam_01"
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(use_enum_values=True)


# ---------------------------------------------------------------------------
# Challenge-spec schemas (POST /events/ingest)
# ---------------------------------------------------------------------------

class ChallengeEventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class ChallengeEvent(BaseModel):
    """Exact schema required by the challenge specification."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str  # ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, etc.
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: ChallengeEventMetadata = Field(default_factory=ChallengeEventMetadata)

    model_config = ConfigDict(extra="allow")
