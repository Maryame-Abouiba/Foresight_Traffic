"""Tables and helpers for congestion history, accidents and predictions."""
import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "foresight.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS congestion_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zone TEXT NOT NULL,
  ts TEXT NOT NULL,
  congestion_index REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'simulated',
  UNIQUE(zone, ts, source));
CREATE INDEX IF NOT EXISTS ix_hist_zone_ts ON congestion_history(zone, ts);

CREATE TABLE IF NOT EXISTS accidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zone TEXT NOT NULL,
  camera TEXT,
  latitude REAL,
  longitude REAL,
  confidence REAL NOT NULL,
  detected_at TEXT NOT NULL,
  expires_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS predicted_congestion(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zone TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  horizon_min INTEGER NOT NULL,
  predicted_index REAL NOT NULL,
  model_name TEXT);
"""

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_tables():
    with connect() as con:
        con.executescript(SCHEMA)


def insert_history(rows, source):
    """rows: iterable of (zone, datetime, index). Duplicates are ignored."""
    data = [(z, t.strftime(TS_FORMAT), float(v), source) for z, t, v in rows]
    with connect() as con:
        con.executemany(
            "INSERT OR IGNORE INTO congestion_history(zone, ts, congestion_index, source) "
            "VALUES (?,?,?,?)", data)


def clear_source(source):
    with connect() as con:
        con.execute("DELETE FROM congestion_history WHERE source=?", (source,))


def recent_history(zone, n=12, source="simulated"):
    """Return the n latest (ts, index) pairs of a zone, oldest first."""
    with connect() as con:
        cur = con.execute(
            "SELECT ts, congestion_index FROM congestion_history "
            "WHERE zone=? AND source=? ORDER BY ts DESC LIMIT ?", (zone, source, n))
        return list(reversed(cur.fetchall()))