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
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import supervision as sv
from ultralytics import YOLO

# --- PATCH A: NEW PACKAGED IMPORTS & CLEANUP ---
from congestion import CongestionTracker
from distracted_driver import CabinDistractionDetector
from plate_anpr import PlateReader
from db_utils_postgres import get_connection, init_schema, save_violation, ViolationEvent

# ══════════════════════════════════════════════════════════════════════════
# CONFIG (edit these, no env vars needed)
# ══════════════════════════════════════════════════════════════════════════

class Config:
    # Model
    YOLO_MODEL          = "yolov8n.pt"        # auto-downloads on first run
    CONFIDENCE          = 0.35
    FRAME_SKIP          = 2                   # process every Nth frame

    # COCO vehicle class IDs
    VEHICLE_CLASS_IDS   = [2, 3, 5, 7]        # car, motorcycle, bus, truck
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
    CALIB_METERS        = 10.0
    CALIB_PX            = 80


# ══════════════════════════════════════════════════════════════════════════
# FILESYSTEM & DATA ROUTING HELPERS
# ══════════════════════════════════════════════════════════════════════════

def resolve_video_path(video_path: str | Path) -> Path:
    vp = Path(video_path)
    if vp.exists():
        return vp
    videos_dir = Path("videos")
    if (videos_dir / vp.name).exists():
        return videos_dir / vp.name
    return vp


def get_source_by_video(video_path: Path) -> Optional[dict]:
    name = video_path.name
    return {
        "id": f"CAM_{name.split('.')[0].upper()}",
        "video_file": name,
        "name": name.replace("_", " ").replace(".mp4", "").title(),
        "lat": 33.5892,
        "lng": -7.6143
    }


def scan_video_files() -> list[Path]:
    videos_dir = Path("videos")
    if videos_dir.exists():
        return list(videos_dir.glob("*.mp4"))
    return []


def list_available_videos() -> list[dict]:
    files = scan_video_files()
    if not files:
        return [{
            "id": "CAM_CASABLANCA_01",
            "video_file": "place_mohammed_v.mp4",
            "name": "Place Mohammed V",
            "lat": 33.5892,
            "lng": -7.6143
        }]
    return [get_source_by_video(f) for f in files]


def annotated_output_path(source_video: str) -> Path:
    return Path(f"annotated_{source_video}")


def exact_coordinates(location: dict) -> tuple[float, float]:
    return location.get("lat", 33.5892), location.get("lng", -7.6143)


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
# SPEED ESTIMATION (pixel displacement → km/h)
# ══════════════════════════════════════════════════════════════════════════

class SpeedEstimator:
    def __init__(self, fps: float, frame_skip: int):
        self._prev: dict[int, tuple[float, float]] = {}  # track_id → (cx, cy)
        self._speeds: dict[int, float] = {}               # track_id → speed_kmh
        self.meters_per_px = Config.CALIB_METERS / Config.CALIB_PX
        self.dt = frame_skip / fps if fps > 0 else 1 / 30

    def update(self, track_id: int, cx: float, cy: float) -> Optional[float]:
        if track_id in self._prev:
            px, py = self._prev[track_id]
            displacement_px = np.hypot(cx - px, cy - py)
            displacement_m  = displacement_px * self.meters_per_px
            speed_kmh       = (displacement_m / self.dt) * 3.6
            speed_kmh       = min(speed_kmh, 200.0)
            self._speeds[track_id] = round(speed_kmh, 1)
        self._prev[track_id] = (cx, cy)
        return self._speeds.get(track_id)

    def get_speed(self, track_id: int) -> Optional[float]:
        return self._speeds.get(track_id)


# ══════════════════════════════════════════════════════════════════════════
# VIOLATION LOGIC
# ══════════════════════════════════════════════════════════════════════════

