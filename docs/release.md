# Release

One source of truth for the version: `agent_browser_bridge/__init__.py`. `pyproject.toml` reads it;
`scripts/build_extension.py` writes it into `manifest.json`. The `edge-agent-bridge` compatibility
shim under `shim/` is the exception: it's published once at 3.0.0 and never bumped, and
`skip-existing` keeps later runs green.

## Automated path

Pushing to `main` runs `.github/workflows/release.yml`. It first runs the whole `ci` workflow
(hermetic and E2E suites on Windows, Linux and macOS); nothing publishes unless that passes. The `gate` job
then builds the extension zip, checks the manifest is in sync, runs the hermetic suite, and asks
PyPI whether the current version already exists. Everything downstream is skipped when it does, so an ordinary push costs one test
run and nothing else.

When the version is new, the workflow publishes both distributions to PyPI (`agent-browser-bridge`
and the shim), cuts a GitHub release carrying the wheel, the sdist and the extension zip, then
submits the extension to the Edge Add-ons store and the Chrome Web Store. Those last steps run only
when a file under `agent_browser_bridge/extension/` changed in the push, because a store review
cycle takes days and a Python-only change doesn't need one. The `force_extension` input on a manual
run submits anyway.

A release is therefore: bump `__version__`, write the `CHANGELOG.md` entry, merge to `main`.

### One-time setup

PyPI uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no PyPI token is
stored anywhere. Two PyPI projects publish from here, and each needs a trusted publisher pointing at
repository `shanewas/agent-browser-bridge`, workflow `release.yml`, environment `release`:
`agent-browser-bridge` (the package) and `edge-agent-bridge` (the shim).

The Edge store needs three secrets from **Partner Center > Microsoft Edge > Publish
API**. If that page still shows an access token URL and secrets, click **Enable** first: the v1.1
credentials are a Client ID and an API key.

| Secret | Where it comes from |
| --- | --- |
| `EDGE_ADDONS_CLIENT_ID` | Client ID on the Publish API page |
| `EDGE_ADDONS_API_KEY` | API key on the same page; note the expiry and renew before it lapses |
| `EDGE_ADDONS_PRODUCT_ID` | Product ID GUID on the extension's overview page |

The Chrome Web Store needs five, and a listing that already exists: the API updates an item, it
can't create one. Publish the first version by hand in the developer dashboard, then automation
takes over.

| Secret | Where it comes from |
| --- | --- |
| `CHROME_CLIENT_ID` | an OAuth client of type Desktop app in Google Cloud Console |
| `CHROME_CLIENT_SECRET` | same client; the console shows a secret once, at creation |
| `CHROME_REFRESH_TOKEN` | minted for scope `https://www.googleapis.com/auth/chromewebstore` with a loopback redirect (`http://127.0.0.1:<port>/`), `access_type=offline`, `prompt=consent` |
| `CHROME_PUBLISHER_ID` | the GUID in the developer dashboard URL |
| `CHROME_ITEM_ID` | the 32-character item id from the listing URL |

Enable the Chrome Web Store API for the project before minting the token, and publish the OAuth
consent screen (Audience > In production): a consent screen left in Testing issues refresh tokens
that expire after seven days. Publishing needs a home page plus privacy and terms pages on an
authorized domain; this project uses `https://shanewas.com/agent-browser-bridge/`.

Create a `release` environment in the repository settings and attach the secrets to it. Adding a
required reviewer to that environment puts a manual approval in front of all three publishers.

## Manual path

Everything the workflow does also runs by hand:

1. **Green tree.** `python -m pytest -q -m "not e2e"` and `python -u -m pytest -q -m e2e` pass
   locally on Windows; `ci` is green on the branch for all three runners.
2. **Bump.** Edit `__version__` in `agent_browser_bridge/__init__.py`, then run
   `python scripts/build_extension.py` (syncs `manifest.json`, writes
   `dist/agent-browser-bridge-extension-<version>.zip`) and `python scripts/build_extension.py --check`.
3. **Docs.** `CHANGELOG.md` entry. Regenerate the README benchmark numbers from
   `python -u -m pytest tests/e2e/test_benchmark.py -q -s` if they moved.
4. **Build.** `python -m build --sdist --wheel` and `python -m build --sdist --wheel --outdir dist shim`.
5. **Identity.** `git config user.email` prints `shanewasahmed@gmail.com`.
6. **Tag.** `git commit -m "release: <version>"`, `git tag v<version>`, `git push origin main --tags`.
7. **GitHub release.** `gh release create v<version> dist/*<version>* --title "v<version>" --generate-notes`.
8. **PyPI.** `python -m twine upload dist/*<version>* --disable-progress-bar` covers both
   distributions (the flag avoids the cp932 crash on Windows terminals).
9. **Stores.** Export the Edge credentials, then
   `python scripts/publish_extension.py dist/agent-browser-bridge-extension-<version>.zip`. Export the
   Chrome ones and run `python scripts/publish_chrome.py` on the same zip. Both take
   `--upload-only` to stage a draft without sending it for review. Review takes days on Edge and
   longer on Chrome, which scrutinises the `debugger` permission; until a store lands the new
   version, a daemon newer than the installed extension answers new actions with
   `extension_outdated`.
10. **Smoke.** On each OS you can reach: `pip install --upgrade agent-browser-bridge`,
    `agent-bridge daemon restart`, `agent-bridge status`, `agent-bridge --json snapshot` with an http(s) page focused.

Listing text, screenshots and the product name still have to be edited in Partner Center. The API
replaces the package and nothing else.

## Edge certification notes

Partner Center asks for notes to the certification team on every submission. The API can't fill
them in, so paste this text; the 2.6.1 submission on 2026-10-21 passed certification with it:

> No credentials are needed. The extension talks only to a daemon on the tester's own machine.
> To test: `pip install agent-browser-bridge`, then `agent-bridge daemon start`; the popup shows
> Connected. Open any http(s) page and run `agent-bridge snapshot`, then
> `agent-bridge click --ref <ref>` with a ref from the snapshot.
>
> The `debugger` permission is what makes input trusted and screenshots possible:
> `Input.dispatchMouseEvent`, `Input.dispatchKeyEvent`, `Input.insertText`,
> `Page.captureScreenshot`, `DOM.setFileInputFiles`, plus dialog and console events. It attaches
> only to the tab being driven and detaches afterwards. Nothing leaves the machine; the daemon
> binds 127.0.0.1 and there is no telemetry.
