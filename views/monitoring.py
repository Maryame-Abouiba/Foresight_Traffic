"""
Monitoring dashboard — violations, video, and Casablanca map.
Available to all authenticated users.
"""

from pathlib import Path

import folium
import pandas as pd
import streamlit as st
import math
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

from auth import require_auth
from congestion import congestion_level_label
from db_utils import load_frame_stats, load_violations, setup_database, get_violation_display_name, add_mapped_columns, VIOLATION_DISPLAY_MAP
from locations import (
    CASABLANCA_CENTER,
    exact_coordinates,
    list_available_videos,
)
from ui_styles import location_bar, page_header

def get_violation_color(category: str) -> str:
    if category == "speeding":
        return "red"
    elif category == "red_light":
        return "darkred"
    elif category == "phone":
        return "orange"
    elif category == "no_seatbelt":
        return "purple"
    elif category == "cigarette":
        return "pink"
    return "blue"


def _build_violation_map(
    df: pd.DataFrame,
    feeds: list[dict] | None = None,
    center: dict | None = None,
) -> folium.Map:
    feeds = feeds or []
    map_center = center or CASABLANCA_CENTER
    zoom = 14 if center else 13

    m = folium.Map(
        location=[map_center["lat"], map_center["lng"]],
        zoom_start=zoom,
        tiles="CartoDB dark_matter",
    )

    # Per-feed violation counts (for the camera-marker popup)
    counts_by_video: dict[str, int] = {}
    if not df.empty and "source_video" in df.columns:
        counts_by_video = df["source_video"].value_counts().to_dict()

    # Camera markers — one per active feed
    for feed in feeds:
        f_lat, f_lng = exact_coordinates(feed)
        f_count = counts_by_video.get(feed["video_file"], 0)
        is_selected = bool(center and center.get("video_file") == feed["video_file"])

        folium.Marker(
            location=[f_lat, f_lng],
            popup=folium.Popup(
                f"<b>{feed['name']}</b><br>"
                f"{feed.get('address', '')}<br>"
                f"Video: {feed['video_file']}<br>"
                f"Violations: <b>{f_count}</b><br>"
                f"<small>GPS: {f_lat:.6f}, {f_lng:.6f}</small>",
                max_width=300,
            ),
            tooltip=f"{feed['name']} — {f_count} violation{'s' if f_count != 1 else ''}",
            icon=folium.Icon(
                color="red" if is_selected else "blue",
                icon="camera",
            ),
        ).add_to(m)

        folium.Circle(
            location=[f_lat, f_lng],
            radius=60,
            color="#ef4444" if is_selected else "#3b82f6",
            fill=True,
            fill_color="#ef4444" if is_selected else "#3b82f6",
            fill_opacity=0.12,
        ).add_to(m)

    if df.empty:
        return m

    cluster = MarkerCluster(name="Violations").add_to(m)

    # Spread overlapping points around each camera so they don't all stack
    spread_idx: dict[tuple[float, float], int] = {}

    for _, row in df.iterrows():
        v_lat = row.get("lat")
        v_lng = row.get("lng")
        if pd.isna(v_lat) or pd.isna(v_lng):
            continue
        v_lat = float(v_lat)
        v_lng = float(v_lng)

        # Tiny offset so multiple violations at the same camera are visible
        key = (round(v_lat, 5), round(v_lng, 5))
        idx = spread_idx.get(key, 0)
        spread_idx[key] = idx + 1
        ring = idx // 8
        slot = idx % 8
        offset_r = 0.00025 + ring * 0.00025
        angle = (slot / 8.0) * 2 * math.pi
        plot_lat = v_lat + offset_r * math.cos(angle)
        plot_lng = v_lng + offset_r * math.sin(angle)

        vcat = row.get("violation_category", "other")
        color = get_violation_color(vcat)
        vdisplay = row.get("violation_display", "Unknown")

        popup_html = (
            f"<b>{row.get('location_name', 'Unknown')}</b><br>"
            f"GPS: {v_lat:.6f}, {v_lng:.6f}<br>"
            f"Video: {row.get('source_video', '—')}<br>"
            f"Type: <b>{vdisplay}</b><br>"
            f"Vehicle: {row.get('class_name', '—')}<br>"
            f"Speed: {row.get('speed_kmh', '—')} km/h<br>"
            f"Plate: {row.get('plate_text') or '—'}<br>"
            f"Time: {row.get('timestamp', '—')}"
        )
        folium.CircleMarker(
            location=[plot_lat, plot_lng],
            radius=6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{row.get('location_name', '')} — {vdisplay} — {row.get('plate_text') or 'no plate'}",
        ).add_to(cluster)

    return m


