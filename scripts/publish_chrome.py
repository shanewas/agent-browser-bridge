#!/usr/bin/env python3
"""Upload and publish the extension package to the Chrome Web Store.

The API updates an item that already exists; create the listing once in the developer
dashboard first. Credentials come from the environment, never from arguments:

    CHROME_CLIENT_ID       OAuth client ID from Google Cloud Console
    CHROME_CLIENT_SECRET   OAuth client secret for that client
    CHROME_REFRESH_TOKEN   refresh token minted for the chromewebstore scope
    CHROME_PUBLISHER_ID    publisher the item belongs to
    CHROME_ITEM_ID         the extension's 32-character item id

    python scripts/publish_chrome.py dist/agent-browser-bridge-extension-<version>.zip
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://chromewebstore.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/chromewebstore"

ENV_CLIENT_ID = "CHROME_CLIENT_ID"
ENV_CLIENT_SECRET = "CHROME_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "CHROME_REFRESH_TOKEN"
ENV_PUBLISHER_ID = "CHROME_PUBLISHER_ID"
ENV_ITEM_ID = "CHROME_ITEM_ID"


class PublishError(RuntimeError):
    pass


def _send(method, url, body=None, headers=None, timeout=300):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()
        raise PublishError(f"{method} {url} -> HTTP {e.code} {e.reason}: {detail}") from e
    except urllib.error.URLError as e:
        raise PublishError(f"{method} {url} -> {e.reason}") from e
    except OSError as e:
        raise PublishError(f"{method} {url} -> {e}") from e


class Store:
    def __init__(self, client_id, client_secret, refresh_token, publisher_id, item_id,
                 api_root=API_ROOT, token_url=TOKEN_URL):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.publisher_id = publisher_id
        self.item_id = item_id
        self.api_root = api_root.rstrip("/")
        self.token_url = token_url
        self._token = None

    def token(self):
        if self._token:
            return self._token
        form = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
            "scope": SCOPE,
        }).encode("utf-8")
        _, raw = _send("POST", self.token_url, form,
                       {"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            raise PublishError(f"token response was not JSON: {raw[:200]!r}") from e
        self._token = data.get("access_token")
        if not self._token:
            raise PublishError(f"token response carried no access_token: {data}")
        return self._token

    def _auth(self, **extra):
        return {"Authorization": f"Bearer {self.token()}", **extra}

    def _path(self, verb, upload=False):
        root = f"{self.api_root}/upload/v2" if upload else f"{self.api_root}/v2"
        return f"{root}/publishers/{self.publisher_id}/items/{self.item_id}:{verb}"

    def upload(self, package_path):
        with open(package_path, "rb") as fh:
            payload = fh.read()
        url = self._path("upload", upload=True)
        status, raw = _send("POST", url, payload, self._auth(**{"Content-Type": "application/zip"}))
        if status not in (200, 202):
            raise PublishError(f"upload returned HTTP {status}, expected 200 or 202")
        return raw

    def fetch_status(self):
        raw = _send("GET", self._path("fetchStatus"), None, self._auth(), timeout=60)[1]
        try:
            return json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError as e:
            raise PublishError(f"fetchStatus was not JSON: {raw[:200]!r}") from e

    def publish(self):
        url = self._path("publish")
        status, raw = _send("POST", url, b"{}", self._auth(**{"Content-Type": "application/json"}))
        if status not in (200, 202):
            raise PublishError(f"publish returned HTTP {status}, expected 200 or 202")
        return raw.decode("utf-8", "replace")


def wait_for_upload(store, timeout, interval):
    """Poll until the asynchronous upload settles.

    The reference documents the field but not its enum members, so anything still reading as
    in-progress keeps the loop running and everything else is reported verbatim rather than
    matched against values that might not exist.
    """
    deadline = time.monotonic() + timeout
    while True:
        body = store.fetch_status()
        state = str(body.get("lastAsyncUploadState") or "").strip()
        if state and "PROGRESS" not in state.upper():
            if "SUCCESS" in state.upper() or state.upper() in ("NOT_APPLICABLE", "UPLOAD_SUCCESS"):
                print(f"upload: {state}")
                return body
            raise PublishError(f"upload failed: {json.dumps(body, ensure_ascii=False)}")
        if time.monotonic() >= deadline:
            raise PublishError(f"upload still in progress after {timeout}s: {state or 'no state'}")
        print(f"upload: {state or 'pending'}")
        time.sleep(interval)


def load_config(args):
    names = [ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN, ENV_PUBLISHER_ID, ENV_ITEM_ID]
    values = {n: os.environ.get(n, "").strip() for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise PublishError("missing environment variables: " + ", ".join(missing))
    return Store(values[ENV_CLIENT_ID], values[ENV_CLIENT_SECRET], values[ENV_REFRESH_TOKEN],
                 values[ENV_PUBLISHER_ID], values[ENV_ITEM_ID],
                 api_root=args.api_root, token_url=args.token_url)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish the extension zip to the Chrome Web Store.")
    parser.add_argument("package", help="path to the extension .zip built by build_extension.py")
    parser.add_argument("--timeout", type=int, default=900, help="seconds to wait for the upload")
    parser.add_argument("--interval", type=int, default=10, help="seconds between status polls")
    parser.add_argument("--upload-only", action="store_true",
                        help="upload the package as a draft without submitting it for review")
    parser.add_argument("--api-root", default=API_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--token-url", default=TOKEN_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not os.path.isfile(args.package):
        print(f"package not found: {args.package}", file=sys.stderr)
        return 2

    try:
        store = load_config(args)
        print(f"uploading {os.path.basename(args.package)}")
        store.upload(args.package)
        wait_for_upload(store, args.timeout, args.interval)
        if args.upload_only:
            print("draft updated; item not submitted for review")
            return 0
        store.publish()
        print("submitted for review")
        return 0
    except PublishError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
