"""
Shared database helpers for Traffic AI Morocco.
Handles schema migration, auth users, and violation loading with geo data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import bcrypt
import pandas as pd

from locations import (
    VIDEO_SOURCES,
    exact_coordinates,
    get_default_video_source,
    get_source_by_video,
)

DB_PATH = "violations.db"

DEFAULT_USERS = [
    ("admin", "admin123", "admin"),
    ("user", "user123", "user"),
]


@dataclass
class ViolationEvent:
    timestamp: str
    track_id: int
    violation_type: str
    class_name: str
    confidence: float
    speed_kmh: Optional[float] = None
    plate_text: Optional[str] = None
    bbox: list = field(default_factory=list)
    frame_number: int = 0
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_name: Optional[str] = None
    camera_id: Optional[str] = None
    source_video: Optional[str] = None
    distraction_label: Optional[str] = None
    media_url: Optional[str] = None
    media_type: str = "image"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "track_id": self.track_id,
            "violation_type": self.violation_type,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "speed_kmh": self.speed_kmh,
            "plate_text": self.plate_text,
            "bbox": self.bbox,
            "frame_number": self.frame_number,
            "lat": self.lat,
            "lng": self.lng,
            "location_name": self.location_name,
            "camera_id": self.camera_id,
            "source_video": self.source_video,
            "distraction_label": self.distraction_label,
            "media_url": self.media_url,
            "media_type": self.media_type,
        }


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS violations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp      TEXT    NOT NULL,
            track_id       INTEGER,
            violation_type TEXT    NOT NULL,
            class_name     TEXT,
            confidence     REAL,
            speed_kmh      REAL,
            plate_text     TEXT,
            bbox           TEXT,
            frame_number   INTEGER,
            lat            REAL,
            lng            REAL,
            location_name  TEXT,
            camera_id      TEXT,
            source_video   TEXT,
            distraction_label TEXT,
            media_url      TEXT,
            media_type     TEXT DEFAULT 'image'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS frame_stats (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_number     INTEGER,
            timestamp        TEXT,
            vehicle_count    INTEGER,
            avg_speed_kmh    REAL,
            congestion_index REAL,
            source_video     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin', 'user'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cameras (
            id                 TEXT PRIMARY KEY,
            name               TEXT NOT NULL,
            address            TEXT,
            lat                REAL NOT NULL,
            lng                REAL NOT NULL,
            video_file         TEXT,
            is_congestion_zone INTEGER DEFAULT 0
        )
    """)

    for table, col, col_type in [
        ("violations", "lat", "REAL"),
        ("violations", "lng", "REAL"),
        ("violations", "location_name", "TEXT"),
        ("violations", "camera_id", "TEXT"),
        ("violations", "source_video", "TEXT"),
        ("violations", "distraction_label", "TEXT"),
        ("violations", "media_url", "TEXT"),
        ("violations", "media_type", "TEXT"),
        ("frame_stats", "source_video", "TEXT"),
        ("frame_stats", "video_time_sec", "REAL"),
        ("frame_stats", "vehicles_per_minute", "REAL"),
        ("cameras", "video_file", "TEXT"),
        ("cameras", "is_congestion_zone", "INTEGER"),
    ]:
        if not _column_exists(conn, table, col):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    conn.commit()


def seed_users(conn: sqlite3.Connection) -> None:
    for username, password, role in DEFAULT_USERS:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (username, _hash_password(password), role),
            )
    conn.commit()


def seed_cameras(conn: sqlite3.Connection) -> None:
    for src in VIDEO_SOURCES:
        is_cong = 1 if src.get("is_congestion_zone") else 0
        conn.execute(
            """
            INSERT OR IGNORE INTO cameras
            (id, name, address, lat, lng, video_file, is_congestion_zone)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                src["id"], src["name"], src["address"],
                src["lat"], src["lng"], src["video_file"], is_cong,
            ),
        )
        conn.execute(
            """
            UPDATE cameras
            SET name = ?, address = ?, lat = ?, lng = ?, video_file = ?,
                is_congestion_zone = ?
            WHERE id = ?
            """,
            (
                src["name"], src["address"], src["lat"], src["lng"],
                src["video_file"], is_cong, src["id"],
            ),
        )
    conn.commit()


def _resolve_violation_location(source_video: str | None) -> dict:
    """Map a violation to exactly one video / intersection."""
    if source_video:
        loc = get_source_by_video(source_video)
        if loc:
            return loc
    return get_default_video_source()


def sync_violation_locations(conn: sqlite3.Connection) -> None:
    """Assign every violation to its video location (one place per video)."""
    rows = conn.execute(
        "SELECT id, track_id, source_video FROM violations"
    ).fetchall()

    for row_id, _track_id, source_video in rows:
        loc = _resolve_violation_location(source_video)
        lat, lng = exact_coordinates(loc)
        conn.execute(
            """
            UPDATE violations
            SET lat = ?, lng = ?, location_name = ?, camera_id = ?, source_video = ?
            WHERE id = ?
            """,
            (lat, lng, loc["name"], loc["id"], loc["video_file"], row_id),
        )

    if rows:
        conn.commit()


def sync_frame_stats_sources(conn: sqlite3.Connection) -> None:
    """Assign missing source_video on frame_stats to the default active video."""
    default = get_default_video_source()
    conn.execute(
        """
        UPDATE frame_stats
        SET source_video = ?
        WHERE source_video IS NULL OR source_video = ''
        """,
        (default["video_file"],),
    )
    conn.commit()


def save_violation(conn, event: ViolationEvent) -> None:
    """Save a violation event to the database."""
    import json
    conn.execute(
        """
        INSERT INTO violations
            (timestamp, track_id, violation_type, distraction_label,
             class_name, confidence, speed_kmh, bbox, frame_number,
             plate_text, lat, lng, location_name, camera_id,
             source_video, media_url, media_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.timestamp,
            event.track_id,
            event.violation_type,
            event.distraction_label,
            event.class_name,
            event.confidence,
            event.speed_kmh,
            json.dumps(event.bbox) if event.bbox else None,
            event.frame_number,
            event.plate_text,
            event.lat,
            event.lng,
            event.location_name,
            event.camera_id,
            event.source_video,
            event.media_url,
            event.media_type,
        ),
    )
    conn.commit()


