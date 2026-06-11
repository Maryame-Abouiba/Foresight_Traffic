"""
Traffic AI Morocco — Legacy entry point
========================================
Prefer:

    streamlit run app.py

This file redirects to the new multi-page application.
"""

import streamlit as st

st.set_page_config(page_title="Traffic AI Morocco", page_icon="🚦", layout="wide")
st.title("Traffic AI Morocco")
st.info("Please run the new dashboard with: `streamlit run app.py`")
st.markdown(
    """
    The application now includes:
    - **Authentication** (user / admin roles)
    - **Monitoring** with Casablanca violation map
    - **Route Optimization** (coming soon)
    - **Admin Dashboard**
    """
)
