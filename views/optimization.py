"""
Route Optimization & Congestion Index — Driver Assistant View.
Focused exclusively on traffic congestion calculation and alternative route suggestion.
"""

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from auth import require_auth
from locations import (
    CASABLANCA_CENTER,
    CASABLANCA_ZONES,
    exact_coordinates,
    get_active_zones,
    get_active_zones_by_id,
)
from routing import (
    congestion_color,
    congestion_table,
    load_zone_congestion,
    plan_routes,
    search_casablanca_place,
)
from ui_styles import page_header

user = require_auth()

active_zones = get_active_zones()
zones_by_id = get_active_zones_by_id()
congestion_data = load_zone_congestion()

page_header(
    "Route Optimization & Congestion",
    "Driver Assistant: Analyze real-time congestion indices and find the best alternative driving route in Casablanca.",
    badges=[
        (f"{len(active_zones)} monitored locations", "tam-badge-live"),
        ("Casablanca Drive", "tam-badge-zone"),
    ],
)

# ── Summary KPI Cards for Driver ──────────────────────────────────────────────
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

total_zones = len(active_zones)
avg_cong = (
    sum(congestion_data.values()) / len(congestion_data)
    if congestion_data
    else 0.0
)

busiest_zone_name = "None"
max_cong_val = 0.0
for z in active_zones:
    c_val = congestion_data.get(z["id"], 0.0)
    if c_val > max_cong_val:
        max_cong_val = c_val
        busiest_zone_name = z["name"]

kpi1.metric("Monitored Zones", total_zones)
kpi2.metric(
    "City Average Index",
    f"{avg_cong:.1f} / 100",
    delta="Low" if avg_cong < 30 else ("Moderate" if avg_cong < 60 else "Heavy"),
    delta_color="normal" if avg_cong < 50 else "inverse",
)
kpi3.metric(
    "Highest Congestion Point",
    busiest_zone_name,
    f"{max_cong_val:.0f}/100" if max_cong_val > 0 else "0/100",
)

traffic_status_text = (
    "Traffic generally smooth."
    if avg_cong < 30
    else ("Localized delays." if avg_cong < 60 else "Significant congestion.")
)
kpi4.metric("Network Condition", traffic_status_text)

st.divider()

# ── Layout: Route Search Form & Congestion Index ──────────────────────────────
col_form, col_info = st.columns([1.25, 1])

with col_form:
    st.subheader("🗺️ Plan Your Route")
    st.caption("Enter departure and destination points in Casablanca:")

    c_orig, c_dest = st.columns(2)
    with c_orig:
        origin_query = st.text_input(
            "📍 From",
            value="Place Mohammed V",
            placeholder="e.g. Maarif, Casa Port, Anfa...",
            help="Departure location or landmark in Casablanca",
        )
    with c_dest:
        dest_query = st.text_input(
            "🏁 To",
            value="Twin Center",
            placeholder="e.g. Maarif, Sidi Maarouf, Ain Diab...",
            help="Destination location or landmark in Casablanca",
        )

    sensitivity = st.slider(
        "🎚️ Congestion Avoidance Sensitivity",
        min_value=0,
        max_value=100,
        value=60,
        help="Higher values steer routes further away from heavy traffic congestion zones.",
    )

    # Preset shortcut buttons for quick selection
    st.caption("Quick Route Presets:")
    q_b1, q_b2, q_b3 = st.columns(3)
    if q_b1.button("Casa Port ➔ Twin Center", use_container_width=True):
        st.session_state["orig_preset"] = "Casa Port"
        st.session_state["dest_preset"] = "Twin Center"
    if q_b2.button("Mohammed V ➔ Maarif", use_container_width=True):
        st.session_state["orig_preset"] = "Place Mohammed V"
        st.session_state["dest_preset"] = "Maarif"
    if q_b3.button("Zerktouni ➔ Ain Diab", use_container_width=True):
        st.session_state["orig_preset"] = "Boulevard Zerktouni"
        st.session_state["dest_preset"] = "Corniche Ain Diab"

    if "orig_preset" in st.session_state:
        origin_query = st.session_state.pop("orig_preset")
    if "dest_preset" in st.session_state:
        dest_query = st.session_state.pop("dest_preset")

    find_route = st.button("🔍 Find Optimal Route", type="primary", use_container_width=True)

with col_info:
    st.subheader("📊 Live Congestion Indices")
    st.caption("Vehicle density measurements per zone:")
    cong_df = congestion_table()
    if len(cong_df):
        st.dataframe(cong_df, use_container_width=True, hide_index=True)
    else:
        st.info("No active camera feeds detected. Route calculations will use default Casablanca zone locations.")

# ── Route Calculation Logic ──────────────────────────────────────────────────
route_result = None
if find_route:
    if not origin_query.strip() or not dest_query.strip():
        st.error("Please enter both departure and destination locations.")
    else:
        with st.spinner("Searching coordinates and calculating alternative routes..."):
            orig_results = search_casablanca_place(origin_query)
            if not orig_results:
                st.error(f"Could not find departure location: '{origin_query}'. Please refine your search.")
            else:
                dest_results = search_casablanca_place(dest_query)
                if not dest_results:
                    st.error(f"Could not find destination location: '{dest_query}'. Please refine your search.")
                else:
                    origin_val = orig_results[0]
                    dest_val = dest_results[0]

                    if (
                        origin_val["lat"] == dest_val["lat"]
                        and origin_val["lng"] == dest_val["lng"]
                    ):
                        st.error("Departure and destination locations must be different.")
                    else:
                        route_result = plan_routes(origin_val, dest_val, sensitivity)

