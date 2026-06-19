"""
Moroccan ANPR Plate Reader — Traffic AI Morocco
================================================
Two-stage Darknet/YOLOv3 pipeline for Moroccan license plates:

    Stage 1 (yolov3.cfg + yolov3_last.weights)
        Localizes the plate region inside a vehicle crop.
    Stage 2 (yolov3_ocr.cfg + yolov3_last_ocr.weights)
        Detects individual characters (digits + Arabic letters) inside
        the plate crop as YOLO objects, sorts them left-to-right, and
        assembles them into the Moroccan plate format:
            "<digits> | <Arabic letter> | <digits>"

Drop-in replacement for the repo's original EasyOCR-based PlateReader.
Same external interface: read(frame, bbox, track_id) -> Optional[str].

Usage:
    pip install opencv-python-headless numpy pillow
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

MODELS_DIR = "models"

PLATE_CFG = "yolov3.cfg"
PLATE_WEIGHTS = "yolov3_last.weights"
CHAR_CFG = "yolov3_ocr.cfg"
CHAR_WEIGHTS = "yolov3_last_ocr.weights"

CLASSES = [
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
    'أ', 'ب', 'د', 'ه', 'و', 'ج', 'ش', 'المغرب', 'ww'
]

ARABIC_LETTERS = {'أ', 'ب', 'د', 'ه', 'و', 'ج', 'ش', 'المغرب', 'ww'}

PLATE_CONF_THRESHOLD = 0.35
CHAR_CONF_THRESHOLD = 0.20
CHAR_NMS_IOU = 0.35

BBOX_PADDING_FRAC = 0.08


def _get_output_layers(net: cv2.dnn_Net) -> list[str]:
    try:
        return net.getUnconnectedOutLayersNames()
    except AttributeError:
        try:
            unconnected = net.getUnconnectedOutLayers().flatten()
        except AttributeError:
            unconnected = net.getUnconnectedOutLayers()
        return [net.getLayerNames()[int(i) - 1] for i in unconnected]


class MoroccanANPREngine:
    def __init__(self, models_dir: str | Path = MODELS_DIR):
        self.models_dir = Path(models_dir)
        self.available = False

        plate_cfg = self.models_dir / PLATE_CFG
        plate_weights = self.models_dir / PLATE_WEIGHTS
        char_cfg = self.models_dir / CHAR_CFG
        char_weights = self.models_dir / CHAR_WEIGHTS

        missing = [p for p in (plate_cfg, plate_weights, char_cfg, char_weights) if not p.exists()]
        if missing:
            logging.warning(
                "ANPR model files missing (%s) — plate reading disabled.",
                ", ".join(str(p) for p in missing),
            )
            return

        logging.info("Loading Stage 1 (plate) and Stage 2 (character) YOLO networks...")
        self.plate_net = cv2.dnn.readNetFromDarknet(str(plate_cfg), str(plate_weights))
        self.plate_layers = _get_output_layers(self.plate_net)

        self.char_net = cv2.dnn.readNetFromDarknet(str(char_cfg), str(char_weights))
        self.char_layers = _get_output_layers(self.char_net)

        self.available = True
        logging.info("Moroccan ANPR engine loaded.")

    def process_crop(self, img: np.ndarray) -> tuple[str, Optional[list[int]]]:
        if not self.available:
            return "", None

        orig_h, orig_w = img.shape[:2]
        if orig_h == 0 or orig_w == 0:
            return "", None

        blob = cv2.dnn.blobFromImage(img, 1.0 / 255.0, (416, 416), (0, 0, 0), swapRB=True, crop=False)
        self.plate_net.setInput(blob)
        outs = self.plate_net.forward(self.plate_layers)

        best_crop, s1_max, plate_box = None, 0.0, None
        for out in outs:
            for det in out:
                scores = det[5:]
                conf = float(det[4]) * (float(scores[np.argmax(scores)]) if len(scores) else 1.0)
                if conf > s1_max and conf > PLATE_CONF_THRESHOLD:
                    s1_max = conf
                    cx, cy, w, h = det[0] * orig_w, det[1] * orig_h, det[2] * orig_w, det[3] * orig_h
                    x, y = max(0, int(cx - w / 2)), max(0, int(cy - h / 2))
                    plate_box = [x, y, int(w), int(h)]
                    best_crop = img[y:min(orig_h, y + int(h)), x:min(orig_w, x + int(w))]

        if best_crop is None or best_crop.size == 0:
            return "", None

        p_h, p_w = best_crop.shape[:2]

        blob_char = cv2.dnn.blobFromImage(best_crop, 1.0 / 255.0, (416, 416), (0, 0, 0), swapRB=True, crop=False)
        self.char_net.setInput(blob_char)
        outs_char = self.char_net.forward(self.char_layers)

        boxes, confs, class_ids = [], [], []
        for out in outs_char:
            for det in out:
                scores = det[5:]
                if len(scores) == 0:
                    continue
                c_id = int(np.argmax(scores))
                conf = float(det[4]) * float(scores[c_id])
                if conf > CHAR_CONF_THRESHOLD:
                    cx, cy, cw, ch = det[0] * p_w, det[1] * p_h, det[2] * p_w, det[3] * p_h
                    boxes.append([max(0, int(cx - cw / 2)), max(0, int(cy - ch / 2)), int(cw), int(ch)])
                    confs.append(conf)
                    class_ids.append(c_id)

        detected_characters = []
        if boxes:
            indices = cv2.dnn.NMSBoxes(boxes, confs, CHAR_CONF_THRESHOLD, CHAR_NMS_IOU)
            if len(indices) > 0:
                for i in indices.flatten():
                    detected_characters.append({"x_coord": boxes[i][0], "class_id": class_ids[i]})

        detected_characters.sort(key=lambda c: c["x_coord"])

        left_digits = []
        plate_letter = ""
        right_region = []
        found_letter = False

        for char in detected_characters:
            c_id = char["class_id"]
            if c_id >= len(CLASSES):
                continue
            token = CLASSES[c_id]

            if token in ARABIC_LETTERS:
                plate_letter = token
                found_letter = True
                continue
            if not found_letter:
                left_digits.append(token)
            else:
                right_region.append(token)

        if plate_letter:
            formatted_plate = f"{''.join(left_digits)} | {plate_letter} | {''.join(right_region)}"
        else:
            formatted_plate = "".join(
                CLASSES[c["class_id"]] for c in detected_characters if c["class_id"] < len(CLASSES)
            )

        return formatted_plate, plate_box


class PlateReader:
    def __init__(self, models_dir: str | Path = MODELS_DIR):
        self.engine = MoroccanANPREngine(models_dir)
        self._cache: dict[int, str] = {}

    @property
    def available(self) -> bool:
        return self.engine.available

    def read(self, frame: np.ndarray, bbox: list, track_id: int) -> Optional[str]:
        if track_id in self._cache:
            return self._cache[track_id]

        if not self.engine.available:
            return None

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]

        pad_x = int((x2 - x1) * BBOX_PADDING_FRAC)
        pad_y = int((y2 - y1) * BBOX_PADDING_FRAC)
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        try:
            plate_text, _plate_box_in_crop = self.engine.process_crop(crop)
        except Exception as e:
            logging.debug("ANPR error for track %d: %s", track_id, e)
            return None

        plate_text = plate_text.strip()
        if not plate_text:
            return None

        self._cache[track_id] = plate_text
        return plate_text


def draw_arabic_text(img: np.ndarray, text: str, position: tuple[int, int], box_w: int) -> np.ndarray:
    text_w = len(text) * 12
    img_h, img_w = img.shape[:2]

    x1, y1 = position[0] - 5, position[1] - 5
    x2, y2 = position[0] + text_w + 15, position[1] + 30

    x_start, y_start = max(0, x1), max(0, y1)
    x_end, y_end = min(img_w, x2), min(img_h, y2)

    if x_end <= x_start or y_end <= y_start:
        return img

    roi = img[y_start:y_end, x_start:x_end]
    img_rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(18, int(box_w * 0.12)))
    except IOError:
        font = ImageFont.load_default()

    draw.rectangle([x1 - x_start, y1 - y_start, x2 - x_start, y2 - y_start], fill=(0, 215, 255))
    draw.text((position[0] - x_start, position[1] - y_start), text, font=font, fill=(0, 0, 0))

    roi_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    img[y_start:y_end, x_start:x_end] = roi_bgr

    return img