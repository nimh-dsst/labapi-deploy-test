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

BROWSER_IDENTITY_JS = r"""
export default function({ data, setStateValue }) {
    // Some callback producers append ?auth_code=... even when redirect_uri
    // already has ?state=.... Repair the delimiter before parsing any values,
    // so percent-encoded +, /, =, and % in the code survive unchanged.
    const search = window.location.search;
    const repaired = search.replace(
        /^(\?state=\d+\.[A-Za-z0-9_-]+\.[a-f0-9]{64})\?(?=(?:auth_code|email|error)=)/,
        "$1&"
    );
    if (repaired !== search) {
        const url = new URL(window.location.href);
        url.search = repaired;
        window.location.replace(url.href);
        return;
    }
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
_login_redirect = component(
    "labapi_login_redirect",
    js="""
    export default function({ data }) {
        window.location.assign(data.url);
    }
    """,
)


def redirect_to_labarchives(url: str) -> None:
    """Navigate the standalone sign-in tab to the signed LabArchives URL."""
    _login_redirect(data={"url": url}, key="labarchives_login_redirect")


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


def needs_callback_query_repair(callback: dict[str, list[str]]) -> bool:
    """Wait for the browser to repair a second '?' after our signed state.

    Do not extract the code from an already decoded state value: decoding it
    twice would change valid '+' characters to spaces.
    """
    states = callback.get("state", [])
    return len(states) == 1 and re.match(
        r"^\d+\.[A-Za-z0-9_-]+\.[a-f0-9]{64}\?(?:auth_code|email|error)=",
        states[0],
    ) is not None


def callback_error(
    callback: dict[str, list[str]],
    callback_url: str,
    browser_token: str,
    secret: str,
) -> str | None:
    """Explain callback failures without displaying credentials or signed URLs."""
    if "error" in callback:
        message = " ".join(callback["error"]).casefold()
        if "akid" in message or "access key id" in message:
            return (
                "LabArchives rejected the API access key ID. Check ACCESS_KEYID "
                "and API_URL; use your LabArchives API key, not a Connect API key."
            )
        if "signature" in message or "access password" in message:
            return (
                "LabArchives rejected the login signature. Check ACCESS_PWD and "
                "confirm API_URL matches your LabArchives region."
            )
        if any(word in message for word in ("expired", "expires", "timestamp")):
            return (
                "LabArchives rejected the login timestamp. Start a new login; "
                "if it repeats immediately, check the Connect server's clock."
            )
        if any(word in message for word in ("cancel", "denied")):
            return "LabArchives login was cancelled or access was denied."
        return (
            "LabArchives returned a login error. Check the LabArchives API key, "
            "API password, and regional API_URL."
        )
    issues = []
    for key in ("email", "auth_code", "state"):
        values = callback.get(key, [])
        if not values:
            issues.append(f"{key} (missing)")
        elif len(values) != 1:
            issues.append(f"{key} (repeated)")
        elif not values[0]:
            issues.append(f"{key} (empty)")
    if issues:
        return "LabArchives login response has invalid parameters: " + ", ".join(issues)
    if not valid_login_state(callback["state"][0], callback_url, browser_token, secret):
        return (
            "The login response did not match this browser or its five-minute "
            "window expired. Start a new login using the button below."
        )
    return None
