"""Shared Streamlit UI styling for Traffic AI Morocco."""

import streamlit as st


def inject_global_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');

        html, body, [class*="css"] {
            font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
            max-width: 1280px;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0f1419 0%, #151b26 100%);
            border-right: 1px solid rgba(255,255,255,0.06);
        }

        [data-testid="stSidebar"] .stMarkdown h3 {
            font-weight: 700;
            letter-spacing: -0.02em;
            color: #f8fafc;
        }

        [data-testid="stMetric"] {
            background: #1a2234;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 10px;
            padding: 0.85rem 1rem;
            box-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }

        [data-testid="stMetric"] label {
            font-size: 0.78rem !important;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #94a3b8 !important;
            font-weight: 600 !important;
        }

        [data-testid="stMetric"] [data-testid="stMetricValue"] {
            font-size: 1.65rem !important;
            font-weight: 700 !important;
            color: #f1f5f9 !important;
        }

        div[data-testid="stExpander"] {
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            background: #1a2234;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.25rem;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        .stTabs [data-baseweb="tab"] {
            font-weight: 600;
            font-size: 0.9rem;
        }

        h1 {
            font-weight: 700 !important;
            letter-spacing: -0.03em !important;
            font-size: 1.85rem !important;
        }

        h2, h3 {
            font-weight: 600 !important;
            letter-spacing: -0.02em !important;
        }

        .tam-page-header {
            margin-bottom: 0.25rem;
        }

        .tam-page-header h1 {
            margin-bottom: 0.15rem;
        }

        .tam-subtitle {
            color: #94a3b8;
            font-size: 0.95rem;
            margin-bottom: 1.25rem;
            line-height: 1.5;
        }

        .tam-badge {
            display: inline-block;
            padding: 0.2rem 0.55rem;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-right: 0.4rem;
        }

        .tam-badge-live {
            background: rgba(34, 197, 94, 0.15);
            color: #4ade80;
            border: 1px solid rgba(34, 197, 94, 0.35);
        }

        .tam-badge-zone {
            background: rgba(59, 130, 246, 0.12);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        .tam-location-bar {
            background: #1a2234;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            padding: 0.75rem 1rem;
            margin: 0.75rem 0 1rem 0;
            font-size: 0.88rem;
            color: #cbd5e1;
        }

        .tam-section-label {
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #64748b;
            margin-bottom: 0.5rem;
        }

        .tam-card {
            background: #1a2234;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 10px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.75rem;
        }

        hr {
            border-color: rgba(255,255,255,0.08) !important;
            margin: 1.5rem 0 !important;
        }

        .stButton > button[kind="primary"] {
            font-weight: 600;
            border-radius: 8px;
        }

        [data-testid="stDataFrame"] {
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            overflow: hidden;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str, badges: list[tuple[str, str]] | None = None) -> None:
    badge_html = ""
    if badges:
        for label, css_class in badges:
            badge_html += f'<span class="tam-badge {css_class}">{label}</span>'

    st.markdown(
        f"""
        <div class="tam-page-header">
            <h1>{title}</h1>
            <div class="tam-subtitle">{subtitle}</div>
            {badge_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def location_bar(name: str, address: str, zone_label: str, coords: str) -> None:
    st.markdown(
        f"""
        <div class="tam-location-bar">
            <strong style="color:#f1f5f9;">{name}</strong>
            &nbsp;·&nbsp; {address}
            <br>
            <span style="color:#64748b; font-size:0.82rem;">
                {zone_label} &nbsp;·&nbsp; GPS {coords}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )
