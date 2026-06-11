"""
Monitoring dashboard — violations, video, and Casablanca map.
Available to all authenticated users.
"""

from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

from auth import require_auth
from congestion import congestion_level_label
from db_utils import load_frame_stats, load_violations, setup_database
from locations import (
    CASABLANCA_CENTER,
    exact_coordinates,
    list_available_videos,
)
from ui_styles import location_bar, page_header

VIOLATION_COLORS = {
    "speeding": "red",
    "red_light": "darkred",
}


def _build_violation_map(
    df: pd.DataFrame,
    center: dict | None = None,
) -> folium.Map:
    map_center = center or CASABLANCA_CENTER
    zoom = 17 if center else 13

    m = folium.Map(
        location=[map_center["lat"], map_center["lng"]],
        zoom_start=zoom,
        tiles="CartoDB dark_matter",
    )

    if center:
        lat, lng = exact_coordinates(center)
        folium.Marker(
            location=[lat, lng],
            popup=(
                f"<b>{center['name']}</b><br>"
                f"{center.get('address', '')}<br>"
                f"<small>GPS: {lat:.6f}, {lng:.6f}</small>"
            ),
            tooltip=f"{center['name']} (camera)",
            icon=folium.Icon(color="blue", icon="camera"),
        ).add_to(m)

        folium.Circle(
            location=[lat, lng],
            radius=40,
            color="#3b82f6",
            fill=True,
            fill_color="#3b82f6",
            fill_opacity=0.12,
            tooltip="Camera coverage area",
        ).add_to(m)

    if df.empty:
        return m

    if center:
        plot_lat, plot_lng = exact_coordinates(center)
    elif "lat" in df.columns and df["lat"].notna().any():
        plot_lat = float(df["lat"].iloc[0])
        plot_lng = float(df["lng"].iloc[0])
    else:
        return m

    cluster = MarkerCluster(name="Violations").add_to(m)

    for _, row in df.iterrows():
        vtype = row.get("violation_type", "unknown")
        color = VIOLATION_COLORS.get(vtype, "orange")
        popup_html = (
            f"<b>{row.get('location_name', center['name'] if center else 'Unknown')}</b><br>"
            f"GPS: {plot_lat:.6f}, {plot_lng:.6f}<br>"
            f"Video: {row.get('source_video', '—')}<br>"
            f"Type: {vtype}<br>"
            f"Vehicle: {row.get('class_name', '—')}<br>"
            f"Speed: {row.get('speed_kmh', '—')} km/h<br>"
            f"Plate: {row.get('plate_text') or '—'}<br>"
            f"Time: {row.get('timestamp', '—')}"
        )
        folium.CircleMarker(
            location=[plot_lat, plot_lng],
            radius=7,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{vtype} — {row.get('plate_text') or 'no plate'}",
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
red_light = len(violations[violations.violation_type == "red_light"]) if total_v else 0
speeding = len(violations[violations.violation_type == "speeding"]) if total_v else 0
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

k1, k2, k3, k4 = st.columns(4)
k1.metric("Violations", total_v)
k2.metric("Red Light", red_light)
k3.metric("Speeding", speeding)
if avg_cong is not None:
    k4.metric(
        "Avg Congestion",
        f"{avg_cong:.0f}/100",
        delta=congestion_level_label(avg_cong),
        delta_color="off",
    )
else:
    k4.metric("Congestion", "—")

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
    vtype_filter = st.multiselect(
        "Filter by violation type",
        options=["red_light", "speeding"],
        default=["red_light", "speeding"],
        key="map_vtype_filter",
    )
    map_df = violations
    if total_v and vtype_filter:
        map_df = violations[violations.violation_type.isin(vtype_filter)]

    st_folium(
        _build_violation_map(map_df, center=selected_meta),
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
            options=["red_light", "speeding"],
            default=["red_light", "speeding"],
            key="table_vtype_filter",
        )
        filtered = violations[violations.violation_type.isin(vtype)] if vtype else violations

        display_cols = [
            "timestamp", "location_name", "violation_type",
            "class_name", "speed_kmh", "plate_text",
        ]
        available_cols = [c for c in display_cols if c in filtered.columns]
        st.dataframe(
            filtered[available_cols].rename(columns={
                "timestamp": "Time",
                "location_name": "Location",
                "violation_type": "Type",
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
            vtype_counts = violations.violation_type.value_counts().reset_index()
            vtype_counts.columns = ["Violation", "Count"]
            st.caption("By violation type")
            st.bar_chart(vtype_counts.set_index("Violation"), color="#ef4444")
    else:
        st.info("No violations logged for this camera feed yet.")