if route_result and "error" in route_result:
    st.error(f"Route calculation error: {route_result['error']}")
    route_result = None

# ── Results & Metric Comparison Section ──────────────────────────────────────
if route_result:
    st.divider()
    st.subheader("⚡ Route Comparison")

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Direct Route (Standard)",
        f"{route_result['direct_km']} km",
        f"~{route_result['direct_eta_min']} min",
    )

    diff_min = route_result["alt_eta_min"] - route_result["direct_eta_min"]
    if diff_min > 0:
        delta_val = f"~{route_result['alt_eta_min']} min (+{diff_min:.1f} min detour)"
    elif diff_min < 0:
        delta_val = f"~{route_result['alt_eta_min']} min (-{abs(diff_min):.1f} min saved!)"
    else:
        delta_val = f"~{route_result['alt_eta_min']} min (equivalent duration)"

    c2.metric(
        "Recommended Route (Congestion-Free)",
        f"{route_result['alt_km']} km",
        delta_val,
    )

    c3.metric(
        "Congestion Exposure",
        f"{route_result['direct_max_congestion']:.0f}/100",
        delta=f"Reduced to {route_result['alt_max_congestion']:.0f}/100",
        delta_color="normal"
        if route_result["alt_max_congestion"] < route_result["direct_max_congestion"]
        else "off",
    )

    if route_result["is_different"]:
        st.success(
            "💡 **Recommended Alternative Route**: Navigates via smoother secondary streets "
            "to avoid major traffic congestion detected on the direct route."
        )
    else:
        st.info("ℹ️ The direct route is currently the fastest and most efficient path under current traffic conditions.")

    if route_result.get("avoided_zone_ids"):
        active_zones_by_id = {z["id"]: z for z in active_zones}
        names = [
            active_zones_by_id[z]["name"]
            for z in route_result["avoided_zone_ids"]
            if z in active_zones_by_id
        ]
        if names:
            st.warning(f"🛡️ **Successfully avoided heavy congestion areas**: {', '.join(names)}")

    if route_result.get("avoided_active_videos"):
        st.info(
            f"🚗 Optimized detour bypasses high-density sectors: "
            f"**{', '.join(route_result['avoided_active_videos'])}**"
        )

# ── Interactive Folium Map Section ───────────────────────────────────────────
st.divider()
st.subheader("📍 Navigation & Traffic Map")

congestion = load_zone_congestion()
if route_result:
    congestion = route_result["congestion"]

if route_result:
    o_lat, o_lng = exact_coordinates(route_result["origin"])
    map_center = [o_lat, o_lng]
    zoom = 13
else:
    map_center = [CASABLANCA_CENTER["lat"], CASABLANCA_CENTER["lng"]]
    zoom = 12

m = folium.Map(location=map_center, zoom_start=zoom, tiles="OpenStreetMap")

# Add markers for monitored traffic congestion zones
for zone in active_zones:
    lat, lng = exact_coordinates(zone)
    cong = congestion.get(zone["id"], 0.0)
    color = congestion_color(cong)
    folium.CircleMarker(
        location=[lat, lng],
        radius=10 + cong / 7,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.7,
        popup=(
            f"<b>{zone['name']}</b><br>"
            f"Source: {zone.get('video_file', '—')}<br>"
            f"Congestion Index: <b>{cong:.0f}/100</b>"
        ),
        tooltip=f"{zone['name']} — Index: {cong:.0f}/100",
    ).add_to(m)

if route_result:
    # Direct Route line (gray dashed)
    folium.PolyLine(
        route_result["direct_geometry"],
        color="#94a3b8",
        weight=5,
        opacity=0.75,
        dash_array="10 8",
        tooltip="Direct Route (Standard)",
    ).add_to(m)

    # Recommended Alternative Route line (blue or green solid)
    line_color = "#2563eb" if route_result["is_different"] else "#16a34a"
    folium.PolyLine(
        route_result["alt_geometry"],
        color=line_color,
        weight=6,
        opacity=0.9,
        tooltip="Recommended Route (Smooth)",
    ).add_to(m)

    o_lat, o_lng = exact_coordinates(route_result["origin"])
    d_lat, d_lng = exact_coordinates(route_result["dest"])

    # Start marker
    folium.Marker(
        [o_lat, o_lng],
        popup=f"<b>Departure</b><br>{route_result['origin']['name']}",
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(m)

    # Destination marker
    folium.Marker(
        [d_lat, d_lng],
        popup=f"<b>Destination</b><br>{route_result['dest']['name']}",
        icon=folium.Icon(color="red", icon="flag"),
    ).add_to(m)

st_folium(m, width=None, height=500, returned_objects=[])

st.caption(
    "💡 **Legend**: 🟩 Green (Low congestion) | 🟧 Orange (Moderate traffic) | 🟥 Red (Heavy congestion) | "
    "🟦 Blue Line (Recommended route) | 🩶 Dashed Gray Line (Direct route)"
)
