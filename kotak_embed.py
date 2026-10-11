"""Runs the Kotak live feed inside the Streamlit process (one background thread)."""
import os
import threading

import streamlit as st

import kotak_feed

_KEYS = ("KOTAK_ALWAYS", "KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC", "KOTAK_MPIN", "KOTAK_TOTP_SECRET")


@st.cache_resource
def _start_thread():
    t = threading.Thread(target=kotak_feed.main, daemon=True, name="kotak-feed")
    t.start()
    return t


def render_sidebar():
    """Call once near the top of app.py. Secrets come from Streamlit Cloud secrets only."""
    for k in _KEYS:
        try:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
        except Exception:
            pass
    _start_thread()
    with st.sidebar:
        st.markdown("**Kotak live feed**")
        st.caption(kotak_feed.STATUS.get("msg", ""))
        st.caption("check: " + ", ".join(k.replace("KOTAK_", "") + "=" + ("yes" if os.environ.get(k) else "no") for k in _KEYS)
                   + " | thread alive=" + str(_start_thread().is_alive()))
        if not os.environ.get("KOTAK_TOTP_SECRET"):
            code = st.text_input("Kotak TOTP (6 digits)", max_chars=6, key="kotak_totp_input")
            if code and len(code) == 6 and code.isdigit():
                os.environ["KOTAK_TOTP_NOW"] = code
                st.caption("Code received; logging in within 15s.")
