"""
Cabin Distraction Detector — Traffic AI Morocco
================================================
Wraps the real YOLOv8 ONNX model (best_Distracted_driver.onnx),
trained on 5 classes: Open Eye, Closed Eye, Cigarette, Phone, Seatbelt.

This is an OBJECT DETECTOR (boxes + classes), not a whole-image classifier.

Runs on the ALREADY-TRACKED vehicle crop from YOLOv8 + ByteTrack — cheaper,
and avoids false positives from objects belonging to other vehicles or
pedestrians in frame.

Violation logic:
    - Phone detected      -> distracted (handheld phone)
    - Cigarette detected  -> distracted (smoking)
    - Seatbelt NOT detected, despite a driver clearly visible -> distracted
      (seatbelt non-compliance)
    Open Eye / Closed Eye are tracked but do not by themselves trigger a
    violation in this integration.

Usage:
    pip install onnxruntime opencv-python-headless numpy
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False
    print("[WARNING] onnxruntime not installed — distracted driver detection disabled. "
          "pip install onnxruntime")


class CabinConfig:
    MODEL_PATH = "models/best_Distracted_driver.onnx"
    INPUT_SIZE = 640                # YOLOv8 export standard
    CONF_THRESHOLD = 0.60

    CLASSES = ['Open Eye', 'Closed Eye', 'Cigarette', 'Phone', 'Seatbelt']

    DISTRACTION_TRIGGER_CLASSES = {'Phone', 'Cigarette'}

    FLAG_MISSING_SEATBELT = True

    RECHECK_EVERY_N_FRAMES = 15

    BBOX_PADDING_FRAC = 0.05


def _preprocess(frame: np.ndarray, size: int) -> np.ndarray:
    img = cv2.resize(frame, (size, size))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img.astype(np.float32) / 255.0
    img = np.transpose(img, (2, 0, 1))
    img = np.expand_dims(img, axis=0)
    return img


def _postprocess_yolov8_onnx(
    outputs, orig_w: int, orig_h: int, input_size: int, conf_threshold: float, classes: list[str]
) -> list[dict]:
    detections = []
    output = np.squeeze(outputs[0])

    if output.shape[0] < output.shape[1]:
        output = output.T

    x_scale = orig_w / float(input_size)
    y_scale = orig_h / float(input_size)

    for row in output:
        classes_scores = row[4:]
        if len(classes_scores) == 0:
            continue
        class_id = int(np.argmax(classes_scores))
        confidence = float(classes_scores[class_id])

        if confidence > conf_threshold and class_id < len(classes):
            cx, cy, w, h = row[0], row[1], row[2], row[3]
            x1 = int((cx - w / 2) * x_scale)
            y1 = int((cy - h / 2) * y_scale)
            box_w = int(w * x_scale)
            box_h = int(h * y_scale)

            detections.append({
                "box": [max(0, x1), max(0, y1), box_w, box_h],
                "conf": confidence,
                "class": classes[class_id],
            })

    return detections


class CabinDistractionDetector:
    """
    Runs the YOLOv8 ONNX cabin-distraction model on a vehicle crop and
    caches the result per track_id (re-checking periodically).
    """

    def __init__(self, model_path: str | Path = CabinConfig.MODEL_PATH):
        self.session: Optional["ort.InferenceSession"] = None
        self._cache: dict[int, tuple[list[dict], int]] = {}

        if not ONNX_AVAILABLE:
            return

        model_path = Path(model_path)
        if not model_path.exists():
            logging.warning("Cabin distraction model not found at %s — feature disabled.", model_path)
            return

        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "CUDAExecutionProvider")

        self.session = ort.InferenceSession(str(model_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        logging.info("Cabin distraction ONNX model loaded: %s (providers=%s)", model_path, providers)

    @property
    def available(self) -> bool:
        return self.session is not None

    def _detect(self, crop: np.ndarray) -> list[dict]:
        h, w = crop.shape[:2]
        blob = _preprocess(crop, CabinConfig.INPUT_SIZE)
        raw_outputs = self.session.run(None, {self.input_name: blob})
        return _postprocess_yolov8_onnx(
            raw_outputs, w, h, CabinConfig.INPUT_SIZE, CabinConfig.CONF_THRESHOLD, CabinConfig.CLASSES
        )

    def analyze(
        self,
        frame: np.ndarray,
        bbox: list,
        track_id: int,
        frame_num: int,
    ) -> list[dict]:
        if not self.available:
            return []

        cached = self._cache.get(track_id)
        if cached is not None:
            detections, last_frame = cached
            if frame_num - last_frame < CabinConfig.RECHECK_EVERY_N_FRAMES:
                return detections

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        pad_x = int((x2 - x1) * CabinConfig.BBOX_PADDING_FRAC)
        pad_y = int((y2 - y1) * CabinConfig.BBOX_PADDING_FRAC)
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return []

        try:
            detections = self._detect(crop)
        except Exception as e:
            logging.debug("Cabin distraction inference error for track %d: %s", track_id, e)
            return []

        self._cache[track_id] = (detections, frame_num)
        return detections

    @staticmethod
    def classify_violation(detections: list[dict]) -> tuple[bool, Optional[str]]:
        found_classes = {d["class"] for d in detections}

        if "Phone" in found_classes:
            return True, "phone"
        if "Cigarette" in found_classes:
            return True, "cigarette"

        if CabinConfig.FLAG_MISSING_SEATBELT:
            driver_visible = ("Open Eye" in found_classes) or ("Closed Eye" in found_classes)
            if driver_visible and "Seatbelt" not in found_classes:
                return True, "no_seatbelt"

        return False, None