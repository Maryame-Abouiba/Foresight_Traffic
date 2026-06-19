"""
Admin dashboard — user management and violation reports.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from auth import is_admin, require_auth
from db_utils import (
    DB_PATH,
    build_html_report,
    build_report_summary,
    create_user_by_admin,
    delete_user,
    filter_violations,
    get_user_count,
    get_violation_count,
    load_users,
    load_violations,
    setup_database,
    add_mapped_columns,
    VIOLATION_DISPLAY_MAP,
)
from locations import count_available_videos, list_available_videos
from ui_styles import page_header

admin_user = require_auth()

if not is_admin(admin_user):
    st.error("Access denied. This page is for administrators only.")
    st.stop()

page_header(
    "Admin Dashboard",
    f"System overview and user management · signed in as {admin_user['username']}",
)

setup_database()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Users", get_user_count())
c2.metric("Violations", get_violation_count())
c3.metric("Video Feeds", count_available_videos())
c4.metric("Database", "Online" if Path(DB_PATH).exists() else "Empty")

st.divider()

tab_users, tab_reports, tab_overview = st.tabs(
    ["User Management", "Violation Reports", "Overview"]
)

# ── User management ───────────────────────────────────────────────────────
with tab_users:
    st.subheader("Manage users")

    users_df = load_users()

    # Buttons above the user table using st.popover
    col_btn1, col_btn2, _ = st.columns([1.2, 1.2, 3])
    
    with col_btn1:
        with st.popover("➕ Add user", use_container_width=True):
            with st.form("admin_create_user_form", clear_on_submit=True):
                new_user = st.text_input("Username")
                new_pass = st.text_input("Password", type="password")
                new_role = st.selectbox("Role", ["user", "admin"])
                create_btn = st.form_submit_button("Add", use_container_width=True)
                if create_btn:
                    ok, msg = create_user_by_admin(new_user, new_pass, new_role)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                        
    with col_btn2:
        with st.popover("🗑️ Delete user", use_container_width=True):
            deletable = users_df["username"].tolist()
            target = st.selectbox("Select user", deletable, key="delete_user_select")
            confirm_delete = st.checkbox(
                f"I confirm I want to delete {target}",
                key="confirm_delete_user",
            )
            if st.button("Delete", type="primary", disabled=not confirm_delete, use_container_width=True):
                ok, msg = delete_user(target, admin_user["username"])
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.write("") # Spacer

    st.dataframe(
        users_df.rename(columns={
            "id": "ID",
            "username": "Username",
            "role": "Role",
        }),
        use_container_width=True,
        hide_index=True,
    )

# ── Violation reports ─────────────────────────────────────────────────────
with tab_reports:
    st.subheader("Generate violation reports")

    all_violations = load_violations()
    all_violations = add_mapped_columns(all_violations)

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        type_opts = ["speeding", "red_light", "phone", "no_seatbelt", "cigarette"]
        sel_types = st.multiselect(
            "Violation type",
            options=type_opts,
            default=type_opts,
            format_func=lambda x: VIOLATION_DISPLAY_MAP.get(x, x),
        )
    with col_f2:
        loc_opts = (
            sorted(all_violations["location_name"].dropna().unique().tolist())
            if len(all_violations) and "location_name" in all_violations.columns
            else []
        )
        sel_locs = st.multiselect("Location", options=loc_opts, default=loc_opts)
    with col_f3:
        vid_opts = (
            sorted(all_violations["source_video"].dropna().unique().tolist())
            if len(all_violations) and "source_video" in all_violations.columns
            else []
        )
        sel_vids = st.multiselect("Source video", options=vid_opts, default=vid_opts)

    report_df = all_violations
    if sel_types:
        report_df = report_df[report_df.violation_category.isin(sel_types)]
    if sel_locs:
        report_df = report_df[report_df.location_name.isin(sel_locs)]
    if sel_vids:
        report_df = report_df[report_df.source_video.isin(sel_vids)]

    summary = build_report_summary(report_df)

    r1, r2, r3, r4, r5, r6, r7 = st.columns(7)
    r1.metric("Total", summary["total"])
    r2.metric("Speeding", summary["speeding"])
    r3.metric("Red light", summary["red_light"])
    r4.metric("Phone", summary["phone"])
    r5.metric("Seatbelt", summary["no_seatbelt"])
    r6.metric("Smoking", summary["cigarette"])
    r7.metric("Avg Speed", f"{summary['avg_speed']} km/h")

    if len(report_df):
        st.markdown("#### Preview")
        preview_cols = [
            "timestamp", "location_name", "violation_display",
            "class_name", "speed_kmh", "plate_text", "source_video",
        ]
        available = [c for c in preview_cols if c in report_df.columns]
        st.dataframe(
            report_df[available].rename(columns={
                "timestamp": "Time",
                "location_name": "Location",
                "violation_display": "Type",
                "class_name": "Vehicle",
                "speed_kmh": "Speed (km/h)",
                "plate_text": "Plate",
                "source_video": "Source Video",
            }).head(100),
            use_container_width=True,
            hide_index=True,
        )

        if "violation_display" in report_df.columns:
            st.markdown("#### Breakdown by type")
            st.bar_chart(report_df["violation_display"].value_counts())

        ts = datetime.now().strftime("%Y%m%d_%H%M")
        export_cols = [
            "timestamp", "location_name", "source_video", "violation_type",
            "class_name", "speed_kmh", "plate_text", "camera_id", "frame_number",
        ]
        export_df = report_df[[c for c in export_cols if c in report_df.columns]]

        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "Download CSV report",
                data=export_df.to_csv(index=False).encode("utf-8"),
                file_name=f"violations_report_{ts}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with d2:
            html = build_html_report(report_df, summary)
            st.download_button(
                "Download HTML report",
                data=html.encode("utf-8"),
                file_name=f"violations_report_{ts}.html",
                mime="text/html",
                use_container_width=True,
            )
    else:
        st.info("No violations match the selected filters.")

# ── Overview ──────────────────────────────────────────────────────────────
with tab_overview:
    st.subheader("Video registry")
    registry = list_available_videos()
    if registry:
        st.dataframe(
            pd.DataFrame(registry)[
                ["video_file", "name", "zone_type", "annotated_exists"]
            ].rename(columns={
                "video_file": "Video file",
                "name": "Location",
                "zone_type": "Feed type",
                "annotated_exists": "Processed",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No camera video feeds registered yet.")

    st.caption(
        "Only camera feeds configured to monitor congestion will display congestion details."
    )

    st.subheader("System Information")
    st.markdown(
        f"""
        - **Database Location:** `{DB_PATH}`
        - **Monitored Directory:** `videos/`
        """
    )
