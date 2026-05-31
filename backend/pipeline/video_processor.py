"""
Video Processor — the main pipeline orchestrator.

Supports two modes:
  REAL mode  : reads frames from a webcam/video file/RTSP stream, runs
               YOLOv8+ByteTrack, updates zone manager, publishes events.
  DEMO mode  : generates synthetic person detections in a background thread,
               no camera / YOLO required.  Great for demos and local dev.

Set VIDEO_SOURCE=demo in .env to use demo mode.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import threading
import time
from typing import Any, Dict, Optional, Set

import cv2
import numpy as np

from config import settings
from pipeline.detector import Detector
from pipeline.zone_manager import ZoneManager
from streaming.event_bus import event_bus
from streaming.schemas import BoundingBox, EventType, StoreEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: EventType, **kwargs: Any) -> StoreEvent:
    return StoreEvent(
        camera_id=settings.camera_id,
        event_type=event_type,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Demo synthetic source
# ---------------------------------------------------------------------------

class DemoPerson:
    """A synthetic person moving around the store."""

    ZONE_COORDS = [
        (100, 120),  # entrance
        (330, 120),  # checkout
        (100, 360),  # aisle_1
        (330, 360),  # aisle_2
        (530, 240),  # storage
    ]

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.x = random.uniform(30, 580)
        self.y = random.uniform(30, 440)
        self.target_x, self.target_y = random.choice(self.ZONE_COORDS)
        self.speed = random.uniform(1.5, 4.0)
        self.alive = True
        self.age = 0
        self.max_age = random.randint(60, 300)  # frames alive

    def step(self) -> None:
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = math.hypot(dx, dy)
        if dist < 10:
            self.target_x, self.target_y = random.choice(self.ZONE_COORDS)
        else:
            self.x += (dx / dist) * self.speed
            self.y += (dy / dist) * self.speed
        self.age += 1
        if self.age >= self.max_age:
            self.alive = False

    @property
    def centroid(self):
        return self.x, self.y

    def bbox(self):
        w, h = 40, 90
        return {"x": self.x - w / 2, "y": self.y - h / 2, "w": w, "h": h}


class DemoFrameRenderer:
    """Renders synthetic annotated frames for the video feed."""

    COLORS = [
        (57, 255, 20), (30, 144, 255), (255, 165, 0),
        (255, 0, 200), (0, 200, 255),
    ]

    def render(self, persons: list, zone_manager: ZoneManager) -> np.ndarray:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:] = (18, 24, 38)  # dark blue-grey bg

        # Draw zones
        for zone_id, zone in zone_manager.zones.items():
            count = zone_manager.zone_counts.get(zone_id, 0)
            alpha_layer = frame.copy()
            cv2.rectangle(alpha_layer, (zone.x1, zone.y1), (zone.x2, zone.y2), zone.color, -1)
            cv2.addWeighted(alpha_layer, 0.12, frame, 0.88, 0, frame)
            cv2.rectangle(frame, (zone.x1, zone.y1), (zone.x2, zone.y2), zone.color, 2)
            label = f"{zone.name}: {count}/{zone.max_capacity}"
            cv2.putText(frame, label, (zone.x1 + 5, zone.y1 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, zone.color, 1)

        # Draw persons
        for p in persons:
            bb = p.bbox()
            x, y, w, h = int(bb["x"]), int(bb["y"]), int(bb["w"]), int(bb["h"])
            color = self.COLORS[p.pid % len(self.COLORS)]
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.circle(frame, (int(p.x), int(p.y)), 4, color, -1)
            cv2.putText(frame, f"P{p.pid}", (x, y - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        # Overlay info
        ts = time.strftime("%H:%M:%S")
        cv2.putText(frame, f"[DEMO] {ts}  |  Active: {len(persons)}",
                    (10, 465), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        return frame


# ---------------------------------------------------------------------------
# Main Video Processor
# ---------------------------------------------------------------------------

class VideoProcessor:
    """Runs the detection/tracking pipeline in a background thread."""

    def __init__(self) -> None:
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Shared annotated frame
        self._frame_lock = threading.Lock()
        self._annotated_frame: Optional[np.ndarray] = None

        # Stats
        self.current_stats: Dict[str, Any] = {
            "fps": 0.0,
            "active_persons": 0,
            "total_persons": 0,
        }

        # Sub-components
        self.detector = Detector(model_path=settings.yolo_model)
        self.zone_manager = ZoneManager()

    # ------------------------------------------------------------------ public API

    def start(self, source: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        event_bus.set_loop(loop)
        self.running = True

        is_demo = (str(source).lower() == "demo")
        target = self._demo_loop if is_demo else self._real_loop
        self._thread = threading.Thread(target=target, args=(source,), daemon=True)
        self._thread.start()
        mode = "DEMO" if is_demo else f"REAL ({source})"
        logger.info("VideoProcessor started — mode: %s", mode)

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return self._annotated_frame.copy() if self._annotated_frame is not None else None

    # ------------------------------------------------------------------ demo loop

    def _demo_loop(self, _source: Any) -> None:
        persons: Dict[int, DemoPerson] = {}
        renderer = DemoFrameRenderer()
        known_ids: Set[str] = set()
        next_pid = 1
        frame_idx = 0
        fps_start = time.time()
        fps_count = 0

        # Demo: occasionally inject crowd surge for anomaly testing
        _force_surge_at = random.randint(100, 200)

        while self.running:
            frame_idx += 1
            fps_count += 1

            # Spawn new persons (0-8 at a time)
            if len(persons) < 8 and random.random() < 0.03:
                # Occasionally force a surge
                to_add = 4 if frame_idx == _force_surge_at else 1
                for _ in range(to_add):
                    if len(persons) < 12:
                        persons[next_pid] = DemoPerson(next_pid)
                        next_pid += 1

            # Update persons
            for pid in list(persons.keys()):
                p = persons[pid]
                p.step()
                if not p.alive:
                    del persons[pid]

            alive = list(persons.values())

            # Scale zones once
            if not self.zone_manager._scaled:
                self.zone_manager.scale_to_frame(640, 480)

            # Process each person
            current_track_ids: Set[str] = set()
            for p in alive:
                tid = f"person_{p.pid}"
                current_track_ids.add(tid)

                if tid not in known_ids:
                    known_ids.add(tid)
                    self.current_stats["total_persons"] += 1
                    event_bus.publish_threadsafe(_make_event(
                        EventType.PERSON_ENTERED,
                        person_id=tid,
                        confidence=round(random.uniform(0.75, 0.98), 3),
                        bounding_box=BoundingBox(**p.bbox()),
                        metadata={"centroid": {"x": p.x, "y": p.y}},
                    ))

                cx, cy = p.centroid
                zone_events = self.zone_manager.update_track(tid, cx, cy)
                for ze in zone_events:
                    event_bus.publish_threadsafe(_make_event(
                        EventType(ze["event_type"]),
                        person_id=tid,
                        zone_id=ze.get("zone_id"),
                        metadata={
                            "dwell_time_seconds": ze.get("dwell_time_seconds", 0),
                            "transitions": ze.get("transitions", 0),
                        },
                    ))

            # Handle disappeared — tracks seen before but not alive now
            for lost in (known_ids - current_track_ids):
                exit_evts = self.zone_manager.remove_track(lost)
                for ee in exit_evts:
                    event_bus.publish_threadsafe(_make_event(
                        EventType(ee["event_type"]) if ee["event_type"] in [e.value for e in EventType]
                        else EventType.PERSON_EXITED,
                        person_id=lost,
                        zone_id=ee.get("zone_id"),
                        metadata={
                            "total_dwell_seconds": ee.get("total_dwell_seconds", 0),
                            "zones_visited": ee.get("zones_visited", []),
                        },
                    ))

            self.current_stats["active_persons"] = len(alive)

            # FPS
            elapsed = time.time() - fps_start
            if elapsed >= 1.0:
                self.current_stats["fps"] = round(fps_count / elapsed, 1)
                fps_count = 0
                fps_start = time.time()

            # Frame-level event
            event_bus.publish_threadsafe(_make_event(
                EventType.FRAME_PROCESSED,
                metadata={
                    "active_persons": len(alive),
                    "total_persons": self.current_stats["total_persons"],
                    "fps": self.current_stats["fps"],
                    "zone_occupancy": self.zone_manager.get_zone_occupancy(),
                },
            ))

            # Render frame
            annotated = renderer.render(alive, self.zone_manager)
            with self._frame_lock:
                self._annotated_frame = annotated

            time.sleep(0.05)  # ~20 synthetic FPS

    # ------------------------------------------------------------------ real loop

    def _real_loop(self, source: Any) -> None:
        if str(source).isdigit():
            source = int(source)

        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            logger.error("Cannot open video source: %s", source)
            return

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
        self.zone_manager.scale_to_frame(width, height)

        known_ids: Set[str] = set()
        active_ids: Set[str] = set()
        frame_idx = 0
        fps_start = time.time()
        fps_count = 0

        while self.running:
            ret, frame = cap.read()
            if not ret:
                # Loop file
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            frame_idx += 1
            fps_count += 1

            # Skip frames for performance
            if frame_idx % max(1, settings.frame_skip) != 0:
                continue

            # Detect + track
            try:
                detections, result = self.detector.detect(
                    frame, conf_threshold=settings.yolo_conf_threshold
                )
            except Exception as exc:
                logger.error("Detection error: %s", exc)
                time.sleep(0.1)
                continue

            current_ids: Set[str] = set()

            for det in detections:
                tid = det["track_id"]
                current_ids.add(tid)
                cx = det["centroid"]["x"]
                cy = det["centroid"]["y"]

                if tid not in known_ids:
                    known_ids.add(tid)
                    self.current_stats["total_persons"] += 1
                    event_bus.publish_threadsafe(_make_event(
                        EventType.PERSON_ENTERED,
                        person_id=tid,
                        confidence=det["confidence"],
                        bounding_box=BoundingBox(**det["bbox"]),
                        metadata={"centroid": det["centroid"]},
                    ))

                zone_events = self.zone_manager.update_track(tid, cx, cy)
                for ze in zone_events:
                    event_bus.publish_threadsafe(_make_event(
                        EventType(ze["event_type"]),
                        person_id=tid,
                        zone_id=ze.get("zone_id"),
                        metadata={
                            "dwell_time_seconds": ze.get("dwell_time_seconds", 0),
                            "transitions": ze.get("transitions", 0),
                        },
                    ))

            # Handle lost tracks
            for lost in active_ids - current_ids:
                exit_evts = self.zone_manager.remove_track(lost)
                for ee in exit_evts:
                    event_bus.publish_threadsafe(_make_event(
                        EventType(ee["event_type"]) if ee["event_type"] in [e.value for e in EventType]
                        else EventType.PERSON_EXITED,
                        person_id=lost,
                        zone_id=ee.get("zone_id"),
                        metadata={
                            "total_dwell_seconds": ee.get("total_dwell_seconds", 0),
                        },
                    ))

            active_ids = current_ids
            self.current_stats["active_persons"] = len(current_ids)

            # FPS
            elapsed = time.time() - fps_start
            if elapsed >= 1.0:
                self.current_stats["fps"] = round(fps_count / elapsed, 1)
                fps_count = 0
                fps_start = time.time()

            # Frame event
            event_bus.publish_threadsafe(_make_event(
                EventType.FRAME_PROCESSED,
                metadata={
                    "active_persons": len(current_ids),
                    "total_persons": self.current_stats["total_persons"],
                    "fps": self.current_stats["fps"],
                    "zone_occupancy": self.zone_manager.get_zone_occupancy(),
                },
            ))

            # Annotate
            annotated = result.plot() if result is not None else frame.copy()
            self._draw_zones(annotated)
            with self._frame_lock:
                self._annotated_frame = annotated

            time.sleep(0.01)

        cap.release()

    def _draw_zones(self, frame: np.ndarray) -> None:
        for zone_id, zone in self.zone_manager.zones.items():
            count = self.zone_manager.zone_counts.get(zone_id, 0)
            cv2.rectangle(frame, (zone.x1, zone.y1), (zone.x2, zone.y2), zone.color, 2)
            label = f"{zone.name}: {count}/{zone.max_capacity}"
            cv2.putText(frame, label, (zone.x1 + 5, zone.y1 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, zone.color, 1)


# Singleton
video_processor = VideoProcessor()
