"""
Route Optimization — simplified UI, only locations from videos/ folder.
"""

import folium
import streamlit as st
from streamlit_folium import st_folium

from auth import require_auth
from locations import CASABLANCA_CENTER, CASABLANCA_ZONES, exact_coordinates, get_active_zones, get_active_zones_by_id
from routing import congestion_color, congestion_table, load_zone_congestion, plan_routes, search_casablanca_place
from ui_styles import page_header

user = require_auth()

active_zones = get_active_zones()
zones_by_id = get_active_zones_by_id()

page_header(
    "Route Optimization",
    "Find a better driving route based on congestion at your monitored camera locations.",
)

col_form, col_info = st.columns([1.2, 1])

with col_form:
    st.subheader("Plan your route")

    origin_query = st.text_input(
        "📍 From (Départ)",
        value="Place Mohammed V",
        placeholder="Type departure place... (e.g. Maarif, Casa Port, Anfa)"
    )
    dest_query = st.text_input(
        "🏁 To (Arrivée)",
        value="Twin Center",
        placeholder="Type arrival place... (e.g. Maarif, Sidi Maarouf, Ain Diab)"
    )

    sensitivity = st.slider(
        "Avoid congestion",
        min_value=0,
        max_value=100,
        value=60,
        help="Higher values prefer routes that stay away from busy camera locations.",
    )

    find_route = st.button("Find route", type="primary", use_container_width=True)

with col_info:
    st.subheader("Current congestion")
    cong_df = congestion_table()
    if len(cong_df):
        st.dataframe(cong_df, use_container_width=True, hide_index=True)
    else:
        st.info("No active camera feeds detected. Congestion data is currently unavailable.")

route_result = None
if find_route:
    if not origin_query.strip() or not dest_query.strip():
        st.error("Please enter both departure and arrival locations.")
    else:
        with st.spinner("Searching locations and calculating route…"):
            # Geocode origin location
            orig_results = search_casablanca_place(origin_query)
            if not orig_results:
                st.error(f"Could not find departure location: '{origin_query}'. Please try another search term.")
            else:
                # Geocode destination location
                dest_results = search_casablanca_place(dest_query)
                if not dest_results:
                    st.error(f"Could not find arrival location: '{dest_query}'. Please try another search term.")
                else:
                    # Select the first (best) match for both
                    origin_val = orig_results[0]
                    dest_val = dest_results[0]
                    
                    if origin_val["lat"] == dest_val["lat"] and origin_val["lng"] == dest_val["lng"]:
                        st.error("Departure and arrival locations must be different.")
                    else:
                        route_result = plan_routes(origin_val, dest_val, sensitivity)

if route_result and "error" in route_result:
    st.error(route_result["error"])
    route_result = None

st.divider()
st.subheader("Map")

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

m = folium.Map(location=map_center, zoom_start=zoom, tiles="CartoDB dark_matter")

for zone in active_zones:
    lat, lng = exact_coordinates(zone)
    cong = congestion.get(zone["id"], 0.0)
    color = congestion_color(cong)
    folium.CircleMarker(
        location=[lat, lng],
        radius=9 + cong / 8,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.7,
        popup=(
            f"<b>{zone['name']}</b><br>"
            f"Video: {zone.get('video_file', '—')}<br>"
            f"Congestion: {cong:.0f}/100"
        ),
        tooltip=f"{zone['name']} — {cong:.0f}/100",
    ).add_to(m)

if route_result:
    folium.PolyLine(
        route_result["direct_geometry"],
        color="#94a3b8",
        weight=5,
        opacity=0.75,
        dash_array="10 8",
        tooltip="Direct route",
    ).add_to(m)

    line_color = "#2563eb" if route_result["is_different"] else "#16a34a"
    folium.PolyLine(
        route_result["alt_geometry"],
        color=line_color,
        weight=6,
        opacity=0.9,
        tooltip="Recommended route",
    ).add_to(m)

    o_lat, o_lng = exact_coordinates(route_result["origin"])
    d_lat, d_lng = exact_coordinates(route_result["dest"])
    folium.Marker(
        [o_lat, o_lng],
        popup=f"<b>Start</b><br>{route_result['origin']['name']}",
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(m)
    folium.Marker(
        [d_lat, d_lng],
        popup=f"<b>Destination</b><br>{route_result['dest']['name']}",
        icon=folium.Icon(color="red", icon="flag"),
    ).add_to(m)

st_folium(m, width=None, height=480, returned_objects=[])

st.caption(f"{len(active_zones)} active video camera feed(s) monitoring traffic conditions")

if route_result:
    st.divider()
    st.subheader("Route comparison")

    r1, r2, r3 = st.columns(3)
    r1.metric(
        "Direct route",
        f"{route_result['direct_km']} km",
        f"~{route_result['direct_eta_min']} min",
    )
    
    diff_min = route_result['alt_eta_min'] - route_result['direct_eta_min']
    if diff_min > 0:
        delta_val = f"~{route_result['alt_eta_min']} min (+{diff_min:.1f} min)"
    elif diff_min < 0:
        delta_val = f"~{route_result['alt_eta_min']} min ({diff_min:.1f} min)"
    else:
        delta_val = f"~{route_result['alt_eta_min']} min"

    r2.metric(
        "Recommended",
        f"{route_result['alt_km']} km",
        delta_val,
    )
    r3.metric(
        "Congestion exposure",
        f"{route_result['direct_max_congestion']:.0f} → {route_result['alt_max_congestion']:.0f}",
    )

    if route_result["is_different"]:
        st.success("The recommended route uses different streets to reduce congestion exposure.")
    else:
        st.info("Both routes are the same for current conditions.")

    if route_result.get("avoided_zone_ids"):
        active_zones_by_id = {z["id"]: z for z in active_zones}
        names = [
            active_zones_by_id[z]["name"]
            for z in route_result["avoided_zone_ids"]
            if z in active_zones_by_id
        ]
        if names:
            st.success(f"Avoids busy areas: **{', '.join(names)}**")

    if route_result.get("avoided_active_videos"):
        st.success(
            f"Alternative route suggested to avoid video-monitored area(s): "
            f"**{', '.join(route_result['avoided_active_videos'])}**"
        )

else:
    st.info("Select a start and destination, then click **Find route**.")
