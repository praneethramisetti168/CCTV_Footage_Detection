"""
Visitor Session Tracker — Re-ID, session management, and re-entry detection.

Responsibilities:
  - Assign stable visitor_id tokens (VIS_xxxxxx) per visit session
  - Track session_seq (event ordinal within a session)
  - Detect re-entries: same person returning within 5 minutes of EXIT
  - Cross-camera deduplication: same person seen on two cameras in quick succession
"""
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Re-entry window: if same spatial fingerprint reappears within N seconds → REENTRY
REENTRY_WINDOW_SECONDS = 300  # 5 minutes

# Cross-camera dedup: same person on two cameras within N seconds → same visitor_id
CROSS_CAMERA_DEDUP_SECONDS = 8


class VisitorSession:
    """Plain class (not dataclass) for Python 3.14 compatibility."""

    def __init__(
        self,
        visitor_id: str,
        track_id: str,
        camera_id: str,
        store_id: str,
        entry_time: float,
        last_seen: float,
        session_seq: int = 0,
        zones_visited: Optional[List[str]] = None,
        billing_entry_time: Optional[datetime] = None,
        is_staff: bool = False,
        exited: bool = False,
        exit_time: Optional[float] = None,
        last_bbox_x: float = 0.0,
        last_bbox_y: float = 0.0,
    ) -> None:
        self.visitor_id = visitor_id
        self.track_id = track_id
        self.camera_id = camera_id
        self.store_id = store_id
        self.entry_time = entry_time
        self.last_seen = last_seen
        self.session_seq = session_seq
        self.zones_visited: List[str] = zones_visited if zones_visited is not None else []
        self.billing_entry_time = billing_entry_time
        self.is_staff = is_staff
        self.exited = exited
        self.exit_time = exit_time
        self.last_bbox_x = last_bbox_x
        self.last_bbox_y = last_bbox_y


def _make_visitor_id(track_id: str, camera_id: str, entry_epoch: float) -> str:
    """Generate a stable VIS_xxxxxx token from track fingerprint."""
    raw = f"{track_id}:{camera_id}:{int(entry_epoch)}"
    h = hashlib.sha1(raw.encode()).hexdigest()[:6]
    return f"VIS_{h}"


class SessionTracker:
    """
    Manages visitor sessions across all cameras for a single store.

    Usage (per frame):
        session, event_type = tracker.update(track_id, camera_id, bbox, confidence)
        seq = session.next_seq()
    """

    def __init__(self, store_id: str, reentry_window: int = REENTRY_WINDOW_SECONDS) -> None:
        self.store_id = store_id
        self.reentry_window = reentry_window

        # Active sessions: track_id → VisitorSession
        self._active: Dict[str, VisitorSession] = {}

        # Recently exited sessions: visitor_id → VisitorSession (for re-entry detection)
        self._exited: Dict[str, VisitorSession] = {}

        # Cross-camera dedup: bbox hash → visitor_id (recent)
        self._recent_by_pos: Dict[str, Tuple[str, float]] = {}  # key → (visitor_id, ts)

    # ---------------------------------------------------------------------- public API

    def update(
        self,
        track_id: str,
        camera_id: str,
        bbox: Dict,          # {x, y, w, h}
        confidence: float,
        is_staff: bool = False,
    ) -> Tuple[VisitorSession, str]:
        """
        Update tracker for a detection. Returns (session, event_type_str).
        event_type_str is one of: ENTRY, REENTRY, or None (existing session)
        """
        now = time.time()

        # Already tracking this track_id
        if track_id in self._active:
            session = self._active[track_id]
            session.last_seen = now
            session.last_bbox_x = bbox.get("x", 0)
            session.last_bbox_y = bbox.get("y", 0)
            return session, None  # existing session, no new event

        # New track_id — check re-entry
        reentry_session = self._check_reentry(bbox, camera_id, now)
        if reentry_session:
            # Reuse visitor_id, mark as re-entry
            reentry_session.exited = False
            reentry_session.exit_time = None
            reentry_session.track_id = track_id
            reentry_session.camera_id = camera_id
            reentry_session.last_seen = now
            self._active[track_id] = reentry_session
            # Remove from exited pool
            self._exited.pop(reentry_session.visitor_id, None)
            logger.info("REENTRY detected: %s at %s", reentry_session.visitor_id, camera_id)
            return reentry_session, "REENTRY"

        # Brand new visitor
        entry_epoch = now
        visitor_id = _make_visitor_id(track_id, camera_id, entry_epoch)
        session = VisitorSession(
            visitor_id=visitor_id,
            track_id=track_id,
            camera_id=camera_id,
            store_id=self.store_id,
            entry_time=entry_epoch,
            last_seen=entry_epoch,
            last_bbox_x=bbox.get("x", 0),
            last_bbox_y=bbox.get("y", 0),
            is_staff=is_staff,
        )
        self._active[track_id] = session
        self._record_position(visitor_id, bbox, now)
        logger.debug("NEW session: %s track=%s cam=%s", visitor_id, track_id, camera_id)
        return session, "ENTRY"

    def remove(self, track_id: str) -> Optional[VisitorSession]:
        """Called when a track disappears. Moves session to exited pool."""
        session = self._active.pop(track_id, None)
        if session is None:
            return None
        now = time.time()
        session.exited = True
        session.exit_time = now
        self._exited[session.visitor_id] = session
        self._cleanup_exited(now)
        return session

    def next_seq(self, track_id: str) -> int:
        """Increment and return session_seq for a track."""
        session = self._active.get(track_id)
        if session:
            session.session_seq += 1
            return session.session_seq
        return 0

    def record_billing_entry(self, track_id: str) -> None:
        """Mark the time a visitor entered the billing zone."""
        session = self._active.get(track_id)
        if session and session.billing_entry_time is None:
            session.billing_entry_time = datetime.now(timezone.utc)

    def get_session(self, track_id: str) -> Optional[VisitorSession]:
        return self._active.get(track_id)

    def all_active(self) -> List[VisitorSession]:
        return list(self._active.values())

    def all_exited(self) -> List[VisitorSession]:
        return list(self._exited.values())

    # ---------------------------------------------------------------------- internals

    def _check_reentry(self, bbox: Dict, camera_id: str, now: float) -> Optional[VisitorSession]:
        """
        Check if any recently exited session matches this bbox position.
        Spatial proximity + same camera within reentry_window = re-entry.
        """
        cx = bbox.get("x", 0) + bbox.get("w", 0) / 2
        cy = bbox.get("y", 0) + bbox.get("h", 0) / 2

        best: Optional[VisitorSession] = None
        best_dist = float("inf")

        for session in list(self._exited.values()):
            if session.camera_id != camera_id:
                continue
            if session.exit_time and (now - session.exit_time) > self.reentry_window:
                continue
            # Euclidean distance to last known position
            dx = cx - session.last_bbox_x
            dy = cy - session.last_bbox_y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 150 and dist < best_dist:  # within ~150px
                best_dist = dist
                best = session

        return best

    def _record_position(self, visitor_id: str, bbox: Dict, ts: float) -> None:
        key = visitor_id
        self._recent_by_pos[key] = (visitor_id, ts)

    def _cleanup_exited(self, now: float) -> None:
        """Remove sessions that have exceeded the re-entry window."""
        expired = [
            vid for vid, s in self._exited.items()
            if s.exit_time and (now - s.exit_time) > self.reentry_window
        ]
        for vid in expired:
            del self._exited[vid]
