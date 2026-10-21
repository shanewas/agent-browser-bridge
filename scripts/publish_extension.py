#!/usr/bin/env python3
"""Upload and publish the extension package to the Microsoft Edge Add-ons store.

Credentials come from the environment, never from arguments, so they stay out of shell
history and CI logs:

    EDGE_ADDONS_CLIENT_ID    Client ID from Partner Center > Microsoft Edge > Publish API
    EDGE_ADDONS_API_KEY      API key from the same page (v1.1 credentials)
    EDGE_ADDONS_PRODUCT_ID   Product ID GUID from the extension's overview page

    python scripts/publish_extension.py dist/agent-browser-bridge-extension-<version>.zip
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.addons.microsoftedge.microsoft.com"
ENV_CLIENT_ID = "EDGE_ADDONS_CLIENT_ID"
ENV_API_KEY = "EDGE_ADDONS_API_KEY"
ENV_PRODUCT_ID = "EDGE_ADDONS_PRODUCT_ID"


class PublishError(RuntimeError):
    pass


class Store:
    def __init__(self, client_id, api_key, product_id, api_root=API_ROOT):
        self.client_id = client_id
        self.api_key = api_key
        self.product_id = product_id
        self.api_root = api_root.rstrip("/")

    def _url(self, path):
        return f"{self.api_root}/v1/products/{self.product_id}{path}"

    def _send(self, method, url, body=None, content_type=None):
        headers = {"Authorization": f"ApiKey {self.api_key}", "X-ClientID": self.client_id}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()
            raise PublishError(f"{method} {url} -> HTTP {e.code} {e.reason}: {detail}") from e
        except urllib.error.URLError as e:
            raise PublishError(f"{method} {url} -> {e.reason}") from e
        except OSError as e:
            # A store that rejects the request mid-upload resets the connection instead of
            # answering, which surfaces here rather than as an HTTPError.
            raise PublishError(f"{method} {url} -> {e}") from e

    # The store returns the operation id in Location, sometimes bare and sometimes as a URL.
    @staticmethod
    def _operation_id(headers, method, url):
        location = (headers.get("Location") or "").strip()
        if not location:
            raise PublishError(f"{method} {url} returned 202 without a Location header")
        tail = location.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return tail.rsplit("/", 1)[-1]

    def upload(self, package_path):
        with open(package_path, "rb") as fh:
            payload = fh.read()
        url = self._url("/submissions/draft/package")
        status, headers, _ = self._send("POST", url, payload, "application/zip")
        if status != 202:
            raise PublishError(f"upload returned HTTP {status}, expected 202")
        return self._operation_id(headers, "POST", url)

    def upload_status(self, operation_id):
        url = self._url(f"/submissions/draft/package/operations/{operation_id}")
        return self._send("GET", url)[2]

    def publish(self, notes):
        url = self._url("/submissions")
        body = json.dumps({"notes": notes}).encode("utf-8")
        status, headers, _ = self._send("POST", url, body, "application/json")
        if status != 202:
            raise PublishError(f"publish returned HTTP {status}, expected 202")
        return self._operation_id(headers, "POST", url)

    def publish_status(self, operation_id):
        url = self._url(f"/submissions/operations/{operation_id}")
        return self._send("GET", url)[2]


def wait_for(fetch, label, timeout, interval):
    deadline = time.monotonic() + timeout
    while True:
        raw = fetch()
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError as e:
            raise PublishError(f"{label} status was not JSON: {raw[:200]!r}") from e
        state = (body.get("status") or "").strip()
        if state and state.lower() != "inprogress":
            if state.lower() == "succeeded":
                print(f"{label}: succeeded")
                return body
            raise PublishError(f"{label} failed: {json.dumps(body, ensure_ascii=False)}")
        if time.monotonic() >= deadline:
            raise PublishError(f"{label} still in progress after {timeout}s")
        print(f"{label}: in progress")
        time.sleep(interval)


def load_config(args):
    values = {
        ENV_CLIENT_ID: os.environ.get(ENV_CLIENT_ID, "").strip(),
        ENV_API_KEY: os.environ.get(ENV_API_KEY, "").strip(),
        ENV_PRODUCT_ID: os.environ.get(ENV_PRODUCT_ID, "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise PublishError("missing environment variables: " + ", ".join(missing))
    return Store(values[ENV_CLIENT_ID], values[ENV_API_KEY], values[ENV_PRODUCT_ID],
                 api_root=args.api_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish the extension zip to the Edge Add-ons store.")
    parser.add_argument("package", help="path to the extension .zip built by build_extension.py")
    parser.add_argument("--notes", default="", help="certification notes sent with the submission")
    parser.add_argument("--timeout", type=int, default=900, help="seconds to wait for each operation")
    parser.add_argument("--interval", type=int, default=10, help="seconds between status polls")
    parser.add_argument("--api-root", default=API_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--upload-only", action="store_true",
                        help="upload the package into the draft without submitting it for review")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.package):
        print(f"package not found: {args.package}", file=sys.stderr)
        return 2

    try:
        store = load_config(args)
        print(f"uploading {os.path.basename(args.package)}")
        op = store.upload(args.package)
        wait_for(lambda: store.upload_status(op), "upload", args.timeout, args.interval)
        if args.upload_only:
            print("draft updated; submission not sent for review")
            return 0
        notes = args.notes or f"Automated submission of {os.path.basename(args.package)}"
        op = store.publish(notes)
        wait_for(lambda: store.publish_status(op), "publish", args.timeout, args.interval)
        print("submitted for certification")
        return 0
    except PublishError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