def setup_database() -> None:
    conn = get_connection()
    init_schema(conn)
    seed_users(conn)
    seed_cameras(conn)
    sync_violation_locations(conn)
    sync_frame_stats_sources(conn)
    conn.close()


def register_user(username: str, password: str) -> tuple[bool, str]:
    """Create a new user account (role: user). Returns (success, message)."""
    username = username.strip().lower()

    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    if not username.replace("_", "").isalnum():
        return False, "Username may only contain letters, numbers, and underscores."

    conn = get_connection()
    exists = conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (username,)
    ).fetchone()
    if exists:
        conn.close()
        return False, "This username is already taken."

    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
        (username, _hash_password(password), "user"),
    )
    conn.commit()
    conn.close()
    return True, "Account created successfully. You can sign in now."


def authenticate(username: str, password: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT username, password_hash, role FROM users WHERE username = ?",
        (username.strip().lower(),),
    ).fetchone()
    conn.close()

    if not row:
        return None
    if not _verify_password(password, row[1]):
        return None
    return {"username": row[0], "role": row[2]}


def load_violations() -> pd.DataFrame:
    if not Path(DB_PATH).exists():
        return pd.DataFrame()

    setup_database()
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM violations ORDER BY id DESC", conn)
    conn.close()
    return df


def load_frame_stats() -> pd.DataFrame:
    if not Path(DB_PATH).exists():
        return pd.DataFrame()

    conn = get_connection()
    df = pd.read_sql("SELECT * FROM frame_stats ORDER BY frame_number", conn)
    conn.close()
    return df


def load_cameras() -> pd.DataFrame:
    setup_database()
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM cameras ORDER BY name", conn)
    conn.close()
    return df


def get_user_count() -> int:
    setup_database()
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count


def get_violation_count() -> int:
    if not Path(DB_PATH).exists():
        return 0
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
    conn.close()
    return count


def load_users() -> pd.DataFrame:
    setup_database()
    conn = get_connection()
    df = pd.read_sql(
        "SELECT id, username, role FROM users ORDER BY username", conn
    )
    conn.close()
    return df


def delete_user(username: str, current_username: str) -> tuple[bool, str]:
    username = username.strip().lower()
    current_username = current_username.strip().lower()

    if username == current_username:
        return False, "You cannot delete your own account."

    conn = get_connection()
    row = conn.execute(
        "SELECT id, role FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row:
        conn.close()
        return False, "User not found."

    if row[1] == "admin":
        admin_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()[0]
        if admin_count <= 1:
            conn.close()
            return False, "Cannot delete the last administrator account."

    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return True, f"User '{username}' has been deleted."


def create_user_by_admin(
    username: str,
    password: str,
    role: str = "user",
) -> tuple[bool, str]:
    username = username.strip().lower()
    if role not in ("user", "admin"):
        return False, "Invalid role."
    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."

    conn = get_connection()
    exists = conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (username,)
    ).fetchone()
    if exists:
        conn.close()
        return False, "This username is already taken."

    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
        (username, _hash_password(password), role),
    )
    conn.commit()
    conn.close()
    return True, f"User '{username}' created ({role})."


