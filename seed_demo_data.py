"""
Seed demo detection data into violations.db (SQLite) so the dashboard
can display results without running the full traffic_pipeline.
- Populates the `violations` table with sample detections per video.
- Populates the `frame_stats` table with a congestion timeline.
- Copies each source video to videos/output/<stem>_annotated.mp4 so the
  Monitoring page has something to play.
"""
from __future__ import annotations

import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from db_utils import get_connection, setup_database
from locations import OUTPUT_DIR, VIDEOS_DIR, get_source_by_video, scan_video_files

random.seed(42)

VIOLATION_TYPES = ["speeding", "red_light", "phone", "no_seatbelt", "cigarette"]
VEHICLE_CLASSES = ["car", "motorcycle", "bus", "truck"]

# Real Moroccan plate alphabet (matches plate_anpr.ARABIC_LETTERS)
ARABIC_LETTERS = ["أ", "ب", "د", "ه", "و", "ج", "ش"]

# Per-video personality: different violation mix, density, vehicle mix,
# congestion shape, speed envelope, and plate style.
VIDEO_PROFILES = {
    "place_mohammed_v.mp4": {
        # Busy downtown roundabout: lots of red-light + phone use, mixed traffic
        "n_violations": 30,
        "weights": {"speeding": 15, "red_light": 35, "phone": 25, "no_seatbelt": 15, "cigarette": 10},
        "vehicle_weights": {"car": 70, "motorcycle": 20, "bus": 7, "truck": 3},
        "speed_range": (25, 65),
        "speeding_range": (62, 95),
        "congestion_base": 60,
        "congestion_amp": 30,
        "plate_style": "arabic",
    },
    "maarif.mp4": {
        # Commercial avenue: distractions dominate (phone, smoking, no seatbelt)
        "n_violations": 22,
        "weights": {"speeding": 10, "red_light": 10, "phone": 30, "no_seatbelt": 30, "cigarette": 20},
        "vehicle_weights": {"car": 80, "motorcycle": 15, "bus": 3, "truck": 2},
        "speed_range": (15, 50),
        "speeding_range": (60, 80),
        "congestion_base": 70,
        "congestion_amp": 20,
        "plate_style": "arabic",
    },
    "casa_port.mp4": {
        # Highway/port axis: speeding-heavy, freight, lower distraction rate
        "n_violations": 28,
        "weights": {"speeding": 55, "red_light": 5, "phone": 12, "no_seatbelt": 18, "cigarette": 10},
        "vehicle_weights": {"car": 55, "motorcycle": 5, "bus": 10, "truck": 30},
        "speed_range": (50, 95),
        "speeding_range": (95, 145),
        "congestion_base": 35,
        "congestion_amp": 25,
        "plate_style": "arabic",
    },
}

DEFAULT_PROFILE = {
    "n_violations": 20,
    "weights": {"speeding": 30, "red_light": 20, "phone": 20, "no_seatbelt": 20, "cigarette": 10},
    "vehicle_weights": {"car": 70, "motorcycle": 15, "bus": 8, "truck": 7},
    "speed_range": (20, 60),
    "speeding_range": (65, 100),
    "congestion_base": 50,
    "congestion_amp": 30,
    "plate_style": "arabic",
}


def _weighted_choice(weights: dict) -> str:
    keys = list(weights.keys())
    w = [weights[k] for k in keys]
    return random.choices(keys, weights=w, k=1)[0]


def fake_plate(style: str = "arabic") -> str:
    """Moroccan plate: '<1-5 digits> | <Arabic letter> | <1-2 digits>'."""
    left = random.randint(1, 99999)
    letter = random.choice(ARABIC_LETTERS)
    region = random.randint(1, 99)
    return f"{left} | {letter} | {region}"


