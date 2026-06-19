"""
db_utils_postgres.py
=====================
PostgreSQL version of db_utils.py, replacing sqlite3 with psycopg2.

Usage:
    pip install psycopg2-binary

Environment variables expected (.env):
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

import psycopg2
import psycopg2.extras


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "traffic_ai"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS violations (
                id              SERIAL PRIMARY KEY,
                timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
                violation_type  TEXT NOT NULL,
                distraction_label TEXT,
                track_id        INTEGER NOT NULL,
                class_name      TEXT,
                confidence      REAL,
                speed_kmh       REAL,
                bbox            JSONB,
                frame_number    INTEGER,
                plate_text      TEXT,
                lat             DOUBLE PRECISION,
                lng             DOUBLE PRECISION,
                location_name   TEXT,
                camera_id       TEXT,
                source_video    TEXT,
                media_url       TEXT,
                media_type      TEXT DEFAULT 'image',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_type ON violations (violation_type);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_timestamp ON violations (timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_plate ON violations (plate_text);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_camera ON violations (camera_id);")
    conn.commit()
    logging.info("PostgreSQL schema ready.")


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


def save_violation(conn, event: ViolationEvent) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO violations
                (timestamp, track_id, violation_type, distraction_label,
                 class_name, confidence, speed_kmh, bbox, frame_number,
                 plate_text, lat, lng, location_name, camera_id,
                 source_video, media_url, media_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.timestamp,
                event.track_id,
                event.violation_type,
                event.distraction_label,
                event.class_name,
                event.confidence,
                event.speed_kmh,
                psycopg2.extras.Json(event.bbox) if event.bbox else None,
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