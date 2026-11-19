"""LabArchives login and notebook listing test for Posit Connect."""

import os
import platform
from importlib.metadata import version
from urllib.parse import urlencode, urlsplit

import streamlit as st
from labapi import Client
from labapi.exceptions import LabArchivesError
from requests.exceptions import RequestException

from labarchives_auth import create_login_state, valid_login_state


def main() -> None:
    st.set_page_config(page_title="labapi deployment test")
    st.title("labapi deployment test")
    st.write("Log in to LabArchives to view the notebooks you can access.")

    with st.expander("Deployment diagnostics"):
        st.success("labapi.Client imported successfully.")
        st.json(
            {
                "python": platform.python_version(),
                "streamlit": version("streamlit"),
                "labapi": version("labapi"),
            }
        )
        if "clicks" not in st.session_state:
            st.session_state.clicks = 0
        if st.button("Test interaction"):
            st.session_state.clicks += 1
        st.write(f"Button clicks this session: {st.session_state.clicks}")

    # Consume credentials once, removing them from the address bar before any
    # network request or subsequent interaction. Never cache them globally.
    callback_fields = ("email", "auth_code", "state", "error")
    callback = {
        key: st.query_params.get_all(key)
        for key in callback_fields
        if key in st.query_params
    }
    for key in callback:
        del st.query_params[key]

    required = ("ACCESS_KEYID", "ACCESS_PWD", "LABARCHIVES_CALLBACK_URL")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        st.info("Configure these app environment variables: " + ", ".join(missing))
        st.button("Log in to LabArchives", disabled=True)
        return

    callback_url = os.environ["LABARCHIVES_CALLBACK_URL"]
    try:
        parsed = urlsplit(callback_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.netloc
            or not (parsed.scheme == "https" or (parsed.scheme == "http" and local))
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError
    except ValueError:
        st.error(
            "LABARCHIVES_CALLBACK_URL must be the app's HTTPS URL without query "
            "parameters; HTTP is allowed for localhost."
        )
        return

    # These cookies are already supplied by Connect or Streamlit. Bind the
    # callback to one of them without storing or displaying its value.
    browser_token = st.context.cookies.get("RSC-XSRF") or st.context.cookies.get(
        "_streamlit_xsrf", ""
    )
    secret = os.environ["ACCESS_PWD"]

    if callback:
        complete = all(
            len(callback.get(key, [])) == 1 and callback[key][0]
            for key in ("email", "auth_code", "state")
        )
        if (
            not complete
            or "error" in callback
            or not valid_login_state(
                callback["state"][0], callback_url, browser_token, secret
            )
        ):
            st.error(
                "Login was incomplete or expired. Please log in again from this browser."
            )
        else:
            try:
                with st.spinner("Loading your LabArchives notebooks…"), Client(
                    timeout=30
                ) as client:
                    user = client.login(callback["email"][0], callback["auth_code"][0])
                    # all_values() preserves notebooks with duplicate names.
                    notebooks = [
                        {"Notebook": notebook.name, "ID": notebook.id}
                        for notebook in user.notebooks.all_values()
                    ]
                st.session_state["labarchives_user"] = {
                    "email": user.email,
                    "notebooks": notebooks,
                }
            except (LabArchivesError, RequestException, ValueError):
                # Exceptions may include signed request URLs or auth codes.
                st.error(
                    "LabArchives login failed. Check the API configuration and "
                    "connection, then try logging in again."
                )

    if "labarchives_user" in st.session_state:
        account = st.session_state["labarchives_user"]
        st.success(f"Logged in as {account['email']}")
        if st.button("Log out"):
            del st.session_state["labarchives_user"]
            st.rerun()
        st.subheader("Your notebooks")
        if account["notebooks"]:
            st.table(account["notebooks"])
        else:
            st.info("Your LabArchives account has no accessible notebooks.")
        st.caption("This list was fetched at login. Log out and back in to refresh it.")
        return

    if not browser_token:
        st.info("Enable browser cookies and reopen the app in its own tab to log in.")
        st.button("Log in to LabArchives", disabled=True)
        return

    try:
        state = create_login_state(callback_url, browser_token, secret)
        redirect_url = f"{callback_url}?{urlencode({'state': state})}"
        with Client(timeout=30) as client:
            auth_url = client.generate_auth_url(redirect_url)
    except (LabArchivesError, RequestException, ValueError):
        st.error("Could not start LabArchives login. Check the app's API configuration.")
        return

    st.link_button("Log in to LabArchives", auth_url, type="primary")
    st.caption(
        "LabArchives opens in a new tab and returns here after login. "
        "Login links expire after five minutes; reload this page if needed."
    )


if __name__ == "__main__":
    main()
