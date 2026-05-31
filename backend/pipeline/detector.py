"""
YOLOv8 + ByteTrack detector wrapper.

Wraps Ultralytics YOLO with ByteTrack tracking enabled.
The model is lazily loaded on first call so startup is fast.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# COCO class index for "person"
PERSON_CLASS_ID = 0


class Detector:
    """Runs YOLOv8 inference with ByteTrack multi-object tracking."""

    def __init__(self, model_path: str = "yolov8n.pt") -> None:
        self.model_path = model_path
        self._model = None  # lazy load
        logger.info("Detector initialised (model will load on first detect call)")

    def _load(self) -> None:
        from ultralytics import YOLO
        logger.info("Loading YOLO model: %s (this may download ~6 MB on first run)", self.model_path)
        self._model = YOLO(self.model_path)
        logger.info("YOLO model loaded successfully")

    def detect(
        self,
        frame: np.ndarray,
        conf_threshold: float = 0.40,
    ) -> Tuple[List[Dict[str, Any]], Optional[Any]]:
        """
        Run YOLOv8 + ByteTrack on a single frame.

        Returns
        -------
        detections : list of dicts with keys:
            track_id, confidence, bbox (x/y/w/h), centroid (x/y)
        result : raw Ultralytics result object (for annotation / frame plotting)
        """
        if self._model is None:
            self._load()

        results = self._model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=[PERSON_CLASS_ID],
            conf=conf_threshold,
            verbose=False,
            imgsz=640,
        )

        detections: List[Dict[str, Any]] = []
        result = results[0] if results else None

        if result is not None and result.boxes is not None:
            for box in result.boxes:
                if box.id is None:
                    continue  # tracker hasn't assigned an ID yet
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                conf = float(box.conf[0])
                raw_id = int(box.id[0])
                w, h = x2 - x1, y2 - y1
                cx, cy = x1 + w / 2, y1 + h / 2

                detections.append({
                    "track_id": f"person_{raw_id}",
                    "raw_id": raw_id,
                    "confidence": round(conf, 3),
                    "bbox": {
                        "x": round(x1, 1),
                        "y": round(y1, 1),
                        "w": round(w, 1),
                        "h": round(h, 1),
                    },
                    "centroid": {"x": round(cx, 1), "y": round(cy, 1)},
                })

        return detections, result
