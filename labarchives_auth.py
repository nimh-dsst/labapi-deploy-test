"""Small helpers for LabArchives callback authentication."""

import hashlib
import hmac
import json
import re
import secrets
import time

import streamlit as st
from streamlit.components.v2 import component

LOGIN_LIFETIME_SECONDS = 300

BROWSER_IDENTITY_JS = """
export default function({ data, setStateValue }) {
    try {
        const key = "labapi-deploy-test:browser:" + data.app_id;
        let token = window.localStorage.getItem(key);
        if (!/^[a-f0-9]{64}$/.test(token ?? "")) {
            const bytes = window.crypto.getRandomValues(new Uint8Array(32));
            token = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
            window.localStorage.setItem(key, token);
        }
        if (token !== data.token) {
            setStateValue("token", token);
        }
    } catch {
        if (!data.storage_error) {
            setStateValue("storage_error", true);
        }
    }
}
"""

_browser_identity = component("labapi_browser_identity", js=BROWSER_IDENTITY_JS)


def get_browser_identity(callback_url: str) -> tuple[str | None, bool]:
    """Read an app-specific browser token without depending on proxy cookies.

    localStorage survives the new tab/session created by LabArchives login.
    It stores only random bytes, never API credentials or notebook information.
    """
    key = "labarchives_browser_identity"
    current = st.session_state.get(key, {})
    result = _browser_identity(
        key=key,
        data={
            "app_id": hashlib.sha256(callback_url.encode()).hexdigest(),
            "token": current.get("token"),
            "storage_error": current.get("storage_error", False),
        },
        default={"token": None, "storage_error": False},
        on_token_change=lambda: None,
        on_storage_error_change=lambda: None,
    )
    token = result.token
    if token is not None and (
        not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{64}", token) is None
    ):
        return None, True
    return token, bool(result.storage_error)


def _signature(payload: str, callback_url: str, browser_token: str, secret: str) -> str:
    message = json.dumps([payload, callback_url, browser_token]).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def create_login_state(callback_url: str, browser_token: str, secret: str) -> str:
    """Bind a short-lived callback to the browser that started login.

    Streamlit creates a new session after a browser redirect, so session_state
    alone cannot validate the returning request. The app's browser-storage token
    stays available across redirects and worker changes.
    """
    if not browser_token:
        raise ValueError("A browser token is required to start login.")
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{_signature(payload, callback_url, browser_token, secret)}"


def valid_login_state(
    state: str, callback_url: str, browser_token: str, secret: str
) -> bool:
    """Check the callback signature, browser binding, and five-minute lifetime."""
    if not browser_token or len(state) > 512:
        return False
    try:
        payload, signature = state.rsplit(".", 1)
        issued_at, nonce = payload.split(".", 1)
        age = time.time() - int(issued_at)
    except (ValueError, TypeError):
        return False
    return (
        bool(nonce)
        and 0 <= age <= LOGIN_LIFETIME_SECONDS
        and hmac.compare_digest(
            signature.encode(),
            _signature(payload, callback_url, browser_token, secret).encode(),
        )
    )