def filter_violations(
    df: pd.DataFrame,
    violation_types: list[str] | None = None,
    locations: list[str] | None = None,
    source_videos: list[str] | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if violation_types and "violation_type" in out.columns:
        out = out[out["violation_type"].isin(violation_types)]
    if locations and "location_name" in out.columns:
        out = out[out["location_name"].isin(locations)]
    if source_videos and "source_video" in out.columns:
        out = out[out["source_video"].isin(source_videos)]
    return out


VIOLATION_DISPLAY_MAP = {
    "speeding": "Speeding",
    "red_light": "Red Light",
    "phone": "Phone Use",
    "no_seatbelt": "No Seatbelt",
    "cigarette": "Smoking",
}

def map_violation_to_category(row) -> str:
    vt = row.get("violation_type")
    dl = row.get("distraction_label")
    if vt == "speeding":
        return "speeding"
    elif vt == "red_light":
        return "red_light"
    elif vt == "distracted":
        if dl in ("phone", "no_seatbelt", "cigarette"):
            return dl
    elif vt in ("phone", "no_seatbelt", "cigarette"):
        return vt
    return "other"

def get_violation_display_name(row) -> str:
    cat = map_violation_to_category(row)
    return VIOLATION_DISPLAY_MAP.get(cat, str(row.get("violation_type")).title())

def add_mapped_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["violation_category"] = out.apply(map_violation_to_category, axis=1)
    out["violation_display"] = out["violation_category"].map(VIOLATION_DISPLAY_MAP).fillna(out["violation_type"])
    return out


def build_report_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "total": 0,
            "speeding": 0,
            "red_light": 0,
            "phone": 0,
            "no_seatbelt": 0,
            "cigarette": 0,
            "avg_speed": 0.0,
            "locations": 0,
            "top_location": "—",
        }

    mapped_df = add_mapped_columns(df)
    
    speeding = int((mapped_df["violation_category"] == "speeding").sum()) if "violation_category" in mapped_df.columns else 0
    red_light = int((mapped_df["violation_category"] == "red_light").sum()) if "violation_category" in mapped_df.columns else 0
    phone = int((mapped_df["violation_category"] == "phone").sum()) if "violation_category" in mapped_df.columns else 0
    no_seatbelt = int((mapped_df["violation_category"] == "no_seatbelt").sum()) if "violation_category" in mapped_df.columns else 0
    cigarette = int((mapped_df["violation_category"] == "cigarette").sum()) if "violation_category" in mapped_df.columns else 0
    
    avg_speed = float(df["speed_kmh"].dropna().mean()) if "speed_kmh" in df.columns else 0.0

    top_location = "—"
    loc_count = 0
    if "location_name" in df.columns and df["location_name"].notna().any():
        loc_count = int(df["location_name"].nunique())
        top_location = str(df["location_name"].value_counts().index[0])

    return {
        "total": len(df),
        "speeding": speeding,
        "red_light": red_light,
        "phone": phone,
        "no_seatbelt": no_seatbelt,
        "cigarette": cigarette,
        "avg_speed": round(avg_speed, 1),
        "locations": loc_count,
        "top_location": top_location,
    }


def build_html_report(df: pd.DataFrame, summary: dict) -> str:
    from datetime import datetime

    mapped_df = add_mapped_columns(df)
    by_type = (
        mapped_df["violation_display"].value_counts().to_frame("Count").to_html()
        if not mapped_df.empty and "violation_display" in mapped_df.columns
        else "<p>No data</p>"
    )
    by_location = (
        df["location_name"].value_counts().to_frame("Count").to_html()
        if not df.empty and "location_name" in df.columns
        else "<p>No data</p>"
    )
    rows_html = (
        df.head(200).to_html(index=False)
        if not df.empty
        else "<p>No violations match the selected filters.</p>"
    )

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Traffic AI Morocco — Violation Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; color: #222; }}
    h1 {{ color: #E63946; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
    th {{ background: #f5f5f5; }}
    .kpi-container {{ display: flex; flex-wrap: wrap; margin-bottom: 2rem; gap: 1.5rem; }}
    .kpi {{ background: #f8f9fa; border: 1px solid #e9ecef; padding: 1rem; border-radius: 8px; min-width: 120px; }}
    .kpi strong {{ display: block; font-size: 0.9rem; color: #6c757d; text-transform: uppercase; }}
    .kpi span {{ font-size: 1.5rem; font-weight: bold; color: #212529; }}
  </style>
</head>
<body>
  <h1>Traffic AI Morocco — Violation Report</h1>
  <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
  <div class="kpi-container">
    <div class="kpi"><strong>Total violations</strong><span>{summary['total']}</span></div>
    <div class="kpi"><strong>Speeding</strong><span>{summary['speeding']}</span></div>
    <div class="kpi"><strong>Red light</strong><span>{summary['red_light']}</span></div>
    <div class="kpi"><strong>Phone</strong><span>{summary['phone']}</span></div>
    <div class="kpi"><strong>No Seatbelt</strong><span>{summary['no_seatbelt']}</span></div>
    <div class="kpi"><strong>Smoking</strong><span>{summary['cigarette']}</span></div>
    <div class="kpi"><strong>Avg speed</strong><span>{summary['avg_speed']} km/h</span></div>
    <div class="kpi"><strong>Top location</strong><span>{summary['top_location']}</span></div>
  </div>
  <h2>By violation type</h2>
  {by_type}
  <h2>By location</h2>
  {by_location}
  <h2>Violation details (up to 200 rows)</h2>
  {rows_html}
</body>
</html>"""