class ViolationDetector:
    # --- PATCH C: DISTRACTED SET INITIALIZATION ---
    def __init__(self, frame_height: int):
        self.stop_line_px = int(frame_height * Config.STOP_LINE_Y_FRAC)
        self._red_light_violators: set[int] = set()
        self._speeding_violators: set[int] = set()
        self._distracted_violators: set[int] = set()   # NEW

    # --- PATCH D: RECONFIGURED CHECK PARAMETERS & CONDITIONAL RULES ---
    def check(
        self,
        track_id: int,
        class_name: str,
        confidence: float,
        bbox: list,
        speed_kmh: Optional[float],
        plate_text: Optional[str],
        frame_num: int,
        is_red: bool,
        is_distracted: bool = False,              # NEW
        distraction_label: Optional[str] = None,  # NEW
    ) -> Optional[ViolationEvent]:
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        now = datetime.now().isoformat()

        # Rule 1: Speeding
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

        # Rule 2: Red light crossing
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

        # Rule 3: Distracted driving — NEW
        if is_distracted and track_id not in self._distracted_violators:
            self._distracted_violators.add(track_id)
            return ViolationEvent(
                timestamp=now, track_id=track_id,
                violation_type="distracted", class_name=class_name,
                confidence=confidence, speed_kmh=speed_kmh,
                plate_text=plate_text, bbox=bbox, frame_number=frame_num,
                distraction_label=distraction_label,
            )

        return None

    def stop_line_y(self) -> int:
        return self.stop_line_px


# ══════════════════════════════════════════════════════════════════════════
# ANNOTATION HELPERS
# ══════════════════════════════════════════════════════════════════════════

GREEN  = (0, 200, 0)
RED    = (0, 0, 220)
WHITE  = (255, 255, 255)
BLACK  = (0, 0, 0)


def draw_hud(
    frame: np.ndarray,
    frame_num: int,
    vehicle_count: int,
    congestion: float,
    violation_count: int,
    vehicles_per_min: float = 0.0,
    video_time_sec: float = 0.0,
):
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
# MAIN INFRASTRUCTURE PIPELINE
# ══════════════════════════════════════════════════════════════════════════

