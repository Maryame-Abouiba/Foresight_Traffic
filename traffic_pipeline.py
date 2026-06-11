"""
Traffic AI Morocco — Student Demo Pipeline
==========================================
Drop-in replacement for the Kafka/Redis/MinIO production stack.
Runs on a single laptop with just a video file.

Requirements:
    pip install ultralytics supervision easyocr opencv-python-headless

Usage:
    python traffic_pipeline.py --list-videos
    python traffic_pipeline.py --video place_mohammed_v.mp4
    python traffic_pipeline.py --all
    python traffic_pipeline.py --video videos/bd_zerktouni.mp4 --show
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import supervision as sv
from ultralytics import YOLO

from congestion import CongestionTracker
from db_utils import init_schema
from locations import (
    annotated_output_path,
    exact_coordinates,
    get_source_by_video,
    list_available_videos,
    resolve_video_path,
    scan_video_files,
)

# ── Optional EasyOCR (graceful fallback if not installed) ────────────────
try:
    import easyocr
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[WARNING] easyocr not installed — plate OCR disabled. pip install easyocr")


# ══════════════════════════════════════════════════════════════════════════
# CONFIG  (edit these, no env vars needed)
# ══════════════════════════════════════════════════════════════════════════

class Config:
    # Model
    YOLO_MODEL          = "yolov8n.pt"        # auto-downloads on first run
    CONFIDENCE          = 0.35
    FRAME_SKIP          = 2                   # process every Nth frame

    # COCO vehicle class IDs
    VEHICLE_CLASS_IDS   = [2, 3, 5, 7]       # car, motorcycle, bus, truck
    CLASS_NAMES         = {2:"car", 3:"motorcycle", 5:"bus", 7:"truck"}

    # ── Violation thresholds ─────────────────────────────────────────
    SPEED_LIMIT_KMH     = 60                  # flag vehicles above this
    # Virtual stop line: horizontal Y position (fraction of frame height)
    # Vehicles below this line during red phase = red-light violation
    STOP_LINE_Y_FRAC    = 0.55                # tune per video

    # ── Output ───────────────────────────────────────────────────────
    DB_PATH             = "violations.db"
    OUTPUT_VIDEO        = "output_annotated.mp4"
    EXPORT_CSV          = "violations.csv"

    # ── Speed estimation ─────────────────────────────────────────────
    # Real-world distance (meters) that corresponds to CALIB_PX pixels
    # on screen. Measure from your video using a known road marking.
    # Default is a rough estimate — tune for your video.
    CALIB_METERS        = 10.0
    CALIB_PX            = 80


# ══════════════════════════════════════════════════════════════════════════
# DATA MODELS  (replaces shared/event_schema.py — no Pydantic needed)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ViolationEvent:
    """One recorded traffic violation."""
    timestamp:      str
    track_id:       int
    violation_type: str                        # "red_light" | "speeding" | "detected"
    class_name:     str
    confidence:     float
    speed_kmh:      Optional[float] = None
    plate_text:     Optional[str]   = None
    bbox:           list            = field(default_factory=list)
    frame_number:   int             = 0
    lat:            Optional[float] = None
    lng:            Optional[float] = None
    location_name:  Optional[str]   = None
    camera_id:      Optional[str]   = None
    source_video:   Optional[str]   = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_row(self) -> tuple:
        """SQLite insert row."""
        return (
            self.timestamp, self.track_id, self.violation_type,
            self.class_name, self.confidence,
            self.speed_kmh, self.plate_text,
            json.dumps(self.bbox), self.frame_number,
            self.lat, self.lng, self.location_name, self.camera_id,
            self.source_video,
        )


# ══════════════════════════════════════════════════════════════════════════
# DATABASE  (SQLite — zero setup, one file)
# ══════════════════════════════════════════════════════════════════════════

def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    init_schema(conn)
    logging.info("SQLite DB ready: %s", path)
    return conn


def save_violation(conn: sqlite3.Connection, event: ViolationEvent):
    conn.execute("""
        INSERT INTO violations
        (timestamp, track_id, violation_type, class_name, confidence,
         speed_kmh, plate_text, bbox, frame_number,
         lat, lng, location_name, camera_id, source_video)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, event.to_row())
    conn.commit()