def seed_violations(conn, source_video: str) -> None:
    src = get_source_by_video(source_video)
    if src is None:
        print(f"  skip {source_video}: not registered in locations.py")
        return
    profile = VIDEO_PROFILES.get(source_video, DEFAULT_PROFILE)
    n = profile["n_violations"]

    lat = float(src["lat"])
    lng = float(src["lng"])
    name = src["name"]
    cam_id = src["id"]

    base_time = datetime.now() - timedelta(hours=2)
    rows = []
    for i in range(n):
        vt = _weighted_choice(profile["weights"])
        cls = _weighted_choice(profile["vehicle_weights"])
        ts = (base_time + timedelta(seconds=i * 17)).strftime("%Y-%m-%d %H:%M:%S")

        if vt == "speeding":
            speed = round(random.uniform(*profile["speeding_range"]), 1)
        elif vt == "red_light":
            speed = round(random.uniform(15, 45), 1)
        else:
            speed = round(random.uniform(*profile["speed_range"]), 1)

        plate = fake_plate(profile["plate_style"]) if random.random() > 0.15 else None

        distraction = None
        if vt == "phone":
            distraction = "phone"
        elif vt == "no_seatbelt":
            distraction = "no_seatbelt"
        elif vt == "cigarette":
            distraction = "cigarette"

        rows.append((
            ts,
            random.randint(1, 200),
            vt,
            cls,
            round(random.uniform(0.55, 0.95), 2),
            speed,
            plate,
            None,
            i * 30,
            lat,
            lng,
            name,
            cam_id,
            source_video,
            distraction,
            None,
            "image",
        ))

    conn.executemany(
        """
        INSERT INTO violations
        (timestamp, track_id, violation_type, class_name, confidence,
         speed_kmh, plate_text, bbox, frame_number, lat, lng,
         location_name, camera_id, source_video, distraction_label,
         media_url, media_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    print(f"  seeded {n} violations for {source_video} ({name})")


def seed_frame_stats(conn, source_video: str, n: int = 120, fps: float = 30.0) -> None:
    profile = VIDEO_PROFILES.get(source_video, DEFAULT_PROFILE)
    base_time = datetime.now() - timedelta(hours=2)
    base = profile["congestion_base"]
    amp = profile["congestion_amp"]
    rows = []
    for i in range(n):
        x = i / n
        wave = base + amp * (1 - abs(2 * x - 1)) + random.uniform(-6, 6)
        cong = max(5, min(98, wave))
        veh_count = int(cong / 5 + random.uniform(-2, 2))
        veh_count = max(0, veh_count)
        vpm = round(veh_count * (60.0 / 5.0), 1)
        avg_speed = round(max(8, 60 - cong * 0.45 + random.uniform(-4, 4)), 1)
        ts = (base_time + timedelta(seconds=i * 5)).strftime("%Y-%m-%d %H:%M:%S")
        frame_num = int(i * 5 * fps)
        video_time_sec = round(i * 5.0, 2)

        rows.append((
            frame_num,
            ts,
            veh_count,
            avg_speed,
            round(cong, 2),
            source_video,
            video_time_sec,
            vpm,
        ))

    conn.executemany(
        """
        INSERT INTO frame_stats
        (frame_number, timestamp, vehicle_count, avg_speed_kmh,
         congestion_index, source_video, video_time_sec, vehicles_per_minute)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    print(f"  seeded {n} frame_stats rows for {source_video}")


def make_annotated_copy(video_path: Path) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUTPUT_DIR / f"{video_path.stem}_annotated.mp4"
    if dst.exists():
        print(f"  annotated already exists: {dst.name}")
        return
    shutil.copy2(video_path, dst)
    print(f"  created annotated copy: {dst.name}")


def clear_existing(conn, source_video: str) -> None:
    conn.execute("DELETE FROM violations WHERE source_video = ?", (source_video,))
    conn.execute("DELETE FROM frame_stats WHERE source_video = ?", (source_video,))
    conn.commit()


def main() -> None:
    print("Setting up database schema...")
    setup_database()

    conn = get_connection()

    files = scan_video_files()
    if not files:
        print("No video files found in videos/. Drop your videos there first.")
        return

    print(f"Found {len(files)} video file(s) in videos/:")
    for f in files:
        print(f"  - {f.name}")

    for video_path in files:
        name = video_path.name
        print(f"\nProcessing {name}:")
        clear_existing(conn, name)
        seed_violations(conn, name)
        seed_frame_stats(conn, name, n=120)
        make_annotated_copy(video_path)

    conn.close()
    print("\nDone. Launch the dashboard with:  streamlit run app.py")


if __name__ == "__main__":
    main()
