"""
detect.py — Main CCTV detection pipeline script.

Processes one video clip with a specified camera role and emits structured
ChallengeEvents to JSONL and optionally to the ingest API.

Usage:
    python detect.py --clip "CCTV Footage/CAM 3.mp4" --camera CAM_ENTRY_01 --role entry
    python detect.py --clip "CCTV Footage/CAM 5.mp4" --camera CAM_BILLING_01 --role billing --api http://localhost:8000

Camera roles:
    entry    — CAM_ENTRY_01: detect entry/exit direction from door threshold
    floor    — CAM_FLOOR_01/02: zone dwell tracking across product areas
    storage  — CAM_STORAGE_01: all detections flagged is_staff=True
    billing  — CAM_BILLING_01: billing queue join/abandon, queue depth
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

# When run as a script, ensure backend/ is on the path
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from pipeline.emit import EventEmitter, frame_to_timestamp
from pipeline.tracker import SessionTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("detect")

STORE_ID = "STORE_BLR_001"
PERSON_CLASS_ID = 0

# Zone definitions per camera role (pixel-space, will be scaled to frame)
# Defined in 1920×1080 space matching the actual clip resolution
CAMERA_ZONES: Dict[str, List[dict]] = {
    "entry": [
        # Entry threshold line: upper half = outside, lower half = inside
        {"zone_id": "ENTRY_THRESHOLD", "name": "Entry Threshold",
         "x1": 0, "y1": 400, "x2": 1920, "y2": 700, "type": "threshold"},
        {"zone_id": "ENTRY_EXIT", "name": "Entry/Exit",
         "x1": 0, "y1": 0, "x2": 1920, "y2": 400, "type": "outside"},
    ],
    "floor": [
        {"zone_id": "SKINCARE",  "name": "Skincare",  "x1": 0,    "y1": 0,   "x2": 640,  "y2": 1080},
        {"zone_id": "MAKEUP",    "name": "Makeup",    "x1": 640,  "y1": 0,   "x2": 1280, "y2": 1080},
        {"zone_id": "HAIRCARE",  "name": "Haircare",  "x1": 1280, "y1": 0,   "x2": 1920, "y2": 1080},
        {"zone_id": "FRAGRANCE", "name": "Fragrance", "x1": 0,    "y1": 540, "x2": 960,  "y2": 1080},
    ],
    "storage": [
        {"zone_id": "STORAGE", "name": "Storage (Restricted)",
         "x1": 0, "y1": 0, "x2": 1920, "y2": 1080, "restricted": True},
    ],
    "billing": [
        {"zone_id": "BILLING",       "name": "Billing Counter",
         "x1": 0,   "y1": 0,   "x2": 960,  "y2": 1080},
        {"zone_id": "BILLING_QUEUE", "name": "Billing Queue",
         "x1": 960, "y1": 0,   "x2": 1920, "y2": 1080},
    ],
}

# Staff detection heuristics
STAFF_CAMERA_ROLES = {"storage"}  # all detections in storage = staff


def is_staff_detection(role: str, bbox: Dict, frame: np.ndarray) -> bool:
    """
    Heuristic staff detection:
    1. If camera is storage role → always staff
    2. If person is stationary behind a counter (billing role, left side, y < 400) → cashier
    3. Dark uniform detection: high ratio of dark pixels in torso region
    """
    if role in STAFF_CAMERA_ROLES:
        return True

    if role == "billing":
        cx = bbox.get("x", 0) + bbox.get("w", 0) / 2
        cy = bbox.get("y", 0) + bbox.get("h", 0) / 2
        # Cashier is typically at left side of billing frame, behind counter
        if cx < 350 and cy < 500:
            return True

    # Dark uniform check: analyse torso region
    x, y, w, h = int(bbox.get("x", 0)), int(bbox.get("y", 0)), int(bbox.get("w", 40)), int(bbox.get("h", 90))
    torso_y1 = y + int(h * 0.25)
    torso_y2 = y + int(h * 0.65)
    torso_x1, torso_x2 = x, x + w

    fh, fw = frame.shape[:2]
    torso_y1 = max(0, min(torso_y1, fh - 1))
    torso_y2 = max(0, min(torso_y2, fh - 1))
    torso_x1 = max(0, min(torso_x1, fw - 1))
    torso_x2 = max(0, min(torso_x2, fw - 1))

    if torso_y2 <= torso_y1 or torso_x2 <= torso_x1:
        return False

    torso_patch = frame[torso_y1:torso_y2, torso_x1:torso_x2]
    if torso_patch.size == 0:
        return False

    # Convert to grayscale and check dark pixel ratio
    gray = cv2.cvtColor(torso_patch, cv2.COLOR_BGR2GRAY)
    dark_ratio = (gray < 60).sum() / gray.size
    return dark_ratio > 0.55  # >55% dark pixels → likely dark uniform


def get_zone(cx: float, cy: float, zones: List[dict]) -> Optional[str]:
    """Find which zone a centroid falls in."""
    for z in zones:
        if z["x1"] <= cx <= z["x2"] and z["y1"] <= cy <= z["y2"]:
            return z["zone_id"]
    return None


def detect_entry_direction(prev_cy: Optional[float], curr_cy: float, threshold_y: float) -> Optional[str]:
    """
    For entry camera: determine ENTRY (inward) vs EXIT (outward) by vertical movement.
    Threshold line divides inside (below) from outside (above).
    """
    if prev_cy is None:
        return None
    crossing_down = prev_cy < threshold_y <= curr_cy   # moving down = entering
    crossing_up = prev_cy >= threshold_y > curr_cy     # moving up = exiting
    if crossing_down:
        return "ENTRY"
    if crossing_up:
        return "EXIT"
    return None


class PipelineProcessor:
    """Processes one video clip and emits ChallengeEvents."""

    def __init__(
        self,
        clip_path: str,
        camera_id: str,
        role: str,
        emitter: EventEmitter,
        frame_skip: int = 3,
        conf_threshold: float = 0.35,
    ) -> None:
        self.clip_path = clip_path
        self.camera_id = camera_id
        self.role = role
        self.emitter = emitter
        self.frame_skip = frame_skip
        self.conf_threshold = conf_threshold

        self.zones = CAMERA_ZONES.get(role, [])
        self.tracker = SessionTracker(store_id=STORE_ID)

        self._model = None
        self._frame_h = 1080
        self._frame_w = 1920

        # For entry camera: track centroid history for direction detection
        self._prev_cy: Dict[str, float] = {}  # track_id → prev cy

        # For ZONE_DWELL: track when we last emitted a dwell event per (track, zone)
        self._last_dwell_emit: Dict[str, float] = {}  # f"{tid}:{zone}" → time.time()

        # For billing queue depth
        self._billing_zone_count: int = 0

    def _load_model(self):
        from ultralytics import YOLO
        logger.info("Loading YOLOv8n model…")
        self._model = YOLO("yolov8n.pt")
        logger.info("Model loaded")

    def process(self) -> int:
        """Run full clip processing. Returns total events emitted."""
        if self._model is None:
            self._load_model()

        cap = cv2.VideoCapture(self.clip_path)
        if not cap.isOpened():
            logger.error("Cannot open clip: %s", self.clip_path)
            return 0

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        logger.info(
            "Processing %s | role=%s | %dx%d @ %.0ffps | %d frames",
            os.path.basename(self.clip_path), self.role,
            self._frame_w, self._frame_h, fps, total_frames
        )

        # Scale zones to actual frame size
        self._scale_zones(self._frame_w, self._frame_h)

        frame_idx = 0
        active_track_ids: Set[str] = set()

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx += 1
            if frame_idx % self.frame_skip != 0:
                continue

            ts = frame_to_timestamp(frame_idx, fps)

            # Run YOLOv8 + ByteTrack
            try:
                results = self._model.track(
                    frame,
                    persist=True,
                    tracker="bytetrack.yaml",
                    classes=[PERSON_CLASS_ID],
                    conf=self.conf_threshold,
                    verbose=False,
                    imgsz=640,
                )
            except Exception as exc:
                logger.warning("Detection error frame %d: %s", frame_idx, exc)
                continue

            result = results[0] if results else None
            current_ids: Set[str] = set()

            if result is not None and result.boxes is not None:
                # Count billing zone occupancy for queue depth
                if self.role == "billing":
                    self._billing_zone_count = 0

                for box in result.boxes:
                    if box.id is None:
                        continue
                    raw_id = int(box.id[0])
                    track_id = f"{self.camera_id}_t{raw_id}"
                    current_ids.add(track_id)

                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    conf = float(box.conf[0])
                    w_box, h_box = x2 - x1, y2 - y1
                    cx, cy = x1 + w_box / 2, y1 + h_box / 2
                    bbox = {"x": round(x1, 1), "y": round(y1, 1),
                            "w": round(w_box, 1), "h": round(h_box, 1)}

                    staff = is_staff_detection(self.role, bbox, frame)
                    session, entry_event = self.tracker.update(track_id, self.camera_id, bbox, conf, is_staff=staff)

                    # ── ENTRY / REENTRY ────────────────────────────────────
                    if entry_event in ("ENTRY", "REENTRY"):
                        if self.role == "entry":
                            # Wait until we see direction of crossing
                            self._prev_cy[track_id] = cy
                        else:
                            seq = self.tracker.next_seq(track_id)
                            self.emitter.emit(
                                visitor_id=session.visitor_id,
                                camera_id=self.camera_id,
                                event_type=entry_event,
                                timestamp=ts,
                                zone_id=None,
                                dwell_ms=0,
                                is_staff=staff,
                                confidence=conf,
                                session_seq=seq,
                            )

                    # ── Entry camera: direction-based ENTRY/EXIT ───────────
                    if self.role == "entry" and track_id in active_track_ids:
                        prev_cy = self._prev_cy.get(track_id)
                        threshold_y = self._frame_h * 0.45  # ~45% down = door threshold
                        direction = detect_entry_direction(prev_cy, cy, threshold_y)
                        if direction:
                            seq = self.tracker.next_seq(track_id)
                            self.emitter.emit(
                                visitor_id=session.visitor_id,
                                camera_id=self.camera_id,
                                event_type=direction,
                                timestamp=ts,
                                zone_id=None,
                                dwell_ms=0,
                                is_staff=staff,
                                confidence=conf,
                                session_seq=seq,
                            )
                        self._prev_cy[track_id] = cy

                    # ── Zone events (floor, billing) ───────────────────────
                    if self.role != "entry":
                        zone_id = get_zone(cx, cy, self.zones)

                        # Zone enter/exit (tracked via session)
                        prev_zone = getattr(session, "_current_zone", None)
                        if zone_id != prev_zone:
                            if prev_zone is not None:
                                # ZONE_EXIT
                                dwell_s = time.time() - getattr(session, "_zone_entry_ts", time.time())
                                seq = self.tracker.next_seq(track_id)
                                self.emitter.emit(
                                    visitor_id=session.visitor_id,
                                    camera_id=self.camera_id,
                                    event_type="ZONE_EXIT",
                                    timestamp=ts,
                                    zone_id=prev_zone,
                                    dwell_ms=int(dwell_s * 1000),
                                    is_staff=staff,
                                    confidence=conf,
                                    session_seq=seq,
                                    sku_zone=prev_zone,
                                )
                            if zone_id is not None:
                                # ZONE_ENTER
                                seq = self.tracker.next_seq(track_id)
                                session._current_zone = zone_id
                                session._zone_entry_ts = time.time()
                                if zone_id not in session.zones_visited:
                                    session.zones_visited.append(zone_id)

                                # Billing queue events
                                queue_depth = None
                                evt_type = "ZONE_ENTER"
                                if zone_id in ("BILLING", "BILLING_QUEUE"):
                                    self._billing_zone_count += 1
                                    queue_depth = self._billing_zone_count
                                    if self._billing_zone_count > 1:
                                        evt_type = "BILLING_QUEUE_JOIN"
                                    self.tracker.record_billing_entry(track_id)

                                self.emitter.emit(
                                    visitor_id=session.visitor_id,
                                    camera_id=self.camera_id,
                                    event_type=evt_type,
                                    timestamp=ts,
                                    zone_id=zone_id,
                                    dwell_ms=0,
                                    is_staff=staff,
                                    confidence=conf,
                                    session_seq=seq,
                                    queue_depth=queue_depth,
                                    sku_zone=zone_id,
                                )
                            else:
                                session._current_zone = None

                        # ZONE_DWELL: emit every 30s of continuous dwell
                        if zone_id:
                            dwell_key = f"{track_id}:{zone_id}"
                            last_dwell = self._last_dwell_emit.get(dwell_key, 0)
                            zone_entry_ts = getattr(session, "_zone_entry_ts", time.time())
                            elapsed = time.time() - zone_entry_ts
                            if elapsed >= 30 and (time.time() - last_dwell) >= 30:
                                self._last_dwell_emit[dwell_key] = time.time()
                                seq = self.tracker.next_seq(track_id)
                                self.emitter.emit(
                                    visitor_id=session.visitor_id,
                                    camera_id=self.camera_id,
                                    event_type="ZONE_DWELL",
                                    timestamp=ts,
                                    zone_id=zone_id,
                                    dwell_ms=int(elapsed * 1000),
                                    is_staff=staff,
                                    confidence=conf,
                                    session_seq=seq,
                                    sku_zone=zone_id,
                                )

            # ── Handle lost tracks ─────────────────────────────────────────
            lost_ids = active_track_ids - current_ids
            for lost_id in lost_ids:
                session = self.tracker.remove(lost_id)
                if session is None:
                    continue

                # Final ZONE_EXIT if in a zone
                current_zone = getattr(session, "_current_zone", None)
                if current_zone:
                    dwell_s = time.time() - getattr(session, "_zone_entry_ts", time.time())
                    # Check BILLING_QUEUE_ABANDON
                    evt_type = "ZONE_EXIT"
                    if current_zone in ("BILLING", "BILLING_QUEUE") and session.billing_entry_time:
                        # If no POS transaction in window → abandon
                        evt_type = "BILLING_QUEUE_ABANDON"
                    seq = self.tracker.next_seq(lost_id)
                    self.emitter.emit(
                        visitor_id=session.visitor_id,
                        camera_id=self.camera_id,
                        event_type=evt_type,
                        timestamp=ts,
                        zone_id=current_zone,
                        dwell_ms=int(dwell_s * 1000),
                        is_staff=session.is_staff,
                        confidence=0.5,
                        session_seq=seq,
                    )

                # EXIT event (for entry/floor cameras)
                if self.role != "storage":
                    total_dwell_ms = int((time.time() - session.entry_time) * 1000)
                    self.emitter.emit(
                        visitor_id=session.visitor_id,
                        camera_id=self.camera_id,
                        event_type="EXIT",
                        timestamp=ts,
                        zone_id=None,
                        dwell_ms=total_dwell_ms,
                        is_staff=session.is_staff,
                        confidence=0.5,
                        session_seq=self.tracker.next_seq(lost_id),
                    )

            active_track_ids = current_ids

            if frame_idx % 300 == 0:
                progress = frame_idx / max(total_frames, 1) * 100
                logger.info("  [%s] %.0f%% (%d/%d frames)", self.camera_id, progress, frame_idx, total_frames)

        cap.release()
        return self.emitter._total_emitted

    def _scale_zones(self, width: int, height: int) -> None:
        """Scale zone definitions from 1920×1080 to actual frame size."""
        sx = width / 1920
        sy = height / 1080
        for z in self.zones:
            z["x1"] = int(z["x1"] * sx)
            z["y1"] = int(z["y1"] * sy)
            z["x2"] = int(z["x2"] * sx)
            z["y2"] = int(z["y2"] * sy)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CCTV Detection Pipeline")
    parser.add_argument("--clip", required=True, help="Path to video clip")
    parser.add_argument("--camera", required=True, help="Camera ID (e.g. CAM_ENTRY_01)")
    parser.add_argument("--role", required=True,
                        choices=["entry", "floor", "storage", "billing"],
                        help="Camera role")
    parser.add_argument("--output", default="events_output/events.jsonl",
                        help="Output JSONL path")
    parser.add_argument("--api", default=None,
                        help="API base URL for live ingest (e.g. http://localhost:8000)")
    parser.add_argument("--skip", type=int, default=3,
                        help="Process every Nth frame (default: 3)")
    parser.add_argument("--conf", type=float, default=0.35,
                        help="YOLO confidence threshold (default: 0.35)")
    args = parser.parse_args()

    if not os.path.isfile(args.clip):
        logger.error("Clip not found: %s", args.clip)
        sys.exit(1)

    # Per-camera output file
    cam_output = args.output.replace(".jsonl", f"_{args.camera}.jsonl")

    with EventEmitter(
        store_id=STORE_ID,
        output_path=cam_output,
        api_url=args.api,
    ) as emitter:
        processor = PipelineProcessor(
            clip_path=args.clip,
            camera_id=args.camera,
            role=args.role,
            emitter=emitter,
            frame_skip=args.skip,
            conf_threshold=args.conf,
        )
        total = processor.process()
        logger.info("Done. %d events emitted → %s", total, cam_output)


if __name__ == "__main__":
    main()
