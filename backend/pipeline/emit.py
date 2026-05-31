"""
Event emitter — builds ChallengeEvent objects and writes them to JSONL / API.

Usage:
    emitter = EventEmitter(store_id="STORE_BLR_001", output_path="events_output/events.jsonl")
    emitter.emit(session, event_type="ENTRY", zone_id=None, camera_id="CAM_ENTRY_01", confidence=0.92)
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _json_default(obj):
    """Fallback JSON serializer for numpy scalars and other non-standard types."""
    try:
        import numpy as np
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

# Clip recording base datetime (from footage timestamp 10/04/2026 ~20:10 IST)
# IST = UTC+5:30, so 20:10 IST = 14:40 UTC
CLIP_BASE_UTC = datetime(2026, 4, 10, 14, 40, 0, tzinfo=timezone.utc)


def frame_to_timestamp(frame_idx: int, fps: float, clip_base: datetime = CLIP_BASE_UTC) -> datetime:
    """Convert frame index to ISO-8601 UTC timestamp based on clip start time."""
    offset_seconds = frame_idx / max(fps, 1)
    return datetime(
        clip_base.year, clip_base.month, clip_base.day,
        clip_base.hour, clip_base.minute, clip_base.second,
        tzinfo=timezone.utc,
    ).replace(microsecond=0) + __import__("datetime").timedelta(seconds=offset_seconds)


class EventEmitter:
    """Builds and emits ChallengeEvent objects to JSONL file and/or API."""

    def __init__(
        self,
        store_id: str,
        output_path: str = "events_output/events.jsonl",
        api_url: Optional[str] = None,
        api_batch_size: int = 50,
    ) -> None:
        self.store_id = store_id
        self.output_path = output_path
        self.api_url = api_url
        self.api_batch_size = api_batch_size

        self._batch: list = []
        self._total_emitted = 0

        # Ensure output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        self._fh = open(output_path, "a", encoding="utf-8", buffering=1)
        logger.info("EventEmitter → %s (API: %s)", output_path, api_url or "disabled")

    def emit(
        self,
        visitor_id: str,
        camera_id: str,
        event_type: str,
        timestamp: datetime,
        zone_id: Optional[str],
        dwell_ms: int,
        is_staff: bool,
        confidence: float,
        session_seq: int = 0,
        queue_depth: Optional[int] = None,
        sku_zone: Optional[str] = None,
    ) -> dict:
        """Build and emit one ChallengeEvent. Returns the event dict."""
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_id": zone_id,
            "dwell_ms": int(dwell_ms),           # cast: may be numpy int
            "is_staff": bool(is_staff),           # cast: may be numpy bool_
            "confidence": round(float(confidence), 3),
            "metadata": {
                "queue_depth": int(queue_depth) if queue_depth is not None else None,
                "sku_zone": sku_zone or zone_id,
                "session_seq": int(session_seq),  # cast: may be numpy int
            },
        }

        # Write to JSONL (use default= for any remaining numpy scalars)
        self._fh.write(json.dumps(event, default=_json_default) + "\n")
        self._total_emitted += 1

        # Batch for API
        if self.api_url:
            self._batch.append(event)
            if len(self._batch) >= self.api_batch_size:
                self._flush_to_api()

        return event

    def flush(self) -> None:
        """Flush remaining batch to API and close file."""
        if self.api_url and self._batch:
            self._flush_to_api()
        self._fh.flush()
        logger.info("EventEmitter flushed. Total events emitted: %d", self._total_emitted)

    def close(self) -> None:
        self.flush()
        self._fh.close()

    def _flush_to_api(self) -> None:
        if not self._batch:
            return
        batch = self._batch[:]
        self._batch = []
        try:
            resp = httpx.post(
                f"{self.api_url}/events/ingest",
                json={"events": batch},
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info(
                    "API ingest: accepted=%d rejected=%d duplicates=%d",
                    data.get("accepted", 0),
                    data.get("rejected", 0),
                    data.get("duplicates_skipped", 0),
                )
            else:
                logger.warning("API ingest returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("API ingest failed: %s — events saved to JSONL only", exc)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
