"""
Video-to-location registry for Traffic AI Morocco.

Coordinates are WGS84 (lat, lng) from OpenStreetMap / Wikipedia.
Edit lat/lng here to match the exact camera position on the map.
"""

from __future__ import annotations

from pathlib import Path

# City center — Casablanca
CASABLANCA_CENTER = {"lat": 33.5731, "lng": -7.5898}

VIDEOS_DIR = Path("videos")
OUTPUT_DIR = VIDEOS_DIR / "output"
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}

# is_congestion_zone: True only when this camera feed tracks congestion
VIDEO_SOURCES = [
    {
        "id": "vid_01",
        "video_file": "place_mohammed_v.mp4",
        "name": "Place Mohammed V",
        "address": "Place Mohammed V, Casablanca 20000",
        "lat": 33.591793,
        "lng": -7.619694,
        "is_congestion_zone": False,
    },
    {
        "id": "vid_02",
        "video_file": "bd_zerktouni.mp4",
        "name": "Boulevard Zerktouni",
        "address": "Bd Zerktouni & Rue Sebou, Casablanca",
        "lat": 33.583800,
        "lng": -7.628500,
        "is_congestion_zone": True,
    },
    {
        "id": "vid_03",
        "video_file": "place_nations_unies.mp4",
        "name": "Place des Nations Unies",
        "address": "Place des Nations Unies, Casablanca",
        "lat": 33.594861,
        "lng": -7.618556,
        "is_congestion_zone": True,
    },
    {
        "id": "vid_04",
        "video_file": "corniche_ain_diab.mp4",
        "name": "Corniche Ain Diab",
        "address": "Boulevard de la Corniche, Ain Diab, Casablanca",
        "lat": 33.587200,
        "lng": -7.676000,
        "is_congestion_zone": False,
    },
    {
        "id": "vid_05",
        "video_file": "bd_anfa.mp4",
        "name": "Boulevard d'Anfa",
        "address": "Bd d'Anfa & Bd de la Résistance, Casablanca",
        "lat": 33.589700,
        "lng": -7.636700,
        "is_congestion_zone": False,
    },
]

SOURCES_BY_ID = {s["id"]: s for s in VIDEO_SOURCES}
SOURCES_BY_FILENAME = {s["video_file"]: s for s in VIDEO_SOURCES}

# Major Casablanca zones for route planning (OpenStreetMap coordinates).
# monitoring_video: links to a camera feed in VIDEO_SOURCES (if any).
CASABLANCA_ZONES = [
    {"id": "casa_01", "name": "Place Mohammed V", "district": "Centre", "lat": 33.591793, "lng": -7.619694, "monitoring_video": "place_mohammed_v.mp4"},
    {"id": "casa_02", "name": "Place des Nations Unies", "district": "Centre", "lat": 33.594861, "lng": -7.618556, "monitoring_video": "place_nations_unies.mp4"},
    {"id": "casa_03", "name": "Boulevard Zerktouni", "district": "Maarif", "lat": 33.583800, "lng": -7.628500, "monitoring_video": "bd_zerktouni.mp4"},
    {"id": "casa_04", "name": "Maarif", "district": "Maarif", "lat": 33.583000, "lng": -7.628000},
    {"id": "casa_05", "name": "Gauthier", "district": "Gauthier", "lat": 33.589000, "lng": -7.625000},
    {"id": "casa_06", "name": "Twin Center", "district": "Gauthier", "lat": 33.589500, "lng": -7.622000},
    {"id": "casa_07", "name": "Boulevard d'Anfa", "district": "Anfa", "lat": 33.589700, "lng": -7.636700, "monitoring_video": "bd_anfa.mp4"},
    {"id": "casa_08", "name": "Corniche Ain Diab", "district": "Ain Diab", "lat": 33.587200, "lng": -7.676000, "monitoring_video": "corniche_ain_diab.mp4"},
    {"id": "casa_09", "name": "Casa Port", "district": "Port", "lat": 33.602000, "lng": -7.618000},
    {"id": "casa_10", "name": "Casa Voyageurs", "district": "Roches Noires", "lat": 33.589500, "lng": -7.590000},
    {"id": "casa_11", "name": "Mers Sultan", "district": "Mers Sultan", "lat": 33.579000, "lng": -7.610000},
    {"id": "casa_12", "name": "Derb Omar", "district": "Centre", "lat": 33.586000, "lng": -7.615000},
    {"id": "casa_13", "name": "Roches Noires", "district": "Roches Noires", "lat": 33.594000, "lng": -7.585000},
    {"id": "casa_14", "name": "Sidi Maarouf", "district": "Sidi Maarouf", "lat": 33.534000, "lng": -7.652000},
    {"id": "casa_15", "name": "Hay Hassani", "district": "Hay Hassani", "lat": 33.548000, "lng": -7.682000},
    {"id": "casa_16", "name": "Oulfa", "district": "Oulfa", "lat": 33.562000, "lng": -7.665000},
    {"id": "casa_17", "name": "Californie", "district": "Californie", "lat": 33.541000, "lng": -7.638000},
    {"id": "casa_18", "name": "Ain Sebaa", "district": "Ain Sebaa", "lat": 33.608000, "lng": -7.585000},
]

ZONES_BY_ID = {z["id"]: z for z in CASABLANCA_ZONES}


def get_zone_by_id(zone_id: str | None) -> dict | None:
    if zone_id and zone_id in ZONES_BY_ID:
        return ZONES_BY_ID[zone_id]
    return None


