"""
Admin dashboard — user management & system video registry.
"""

from pathlib import Path
import pandas as pd
import streamlit as st

from auth import is_admin, require_auth
from db_utils import (
    DB_PATH,
    create_user_by_admin,
    delete_user,
    get_user_count,
    load_users,
    setup_database,
)
from locations import count_available_videos, list_available_videos
from ui_styles import page_header

admin_user = require_auth()

if not is_admin(admin_user):
    st.error("Access denied. This page is for administrators only.")
    st.stop()

page_header(
    "System Administration",
    f"User account management and traffic video source registry · Signed in as {admin_user['username']}",
)

setup_database()

c1, c2, c3 = st.columns(3)
c1.metric("Registered Users", get_user_count())
c2.metric("Traffic Video Feeds", count_available_videos())
c3.metric("Database Status", "Online" if Path(DB_PATH).exists() else "Empty")

st.divider()

tab_users, tab_overview = st.tabs(
    ["User Management", "Camera & Video Registry"]
)

# ── User management ───────────────────────────────────────────────────────
with tab_users:
    st.subheader("Manage User Accounts")

    users_df = load_users()

    col_btn1, col_btn2, _ = st.columns([1.2, 1.2, 3])

    with col_btn1:
        with st.popover("➕ Add User", use_container_width=True):
            with st.form("admin_create_user_form", clear_on_submit=True):
                new_user = st.text_input("Username")
                new_pass = st.text_input("Password", type="password")
                new_role = st.selectbox("Role", ["user", "admin"])
                create_btn = st.form_submit_button("Create User", use_container_width=True)
                if create_btn:
                    ok, msg = create_user_by_admin(new_user, new_pass, new_role)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    with col_btn2:
        with st.popover("🗑️ Delete User", use_container_width=True):
            deletable = users_df["username"].tolist()
            target = st.selectbox("Select User", deletable, key="delete_user_select")
            confirm_delete = st.checkbox(
                f"I confirm I want to delete {target}",
                key="confirm_delete_user",
            )
            if st.button("Delete User", type="primary", disabled=not confirm_delete, use_container_width=True):
                ok, msg = delete_user(target, admin_user["username"])
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.write("")

    st.dataframe(
        users_df.rename(columns={
            "id": "ID",
            "username": "Username",
            "role": "Role",
        }),
        use_container_width=True,
        hide_index=True,
    )

# ── Video Registry Overview ──────────────────────────────────────────────
with tab_overview:
    st.subheader("Monitored Camera Registry")
    registry = list_available_videos()
    if registry:
        st.dataframe(
            pd.DataFrame(registry)[
                ["video_file", "name", "zone_type", "coords_label"]
            ].rename(columns={
                "video_file": "Video File",
                "name": "Location / Zone Name",
                "zone_type": "Feed Type",
                "coords_label": "GPS Coordinates",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No traffic video feeds registered yet.")

    st.caption(
        "Configured camera feeds supply live video density data to compute congestion indices for the driver assistant."
    )

    st.subheader("System Information")
    st.markdown(
        f"""
        - **Database File Path:** `{DB_PATH}`
        - **Video Feeds Directory:** `videos/`
        """
    )