def _enrich_stats(stats: pd.DataFrame, fps: float = 30.0) -> pd.DataFrame:
    """Add video time axis; backfill for legacy rows without video_time_sec."""
    out = stats.copy()
    if "video_time_sec" not in out.columns:
        out["video_time_sec"] = out["frame_number"] / fps
    else:
        missing = out["video_time_sec"].isna()
        out.loc[missing, "video_time_sec"] = out.loc[missing, "frame_number"] / fps
    return out


user = require_auth()
setup_database()

available_videos = list_available_videos()
video_count = len(available_videos)

page_header(
    "Monitoring",
    "Real-time violation tracking and congestion analysis across Casablanca camera feeds.",
    badges=[
        (f"{video_count} active feed{'s' if video_count != 1 else ''}", "tam-badge-live"),
        ("Casablanca", "tam-badge-zone"),
    ],
)

violations = load_violations()
violations = add_mapped_columns(violations)
all_violations = violations.copy()  # unfiltered, used for the all-feeds map
stats = load_frame_stats()

if not available_videos:
    st.warning(
        "No active camera video feeds found. Please contact the administrator to configure video feeds."
    )
    selected_video = None
    selected_meta = None
else:
    labels = {
        v["video_file"]: f"{v['name']} — {v['video_file']}"
        for v in available_videos
    }
    selected_video = st.selectbox(
        "Camera feed",
        options=[v["video_file"] for v in available_videos],
        format_func=lambda f: labels.get(f, f),
        index=0,
    )
    selected_meta = next(
        (v for v in available_videos if v["video_file"] == selected_video),
        None,
    )

if selected_video and "source_video" in violations.columns and violations["source_video"].notna().any():
    mask = violations["source_video"].isna() | (violations["source_video"] == selected_video)
    violations = violations[mask]
if selected_video and "source_video" in stats.columns and stats["source_video"].notna().any():
    stats = stats[stats["source_video"] == selected_video]

total_v = len(violations)
speeding = len(violations[violations.violation_category == "speeding"]) if total_v else 0
red_light = len(violations[violations.violation_category == "red_light"]) if total_v else 0
phone = len(violations[violations.violation_category == "phone"]) if total_v else 0
seatbelt = len(violations[violations.violation_category == "no_seatbelt"]) if total_v else 0
cigarette = len(violations[violations.violation_category == "cigarette"]) if total_v else 0

avg_cong = None
avg_flow = None
peak_cong = None
if len(stats) and selected_meta:
    stats = _enrich_stats(stats)
    stats = stats.dropna(subset=["congestion_index"]) if "congestion_index" in stats.columns else stats
    if len(stats):
        avg_cong = stats["congestion_index"].mean()
        peak_cong = stats["congestion_index"].max()
        if "vehicles_per_minute" in stats.columns and stats["vehicles_per_minute"].notna().any():
            avg_flow = stats["vehicles_per_minute"].mean()

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Speeding", speeding)
k2.metric("Red Light", red_light)
k3.metric("Phone Use", phone)
k4.metric("No Seatbelt", seatbelt)
k5.metric("Smoking", cigarette)
if avg_cong is not None:
    k6.metric(
        "Avg Congestion",
        f"{avg_cong:.0f}/100",
        delta=congestion_level_label(avg_cong),
        delta_color="off",
    )
else:
    k6.metric("Congestion", "—")

if selected_meta:
    lat, lng = exact_coordinates(selected_meta)
    location_bar(
        selected_meta["name"],
        selected_meta["address"],
        selected_meta.get("zone_type", "Active camera feed"),
        f"{lat:.6f}, {lng:.6f}",
    )

st.markdown('<div class="tam-section-label">Geographic view</div>', unsafe_allow_html=True)

if total_v or selected_meta:
    map_col1, map_col2 = st.columns([2, 1])
    with map_col1:
        vtype_filter = st.multiselect(
            "Filter by violation type",
            options=["speeding", "red_light", "phone", "no_seatbelt", "cigarette"],
            default=["speeding", "red_light", "phone", "no_seatbelt", "cigarette"],
            format_func=lambda x: VIOLATION_DISPLAY_MAP.get(x, x),
            key="map_vtype_filter",
        )
    with map_col2:
        show_all_feeds = st.toggle(
            "Show all camera feeds on map",
            value=True,
            help="When ON, the map displays violations from all 3 places. When OFF, only the selected feed.",
            key="show_all_feeds_toggle",
        )

    map_df = all_violations if show_all_feeds else violations
    if vtype_filter and "violation_category" in map_df.columns:
        map_df = map_df[map_df.violation_category.isin(vtype_filter)]

    map_center = None if show_all_feeds else selected_meta

    st_folium(
        _build_violation_map(map_df, feeds=available_videos, center=map_center),
        width=None,
        height=420,
        returned_objects=[],
    )
