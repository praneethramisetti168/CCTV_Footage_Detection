"""
Real-time metrics aggregator.

Subscribes to the event bus and maintains in-memory aggregates:
  - Per-hour footfall counts
  - Per-minute timeseries for charts
  - Zone occupancy snapshot
  - Per-zone dwell-time statistics
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List

logger = logging.getLogger(__name__)


class StoreAggregator:
    def __init__(self) -> None:
        # Footfall
        self.hourly_footfall: Dict[str, int] = defaultdict(int)
        self.footfall_timeseries: Deque[Dict[str, Any]] = deque(maxlen=300)

        # Dwell times per zone (rolling window of last N values)
        self.zone_dwell_times: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )

        # Live snapshot (updated on every FRAME_PROCESSED event)
        self.active_persons: int = 0
        self.total_persons: int = 0
        self.fps: float = 0.0
        self.zone_occupancy: Dict[str, Any] = {}

        # Track IDs seen today
        self._seen_tracks: set = set()

    # ------------------------------------------------------------------ update

    def update_from_event(self, event: Any) -> None:
        """Update aggregates from a StoreEvent."""
        from streaming.schemas import EventType

        etype = event.event_type

        if etype == EventType.PERSON_ENTERED:
            if event.person_id and event.person_id not in self._seen_tracks:
                self._seen_tracks.add(event.person_id)
                hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00")
                self.hourly_footfall[hour_key] += 1
                self.total_persons += 1

        elif etype == EventType.ZONE_EXITED:
            if event.zone_id and event.metadata:
                dwell = event.metadata.get("dwell_time_seconds", 0.0)
                if dwell > 0:
                    self.zone_dwell_times[event.zone_id].append(dwell)

        elif etype == EventType.FRAME_PROCESSED:
            if event.metadata:
                self.active_persons = event.metadata.get("active_persons", 0)
                self.fps = event.metadata.get("fps", 0.0)
                self.zone_occupancy = event.metadata.get("zone_occupancy", {})

                self.footfall_timeseries.append({
                    "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    "count": self.active_persons,
                })

    # ------------------------------------------------------------------ queries

    def get_summary(self) -> Dict[str, Any]:
        avg_dwell: Dict[str, Any] = {}
        for zone_id, dwells in self.zone_dwell_times.items():
            if dwells:
                lst = list(dwells)
                avg_dwell[zone_id] = {
                    "avg": round(sum(lst) / len(lst), 1),
                    "max": round(max(lst), 1),
                    "min": round(min(lst), 1),
                    "samples": len(lst),
                }

        return {
            "active_persons": self.active_persons,
            "total_persons_today": self.total_persons,
            "fps": self.fps,
            "zone_occupancy": self.zone_occupancy,
            "avg_dwell_per_zone": avg_dwell,
            "hourly_footfall": dict(self.hourly_footfall),
            "footfall_timeseries": list(self.footfall_timeseries)[-60:],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_hourly_footfall(self) -> List[Dict[str, Any]]:
        return [
            {"hour": k, "count": v}
            for k, v in sorted(self.hourly_footfall.items())
        ]


# Singleton
aggregator = StoreAggregator()
