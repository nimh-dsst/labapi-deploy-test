"""Small helpers for LabArchives callback authentication."""

import hashlib
import hmac
import json
import secrets
import time

LOGIN_LIFETIME_SECONDS = 300


def _signature(payload: str, callback_url: str, browser_token: str, secret: str) -> str:
    message = json.dumps([payload, callback_url, browser_token]).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def create_login_state(callback_url: str, browser_token: str, secret: str) -> str:
    """Bind a short-lived callback to the browser that started login.

    Streamlit creates a new session after a browser redirect, so session_state
    alone cannot validate the returning request. The existing XSRF cookie stays
    with the browser across redirects and worker changes.
    """
    if not browser_token:
        raise ValueError("A browser cookie is required to start login.")
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