else:
    st.info("No geolocated violations for this feed yet. Run the pipeline to populate data.")

st.divider()

left, right = st.columns([1.55, 1])

with left:
    st.markdown('<div class="tam-section-label">Processed footage</div>', unsafe_allow_html=True)
    annotated_path = selected_meta["annotated"] if selected_meta else None
    if annotated_path and Path(annotated_path).exists():
        with open(annotated_path, "rb") as f:
            st.video(f.read())
    elif selected_meta:
        st.info(
            "The processed video footage is currently being generated or is unavailable."
        )
    else:
        st.info("No video feeds available.")

    st.markdown('<div class="tam-section-label">Congestion over video time</div>', unsafe_allow_html=True)
    if len(stats):
        stats = _enrich_stats(stats)
        chart_df = stats.copy()
        chart_df["Time (s)"] = chart_df["video_time_sec"].round(1)

        plot_cols = ["Congestion Index"]
        chart_df = chart_df.rename(columns={"congestion_index": "Congestion Index"})
        if "vehicles_per_minute" in chart_df.columns:
            chart_df["Vehicles / min"] = chart_df["vehicles_per_minute"]
            plot_cols.append("Vehicles / min")

        st.line_chart(chart_df.set_index("Time (s)")[plot_cols])
        if avg_flow is not None and peak_cong is not None:
            st.caption(
                f"Based on average vehicle count over video time "
                f"(**{avg_flow:.1f}** veh/min avg presence, peak **{peak_cong:.0f}**/100)."
            )
    else:
        st.info("No congestion data available for this camera feed.")

with right:
    st.markdown('<div class="tam-section-label">Violation log</div>', unsafe_allow_html=True)

    if total_v:
        vtype = st.multiselect(
            "Filter by type",
            options=["speeding", "red_light", "phone", "no_seatbelt", "cigarette"],
            default=["speeding", "red_light", "phone", "no_seatbelt", "cigarette"],
            format_func=lambda x: VIOLATION_DISPLAY_MAP.get(x, x),
            key="table_vtype_filter",
        )
        filtered = violations[violations.violation_category.isin(vtype)] if vtype else violations

        display_cols = [
            "timestamp", "location_name", "violation_display",
            "class_name", "speed_kmh", "plate_text",
        ]
        available_cols = [c for c in display_cols if c in filtered.columns]
        st.dataframe(
            filtered[available_cols].rename(columns={
                "timestamp": "Time",
                "location_name": "Location",
                "violation_display": "Violation Type",
                "class_name": "Vehicle",
                "speed_kmh": "Speed (km/h)",
                "plate_text": "Plate",
            }),
            use_container_width=True,
            height=300,
            hide_index=True,
        )

        st.markdown('<div class="tam-section-label">Breakdown</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            type_counts = violations.class_name.value_counts().reset_index()
            type_counts.columns = ["Type", "Count"]
            st.caption("By vehicle type")
            st.bar_chart(type_counts.set_index("Type"), color="#3b82f6")
        with c2:
            vtype_counts = violations["violation_display"].value_counts().reset_index()
            vtype_counts.columns = ["Violation", "Count"]
            st.caption("By violation type")
            st.bar_chart(vtype_counts.set_index("Violation"), color="#ef4444")
    else:
        st.info("No violations logged for this camera feed yet.")

st.divider()
st.markdown('<div class="tam-section-label">Violations by zone</div>', unsafe_allow_html=True)

if len(all_violations) and "location_name" in all_violations.columns:
    by_zone = (
        all_violations.groupby(["location_name", "violation_display"])
        .size()
        .reset_index(name="Count")
        .pivot(index="location_name", columns="violation_display", values="Count")
        .fillna(0)
        .astype(int)
    )

    # Keep a stable column order
    preferred_order = [VIOLATION_DISPLAY_MAP[k] for k in
                       ["speeding", "red_light", "phone", "no_seatbelt", "cigarette"]]
    ordered_cols = [c for c in preferred_order if c in by_zone.columns]
    by_zone = by_zone[ordered_cols]
    by_zone["Total"] = by_zone.sum(axis=1)
    by_zone = by_zone.sort_values("Total", ascending=False)

    z1, z2 = st.columns([1.2, 1])
    with z1:
        st.caption("Counts per zone (one row per camera)")
        st.dataframe(
            by_zone.reset_index().rename(columns={"location_name": "Zone"}),
            use_container_width=True,
            hide_index=True,
        )
    with z2:
        st.caption("Stacked bar — violation mix per zone")
        st.bar_chart(by_zone.drop(columns=["Total"]))
else:
    st.info("No violations recorded yet across the camera feeds.")