def save_frame_stats(
    conn: sqlite3.Connection,
    frame_num: int,
    vehicle_count: int,
    speeds: list[float],
    source_video: str = "",
    video_time_sec: float = 0.0,
    vehicles_per_minute: float = 0.0,
    congestion_index: float = 0.0,
):
    avg_speed = float(np.mean(speeds)) if speeds else 0.0
    conn.execute("""
        INSERT INTO frame_stats (
            frame_number, timestamp, vehicle_count, avg_speed_kmh,
            congestion_index, source_video, video_time_sec, vehicles_per_minute
        )
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        frame_num, datetime.now().isoformat(), vehicle_count,
        round(avg_speed, 1), congestion_index, source_video,
        round(video_time_sec, 2), round(vehicles_per_minute, 1),
    ))
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════
# SPEED ESTIMATION  (pixel displacement → km/h)
# ══════════════════════════════════════════════════════════════════════════

class SpeedEstimator:
    """
    Estimates vehicle speed from centroid displacement between frames.

    How it works:
        1. Record centroid (cx, cy) per track_id each processed frame.
        2. Compute pixel displacement Δpx between consecutive frames.
        3. Convert using calibration: meters_per_px = CALIB_METERS / CALIB_PX
        4. speed = (displacement_m / time_s) × 3.6  →  km/h

    Tune Config.CALIB_METERS and Config.CALIB_PX for your specific video.
    """

    def __init__(self, fps: float, frame_skip: int):
        self._prev: dict[int, tuple[float, float]] = {}  # track_id → (cx, cy)
        self._speeds: dict[int, float] = {}               # track_id → speed_kmh
        self.meters_per_px = Config.CALIB_METERS / Config.CALIB_PX
        # Time elapsed between processed frames (accounting for frame skip)
        self.dt = frame_skip / fps if fps > 0 else 1 / 30

    def update(self, track_id: int, cx: float, cy: float) -> Optional[float]:
        """Update tracker and return speed estimate in km/h (None on first frame)."""
        if track_id in self._prev:
            px, py = self._prev[track_id]
            displacement_px = np.hypot(cx - px, cy - py)
            displacement_m  = displacement_px * self.meters_per_px
            speed_kmh       = (displacement_m / self.dt) * 3.6
            # Clamp absurd values (camera shake, occlusion, etc.)
            speed_kmh = min(speed_kmh, 200.0)
            self._speeds[track_id] = round(speed_kmh, 1)
        self._prev[track_id] = (cx, cy)
        return self._speeds.get(track_id)

    def get_speed(self, track_id: int) -> Optional[float]:
        return self._speeds.get(track_id)


# ══════════════════════════════════════════════════════════════════════════
# PLATE OCR
# ══════════════════════════════════════════════════════════════════════════

class PlateReader:
    """
    Crops the lower portion of a vehicle bbox and runs EasyOCR on it.
    Returns the most confident text string, or None if unreadable.
    """

    def __init__(self):
        if OCR_AVAILABLE:
            # Arabic + English covers Moroccan plates (latin + arabic chars)
            self.reader = easyocr.Reader(["ar", "en"], verbose=False)
            logging.info("EasyOCR loaded (Arabic + English)")
        else:
            self.reader = None
        self._cache: dict[int, str] = {}   # track_id → plate text

    def read(self, frame: np.ndarray, bbox: list,
             track_id: int) -> Optional[str]:
        """Read plate from vehicle bbox. Caches result per track_id."""
        if track_id in self._cache:
            return self._cache[track_id]
        if self.reader is None:
            return None

        x1, y1, x2, y2 = [int(v) for v in bbox]
        # Crop bottom third of vehicle bbox (where plates usually are)
        plate_y1 = y1 + int((y2 - y1) * 0.65)
        crop = frame[plate_y1:y2, x1:x2]

        if crop.size == 0:
            return None

        try:
            results = self.reader.readtext(crop, detail=1)
            if results:
                # Pick the result with highest confidence
                best = max(results, key=lambda r: r[2])
                text, conf = best[1], best[2]
                if conf > 0.4 and len(text) >= 3:
                    cleaned = text.strip().upper()
                    self._cache[track_id] = cleaned
                    return cleaned
        except Exception as e:
            logging.debug("OCR error for track %d: %s", track_id, e)

        return None


# ══════════════════════════════════════════════════════════════════════════
# VIOLATION LOGIC
# ══════════════════════════════════════════════════════════════════════════

class ViolationDetector:
    """
    Stateless rule engine that evaluates each tracked vehicle
    and returns a ViolationEvent if a rule fires.

    Rules:
        1. SPEEDING   — estimated speed > Config.SPEED_LIMIT_KMH
        2. RED LIGHT  — vehicle centroid Y > stop_line_px AND
                        frame is in a simulated "red phase"
                        (real implementation: read signal state from video)
    """

    def __init__(self, frame_height: int):
        self.stop_line_px = int(frame_height * Config.STOP_LINE_Y_FRAC)
        self._red_light_violators: set[int] = set()  # avoid duplicate alerts
        self._speeding_violators:  set[int] = set()

    def check(
        self,
        track_id:   int,
        class_name: str,
        confidence: float,
        bbox:       list,
        speed_kmh:  Optional[float],
        plate_text: Optional[str],
        frame_num:  int,
        is_red:     bool,   # True when traffic light is red
    ) -> Optional[ViolationEvent]:

        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        now = datetime.now().isoformat()

        # ── Rule 1: Speeding ─────────────────────────────────────────
        if (speed_kmh is not None
                and speed_kmh > Config.SPEED_LIMIT_KMH
                and track_id not in self._speeding_violators):
            self._speeding_violators.add(track_id)
            return ViolationEvent(
                timestamp=now, track_id=track_id,
                violation_type="speeding", class_name=class_name,
                confidence=confidence, speed_kmh=speed_kmh,
                plate_text=plate_text, bbox=bbox, frame_number=frame_num,
            )

        # ── Rule 2: Red light crossing ───────────────────────────────
        if (is_red
                and cy > self.stop_line_px
                and track_id not in self._red_light_violators):
            self._red_light_violators.add(track_id)
            return ViolationEvent(
                timestamp=now, track_id=track_id,
                violation_type="red_light", class_name=class_name,
                confidence=confidence, speed_kmh=speed_kmh,
                plate_text=plate_text, bbox=bbox, frame_number=frame_num,
            )

        return None

    def stop_line_y(self) -> int:
        return self.stop_line_px


# ══════════════════════════════════════════════════════════════════════════
# ANNOTATION HELPERS
# ══════════════════════════════════════════════════════════════════════════

# Colour palette
GREEN  = (0, 200, 0)
RED    = (0, 0, 220)
ORANGE = (0, 140, 255)
WHITE  = (255, 255, 255)
BLACK  = (0, 0, 0)
YELLOW = (0, 220, 220)


def draw_hud(
    frame: np.ndarray,
    frame_num: int,
    vehicle_count: int,
    congestion: float,
    violation_count: int,
    vehicles_per_min: float = 0.0,
    video_time_sec: float = 0.0,
):
    """Top-left heads-up display panel."""
    overlay = frame.copy()
    cv2.rectangle(overlay, (10, 10), (380, 155), BLACK, -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
    lines = [
        f"Time: {video_time_sec:.1f}s | Frame: {frame_num}",
        f"Vehicles in frame: {vehicle_count}",
        f"Flow: {vehicles_per_min:.1f} veh/min",
        f"Congestion Index: {congestion:.0f}/100",
        f"Violations logged: {violation_count}",
    ]
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (18, 32 + i * 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 1, cv2.LINE_AA)


def draw_vehicle_label(frame: np.ndarray, bbox: list, track_id: int,
                       class_name: str, speed: Optional[float],
                       plate: Optional[str], is_violation: bool):
    """Draw bbox + label for one vehicle."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    color = RED if is_violation else GREEN
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

    parts = [f"#{track_id} {class_name}"]
    if speed is not None:
        parts.append(f"{speed:.0f}km/h")
    if plate:
        parts.append(plate)
    label = " | ".join(parts)

    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
    cv2.putText(frame, label, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)


# ══════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE  (replaces InferencePipeline + Kafka producer)
# ══════════════════════════════════════════════════════════════════════════

class TrafficPipeline:
    """
    Self-contained pipeline — processes a video file and writes:
      • output_annotated.mp4  — annotated video
      • violations.db         — SQLite with all events
      • violations.csv        — flat CSV for Streamlit dashboard
    """

    def __init__(self, video_path: str | Path, show: bool = False):
        self.video_path = resolve_video_path(video_path)
        self.show       = show
        self.source     = get_source_by_video(self.video_path)

        if not self.source:
            available = ", ".join(p.name for p in scan_video_files()) or "(none)"
            raise ValueError(
                f"Video '{self.video_path.name}' not found in videos/. "
                f"Available files: {available}"
            )

        self.source_id    = self.source["id"]
        self.source_video = self.source["video_file"]
        self.location     = self.source
        self.output_path  = str(annotated_output_path(self.source_video))

        logging.info("Loading YOLOv8 model: %s", Config.YOLO_MODEL)
        self.model   = YOLO(Config.YOLO_MODEL)
        self.tracker = sv.ByteTrack(
            track_activation_threshold=Config.CONFIDENCE,
            lost_track_buffer=30,
            minimum_matching_threshold=0.8,
            frame_rate=30,
        )

        self.plate_reader = PlateReader()
        self.conn         = init_db(Config.DB_PATH)

        self._violation_count = 0
        self._violations_for_csv: list[dict] = []

        if not self.video_path.exists():
            raise FileNotFoundError(
                f"Video not found: {self.video_path}. "
                f"Place '{self.source_video}' in the videos/ folder."
            )

        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self.video_path}")

        self.fps    = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.width  = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.total  = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))

        logging.info(
            "Video: %s | Location: %s | %dx%d @ %.1f FPS | %d frames",
            self.video_path.name, self.location["name"],
            self.width, self.height, self.fps, self.total,
        )

        self.speed_est  = SpeedEstimator(self.fps, Config.FRAME_SKIP)
        self.violations = ViolationDetector(self.height)
        self.congestion_tracker = CongestionTracker()

        # Output video writer
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(
            self.output_path, fourcc, self.fps / Config.FRAME_SKIP,
            (self.width, self.height),
        )

    def _is_red_phase(self, frame_num: int) -> bool:
        """
        Simulate a traffic light cycle for demo purposes.
        Real implementation: detect signal head colour with a ROI classifier.

        Cycle: 90 frames green → 30 frames red (at video FPS).
        """
        cycle = frame_num % 120
        return cycle >= 90   # red for last 30 frames of each 120-frame cycle

    def run(self):
        self.conn.execute(
            "DELETE FROM frame_stats WHERE source_video = ?",
            (self.source_video,),
        )
        self.conn.commit()

        frame_count = 0
        processed   = 0
        start       = time.time()

        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            frame_count += 1

            # Frame skip
            if frame_count % Config.FRAME_SKIP != 0:
                continue
            processed += 1

            is_red = self._is_red_phase(frame_count)

            # ── Detection ────────────────────────────────────────────
            results = self.model(
                frame,
                conf=Config.CONFIDENCE,
                classes=Config.VEHICLE_CLASS_IDS,
                verbose=False,
            )[0]
            detections = sv.Detections.from_ultralytics(results)

            # ── Tracking ─────────────────────────────────────────────
            tracked = self.tracker.update_with_detections(detections)

            # Draw stop line
            color_line = RED if is_red else GREEN
            stop_y = self.violations.stop_line_y()
            cv2.line(frame, (0, stop_y), (self.width, stop_y), color_line, 2)
            cv2.putText(frame, "STOP LINE", (10, stop_y - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_line, 1)

            speeds_this_frame: list[float] = []
            violation_track_ids: set[int] = set()
            active_track_ids: set[int] = set()

            for i in range(len(tracked)):
                bbox       = tracked.xyxy[i].tolist()
                confidence = float(tracked.confidence[i])
                class_id   = int(tracked.class_id[i])
                class_name = Config.CLASS_NAMES.get(class_id, "vehicle")
                track_id   = int(tracked.tracker_id[i]) if tracked.tracker_id is not None else i
                active_track_ids.add(track_id)

                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2

                # Speed
                speed = self.speed_est.update(track_id, cx, cy)
                if speed is not None:
                    speeds_this_frame.append(speed)

                # Plate OCR (only attempt once per track to save time)
                plate = self.plate_reader.read(frame, bbox, track_id)

                # Violation check
                event = self.violations.check(
                    track_id, class_name, confidence, bbox,
                    speed, plate, frame_count, is_red,
                )
                if event:
                    lat, lng = exact_coordinates(self.location)
                    event.lat = lat
                    event.lng = lng
                    event.location_name = self.location["name"]
                    event.camera_id = self.source_id
                    event.source_video = self.source_video
                    save_violation(self.conn, event)
                    self._violations_for_csv.append(event.to_dict())
                    self._violation_count += 1
                    violation_track_ids.add(track_id)
                    logging.info(
                        "VIOLATION [%s] track=%d plate=%s speed=%s",
                        event.violation_type, track_id, plate, speed,
                    )

                draw_vehicle_label(
                    frame, bbox, track_id, class_name, speed, plate,
                    is_violation=(track_id in violation_track_ids),
                )

            # ── Congestion KPI (avg vehicles over video time) ────────
            vehicle_count = len(tracked)
            video_time_sec = frame_count / self.fps if self.fps > 0 else 0.0
            _avg, vehicles_per_min, congestion = self.congestion_tracker.update(
                video_time_sec, vehicle_count,
            )
            save_frame_stats(
                self.conn, frame_count, vehicle_count, speeds_this_frame,
                self.source_video, video_time_sec, vehicles_per_min, congestion,
            )

            # ── HUD ──────────────────────────────────────────────────
            phase_label = "RED" if is_red else "GREEN"
            cv2.putText(frame, f"SIGNAL: {phase_label}",
                        (self.width - 180, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        RED if is_red else GREEN, 2)
            draw_hud(
                frame, frame_count, vehicle_count, congestion,
                self._violation_count, vehicles_per_min, video_time_sec,
            )

            self.writer.write(frame)

            if self.show:
                cv2.imshow("Traffic AI Morocco", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    logging.info("User quit")
                    break

            # Progress every 100 processed frames
            if processed % 100 == 0:
                elapsed = time.time() - start
                pct = (frame_count / self.total * 100) if self.total else 0
                logging.info(
                    "Progress: %.0f%% | %d violations | %.1f FPS",
                    pct, self._violation_count, processed / elapsed,
                )

        self._finish()

    def _finish(self):
        self.cap.release()
        self.writer.release()
        if self.show:
            cv2.destroyAllWindows()

        # Export CSV for Streamlit
        if self._violations_for_csv:
            keys = self._violations_for_csv[0].keys()
            with open(Config.EXPORT_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(self._violations_for_csv)
            logging.info("CSV exported: %s", Config.EXPORT_CSV)

        logging.info(
            "Done! Violations: %d | Location: %s | DB: %s | Output: %s",
            self._violation_count, self.location["name"],
            Config.DB_PATH, self.output_path,
        )


# ══════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Traffic AI Morocco — Demo Pipeline")
    parser.add_argument("--video", help="Video filename or path (must be in locations.py)")
    parser.add_argument("--all", action="store_true",
                        help="Process all registered videos found in videos/")
    parser.add_argument("--list-videos", action="store_true",
                        help="List registered videos and their Casablanca locations")
    parser.add_argument("--show", action="store_true",
                        help="Display annotated video in real time (slow)")
    args = parser.parse_args()

    if args.list_videos:
        available = list_available_videos()
        print("\nVideos in videos/ folder:\n")
        if not available:
            print("  (no video files found — add .mp4 files to videos/)")
        for entry in available:
            print(f"  [found  ] {entry['video_file']}")
            print(f"            -> {entry['name']} ({entry['lat']}, {entry['lng']})")
        return

    if args.all:
        to_run = list_available_videos()
        if not to_run:
            print("[ERROR] No registered videos found in videos/ folder.")
            print("Run with --list-videos to see expected filenames.")
            return
        for entry in to_run:
            logging.info("Processing %s -> %s", entry["video_file"], entry["name"])
            TrafficPipeline(entry["video_file"], show=args.show).run()
        return

    if not args.video:
        parser.error("Provide --video <file>, --all, or --list-videos")

    try:
        pipeline = TrafficPipeline(args.video, show=args.show)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}")
        return
    pipeline.run()


if __name__ == "__main__":
    main()
