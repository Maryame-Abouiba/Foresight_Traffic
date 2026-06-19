-- =========================================================================
-- Traffic AI Morocco — PostgreSQL schema
-- Replaces the SQLite violations.db used during prototyping.
-- =========================================================================

CREATE TABLE IF NOT EXISTS violations (
    id                  SERIAL PRIMARY KEY,

    -- Core violation identity
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT now(),
    violation_type       TEXT NOT NULL,          -- 'speeding' | 'red_light' | 'distracted'
    distraction_label    TEXT,                    -- 'phone' | 'cigarette' | 'no_seatbelt' | NULL

    -- Vehicle / tracking
    track_id             INTEGER NOT NULL,
    class_name           TEXT,                    -- e.g. 'car', 'truck'
    confidence           REAL,
    speed_kmh            REAL,                    -- NULL unless violation_type = 'speeding'
    bbox                 JSONB,                   -- [x1, y1, x2, y2] at time of violation
    frame_number         INTEGER,

    -- Plate (read ONLY after a violation already fired)
    plate_text           TEXT,

    -- Location ("address of violation")
    lat                  DOUBLE PRECISION,
    lng                  DOUBLE PRECISION,
    location_name        TEXT,
    camera_id            TEXT,
    source_video         TEXT,

    -- Object storage pointer — populated later once MinIO/S3 is wired in
    media_url             TEXT,
    media_type            TEXT DEFAULT 'image',

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_violations_type      ON violations (violation_type);
CREATE INDEX IF NOT EXISTS idx_violations_timestamp ON violations (timestamp);
CREATE INDEX IF NOT EXISTS idx_violations_plate     ON violations (plate_text);
CREATE INDEX IF NOT EXISTS idx_violations_camera    ON violations (camera_id);