# Agent Browser Bridge

[![PyPI - Version](https://img.shields.io/pypi/v/agent-browser-bridge?color=0078D4&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/agent-browser-bridge/)
[![Edge Add-ons](https://img.shields.io/badge/Edge%20Add--ons-v2.6.1-0078D4?logo=microsoftedge&logoColor=white)](https://microsoftedge.microsoft.com/addons/detail/agent-browser-bridge/dfkieodkfepoidihjapiggpjmfapanpd)
[![Chrome Web Store](https://img.shields.io/chrome-web-store/v/lknjdleggnheadkkjfngphdbcglalmmg?color=4285F4&logo=googlechrome&logoColor=white&label=Chrome%20Web%20Store)](https://chromewebstore.google.com/detail/lknjdleggnheadkkjfngphdbcglalmmg)
[![GitHub Release](https://img.shields.io/github/v/release/shanewas/agent-browser-bridge?color=2ea44f&logo=github&label=Release)](https://github.com/shanewas/agent-browser-bridge/releases/latest)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/agent-browser-bridge?color=3776AB&logo=python&logoColor=white)](https://pypi.org/project/agent-browser-bridge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/shanewas/agent-browser-bridge/blob/main/LICENSE)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/shanewas)

Let an AI agent drive the Microsoft Edge window you already have open, on the active tab on your screen. On Windows the same daemon also drives the desktop itself: screenshots, mouse and keyboard for the dialogs and native apps a browser extension cannot reach.

Automating a clean browser profile drops existing cookies and auth state. Agent Browser Bridge attaches to your running browser instead, keeping active logins and extensions available while sending trusted Chrome DevTools Protocol events directly to the page.

A single background daemon provides the MCP server, CLI, and Python library.

Tested on Microsoft Edge. The extension uses standard MV3 and speaks CDP, so other Chromium browsers may work, but Edge is the only tested target.

---

## Install

```bash
pip install agent-browser-bridge
```

Python 3.10 or newer, with no third-party packages at install time or runtime. The optional `desktop` extra adds Pillow for the Windows desktop half (`pip install "agent-browser-bridge[desktop]"`).

### Windows

When `agent-bridge` isn't recognized after install (Python's `Scripts` folder isn't on your PATH), run commands with `py -m`:

```powershell
py -m agent_browser_bridge daemon start
py -m agent_browser_bridge setup
```

To add `Scripts` to your user PATH automatically, run `py -m agent_browser_bridge setup --fix-path` and reopen your terminal. If you're using `pipx`, run `pipx ensurepath`. To check launcher and environment health, run `py -m agent_browser_bridge doctor`.

Then install the companion extension directly from the
**[Microsoft Edge Add-ons store](https://microsoftedge.microsoft.com/addons/detail/agent-browser-bridge/dfkieodkfepoidihjapiggpjmfapanpd)** (**Agent Browser Bridge**) or the **[Chrome Web Store](https://chromewebstore.google.com/detail/lknjdleggnheadkkjfngphdbcglalmmg)**, or load the extension from the local folder while developing:

```bash
agent-bridge extension open     # opens the bundled extension directory
```

Turn on **Developer mode** at `edge://extensions`, select the opened extension folder, and pick that
directory. Start the daemon and verify Edge connected:

```bash
agent-bridge daemon start
agent-bridge daemon status
```

```text
Daemon: running on 127.0.0.1:18999 (PID 24188)
Extension: 2.6.1 (connected)
```

CLI commands and Python's `with Edge()` start the daemon on first use if it isn't running, but
`daemon start` brings the extension online right away.

### Upgrading from Edge Agent Bridge

`pip install -U edge-agent-bridge` moves an existing install over: it installs
`agent-browser-bridge` and leaves a small compatibility shim so `edge-bridge`, `edge`,
`python -m edge_agent_bridge` and `from edge_agent_bridge import Edge` keep working.
From then on, upgrade with `pip install -U agent-browser-bridge`. Nothing else changes:
same port, same data directory, same MCP server key and tool names, and the store
extension updates in place under its new name.

---

## Wire it into an agent

```bash
agent-bridge setup
```

That detects the MCP clients on your machine and registers the server with each one. Claude Code,
Cursor, Windsurf, Gemini CLI, Codex CLI and VS Code are covered. JSON config files are backed up
before they're touched, and a file that doesn't parse is left alone and reported.

To see the config without writing anything:

```bash
agent-bridge mcp-config --client claude
agent-bridge mcp-config --client cursor
```

The server speaks stdio JSON-RPC and exposes 34 tools, all prefixed `edge_` so they don't collide
with Playwright MCP in a mixed setup. [`docs/agent-guide.md`](docs/agent-guide.md) is written for
the agent rather than for you: snapshot first, act by ref, re-snapshot after navigation.

---

## Snapshot and refs

The agent reads the page as a tree of roles, names and current values, each interactive node
carrying a short id:

```text
$ agent-bridge snapshot
tab 1459 "Files - Example App" http://localhost:8080/files
page "Files - Example App" url=http://localhost:8080/files
- heading "Files" level=1
- textbox "Search" [e1] value=""
- button "Upload" [e2]
- file "Choose file" [e3] hidden
- table "Files"
  - row "12080 spec.pdf 2026-09-01" [e4]
    - button "Open" [e5]
```

Every action that takes a target accepts one of those refs, so `click e5` hits the button that was
actually named in the tree. A ref that no longer resolves returns `stale_ref`, and a ref from a
snapshot taken before the last navigation returns `stale_snapshot`. Neither one silently clicks
something else, which is the failure mode that makes text matching unusable on dense pages.

Values come back too, which is what makes "fill in the rest of this form" work: the agent can see
which fields you already filled. Passwords are masked. Hidden `input[type=file]` nodes are listed
anyway and tagged `hidden`, because upload buttons almost always hide the real input. Inside an
iframe a ref carries its frame, as in `f31e1`, including cross-origin frames.

---

## Python

```python
from agent_browser_bridge import Edge

with Edge() as browser:
    snap = browser.snapshot()
    print(snap["text"])

    browser.fill("e1", "spec")
    browser.click("e5")
    browser.wait(text="Opened")
    print(browser.console())
```

The daemon starts on first use. `Edge` pins itself to the tab of its first result, so a later call
can't drift onto a different tab because you switched windows in the meantime. Bare CLI commands share one daemon-side pin instead: `switch` and `new` set it, tab-less commands keep acting on that tab, and `--tab` stays a one-shot override; several agents at once still need one session each. Methods return the
raw result dict and never raise on a failed action: check `success`, read `code` and `error`.

---

## Command line

```bash
agent-bridge snapshot                      # tree with refs; --full adds static text
agent-bridge snapshot --compact --max-nodes 50  # terse refs-only lines; 0 = unlimited
agent-bridge elements                      # interactive nodes with coordinates
agent-bridge click e5                      # ref, CSS selector or visible text
agent-bridge fill e1 "AI agents"
agent-bridge type "slow typing" --delay 30 # per-key events, for autocomplete widgets
agent-bridge key Enter
agent-bridge select e6 --label 日本語
agent-bridge upload e3 C:/tmp/spec.pdf    # WSL /mnt/<drive>/... paths translate automatically
agent-bridge wait --text Opened
agent-bridge wait --idle                   # no in-flight requests; ignores WebSockets
agent-bridge screenshot out.jpg --of e4
agent-bridge eval "document.title"         # prints the returned value (here: the page title)
agent-bridge console                       # console output, exceptions, dialogs
agent-bridge nav https://example.com
agent-bridge new https://example.com --group Agent
agent-bridge close --tab 1459
agent-bridge history search "quarterly report"  # search titles/URLs; --max-results, --start-time/--end-time
agent-bridge history delete https://example.com/page
agent-bridge group list                     # tab groups; --window-id filters
agent-bridge group move 1459 1460 --title Agent --color blue   # omit --group-id for a new group
agent-bridge group ungroup 1459
agent-bridge batch '[{"action":"click","target":"e5"},{"action":"sleep","ms":50},{"action":"fill","target":"e1","text":"spec"}]'
agent-bridge run steps.json --stop-on-error  # batch from a file; one JSON line per step
agent-bridge session start --name agent-a  # prints sessionToken=<uuid>; --new always mints
agent-bridge session list                  # named pins; prune drops closed-tab sessions
agent-bridge session status --session <uuid>
agent-bridge session stop --session <uuid>
agent-bridge click e5 --session <uuid>   # session pin; --tab is a one-shot override
agent-bridge click e5 --no-fallback      # disable the ref→text→scan→coords ladder
agent-bridge doctor                      # diagnose daemon, extension, token, pairing
agent-bridge logs --tail 50              # daemon log (--follow streams)
agent-bridge daemon stop                 # only if /status confirms bridge.pid; --force for a hung daemon
```

`--json` prints the raw result as a single line and sets the exit code: 0 on success, 1 when the
action failed, 2 when the daemon is unreachable, 3 on a usage error. `--no-highlight` turns off the
ring drawn around the element about to be acted on.

Each CLI call pays roughly 600 ms of Python startup. For anything repetitive use MCP, the Python
API, or `run` (one process for a whole step file).

---

## Desktop (Windows)

```bash
pip install "agent-browser-bridge[desktop]"
```

The daemon drives the Windows desktop through the same token and port as the browser. An agent
takes a screenshot, reads it, acts with coordinates read off that image, then takes another
screenshot to confirm. OS file dialogs, installers, native applications and pages the extension
cannot attach to all become reachable.

```bash
agent-bridge desktop shot                  # writes <data dir>/shot.png, prints view/display/scale
agent-bridge desktop shot look.png --monitor 2 --region 100,80,400,120   # one part, for small text
agent-bridge desktop screen                # list displays (numbered left to right) without capturing
agent-bridge desktop click 640,360 --button right --count 2 --modifiers ctrl
agent-bridge desktop move 640,360          # reveal a hover state, then shot, then click
agent-bridge desktop drag 100,100 400,300
agent-bridge desktop scroll down 3 --at 640,360
agent-bridge desktop type "hello"
agent-bridge desktop key ctrl+s --repeat 2
agent-bridge desktop hold shift 2
agent-bridge desktop wait 1.5
agent-bridge desktop cursor                # where the pointer is, in both spaces
```

MCP tools: `desktop_shot`, `desktop_screen`, `desktop_cursor`, `desktop_click`, `desktop_move`,
`desktop_drag`, `desktop_scroll`, `desktop_type`, `desktop_key`, `desktop_hold`. `desktop_shot`
returns the image inline plus the coordinate space. Python: `Edge().send("desktop_click", {"at": [640, 360]})`.

Coordinates are **view pixels**: the pixel space of the last `shot` of that display, which is
scaled down to a 1280 long edge by default. The daemon scales them back up; never convert
yourself. A point outside the view is refused, not clamped. Every shot result carries `space`
(`view`, `display`, or `region`); a region shot is a crop with its own origin and says not to
click from it. Pass `--space display` to use real display pixels. Each display has its own view
starting at 0,0; `--monitor all` captures every display as one wide image for finding things,
then capture the one display before clicking.

Windows only: elsewhere every desktop action answers `desktop_unsupported`, and without Pillow
`desktop_unavailable`. An elevated daemon cannot send input into unelevated windows (Windows
UIPI), so run the daemon from a normal shell; `agent-bridge doctor` reports both halves.

---

## Security model

The trust boundary is your OS user account. Anything running as you can already read your files and
your browser profile, so the daemon doesn't try to defend against it.

- The daemon binds `127.0.0.1` only, and checks the `Host` header against the loopback names so a
  DNS rebinding attempt gets a 421 rather than a command.
- `/exec` requires the token written to the data directory at first start, mode 0600 on POSIX.
  A request carrying an `Origin` or `Sec-Fetch-*` header is refused outright, so a page you are
  browsing can't reach the daemon even if it guesses the token.
- `/ws` requires an `Origin` beginning `chrome-extension://`, which keeps stray local clients off
  the extension channel, though a second OS user on a shared machine could forge that header. If that is
  your situation, run `agent-bridge daemon start --require-pairing` and paste the token into the
  extension popup once; unpaired sockets are then rejected.
- WebSocket frames above 16 MB close the connection, and the socket carries a read timeout, so a
  local client can't make the daemon buffer without bound or pin a worker thread forever.
- Nothing leaves the machine: no telemetry, no outbound requests of any kind.

`eval` runs arbitrary JavaScript in the page with your session. It's there on purpose, and it's
the reason to think about which agent you hand this to. Actions that destroy state need an explicit
tab id: `tab_close` without one is an error rather than a guess.

The extension asks for `debugger`, which is what makes input trusted and screenshots possible.
While it is attached, Edge shows its "is debugging this browser" bar under a Chromium policy
no extension can dismiss.

---

## Measured benchmarks

Against live Edge tabs, from `tests/e2e/test_benchmark.py`. These are one machine's numbers;
the timings move with the host, so the suite treats them as advisory and gates on the
correctness assertions instead. `EDGE_BRIDGE_BENCH_STRICT=1` makes the timings gate too.

| Gate | What | Conditions | Result |
| :--- | :--- | :--- | :--- |
| BM-01 | WebSocket latency | 200 sequential calls | P50 9.09 ms, P95 14.35 ms |
| BM-02 | Action batching | 50 ops batched against sequential | 7.9x, 140 ms against 1103 ms |
| BM-03 | Native click burst | 100 rapid CDP clicks | 100/100 with `isTrusted: true` |
| BM-04 | Typing integrity | 55 chars of Unicode, symbols and Japanese | byte-exact in 216 ms |
| BM-05 | CSS `:hover` cascade | multi-tier pure CSS dropdown | `sub1` then `sub2` opened, target clicked |
| BM-06 | CDP mouse drag | 300 px over 15 steps | dropped in 612 ms |
| BM-07 | DOM scanner | 3,000 synthetic elements | 2,253 interactive found in 134.8 ms |
| BM-08 | Screenshot | viewport capture and base64 decode | 111.2 ms average, 99.8 KB |
| BM-09 | Tab query | open tabs and active tab id | 12 tabs in 11.9 ms |

```bash
python -m pytest tests/e2e/test_benchmark.py -q -s
```

---

## Development

```bash
python -m pytest -q -m "not e2e"   # hermetic, no browser needed
python -m pytest -q -m e2e         # drives a real Edge
python scripts/build_extension.py  # syncs the manifest version, zips to dist/
```

The hermetic suite runs a real daemon against a fake extension over a real WebSocket, so it covers
the wire protocol without Edge installed. CI runs it on Windows, Linux and macOS and the E2E suite
against a real Edge on Windows; releases are automated, see [`docs/release.md`](docs/release.md).

---

## Support

Built by Shanewas Ahmed. If it saves you time, sponsorship funds keeping the CDP handlers current
with Chromium releases:

- [GitHub Sponsors](https://github.com/sponsors/shanewas)

---

## License

MIT. See [LICENSE](LICENSE).
