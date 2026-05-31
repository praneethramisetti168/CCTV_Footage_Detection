"""
Zone Manager — defines named rectangular zones on the store floor plan
and tracks which zone each active person ID is currently in.

Zone coordinates use a 640×480 logical space and are scaled to the
actual frame resolution at runtime.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Zone:
    zone_id: str
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    max_capacity: int = 10
    color: Tuple[int, int, int] = (0, 255, 0)  # BGR for OpenCV
    restricted: bool = False


@dataclass
class TrackState:
    track_id: str
    current_zone: Optional[str] = None
    zone_entry_time: float = 0.0
    total_dwell_seconds: float = 0.0
    zones_visited: List[str] = field(default_factory=list)
    last_zone_transitions: List[float] = field(default_factory=list)  # timestamps


# ---------------------------------------------------------------------------
# Zone Manager
# ---------------------------------------------------------------------------

class ZoneManager:
    """Tracks person positions relative to named store zones."""

    # Default zone layout in 640×480 logical space
    _DEFAULT_ZONES: List[dict] = [
        dict(zone_id="entrance",  name="Entrance",            x1=0,   y1=0,   x2=213, y2=240, max_capacity=8,  color=(57, 255, 20),   restricted=False),
        dict(zone_id="checkout",  name="Checkout",            x1=213, y1=0,   x2=427, y2=240, max_capacity=5,  color=(255, 165, 0),   restricted=False),
        dict(zone_id="aisle_1",   name="Aisle A",             x1=0,   y1=240, x2=213, y2=480, max_capacity=6,  color=(30, 144, 255),  restricted=False),
        dict(zone_id="aisle_2",   name="Aisle B",             x1=213, y1=240, x2=427, y2=480, max_capacity=6,  color=(255, 0, 200),   restricted=False),
        dict(zone_id="storage",   name="Storage (Restricted)",x1=427, y1=0,   x2=640, y2=480, max_capacity=2,  color=(0, 0, 220),     restricted=True),
    ]

    def __init__(self) -> None:
        self.zones: Dict[str, Zone] = {}
        self.track_states: Dict[str, TrackState] = {}
        self.zone_counts: Dict[str, int] = {}
        self._scaled = False
        self._load_defaults()

    def _load_defaults(self) -> None:
        for z in self._DEFAULT_ZONES:
            zone = Zone(**z)
            self.zones[zone.zone_id] = zone
            self.zone_counts[zone.zone_id] = 0

    def scale_to_frame(self, width: int, height: int) -> None:
        """Scale zone coordinates from the 640×480 logical space to actual frame size."""
        if self._scaled:
            return
        sx, sy = width / 640, height / 480
        for zone in self.zones.values():
            zone.x1 = int(zone.x1 * sx)
            zone.y1 = int(zone.y1 * sy)
            zone.x2 = int(zone.x2 * sx)
            zone.y2 = int(zone.y2 * sy)
        self._scaled = True

    # ------------------------------------------------------------------ lookups

    def get_zone_for_point(self, cx: float, cy: float) -> Optional[str]:
        for zone_id, zone in self.zones.items():
            if zone.x1 <= cx <= zone.x2 and zone.y1 <= cy <= zone.y2:
                return zone_id
        return None

    # ------------------------------------------------------------------ updates

    def update_track(self, track_id: str, cx: float, cy: float) -> List[dict]:
        """
        Update a track's position.
        Returns a list of zone-transition event dicts (may be empty).
        """
        events: List[dict] = []
        now = time.time()

        if track_id not in self.track_states:
            self.track_states[track_id] = TrackState(track_id=track_id)

        state = self.track_states[track_id]
        new_zone = self.get_zone_for_point(cx, cy)

        if new_zone != state.current_zone:
            # --- Exit old zone ---
            if state.current_zone is not None:
                dwell = now - state.zone_entry_time
                state.total_dwell_seconds += dwell
                self.zone_counts[state.current_zone] = max(
                    0, self.zone_counts.get(state.current_zone, 1) - 1
                )
                events.append({
                    "event_type": "ZONE_EXITED",
                    "zone_id": state.current_zone,
                    "dwell_time_seconds": round(dwell, 2),
                })

            # --- Enter new zone ---
            if new_zone is not None:
                self.zone_counts[new_zone] = self.zone_counts.get(new_zone, 0) + 1
                state.zone_entry_time = now
                if new_zone not in state.zones_visited:
                    state.zones_visited.append(new_zone)
                state.last_zone_transitions.append(now)
                # Keep only last 10 transitions for rapid-transition detection
                state.last_zone_transitions = state.last_zone_transitions[-10:]
                events.append({
                    "event_type": "ZONE_ENTERED",
                    "zone_id": new_zone,
                    "transitions": len(state.last_zone_transitions),
                })

            state.current_zone = new_zone

        return events

    def remove_track(self, track_id: str) -> List[dict]:
        """Called when a track disappears. Cleans up state and returns exit events."""
        events: List[dict] = []
        if track_id not in self.track_states:
            return events

        state = self.track_states.pop(track_id)
        if state.current_zone is not None:
            self.zone_counts[state.current_zone] = max(
                0, self.zone_counts.get(state.current_zone, 1) - 1
            )
            dwell = time.time() - state.zone_entry_time
            state.total_dwell_seconds += dwell
            events.append({
                "event_type": "ZONE_EXITED",
                "zone_id": state.current_zone,
                "dwell_time_seconds": round(dwell, 2),
            })

        events.append({
            "event_type": "PERSON_EXITED",
            "total_dwell_seconds": round(state.total_dwell_seconds, 2),
            "zones_visited": state.zones_visited,
        })
        return events

    # ------------------------------------------------------------------ queries

    def get_zone_occupancy(self) -> Dict[str, dict]:
        result = {}
        for zone_id, zone in self.zones.items():
            count = self.zone_counts.get(zone_id, 0)
            result[zone_id] = {
                "name": zone.name,
                "count": count,
                "max_capacity": zone.max_capacity,
                "utilization_pct": round(count / max(zone.max_capacity, 1) * 100, 1),
                "restricted": zone.restricted,
                "color": zone.color,
            }
        return result

    def get_track_state(self, track_id: str) -> Optional[TrackState]:
        return self.track_states.get(track_id)

    def active_track_count(self) -> int:
        return len(self.track_states)
