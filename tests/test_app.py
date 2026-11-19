"""Exercise login callbacks without contacting LabArchives."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

from labapi import Client
from labapi.user import User
from labapi.util import NotebookInit
from requests.exceptions import ConnectionError
from streamlit.testing.v1 import AppTest

from labarchives_auth import create_login_state, valid_login_state

CALLBACK_URL = "https://connect.example.org/content/test-app/"
APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")
BROWSER_TOKEN = "test-browser-cookie"
API_PASSWORD = "test-api-password"
ENVIRONMENT = {
    "ACCESS_KEYID": "test-key-id",
    "ACCESS_PWD": API_PASSWORD,
    "API_URL": "https://api.labarchives.com",
    "LABARCHIVES_CALLBACK_URL": CALLBACK_URL,
}


class LoginStateTests(unittest.TestCase):
    def test_state_is_bound_to_browser_app_and_secret(self):
        state = create_login_state(CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
        self.assertTrue(
            valid_login_state(state, CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
        )
        for url, cookie, secret in (
            (CALLBACK_URL, "other-browser", API_PASSWORD),
            (CALLBACK_URL + "other/", BROWSER_TOKEN, API_PASSWORD),
            (CALLBACK_URL, BROWSER_TOKEN, "other-secret"),
            (CALLBACK_URL, "", API_PASSWORD),
        ):
            with self.subTest(url=url, cookie=cookie):
                self.assertFalse(valid_login_state(state, url, cookie, secret))
        self.assertFalse(
            valid_login_state(state + "0", CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
        )

    def test_expired_future_and_malformed_states_are_rejected(self):
        with patch("labarchives_auth.time.time", return_value=1000):
            state = create_login_state(CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
        for now in (999, 1301):
            with self.subTest(now=now), patch(
                "labarchives_auth.time.time", return_value=now
            ):
                self.assertFalse(
                    valid_login_state(state, CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
                )
        for state in (
            "",
            "malformed",
            "not-a-time.nonce.signature",
            "x" * 513,
            create_login_state(CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD) + "é",
        ):
            self.assertFalse(
                valid_login_state(state, CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
            )

    def test_real_client_generates_url_with_signed_callback(self):
        state = create_login_state(CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
        redirect = CALLBACK_URL + "?state=" + state
        with Client(
            base_url="https://api.labarchives.com",
            akid="test-key-id",
            akpass=API_PASSWORD,
        ) as client:
            auth_url = client.generate_auth_url(redirect)
        self.assertEqual(parse_qs(urlsplit(auth_url).query)["redirect_uri"], [redirect])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENVIRONMENT)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cookies = patch(
            "streamlit.runtime.context.ContextProxy.cookies",
            new_callable=PropertyMock,
            return_value={"_streamlit_xsrf": BROWSER_TOKEN},
        )
        self.cookies.start()
        self.addCleanup(self.cookies.stop)

        # Use the installed package's actual User/Notebooks implementation,
        # including duplicate notebook names. Only network-facing calls are mocked.
        with Client() as client:
            self.user = User(
                "user-1",
                "person@example.org",
                [
                    NotebookInit("101", "Experiment notes", False),
                    NotebookInit("202", "Shared notebook", False),
                    NotebookInit("303", "Shared notebook", False),
                ],
                client,
            )
        self.client_patch = patch("labapi.Client", autospec=True)
        self.client_factory = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.client = self.client_factory.return_value.__enter__.return_value
        self.client.login.return_value = self.user
        self.client.generate_auth_url.return_value = "https://api.labarchives.com/login"

    def callback_app(self, **overrides):
        params = {
            "email": self.user.email,
            "auth_code": "test-auth-code",
            "state": create_login_state(CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD),
        }
        params.update(overrides)
        app = AppTest.from_file(APP_PATH)
        app.query_params.update(params)
        return app.run(timeout=15)

    def test_no_configuration_still_renders_diagnostics(self):
        with patch.dict(os.environ, {}, clear=True):
            app = AppTest.from_file(APP_PATH).run()
        self.assertFalse(app.exception)
        self.client_factory.assert_not_called()
        self.assertIn("ACCESS_KEYID", app.info[0].value)
        self.assertTrue(app.button[-1].disabled)
        self.assertTrue(json.loads(app.json[0].value)["python"].startswith("3.12."))

    def test_login_button_uses_signed_callback_with_connect_path(self):
        app = AppTest.from_file(APP_PATH).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.get("link_button")[0].proto.label, "Log in to LabArchives")
        redirect = self.client.generate_auth_url.call_args.args[0]
        parsed = urlsplit(redirect)
        self.assertEqual(parsed.path, "/content/test-app/")
        state = parse_qs(parsed.query)["state"][0]
        self.assertTrue(
            valid_login_state(state, CALLBACK_URL, BROWSER_TOKEN, API_PASSWORD)
        )
        self.client.login.assert_not_called()

    def test_login_in_new_session_lists_all_notebooks_and_clears_callback(self):
        app = self.callback_app()
        self.assertFalse(app.exception)
        self.client.login.assert_called_once_with(self.user.email, "test-auth-code")
        self.assertEqual(app.query_params, {})
        rows = app.session_state["labarchives_user"]["notebooks"]
        self.assertEqual([row["ID"] for row in rows], ["101", "202", "303"])
        self.assertEqual(len(app.table[0].value), 3)
        self.assertIn(self.user.email, app.success[-1].value)
        app.run()
        self.client.login.assert_called_once()
        next(button for button in app.button if button.label == "Log out").click().run()
        self.assertNotIn("labarchives_user", app.session_state)
        self.assertFalse(app.table)
        self.assertTrue(app.get("link_button"))

    def test_login_with_no_notebooks(self):
        self.client.login.return_value = User("user-1", self.user.email, [], None)
        app = self.callback_app()
        self.assertFalse(app.exception)
        self.assertIn("no accessible notebooks", app.info[0].value)
        self.assertFalse(app.table)

    def test_invalid_callback_is_not_exchanged_and_secrets_are_cleared(self):
        for overrides in (
            {"state": "tampered"},
            {"auth_code": ""},
            {"email": ["first@example.org", "second@example.org"]},
            {"error": "authentication-denied"},
        ):
            with self.subTest(overrides=overrides):
                app = self.callback_app(**overrides)
                self.assertFalse(app.exception)
                self.assertTrue(app.error)
                self.assertEqual(app.query_params, {})
                self.assertNotIn("labarchives_user", app.session_state)
                self.client.login.assert_not_called()

    def test_login_failure_does_not_expose_request_credentials(self):
        self.client.login.side_effect = ConnectionError("private test-auth-code")
        app = self.callback_app()
        self.assertFalse(app.exception)
        self.assertNotIn("test-auth-code", app.error[0].value)
        self.assertNotIn("labarchives_user", app.session_state)
        self.assertEqual(app.query_params, {})
        self.assertTrue(self.client_factory.return_value.__exit__.called)

    def test_missing_cookie_disables_login(self):
        with patch(
            "streamlit.runtime.context.ContextProxy.cookies",
            new_callable=PropertyMock,
            return_value={},
        ):
            app = AppTest.from_file(APP_PATH).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.button[-1].disabled)
        self.client_factory.assert_not_called()

    def test_connect_cookie_can_bind_login(self):
        with patch(
            "streamlit.runtime.context.ContextProxy.cookies",
            new_callable=PropertyMock,
            return_value={"RSC-XSRF": BROWSER_TOKEN},
        ):
            app = self.callback_app()
        self.assertFalse(app.exception)
        self.assertTrue(app.table)

    def test_callback_url_requires_https_outside_localhost(self):
        with patch.dict(
            os.environ, {"LABARCHIVES_CALLBACK_URL": "http://connect.example.org/"}
        ):
            app = AppTest.from_file(APP_PATH).run()
        self.assertFalse(app.exception)
        self.assertIn("HTTPS", app.error[0].value)
        self.client_factory.assert_not_called()

    def test_a_different_session_has_no_notebook_data(self):
        signed_in = self.callback_app()
        separate = AppTest.from_file(APP_PATH).run()
        self.assertTrue(signed_in.table)
        self.assertFalse(separate.table)
        self.assertNotIn("labarchives_user", separate.session_state)


if __name__ == "__main__":
    unittest.main()