def video_as_zone(video_entry: dict) -> dict:
    """Turn an on-disk video feed into a routable map point."""
    return {
        "id": video_entry["id"],
        "name": video_entry["name"],
        "district": "Casablanca",
        "lat": video_entry["lat"],
        "lng": video_entry["lng"],
        "monitoring_video": video_entry["video_file"],
        "video_file": video_entry["video_file"],
        "is_congestion_zone": True,
    }


def get_active_video_filenames() -> set[str]:
    """Return filenames of video files physically present in videos/."""
    return {p.name for p in scan_video_files()}


def is_zone_active_video(zone: dict) -> bool:
    """Check if a zone is associated with a video that is physically present."""
    vf = zone.get("monitoring_video") or zone.get("video_file")
    if not vf:
        return False
    return Path(vf).name in get_active_video_filenames()


def get_active_zones() -> list[dict]:
    """Only locations that have a video file in videos/."""
    return [video_as_zone(v) for v in list_available_videos()]



def get_active_zones_by_id() -> dict[str, dict]:
    return {z["id"]: z for z in get_active_zones()}


def get_routing_zones() -> list[dict]:
    active = get_active_zones()
    return active if active else list(CASABLANCA_ZONES)


def zone_monitoring_source(zone: dict) -> dict | None:
    """Return linked VIDEO_SOURCE for a zone, if configured."""
    vf = zone.get("monitoring_video")
    if vf:
        return get_source_by_video(vf)
    return None


def zone_is_congestion_monitored(zone: dict) -> bool:
    src = zone_monitoring_source(zone)
    return bool(src and is_congestion_zone(src))


def exact_coordinates(source: dict) -> tuple[float, float]:
    """Canonical GPS point for a video / camera."""
    return float(source["lat"]), float(source["lng"])


def is_congestion_zone(source: dict) -> bool:
    return bool(source.get("is_congestion_zone", False))


def get_congestion_zones() -> list[dict]:
    return [s for s in VIDEO_SOURCES if is_congestion_zone(s)]


def get_violation_sources() -> list[dict]:
    return list(VIDEO_SOURCES)


def ensure_dirs() -> None:
    VIDEOS_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)


def resolve_video_path(video: str | Path) -> Path:
    path = Path(video)
    if path.exists():
        return path.resolve()

    name = path.name
    if name in SOURCES_BY_FILENAME:
        candidate = VIDEOS_DIR / name
        if candidate.exists():
            return candidate.resolve()

    in_videos = VIDEOS_DIR / name
    if in_videos.exists():
        return in_videos.resolve()

    return path


def scan_video_files() -> list[Path]:
    """Return video files physically present in videos/ (not output/)."""
    ensure_dirs()
    return sorted(
        p for p in VIDEOS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def _auto_source(video_file: str) -> dict:
    """Build metadata for a video file not listed in VIDEO_SOURCES."""
    stem = Path(video_file).stem.replace("_", " ").replace("-", " ")
    return {
        "id": f"vid_{Path(video_file).stem[:24]}",
        "video_file": video_file,
        "name": stem.title(),
        "address": "Casablanca — auto-detected from videos/",
        "lat": CASABLANCA_CENTER["lat"],
        "lng": CASABLANCA_CENTER["lng"],
        "is_congestion_zone": True,
        "auto_detected": True,
    }


def get_source_by_video(video: str | Path) -> dict | None:
    name = Path(video).name
    if name in SOURCES_BY_FILENAME:
        return SOURCES_BY_FILENAME[name]
    candidate = VIDEOS_DIR / name
    if candidate.exists() and candidate.suffix.lower() in VIDEO_EXTENSIONS:
        return _auto_source(name)
    return None


def get_source_by_id(source_id: str | None) -> dict | None:
    if source_id and source_id in SOURCES_BY_ID:
        return SOURCES_BY_ID[source_id]
    return None


def get_location(source_id: str | None = None, seed: int = 0) -> dict:
    source = get_source_by_id(source_id)
    if source:
        return source
    idx = seed % len(VIDEO_SOURCES)
    return VIDEO_SOURCES[idx]


def annotated_output_path(video_file: str) -> Path:
    ensure_dirs()
    stem = Path(video_file).stem
    return OUTPUT_DIR / f"{stem}_annotated.mp4"


def _build_video_entry(src: dict, path: Path) -> dict:
    lat, lng = exact_coordinates(src)
    return {
        **src,
        "is_congestion_zone": True,
        "path": str(path),
        "exists": True,
        "annotated": str(annotated_output_path(src["video_file"])),
        "annotated_exists": annotated_output_path(src["video_file"]).exists(),
        "zone_type": "Active camera feed",
        "coords_label": f"{lat:.6f}, {lng:.6f}",
    }


def list_available_videos() -> list[dict]:
    """Only videos that exist on disk in videos/."""
    result = []
    for path in scan_video_files():
        src = get_source_by_video(path.name)
        if src:
            result.append(_build_video_entry(src, path))
    return result


def list_registered_videos() -> list[dict]:
    """Alias — returns only files present in videos/ (not the full registry)."""
    return list_available_videos()


def count_available_videos() -> int:
    return len(scan_video_files())


def get_default_video_source() -> dict:
    available = list_available_videos()
    if available:
        return available[0]
    return VIDEO_SOURCES[0]


# Deprecated — kept so old imports do not break; returns exact coords now
def marker_spread(lat: float, lng: float, seed: int = 0) -> tuple[float, float]:
    return lat, lng


jitter_coordinates = marker_spread
CASABLANCA_LOCATIONS = VIDEO_SOURCES
LOCATIONS_BY_ID = SOURCES_BY_ID
