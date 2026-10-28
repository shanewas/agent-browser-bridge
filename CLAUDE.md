# CLAUDE.md - Agent Browser Bridge

## Identity (STRICT)
- Author & committer: Shanewas Ahmed <shanewasahmed@gmail.com>. No work address anywhere in this repo, its packages, or its docs.
- `git config user.email` must print shanewasahmed@gmail.com before any commit.

## Layout
- One source tree: `agent_browser_bridge/` (daemon, clients, `extension/`, `desktop/`). There are no root copies to mirror.
- `desktop/` is the Windows desktop layer (screenshot, mouse, keyboard) absorbed from doppelhand; the daemon routes `desktop_*` actions to it. Pillow is its only dependency, installed by the optional `desktop` extra; off Windows or without Pillow every desktop action answers a failure dict.
- Version lives in `agent_browser_bridge/__init__.py` only. `pyproject.toml` reads it; `scripts/build_extension.py` writes it into `manifest.json` and zips the extension.
- `shim/` is the `edge-agent-bridge` compatibility package: frozen at 3.0.0, never bumped.

## Rules
- Zero runtime dependencies; the one exception is the optional `desktop` extra (Pillow). Daemon binds 127.0.0.1 only. No telemetry.
- Every tab-bound action result carries `tab: {id, title, url}`; errors are `{success:false, code, error}`.
- Hermetic tests: `python -m pytest -m "not e2e"`. E2E (needs Edge): `python -m pytest -m e2e`. Run background scripts with `python -u`.
- Commit only when the owner says "commit". Conventional Commits, subject <= 50 chars, no AI attribution, no emoji.

## Release
Follow `docs/release.md` (build, tag, GitHub release with wheel + sdist + extension zip, `twine upload --disable-progress-bar`, store upload, smoke on each OS).