class TrafficPipeline:
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

        # --- PATCH B: PACKAGED CONNECTION INSTANTIATIONS ---
        self.conn = get_connection()
        init_schema(self.conn)
        self.cabin_detector = CabinDistractionDetector()
        self.plate_reader = PlateReader()

        self._violation_count = 0
        self._violations_for_csv: list[dict] = []

        if not self.video_path.exists():
            raise FileNotFoundError(
                f"Video not found: {self.video_path}. "
                f"Place '{self.source_video}' in the videos/ folder."
            )

        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open video target track: {self.video_path}")

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

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(
            self.output_path, fourcc, self.fps / Config.FRAME_SKIP,
            (self.width, self.height),
        )

    def _is_red_phase(self, frame_num: int) -> bool:
        return (frame_num % 120) >= 90

    def run(self):
        # Clear out legacy statistics tracking metrics for dynamic clean runs
        try:
            self.conn.execute("DELETE FROM frame_stats WHERE source_video = ?", (self.source_video,))
            self.conn.commit()
        except Exception:
            pass

        frame_count = 0
        processed   = 0
        start       = time.time()

        # Cache plates to preserve visual render state safely across frame loops
        plate_render_cache: dict[int, str] = {}

        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            frame_count += 1

            if frame_count % Config.FRAME_SKIP != 0:
                continue
            processed += 1

            is_red = self._is_red_phase(frame_count)

            results = self.model(
                frame, conf=Config.CONFIDENCE,
                classes=Config.VEHICLE_CLASS_IDS, verbose=False
            )[0]
            detections = sv.Detections.from_ultralytics(results)
            tracked = self.tracker.update_with_detections(detections)

            color_line = RED if is_red else GREEN
            stop_y = self.violations.stop_line_y()
            cv2.line(frame, (0, stop_y), (self.width, stop_y), color_line, 2)
            cv2.puttext(frame, "STOP LINE", (10, stop_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_line, 1)

            speeds_this_frame: list[float] = []
            violation_track_ids: set[int] = set()

            for i in range(len(tracked)):
                bbox       = tracked.xyxy[i].tolist()
                confidence = float(tracked.confidence[i])
                class_id   = int(tracked.class_id[i])
                class_name = Config.CLASS_NAMES.get(class_id, "vehicle")
                track_id   = int(tracked.tracker_id[i]) if tracked.tracker_id is not None else i

                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2

                # --- PATCH E: INFERENCE SCHEDULING (CRITICAL LOOP ORDERING) ---
                speed = self.speed_est.update(track_id, cx, cy)
                if speed is not None:
                    speeds_this_frame.append(speed)

                # Cabin distraction check (cheap, cached per track_id)
                cabin_detections = self.cabin_detector.analyze(frame, bbox, track_id, frame_count)
                is_distracted, distraction_reason = self.cabin_detector.classify_violation(cabin_detections)

                # Violation check — runs BEFORE plate OCR on purpose.
                # plate_text passed as None — we haven't read it yet.
                event = self.violations.check(
                    track_id, class_name, confidence, bbox,
                    speed, None, frame_count, is_red,
                    is_distracted=is_distracted,
                    distraction_label=distraction_reason,
                )

                if event:
                    # Plate read ONLY now — after a violation already fired.
                    event.plate_text = self.plate_reader.read(frame, bbox, track_id)
                    if event.plate_text:
                        plate_render_cache[track_id] = event.plate_text

                    event.lat, event.lng = exact_coordinates(self.location)
                    event.location_name = self.location["name"]
                    event.camera_id = self.source_id
                    event.source_video = self.source_video

                    save_violation(self.conn, event)
                    self._violations_for_csv.append(event.to_dict())
                    self._violation_count += 1
                    violation_track_ids.add(track_id)
                    logging.info(
                        "VIOLATION [%s] track=%d plate=%s speed=%s",
                        event.violation_type, track_id, event.plate_text, speed,
                    )

                # Safe visualization render passing tracking properties correctly
                draw_vehicle_label(
                    frame, bbox, track_id, class_name, speed,
                    plate_render_cache.get(track_id, None),
                    is_violation=(track_id in violation_track_ids),
                )

            # ── Congestion KPI Engine ──
            vehicle_count = len(tracked)
            video_time_sec = frame_count / self.fps if self.fps > 0 else 0.0
            _avg, vehicles_per_min, congestion = self.congestion_tracker.update(video_time_sec, vehicle_count)
            save_frame_stats(self.conn, frame_count, vehicle_count, speeds_this_frame, self.source_video, video_time_sec, vehicles_per_min, congestion)

            # ── HUD Interface Render ──
            phase_label = "RED" if is_red else "GREEN"
            cv2.putText(frame, f"SIGNAL: {phase_label}", (self.width - 180, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, RED if is_red else GREEN, 2)
            draw_hud(frame, frame_count, vehicle_count, congestion, self._violation_count, vehicles_per_min, video_time_sec)

            self.writer.write(frame)

            if self.show:
                cv2.imshow("Traffic AI Morocco", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    logging.info("User quit manually.")
                    break

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

        if self._violations_for_csv:
            keys = self._violations_for_csv[0].keys()
            with open(Config.EXPORT_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(self._violations_for_csv)
            logging.info("CSV exported: %s", Config.EXPORT_CSV)

        logging.info(
            "Done! Violations: %d | Location: %s | DB Connected | Output: %s",
            self._violation_count, self.location["name"], self.output_path,
        )


# ══════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="Traffic AI Morocco — Demo Pipeline")
    parser.add_argument("--video", help="Video filename or path")
    parser.add_argument("--all", action="store_true", help="Process all registered videos found in videos/")
    parser.add_argument("--list-videos", action="store_true", help="List registered videos and locations")
    parser.add_argument("--show", action="store_true", help="Display annotated video in real time (slow)")
    args = parser.parse_args()

    if args.list_videos:
        available = list_available_videos()
        print("\nVideos in videos/ folder:\n")
        if not available:
            print("  (no video files found — add .mp4 files to videos/)")
        for entry in available:
            print(f"  [found    ] {entry['video_file']}")
            print(f"              -> {entry['name']} ({entry['lat']}, {entry['lng']})")
        return

    if args.all:
        to_run = list_available_videos()
        if not to_run:
            print("[ERROR] No registered videos found in videos/ folder.")
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
