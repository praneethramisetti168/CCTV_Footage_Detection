"""
Anomaly Detector — two complementary strategies:

1. Rule-based  — hard thresholds (capacity exceeded, restricted zone dwell, after-hours)
2. Statistical — Z-score on rolling zone-occupancy history detects unusual surges
                 even when counts stay below absolute thresholds.

All methods return None when no anomaly, or an AnomalyEvent when triggered.
A per-key cooldown prevents alert storms.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from backend.config import settings
from backend.streaming.schemas import AnomalyEvent, AnomalyType, Severity

logger = logging.getLogger(__name__)


class AnomalyDetector:
    COOLDOWNS: Dict[str, float] = {
        "crowd_surge": 30.0,
        "unusual_pattern": 60.0,
        "long_dwell": 0.0,       # De-dup via alerted_keys set
        "after_hours": 3600.0,
        "rapid_transition": 60.0,
    }

    def __init__(self) -> None:
        # Rolling history for Z-score (last 50 readings per zone)
        self.zone_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        # Cooldown tracking: key -> last_alert_timestamp
        self._last_alert: Dict[str, float] = defaultdict(float)
        # One-time alert keys (no cooldown, just prevent duplicates)
        self._alerted: set = set()

    # ------------------------------------------------------------------ helpers

    def _cooldown_ok(self, key: str, kind: str) -> bool:
        cd = self.COOLDOWNS.get(kind, 30.0)
        if cd == 0.0:
            return key not in self._alerted
        return (time.time() - self._last_alert[key]) > cd

    def _mark(self, key: str, kind: str) -> None:
        self.COOLDOWNS.get(kind)  # just touch
        self._last_alert[key] = time.time()
        self._alerted.add(key)

    # ------------------------------------------------------------------ checks

    def check_crowd_surge(
        self, zone_id: str, count: int, max_capacity: int, zone_name: str
    ) -> Optional[AnomalyEvent]:
        """Rule-based capacity check + Z-score statistical check."""
        history = self.zone_history[zone_id]
        history.append(count)

        # --- Rule-based ---
        if count > max_capacity:
            key = f"surge_{zone_id}"
            if self._cooldown_ok(key, "crowd_surge"):
                self._mark(key, "crowd_surge")
                sev = Severity.HIGH if count > max_capacity * 1.5 else Severity.MEDIUM
                return AnomalyEvent(
                    anomaly_type=AnomalyType.CROWD_SURGE,
                    severity=sev,
                    description=(
                        f"Capacity exceeded in {zone_name}: {count}/{max_capacity} people"
                    ),
                    zone_id=zone_id,
                    metadata={"count": count, "max_capacity": max_capacity},
                )

        # --- Z-score statistical ---
        if len(history) >= 15:
            arr = np.array(list(history)[:-1])
            mean, std = arr.mean(), arr.std()
            if std > 0:
                z = (count - mean) / std
                if z > settings.anomaly_zscore_threshold:
                    key = f"zscore_{zone_id}"
                    if self._cooldown_ok(key, "unusual_pattern"):
                        self._mark(key, "unusual_pattern")
                        return AnomalyEvent(
                            anomaly_type=AnomalyType.UNUSUAL_CROWD_PATTERN,
                            severity=Severity.MEDIUM,
                            description=(
                                f"Statistical crowd surge in {zone_name} "
                                f"(Z={z:.2f}, count={count})"
                            ),
                            zone_id=zone_id,
                            metadata={
                                "z_score": round(float(z), 3),
                                "mean": round(float(mean), 2),
                                "std": round(float(std), 2),
                                "count": count,
                            },
                        )
        return None

    def check_long_dwell(
        self, track_id: str, zone_id: str, dwell_seconds: float, restricted: bool
    ) -> Optional[AnomalyEvent]:
        """Alert when a person dwells too long, lower threshold for restricted zones."""
        threshold = settings.dwell_threshold_seconds
        if restricted:
            threshold = max(10, threshold // 3)

        if dwell_seconds >= threshold:
            key = f"dwell_{track_id}_{zone_id}"
            if self._cooldown_ok(key, "long_dwell"):
                self._mark(key, "long_dwell")
                sev = Severity.HIGH if restricted else Severity.MEDIUM
                return AnomalyEvent(
                    anomaly_type=AnomalyType.LONG_DWELL,
                    severity=sev,
                    description=(
                        f"Person {track_id} has been in {zone_id} "
                        f"for {int(dwell_seconds)}s (limit {threshold}s)"
                    ),
                    zone_id=zone_id,
                    metadata={
                        "track_id": track_id,
                        "dwell_seconds": round(dwell_seconds, 1),
                        "threshold": threshold,
                        "restricted": restricted,
                    },
                )
        return None

    def check_after_hours(self, person_count: int) -> Optional[AnomalyEvent]:
        """Detect any presence outside store operating hours (IST ≈ UTC+5:30)."""
        now = datetime.now(timezone.utc)
        ist_hour = (now.hour * 60 + now.minute + 330) // 60 % 24

        if person_count > 0 and (
            ist_hour < settings.store_open_hour or ist_hour >= settings.store_close_hour
        ):
            key = f"afterhours_{now.strftime('%Y-%m-%d-%H')}"
            if self._cooldown_ok(key, "after_hours"):
                self._mark(key, "after_hours")
                return AnomalyEvent(
                    anomaly_type=AnomalyType.AFTER_HOURS_PRESENCE,
                    severity=Severity.CRITICAL,
                    description=(
                        f"Activity detected outside operating hours "
                        f"(IST {ist_hour:02d}:xx, store {settings.store_open_hour}–{settings.store_close_hour})"
                    ),
                    metadata={
                        "ist_hour": ist_hour,
                        "person_count": person_count,
                        "open": settings.store_open_hour,
                        "close": settings.store_close_hour,
                    },
                )
        return None

    def check_rapid_transitions(
        self, track_id: str, zone_id: str, transitions: int
    ) -> Optional[AnomalyEvent]:
        """Flag suspiciously rapid zone hopping (>5 transitions in a short window)."""
        if transitions >= 6:
            key = f"rapid_{track_id}"
            if self._cooldown_ok(key, "rapid_transition"):
                self._mark(key, "rapid_transition")
                return AnomalyEvent(
                    anomaly_type=AnomalyType.RAPID_ZONE_TRANSITION,
                    severity=Severity.LOW,
                    description=(
                        f"Person {track_id} made {transitions} rapid zone transitions"
                    ),
                    zone_id=zone_id,
                    metadata={"track_id": track_id, "transitions": transitions},
                )
        return None

    # ------------------------------------------------------------------ batch analysis

    def analyze_frame(
        self, zone_occupancy: dict, active_persons: int
    ) -> List[AnomalyEvent]:
        """Run all frame-level anomaly checks and return detected anomalies."""
        detected: List[AnomalyEvent] = []

        for zone_id, occ in zone_occupancy.items():
            count = occ.get("count", 0)
            max_cap = occ.get("max_capacity", 10)
            name = occ.get("name", zone_id)
            anomaly = self.check_crowd_surge(zone_id, count, max_cap, name)
            if anomaly:
                detected.append(anomaly)

        after_hours = self.check_after_hours(active_persons)
        if after_hours:
            detected.append(after_hours)

        return detected

    def analyze_zone_event(
        self, track_id: str, zone_id: str, dwell_seconds: float,
        restricted: bool, transitions: int
    ) -> List[AnomalyEvent]:
        """Run per-zone-exit anomaly checks."""
        detected: List[AnomalyEvent] = []

        ld = self.check_long_dwell(track_id, zone_id, dwell_seconds, restricted)
        if ld:
            detected.append(ld)

        rt = self.check_rapid_transitions(track_id, zone_id, transitions)
        if rt:
            detected.append(rt)

        return detected


# Singleton
anomaly_detector = AnomalyDetector()
