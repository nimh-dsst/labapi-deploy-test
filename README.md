# labapi-deploy-test

A minimal Streamlit app to test whether Posit Connect can restore a Python
environment from `manifest.json`, install [labapi from PyPI](https://pypi.org/project/labapi/),
and serve an interactive app.

The app provides a **Log in to LabArchives** button and displays the logged-in
user's accessible notebook names and IDs. The **Deployment diagnostics** expander
shows package versions and an interaction counter, even before API credentials
are configured. The app only reads notebook access information.

For the working authentication flow, deployment details, troubleshooting, and
lessons from testing on Connect, see the
[LabArchives authentication guide](LABARCHIVES_AUTH_GUIDE.md).

## Run locally

Use Python 3.12, matching the Posit Connect server:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export ACCESS_KEYID='your_labarchives_api_key_id'
export ACCESS_PWD='your_labarchives_api_password'
export LABARCHIVES_CALLBACK_URL='http://localhost:8501/'
python -m streamlit run app.py
```

`API_URL` defaults to `https://api.labarchives.com`. Set it to the API URL supplied
by LabArchives if your institution uses a different region.

## Configure LabArchives login on Connect

Set these variables in the deployed content's **Settings > Vars** panel, then
restart the app:

| Variable | Value |
| --- | --- |
| `ACCESS_KEYID` | Your institution's LabArchives API access key ID. |
| `ACCESS_PWD` | The corresponding API access password. |
| `API_URL` | Optional regional API URL; defaults to `https://api.labarchives.com`. |
| `LABARCHIVES_CALLBACK_URL` | The app's public HTTPS URL, such as `https://connect.example.org/content/CONTENT_GUID/`. |

Use the direct app URL, including its path and trailing slash, rather than the
Connect dashboard URL. Do not include query parameters or a URL fragment. Open
the app in its own browser tab for login. API access credentials are issued by
LabArchives; they are separate from your Connect API key and your personal login.
Keep their values in environment variables, outside the repository and manifest.

Click **Log in to LabArchives**. The button first opens the configured app URL in
a standalone tab, then redirects that tab to LabArchives. This also works when
you start in Connect's dashboard iframe or through an alternate app URL.
Authenticate on the LabArchives page, including institutional SSO if applicable.
LabArchives returns to the app in the new tab;
the app exchanges the callback's `email` and `auth_code` using `Client.login()` and
displays your notebooks. Complete login within five minutes; use the login button
again to start a fresh attempt if needed.

The app creates a random browser token in `localStorage` to validate the returning
login request. It does not need access to Connect or Streamlit cookies, which may
be unavailable behind a proxy. A small built-in Streamlit component reads this
token in both the original and returning tabs. Browser storage contains only this
random token, never login credentials or notebook information. Authentication
starts and ends at `LABARCHIVES_CALLBACK_URL` so both steps use the same browser
storage context.

If LabArchives returns an `error` instead of login credentials, the app explains
the rejection separately from browser verification failures. For an invalid
access key ID, check `ACCESS_KEYID` and `API_URL`. For a rejected signature, check
`ACCESS_PWD` and the region. These must be LabArchives API credentials, separate
from your Posit Connect publisher key. A timestamp rejection may require checking
the Connect server clock.

The app also handles callbacks that append `?auth_code=...` to a URL already
containing `?state=...`. It repairs the delimiter before decoding the code and
still verifies the signed state. Missing, empty, and repeated callback parameters
are reported separately.

Callback credentials are removed from the address bar before the API call. Only
your email and notebook list remain in your Streamlit session; they are not
shared through an app cache.
**Log out** clears that session's data. Reloading the page requires logging in
again. To refresh notebook access, log out and log back in. Logging out of this
app does not end your LabArchives or institutional SSO session.

## Deploy using the manifest

The repository includes a generated `manifest.json` with app mode
`python-streamlit`, entry point `app.py`, and `requirements.txt` for dependencies.
The Connect server needs a compatible Python 3.12 runtime and access to PyPI (or a
package mirror containing these dependencies), plus outbound access to the
LabArchives API. For a different Python runtime,
update `.python-version`, install the requirements in that environment, and
regenerate the manifest there before deploying.

### Import from Git

1. Commit and push `app.py`, `labarchives_auth.py`, `requirements.txt`,
   `.python-version`, and `manifest.json` to your Git remote.
2. In Posit Connect, choose **Publish > Import from Git**.
3. Select this repository, the branch containing these files, and the repository
   root as the content directory.
4. Deploy, configure the LabArchives variables above, and open the app directly.
5. Log in and confirm your accessible notebooks are listed. You can also check
   Python/package versions and the interaction counter in **Deployment diagnostics**.

### Deploy the existing manifest from the CLI

In the activated local environment, install the deployment tool and configure
your server using your Connect URL and publisher API key:

```sh
python -m pip install rsconnect-python==1.32.0
rsconnect add --name test --server https://connect.example.org --api-key YOUR_CONNECT_API_KEY
rsconnect deploy manifest manifest.json --name test --title "labapi deployment test"
```

Replace the example URL and key with your own values. `rsconnect-python` is local
deployment tooling and is intentionally separate from the app's requirements.

## Regenerate the manifest

After changing the app or dependencies, run this from the repository root in the
activated environment with `rsconnect-python` installed:

```sh
rsconnect write-manifest streamlit . \
  --entrypoint app.py \
  --overwrite \
  --exclude '.*' \
  --exclude '.*/**' \
  --exclude '__pycache__/**' \
  --exclude 'tests/**'
```

Hidden local files (including environments, caches, and secrets) and tests are
excluded from the bundle. The CLI still reads `.python-version` to set the
manifest's Python runtime requirement.

Keep the explicit `requirements.txt`; do not use `--force-generate`, which would
replace it with packages from the local environment. Commit and push the updated
manifest with the changed files before requesting a Git-backed redeployment.

## Tests

With the app requirements installed, run:

```sh
python -m unittest discover -s tests -v
```

These tests use mocked LabArchives responses and real `labapi` notebook objects;
they do not require live credentials. A live login on your Connect server is still
needed to verify institutional authentication, API access, and the callback URL.

See Posit's documentation on [Streamlit manifests](https://docs.posit.co/rsconnect-python/commands/write-manifest/#write-manifest-streamlit)
and [Git-backed deployment](https://docs.posit.co/connect/user/git-backed/), and
the [labapi authentication guide](https://nimh-dsst.github.io/labapi/guide/auth.html).
