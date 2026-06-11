"""
Authentication helpers for Traffic AI Morocco.
"""

import streamlit as st

from db_utils import authenticate, register_user, setup_database


def init_session() -> None:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "user" not in st.session_state:
        st.session_state.user = None
    if "auth_tab" not in st.session_state:
        st.session_state.auth_tab = "login"


def auth_page() -> None:
    """Login and registration screen."""
    setup_database()

    # Injected styles for login centering and card styling
    st.markdown(
        """
        <style>
        .login-box {
            background: #1a2234;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 2.2rem 1.8rem;
            box-shadow: 0 10px 25px -5px rgba(0,0,0,0.4), 0 8px 10px -6px rgba(0,0,0,0.4);
            margin-top: 4vh;
            text-align: center;
        }
        .login-box h1 {
            color: #f8fafc !important;
            font-size: 1.8rem !important;
            font-weight: 700 !important;
            margin-bottom: 0.25rem !important;
            text-align: center !important;
            letter-spacing: -0.03em !important;
        }
        .login-box p {
            color: #94a3b8 !important;
            font-size: 0.88rem !important;
            margin-bottom: 1.5rem !important;
            text-align: center !important;
        }
        div[data-testid="stForm"] {
            border: none !important;
            padding: 0 !important;
            background: transparent !important;
        }
        @media (min-width: 768px) {
            div[data-testid="column"]:nth-of-type(2) {
                max-width: 420px !important;
                margin: 0 auto !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col_l, col_m, col_r = st.columns([1, 1.3, 1])

    with col_m:
        st.markdown(
            """
            <div class="login-box">
                <h1>Traffic AI Morocco</h1>
                <p>Casablanca Traffic Intelligence & Route Optimization</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        tab_login, tab_register = st.tabs(["Sign in", "Create account"])

        with tab_login:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("login_form", clear_on_submit=False):
                username = st.text_input("Username", key="login_username", placeholder="Enter your username")
                password = st.text_input("Password", type="password", key="login_password", placeholder="Enter your password")
                submitted = st.form_submit_button("Sign in", use_container_width=True)

                if submitted:
                    if not username.strip() or not password:
                        st.error("Please enter your username and password.")
                    else:
                        user = authenticate(username, password)
                        if user:
                            st.session_state.authenticated = True
                            st.session_state.user = user
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")

            st.markdown(
                "<div style='text-align: center; color: #64748b; font-size: 0.8rem; margin-top: 1rem;'>"
                "Demo Admin: <code>admin</code> / <code>admin123</code>"
                "</div>",
                unsafe_allow_html=True
            )

        with tab_register:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("register_form", clear_on_submit=False):
                new_username = st.text_input("Choose a username", key="reg_username", placeholder="e.g. yassine")
                new_password = st.text_input("Choose a password", type="password", key="reg_password", placeholder="At least 6 characters")
                confirm = st.text_input("Confirm password", type="password", key="reg_confirm", placeholder="Repeat your password")
                register = st.form_submit_button("Create account", use_container_width=True)

                if register:
                    if not new_username.strip() or not new_password:
                        st.error("Please fill in all fields.")
                    elif new_password != confirm:
                        st.error("Passwords do not match.")
                    else:
                        ok, message = register_user(new_username, new_password)
                        if ok:
                            st.success(message)
                        else:
                            st.error(message)


def require_auth() -> dict:
    init_session()
    if not st.session_state.authenticated or not st.session_state.user:
        auth_page()
        st.stop()
    return st.session_state.user


def is_admin(user: dict) -> bool:
    return user.get("role") == "admin"
