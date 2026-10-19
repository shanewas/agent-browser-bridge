# Changelog

## Unreleased

- Verified writes no longer clear the field: `type` is never retried, the clearing
  `fill` retry skips contenteditable targets, contenteditable readback ignores
  leading and trailing newlines and U+00A0, and an unreadable retry returns
  `match: "unknown"` instead of `write_mismatch`. The CLI warns on stderr when a
  write is unverified and exits 1 on a mismatch (#6).
- Extension attaches to tabs even when DevTools or another CDP client is
  already attached, removing the premature `getTargets` shortcut. Failed
  attaches report `native: false` with `debuggerError` instead of silent
  synthetic event fallback, and commands map missing tabs to `tab_not_found`.
- macOS: the daemon no longer blocks about 30 s in `socket.getfqdn()` before it
  listens, which made auto-start time out and every hermetic macOS CI leg fail.
- Releases publish only after the full `ci` workflow passes, including the
  Windows E2E suite.
- `fill` and `type` by ref work inside iframes not at the page origin: in-frame
  hit-testing now uses frame-local coordinates instead of failing with
  `focus_lost`. Corrects the 2.4.0 known-issue note:
  `test_fill_ref_in_cross_origin_frame` was this bug, not a headless Edge flake.
- `daemon stop` and `daemon restart` kill only when `/status` on the configured
  port reports the PID in `bridge.pid`. A stale file left by a crash or reboot is
  deleted and nothing is killed, so a recycled PID no longer takes down an
  unrelated process. If the PID is alive and the port accepts connections without
  answering, the file is kept and stop exits 1; `--force` keeps the old
  unconditional kill for that hung daemon. `daemon restart` exits 1 when a daemon
  still answers after the stop, instead of reporting success.
- Upgrading: a daemon from 2.5.0 or earlier does not report its PID, so plain
  `daemon stop` refuses it. Run `edge-bridge daemon restart --force` once after
  upgrading.
- `/status` reports the daemon's `pid`; `status` and `daemon status` print it
  from there instead of reading `bridge.pid`.
- Windows: the daemon no longer sets `SO_REUSEADDR`, so a second daemon on a busy
  port fails with `WinError 10048` and exits 1 instead of binding silently and
  overwriting `bridge.pid`.
- `run --tab N` forwards `tabId: N` to every step that does not carry its
  own, instead of running all steps on the focused tab.
- `eval` and `text` print their result in human output instead of `OK`, and
  a path-less `screenshot` reports the size of the returned `dataUrl`.

## 2.5.0

- PATH-proof entry points: `py -m edge_agent_bridge` and `python -m edge_agent_bridge`
  delegate directly to the CLI, allowing commands to run even when `edge-bridge`
  is not recognized on PATH.
- `edge-bridge doctor` adds `launcher`, `path`, and Windows `elevation` diagnostics:
  identifies missing scripts, broken PATH, stale installs, and elevated shells with
  targeted remediation commands. Machine-parseable `--json` output remains strictly clean.
- `edge-bridge setup --fix-path` (Windows): safely appends the Python scripts directory
  to user PATH in HKCU with duplicate checking, 2047-character guard, confirmation
  prompt (`--yes` to skip), and `WM_SETTINGCHANGE` broadcast.
- Extension onboarding: fresh extension installs open `onboarding.html` on first install
  probing local daemon status, offering copyable `py -m` startup commands, upgrade nudges
  on version mismatch, and real-time connection state.

## 2.4.0

- `upload` pierces open shadow roots and temp-reveals hidden file inputs: the
  page marks the input through shadow trees, the worker resolves it with a
  pierced CDP lookup, and styles are restored after. Fixes `no_file_input` on
  Salesforce Lightning forms and `upload_failed (-32000)` on `display:none`
  inputs. New e2e page `shadow-upload.html` covers both plus style restore.
- `eval --file PATH` (`-` for stdin) runs JS from a file instead of a
  shell-quoted argument.
- New `edge_agent_bridge.doppelhand` companion client for OS-level file
  dialogs: POST-only `shot`/`click`/`move`/`type`/`key`, `type_file_and_confirm`
  helper, and a CSS-to-view coord mapper. Token via arg or `DOPPELHAND_TOKEN`.
- New `docs/recipes.md`: shadow-DOM upload, `--file`, doppelhand fallback,
  tight captcha chains via `run`.
- E2E harness passes `--no-sandbox` to Edge when running as root (CI
  containers, root-mapped WSL users).
- Known issue: `test_fill_ref_in_cross_origin_frame` flakes under headless
  Edge 154 + `--no-sandbox` in this environment; fails identically without
  this release's changes.
## 2.3.0

- `edge-bridge doctor` diagnoses daemon, token, extension, versions, tabs, pairing,
  stale pid, and MCP registration in one command (exit 0/1, `--json` for agents).
  `edge-bridge logs [--tail N] [--follow]` tails the daemon log, including a `no_log`
  error when the daemon never started. `status` and `daemon status` share one
  implementation; `status` no longer prints `PID None` and shows the session pin.
- Sessions: `session start --name`, `session list`, `session prune`; `session_status`
  reports `name` and `age_s`; list output never contains tokens. The client remembers
  the last tab and re-pins it after a daemon restart (`unknown_session` re-mint);
  when the tab is gone the session resolves the active tab like a fresh one.
- Snapshot budgets: `maxNodes` (CLI `--max-nodes`, default 400, 0 = unlimited),
  `truncated`/`total` in every snapshot result, and `mode: "compact"` (CLI
  `--compact`, MCP `mode`) with one terse line per ref and no values.
- Clicks that land: resolve hit-tests the click point, nudges the scroll toward free
  space once, and returns `click_covered` instead of dispatching into a sticky
  overlay; subframe targets get a main-frame cover check. Explicit `--x/--y` never
  scrolls, by design.
- Popup v2: last-error box with a fix hint, Reconnect button, Copy-diagnostics
  button (versions, extension id, connection state; no token or URLs), store link in
  the outdated banner, and a pairing placeholder that names the token file.
- `edge-bridge run STEPS.json [--stop-on-error]` executes `{action, params}` steps
  over one connection with one JSON line per step (`-` reads stdin). CLI `upload`
  checks files exist first (`file_not_found` naming both forms) and translates WSL
  `/mnt/<drive>/...` paths to Windows form for Windows Edge.

## 2.2.0

- Browsing history: `edge-bridge history search "query"` searches titles/URLs with `--max-results`,
  `--start-time`/`--end-time`; `history delete <url>` removes one entry. MCP tools
  `edge_history_search` / `edge_history_delete`. Requires the `history` permission
  (added to the manifest in this release).
- Tab groups: `edge-bridge group list [--window-id N]`, `group move <tab ids>
  [--group-id N] [--title T] [--color C]` (new group when `--group-id` is omitted),
  `group ungroup <tab ids>`. MCP tools `edge_group_list` / `edge_group_move` /
  `edge_group_ungroup`. 34 MCP tools total.
- CLI rejects non-integer tab IDs for `group move`/`ungroup` with exit code 3
  instead of a traceback.

## 2.1.0

- Daemon-side tab sessions: `edge-bridge session start/status/stop`, `--session` /
  `EDGE_BRIDGE_SESSION`, MCP implicit session. Forgetting `--tab` is harmless inside a
  session; explicit `--tab` is a one-shot override; only `switch`/`new` re-pin.
- Per-tab locks in the daemon (10s acquire, `tab_busy` after); batches expand
  daemon-side and lock per step (non-atomic). Offline dispatches fail fast with
  `extension_offline`; every dispatch carries a 12s `deadlineMs` the extension enforces.
- Extension focus guards on `type`/`fill`: frame-aware pre-assert (≤3 tries),
  mid-type sampling (1st + every 8th char) with `focus_stolen` resume info, combobox
  carve-out. New codes: `focus_lost`, `focus_unverifiable`, `focus_stolen`,
  `deadline_exceeded`, `stale_ref` (vanished ref frame).
- Verified writes: `type`/`fill` return `{written, readback, match}`; the client
  retries once on mismatch and returns `write_mismatch` after that. Old extensions
  degrade to `match: "unknown"` with a one-time warning.
- Client fallback ladder for ref-taking actions (ref → text → fresh scan → coords,
  ≤2 extra scans, `--no-fallback` opt-out, `exhausted_fallback` shape).

## 2.0.2

- Direct link to the Microsoft Edge Add-ons store listing (`Agent Browser Bridge`) added to `README.md`, `pyproject.toml` URLs, and package metadata.
- `edge-bridge screenshot <path>` now saves the file when given a destination path.
- `release.yml` distinguishes package-only bumps from extension changes, skipping redundant store submissions when only Python files change.
- `scripts/publish_chrome.py` submits the extension to the Chrome Web Store through its v2 API,
  exchanging a refresh token for a bearer token and polling the upload before it submits.
- `build_extension.py --store chrome|edge` writes the listing name for each store and a shared
  description within the 132-character Chrome limit. `--check` enforces length limits at build time.

## 2.0.1

Fixes an authentication hole in the daemon, and corrects what 2.0.0 shipped around the code.

### Security

- Removed `/api/eval`, `/api/tabs` and the `/api/status` alias. `/api/eval` forwarded arbitrary
  JavaScript to the extension without checking the token, so any local process could run script in
  the browser with the user's session. The documented routes are `/exec`, `/status` and `/ws`, and
  `/exec` has always required the token.
- The token file is now created with mode 0600 instead of written and then chmodded, which left it
  world-readable on POSIX for the window in between.

### Fixed

- The MCP server treated a request with `id: 0` as a notification and never answered it, so a
  client numbering from zero hung on its first call.
- An exception inside a tool call killed the MCP session. It now answers -32603 and keeps
  serving.
- `screenshot(path=...)` wrote no file and `edge_screenshot` returned no image. The extension
  reports the capture as `dataUrl`; both callers read a `data` key that never existed, so the
  Python helper returned silently and the MCP tool handed the agent a base64 JSON dump.
- An explicit `tabId` argument now re-pins the session, matching `tab_switch` and `tab_new`.
- `setup` and `mcp-config --client vscode` wrote the server under `mcpServers`. VS Code reads
  `mcp.json` under `servers`, so it registered nothing. Cursor and Windsurf keep `mcpServers`.
- Client config files are written through a temporary file and renamed, so an interrupted
  `setup` can no longer leave a truncated editor config behind.
- `setup` runs the client registration commands as argument lists instead of through a shell.
- `daemon status` printed `PID None`. The status response carries no pid, so the line now reads
  the pid file the daemon already writes.
- `key +` and `key Ctrl++` sent an empty key. The modifier split now takes the last separator.
- Benchmark wall-clock thresholds no longer fail the run by default. A loaded CI runner missed
  them at random, which would have made the pipeline flap. `EDGE_BRIDGE_BENCH_STRICT=1`
  restores them as gates.

### Docs and packaging

- README describes the 2.x surfaces: the MCP server and `edge-bridge setup`, snapshot refs, the
  token on `/exec`, and the shared-host limit that pairing covers. It had still been documenting
  the 1.x CLI, and claimed a Python 3.8 floor against a package that needs 3.10.
- Store screenshots and promo tiles were regenerated. The published ones carried the extension's
  former name and demonstrated commands that no longer exist.
- Targeting and the snapshot role table no longer carry vendor-specific custom-element tag lists.
  Components are still reached through their `role` attribute or the native control in their
  shadow root.
- Frame discovery and subframe snapshot composition moved into their own extension module.
- `scripts/publish_extension.py` submits the extension zip through the Edge Add-ons Update API
  (v1.1, API key auth). `release.yml` publishes to PyPI, cuts the GitHub release, and submits the
  extension when an extension file changed in the push.
- E2E on Linux and macOS reports without failing the workflow until a run there is green. Only
  Windows has been verified against a real browser.

## 2.0.0

Drive the user's open Edge from any agent: MCP server, `--json` CLI, and a Python client that
share one daemon, with element targeting by snapshot refs instead of fuzzy text.

### Added
- `edge-bridge mcp`: stdio MCP server (protocol 2025-11-25) exposing 29 `edge_*` tools; `edge-bridge setup` registers it with Claude Code, Cursor, Windsurf, Gemini CLI, Codex CLI and VS Code; `edge-bridge mcp-config` prints the snippet.
- `snapshot`: accessibility-style page tree with `e<n>` refs (and `f<frame>e<n>` inside iframes, cross-origin included); every action accepts `ref`; `stale_snapshot`/`stale_ref` errors instead of misclicks; hidden file inputs listed.
- Sessions pin to the tab they first touch and carry `tab: {id, title, url}` on every result; `tab_new` with tab groups or a separate window; `back`, `forward`, `reload`.
- `select`, `upload` (native file chooser over CDP), `type` (per-key events), `screenshot` with jpeg quality, clip, and element capture without stealing focus.
- `wait` on text, selector, ref, url (regex), load, or network idle (WebSocket/EventSource excluded).
- `console`: recent console output, uncaught exceptions and dialogs per tab; `dialog` policy (accept, dismiss, manual).
- `check_radio` / `edge-bridge check-radio`: check radio buttons and checkboxes by selector, label text, or value; ported from v1.2.0 with full pointer+click event sequence.
- Visual highlight ring on the element about to be acted on; `--no-highlight` / `highlight:false`.
- Daemon: token on `/exec`, Host allowlist, Origin required on `/ws`, heartbeat, held command queue while the extension reconnects, frame-size cap, rotating log and pid file in the user data dir, `extension_outdated` message during store review lag, optional pairing (`--require-pairing`). REST aliases `/api/status`, `/api/tabs`, `/api/eval`.
- Hermetic test suite with a fake extension, real-Edge E2E suite, CI on Windows, Linux and macOS.

### Changed
- Extension renamed to "Edge Agent Bridge"; service worker split into modules.
- Python floor 3.10. Single source tree under `edge_agent_bridge/`; version only in `edge_agent_bridge/__init__.py`.
- `tab_close` requires an explicit `tabId`.

### Removed
- `login` and `open_file` verbs, root-level `edge.py`/`bridge.py` copies, HTTP long-poll fallback.

## 1.1.2
- WebSocket continuation frames and faster unmasking in the daemon.

## 1.1.1
- Verified author email; branding assets.

## 1.1.0
- First public release: WebSocket transport, CDP native input, CLI and Python API.
