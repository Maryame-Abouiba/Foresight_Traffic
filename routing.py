"""
Road routing for Casablanca — uses OSRM silently in the background.
Congestion data comes only from videos present in videos/.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import requests

from congestion import congestion_level_label
from db_utils import load_frame_stats
from locations import (
    CASABLANCA_ZONES,
    exact_coordinates,
    get_active_zones,
    get_active_zones_by_id,
)

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
CONGESTION_RADIUS_KM = 0.35

# Both the "Avoid congestion" slider and each zone's congestion index are
# on the same 0-100 scale. A zone becomes a hard constraint (must be
# avoided entirely) when the user's slider value is at or above that
# zone's own congestion index; below it, the zone is only a soft
# preference in the duration/congestion scoring.


def search_casablanca_place(query: str) -> list[dict]:
    """Search for locations in Casablanca using Nominatim OpenStreetMap API."""
    if not query or not query.strip():
        return []

    # Biasing query to Casablanca, Morocco
    full_query = f"{query.strip()}, Casablanca, Morocco"
    headers = {
        "User-Agent": "TrafficAIMorocco/1.0 (contact@trafficaimorocco.ma)"
    }
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": full_query,
        "format": "json",
        "limit": 6,
        "addressdetails": 1
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()

        places = []
        for r in results:
            # Casablanca coordinates boundary check
            # lat: [33.4, 33.7], lng: [-7.8, -7.4]
            lat = float(r["lat"])
            lng = float(r["lon"])
            if 33.4 <= lat <= 33.7 and -7.8 <= lng <= -7.4:
                # Format name nicely
                raw_name = r.get("display_name", "")
                parts = [p.strip() for p in raw_name.split(",")]
                name = parts[0] if parts else "Location"
                display_name = ", ".join(parts[:4])

                places.append({
                    "name": name,
                    "display_name": display_name,
                    "lat": lat,
                    "lng": lng
                })
        return places
    except Exception:
        return []


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _offset_point(lat: float, lng: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """Move a point a given distance in a given bearing direction (spherical formula)."""
    r = 6371.0
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    d_r = distance_km / r

    lat2 = math.asin(
        math.sin(lat1) * math.cos(d_r) + math.cos(lat1) * math.sin(d_r) * math.cos(bearing)
    )
    lng2 = lng1 + math.atan2(
        math.sin(bearing) * math.sin(d_r) * math.cos(lat1),
        math.cos(d_r) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lng2)


def load_zone_congestion() -> dict[str, float]:
    """Congestion keyed by active video zone id."""
    congestion: dict[str, float] = {}
    stats = load_frame_stats()
    by_video: dict[str, float] = {}
    if not stats.empty and "source_video" in stats.columns:
        by_video = stats.groupby("source_video")["congestion_index"].mean().to_dict()

    for zone in get_active_zones():
        vf = zone.get("video_file") or zone.get("monitoring_video")
        if vf:
            congestion[zone["id"]] = float(by_video.get(vf, 0.0))
    return congestion


def congestion_color(value: float) -> str:
    if value <= 0:
        return "blue"
    if value < 30:
        return "green"
    if value < 60:
        return "orange"
    if value < 80:
        return "red"
    return "darkred"


def _geojson_to_latlng(geometry: dict) -> list[list[float]]:
    coords = geometry.get("coordinates", [])
    return [[pt[1], pt[0]] for pt in coords]


def _fetch_osrm_routes(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    alternatives: bool = True,
) -> list[dict]:
    coord_str = f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
    resp = requests.get(
        f"{OSRM_URL}/{coord_str}",
        params={
            "overview": "full",
            "geometries": "geojson",
            "alternatives": "true" if alternatives else "false",
            "steps": "false",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(data.get("message", "Could not find a route."))
    return data["routes"]


def _route_avoids_zones(
    geometry: list[list[float]],
    zones: list[dict],
    radius_km: float = CONGESTION_RADIUS_KM,
) -> bool:
    """True if the route never comes within radius_km of any of the given zones."""
    if not geometry:
        return False
    step = max(1, len(geometry) // 60)
    for i in range(0, len(geometry), step):
        lat, lng = geometry[i]
        for zone in zones:
            zlat, zlng = exact_coordinates(zone)
            if haversine_km(lat, lng, zlat, zlng) <= radius_km:
                return False
    return True


def _fetch_detour_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    avoid_zones: list[dict],
    require_clear: bool = False,
) -> dict | None:
    """Build a route through an offset waypoint to steer around congested zones.

    Used as a fallback when OSRM's public server does not return a genuine
    alternative route (a common limitation on short/medium urban trips).

    Tries increasing offset distances, on both sides of the direct bearing,
    and checks whether each candidate actually clears the congestion radius
    around the zones being avoided (OSRM can otherwise snap the waypoint
    back onto a road inside that radius, common near dense/constrained
    areas like a port, producing a route that loops right past the zone
    instead of avoiding it).

    require_clear=False (soft mode): returns the first candidate that
    clears the zones, or — if none does — the best-effort candidate found,
    so it can still be scored against the direct route.

    require_clear=True (hard mode): the zone must genuinely be avoided, so
    the offsets tried go further, and None is returned if no candidate
    manages to clear the zones (the caller must then decide how to handle
    that, rather than silently returning a route that still crosses it).
    """
    if not avoid_zones:
        return None

    # Average position of the zones to avoid
    avg_lat = sum(exact_coordinates(z)[0] for z in avoid_zones) / len(avoid_zones)
    avg_lng = sum(exact_coordinates(z)[1] for z in avoid_zones) / len(avoid_zones)

    # General direction of the trip
    bearing_to_dest = math.degrees(
        math.atan2(dest_lng - origin_lng, dest_lat - origin_lat)
    )

    if require_clear:
        # Hard constraint: allow much larger detours before giving up.
        offsets_km = [f * CONGESTION_RADIUS_KM for f in (1.5, 2.5, 4.0, 6.0, 9.0, 13.0, 18.0)]
    else:
        offsets_km = [f * CONGESTION_RADIUS_KM for f in (1.5, 2.5, 4.0, 6.0)]

    fallback_route = None  # best route found even if it doesn't fully clear

    for offset_km in offsets_km:
        for perpendicular_offset in (90, -90):
            bearing = (bearing_to_dest + perpendicular_offset) % 360
            wp_lat, wp_lng = _offset_point(avg_lat, avg_lng, bearing, offset_km)

            coord_str = f"{origin_lng},{origin_lat};{wp_lng},{wp_lat};{dest_lng},{dest_lat}"
            try:
                resp = requests.get(
                    f"{OSRM_URL}/{coord_str}",
                    params={
                        "overview": "full",
                        "geometries": "geojson",
                        "alternatives": "false",
                        "steps": "false",
                    },
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "Ok" or not data.get("routes"):
                    continue
                candidate = _normalize_route(data["routes"][0])
            except Exception:
                continue

            if fallback_route is None or candidate["duration_min"] < fallback_route["duration_min"]:
                fallback_route = candidate

            if _route_avoids_zones(candidate["geometry"], avoid_zones):
                # First candidate that genuinely clears the zone: keep the
                # smallest offset that works, to avoid unnecessarily long detours.
                return candidate

    if require_clear:
        # No offset managed to fully clear the zone (e.g. a peninsula-like
        # port road network with no viable bypass). Let the caller decide;
        # do not silently pretend a non-clearing route satisfies the
        # hard constraint.
        return None

    # Soft mode: no offset fully cleared the zone, return the closest
    # attempt rather than nothing, the scoring step will still penalize
    # it correctly against the direct route.
    return fallback_route


def _zones_on_direct_path(
    direct_geometry: list[list[float]],
    congestion: dict[str, float],
) -> list[tuple[dict, float]]:
    """Return (zone, congestion_value) pairs for every zone with congestion > 0
    that actually lies on the direct route. Filtering by sensitivity happens
    in the caller, since the same zone can be a hard constraint for one
    trip and a soft preference for another depending on the slider value.
    """
    zones_by_id = get_active_zones_by_id()
    hits: list[tuple[dict, float]] = []
    if not direct_geometry:
        return hits
    step = max(1, len(direct_geometry) // 40)
    for zid, cong in congestion.items():
        if cong <= 0:
            continue
        zone = zones_by_id.get(zid)
        if not zone:
            continue
        zlat, zlng = exact_coordinates(zone)
        for i in range(0, len(direct_geometry), step):
            lat, lng = direct_geometry[i]
            if haversine_km(lat, lng, zlat, zlng) <= CONGESTION_RADIUS_KM:
                hits.append((zone, cong))
                break
    return hits


def _route_congestion_score(
    geometry: list[list[float]],
    congestion: dict[str, float],
    sensitivity: float,
) -> float:
    if not geometry or not congestion:
        return 0.0

    step = max(1, len(geometry) // 40)
    score = 0.0
    zones_by_id = get_active_zones_by_id()
    for i in range(0, len(geometry), step):
        lat, lng = geometry[i]
        for zid, cong in congestion.items():
            if cong <= 0:
                continue
            zone = zones_by_id.get(zid)
            if not zone:
                continue
            zlat, zlng = exact_coordinates(zone)
            dist = haversine_km(lat, lng, zlat, zlng)
            if dist <= CONGESTION_RADIUS_KM:
                weight = (1.0 - dist / CONGESTION_RADIUS_KM) * (cong / 100.0)
                score += weight * (sensitivity / 100.0)
    return score


def _normalize_route(raw: dict) -> dict:
    geometry = _geojson_to_latlng(raw["geometry"])
    return {
        "geometry": geometry,
        "distance_km": round(raw["distance"] / 1000.0, 2),
        "duration_min": round(raw["duration"] / 60.0, 1),
        "summary": raw.get("summary", "Route"),
    }


def _route_passes_active_video(
    geometry: list[list[float]],
    active_zones: list[dict],
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> bool:
    """Check if the route passes near any active video zone, excluding origin and destination zones."""
    if not geometry or not active_zones:
        return False
    step = max(1, len(geometry) // 40)
    for i in range(0, len(geometry), step):
        lat, lng = geometry[i]
        for zone in active_zones:
            zlat, zlng = exact_coordinates(zone)
            # Skip checking if the zone is close to the start or end of the route (within 100 meters)
            if haversine_km(zlat, zlng, origin_lat, origin_lng) < 0.1:
                continue
            if haversine_km(zlat, zlng, dest_lat, dest_lng) < 0.1:
                continue
            if haversine_km(lat, lng, zlat, zlng) <= CONGESTION_RADIUS_KM:
                return True
    return False


def plan_routes(
    origin_id_or_dict: str | dict[str, Any],
    dest_id_or_dict: str | dict[str, Any],
    sensitivity: float = 60.0,
    provider: str = "osrm",
) -> dict[str, Any]:
    # Build a lookup dictionary of all possible zones (both Casablanca zones and active ones)
    zones_by_id = {}
    for z in CASABLANCA_ZONES:
        zones_by_id[z["id"]] = z
    for z in get_active_zones():
        zones_by_id[z["id"]] = z

    if isinstance(origin_id_or_dict, dict):
        origin = origin_id_or_dict
        if "id" not in origin:
            origin["id"] = "custom_origin"
    else:
        origin = zones_by_id.get(origin_id_or_dict)

    if isinstance(dest_id_or_dict, dict):
        dest = dest_id_or_dict
        if "id" not in dest:
            dest["id"] = "custom_dest"
    else:
        dest = zones_by_id.get(dest_id_or_dict)

    if not origin or not dest:
        return {"error": "Invalid origin or destination."}

    o_lat, o_lng = exact_coordinates(origin)
    d_lat, d_lng = exact_coordinates(dest)
    congestion = load_zone_congestion()
    active_zones = get_active_zones()

    try:
        raw_routes = _fetch_osrm_routes(o_lat, o_lng, d_lat, d_lng, alternatives=True)
    except Exception as exc:
        return {"error": str(exc)}

    candidates = [_normalize_route(r) for r in raw_routes]
    direct = min(candidates, key=lambda r: r["duration_min"])

    # Zones the direct route actually passes through, each with its own
    # congestion index (0-100), on the same scale as the "Avoid congestion"
    # slider (sensitivity). Compare the two per zone:
    #   sensitivity >= zone congestion  -> hard constraint, must be avoided
    #   sensitivity <  zone congestion  -> soft preference, only scored
    zones_on_path = _zones_on_direct_path(direct["geometry"], congestion)
    hard_zones = [zone for zone, cong in zones_on_path if sensitivity >= cong]
    soft_zones = [zone for zone, cong in zones_on_path if sensitivity < cong]

    recommended = None

    if hard_zones:
        forced = _fetch_detour_route(o_lat, o_lng, d_lat, d_lng, hard_zones, require_clear=True)
        if forced is not None:
            recommended = forced
        else:
            # No offset could fully clear the hard-constraint zone(s)
            # (e.g. a port peninsula with no real bypass). Fall back to the
            # best-effort detour and let the scoring below pick between it
            # and the direct route, rather than pretending it is clear.
            best_effort = _fetch_detour_route(o_lat, o_lng, d_lat, d_lng, hard_zones, require_clear=False)
            if best_effort is not None:
                candidates.append(best_effort)

    if recommended is None and soft_zones:
        detour = _fetch_detour_route(o_lat, o_lng, d_lat, d_lng, soft_zones, require_clear=False)
        if detour is not None:
            candidates.append(detour)

    if recommended is None:
        if len(candidates) > 1:
            # Score candidates. We add a 30-minute penalty if the candidate route passes near an active video camera.
            scored = sorted(
                candidates,
                key=lambda r: (
                    r["duration_min"]
                    + _route_congestion_score(r["geometry"], congestion, sensitivity) * 15
                    + (30.0 if _route_passes_active_video(r["geometry"], active_zones, o_lat, o_lng, d_lat, d_lng) else 0.0)
                ),
            )
            recommended = scored[0]
        else:
            recommended = direct

    direct_cong = _route_congestion_score(direct["geometry"], congestion, 100.0)
    alt_cong = _route_congestion_score(recommended["geometry"], congestion, 100.0)

    # Congestion avoidance display
    avoided = []
    for zid, val in congestion.items():
        if val < 50:
            continue
        zone = zones_by_id.get(zid)
        if not zone:
            continue
        zlat, zlng = exact_coordinates(zone)
        direct_near = any(
            haversine_km(pt[0], pt[1], zlat, zlng) <= CONGESTION_RADIUS_KM
            for pt in direct["geometry"][::max(1, len(direct["geometry"]) // 20)]
        )
        alt_near = any(
            haversine_km(pt[0], pt[1], zlat, zlng) <= CONGESTION_RADIUS_KM
            for pt in recommended["geometry"][::max(1, len(recommended["geometry"]) // 20)]
        )
        if direct_near and not alt_near:
            avoided.append(zid)

    # Active video camera avoidance display
    avoided_active_videos = []
    for zone in active_zones:
        zlat, zlng = exact_coordinates(zone)
        # Skip origin/destination zones
        if haversine_km(zlat, zlng, o_lat, o_lng) < 0.1 or haversine_km(zlat, zlng, d_lat, d_lng) < 0.1:
            continue
        direct_near = _route_passes_active_video(direct["geometry"], [zone], o_lat, o_lng, d_lat, d_lng)
        alt_near = _route_passes_active_video(recommended["geometry"], [zone], o_lat, o_lng, d_lat, d_lng)
        if direct_near and not alt_near:
            avoided_active_videos.append(zone["name"])

    return {
        "congestion": congestion,
        "origin": origin,
        "dest": dest,
        "direct_geometry": direct["geometry"],
        "alt_geometry": recommended["geometry"],
        "direct_km": direct["distance_km"],
        "alt_km": recommended["distance_km"],
        "direct_eta_min": direct["duration_min"],
        "alt_eta_min": recommended["duration_min"],
        "direct_max_congestion": round(direct_cong * 100, 1),
        "alt_max_congestion": round(alt_cong * 100, 1),
        "avoided_zone_ids": avoided,
        "avoided_active_videos": avoided_active_videos,
        "is_different": direct["geometry"] != recommended["geometry"],
        "direct_summary": direct.get("summary", ""),
        "alt_summary": recommended.get("summary", ""),
    }


def congestion_table() -> pd.DataFrame:
    congestion = load_zone_congestion()
    rows = []
    for zone in get_active_zones():
        val = congestion.get(zone["id"], 0.0)
        rows.append({
            "Location": zone["name"],
            "Video": zone.get("video_file", "—"),
            "Congestion": round(val, 1),
            "Level": congestion_level_label(val),
        })
    if not rows:
        return pd.DataFrame(columns=["Location", "Video", "Congestion", "Level"])
    return pd.DataFrame(rows)