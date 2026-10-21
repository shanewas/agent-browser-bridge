# CLAUDE.md - Edge Agent Bridge

## Identity (STRICT)
- Author & committer: Shanewas Ahmed <shanewasahmed@gmail.com>. No work address anywhere in this repo, its packages, or its docs.
- `git config user.email` must print shanewasahmed@gmail.com before any commit.

## Layout
- One source tree: `agent_browser_bridge/` (daemon, clients, `extension/`). There are no root copies to mirror.
- Version lives in `agent_browser_bridge/__init__.py` only. `pyproject.toml` reads it; `scripts/build_extension.py` writes it into `manifest.json` and zips the extension.
- Design: `docs/superpowers/specs/2026-09-07-v2-design.md`. Plan: `docs/superpowers/plans/2026-09-07-v2-implementation.md`.

## Rules
- Zero runtime dependencies. Daemon binds 127.0.0.1 only. No telemetry.
- Every tab-bound action result carries `tab: {id, title, url}`; errors are `{success:false, code, error}`.
- Hermetic tests: `python -m pytest -m "not e2e"`. E2E (needs Edge): `python -m pytest -m e2e`. Run background scripts with `python -u`.
- Commit only when the owner says "commit". Conventional Commits, subject <= 50 chars, no AI attribution, no emoji.

## Release
Follow `docs/release.md` (build, tag, GitHub release with wheel + sdist + extension zip, `twine upload --disable-progress-bar`, store upload, smoke on each OS).
