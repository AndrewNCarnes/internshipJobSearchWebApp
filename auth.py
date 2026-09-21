"""
auth.py -- one shared password for the deployed dashboard ("teaser" mode).

What this is: a single password that hides job titles, locations and links.
Counts and company names stay public so the page still shows what it is.

What this is NOT: a payment system or user accounts. One password for
everyone, no per-person revocation, and anyone who knows it can pass it on.

The password comes from Streamlit secrets (`app_password`). The fallback
below is a placeholder for local use -- THE REPO IS PUBLIC, so the fallback
is readable by anyone. Set a real one in Streamlit Cloud:
    Settings -> Secrets ->  app_password = "something-only-you-know"
"""
import hmac

import streamlit as st

FALLBACK_PASSWORD = "123"     # placeholder only -- see the note above
STATE_KEY = "unlocked"
INPUT_KEY = "password_input"


def expected_password():
    """The configured password, or the placeholder when no secret is set.
    st.secrets raises when no secrets file exists at all, hence the guard."""
    try:
        value = st.secrets.get("app_password")
    except Exception:
        value = None
    return str(value) if value else FALLBACK_PASSWORD


def unlocked():
    return bool(st.session_state.get(STATE_KEY, False))


def _submit():
    entered = str(st.session_state.get(INPUT_KEY, ""))
    # compare_digest keeps the check constant-time; encode() because it
    # refuses to compare str with non-ASCII in some builds.
    if hmac.compare_digest(entered.encode("utf-8"),
                           expected_password().encode("utf-8")):
        st.session_state[STATE_KEY] = True
        st.session_state[INPUT_KEY] = ""          # don't leave it on screen
        st.session_state["unlock_error"] = False
    else:
        st.session_state["unlock_error"] = True


def render_unlock(container=None):
    """Draw the password box. Returns True if the session is unlocked."""
    target = container or st
    if unlocked():
        return True
    target.text_input(
        "Password",
        key=INPUT_KEY,
        type="password",
        placeholder="🔒 Enter password to see job titles and links",
        label_visibility="collapsed",
        on_change=_submit,
    )
    if st.session_state.get("unlock_error"):
        target.caption("❌ Wrong password.")
    return unlocked()
