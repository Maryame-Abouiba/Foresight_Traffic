"""
Traffic AI Morocco — Main Application
======================================
Run with:

    streamlit run app.py
"""

import streamlit as st

from auth import auth_page, init_session, is_admin
from ui_styles import inject_global_styles

st.set_page_config(
    page_title="Traffic AI Morocco",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_global_styles()
init_session()

if not st.session_state.get("authenticated"):
    auth_page()
    st.stop()

user = st.session_state.user

pages = {
    "Operations": [
        st.Page("views/monitoring.py", title="Monitoring", icon=":material/videocam:", default=True),
        st.Page("views/optimization.py", title="Route Optimization", icon=":material/route:"),
    ],
}

if is_admin(user):
    pages["Administration"] = [
        st.Page("views/admin.py", title="Admin Dashboard", icon=":material/admin_panel_settings:"),
    ]

with st.sidebar:
    st.markdown("### Traffic AI Morocco")
    st.caption("Casablanca traffic intelligence")
    st.markdown("---")
    st.caption(f"**{user['username']}** · {user['role']}")
    if st.button("Sign out", use_container_width=True, type="secondary"):
        st.session_state.authenticated = False
        st.session_state.user = None
        st.rerun()

pg = st.navigation(pages)
pg.run()
