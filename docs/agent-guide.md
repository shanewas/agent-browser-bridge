# Driving Edge through Edge Agent Bridge: a guide for agents

You control the user's real Microsoft Edge, on the tab they are looking at, with their cookies
and sessions. Every action you take is visible to them. Work the way a careful colleague
sitting next to them would.

## The loop

1. `edge_snapshot` first. It returns the page as a tree of roles and names with reference IDs:

   ```
   tab 1459 "Files - Example App" http://localhost:8080/files
   page "Files - Example App" url=http://localhost:8080/files
   - heading "Files" level=1
   - textbox "Search" [e1] value=""
   - button "Upload" [e2]
   - table "Files"
     - row "12080 spec.pdf 2026-09-01" [e4]
       - button "Open" [e5]
   ```

2. Act by ref: `edge_click {ref: "e5"}`, `edge_fill {ref: "e1", text: "spec"}`. Refs are exact;
   text targets (`target: "Open"`) are a fallback when you have no fresh snapshot. Only refs reach
   inside an iframe: a ref carries its frame (`f31e1`), while a text or selector target is looked
   up in the main frame alone and comes back `target_not_found`. If a control you can see in the
   snapshot under an `iframe` line will not resolve, use its ref.
3. After anything that navigates or re-renders, snapshot again. Refs from an old snapshot fail
   with `stale_snapshot`; a removed element fails with `stale_ref`. Both mean: snapshot again.
   Wide scans cost less with `mode: "compact"` (one terse line per ref, no values); switch to
   `interactive` before filling forms. Snapshots truncate after `maxNodes` (default 400,
   0 = unlimited) and say so: `truncated: true` with `total` nodes seen.
4. Prefer `edge_wait` over sleeping: `{text: "Saved"}`, `{url: "/done"}`, `{load: true}`, or
   `{idle: true}` for pages that fetch after load. A timeout tells you what was still loading.
5. When a page misbehaves, read `edge_console`: it holds recent console output, uncaught
   exceptions, and any dialogs the bridge answered for you.

## Read the `tab` line before acting

Every result carries `tab: {id, title, url}`. If the user didn't name a page, confirm from the
first snapshot that you are on the one they meant before you type into it. Your session pins
to that tab afterwards, so a tab the user switches to will not receive your commands.

Without a tab id or a session, commands follow the daemon's one tokenless pin: `switch` and `new` set it, and while it is empty the first successful tab-bound result sets it. Once pinned, a tokenless command goes to the pinned tab, not the focused one; an explicit tab id is a one-shot override that leaves the pin alone. When the pinned tab closes outside the bridge, tokenless calls fail with `tab_closed` until the next `switch` or `new`; closing it through the bridge clears the pin. All tokenless clients share this one pin, so several agents still need one session each. Before any pin exists, a command goes to the active tab of the last focused Edge window
that runs the extension. An InPrivate window or a window of another Edge profile doesn't count,
so while the user works in one of those, that command lands in a different window. Pass the tab
id or work inside a session there.

## Filling forms the user started

Snapshots include current values (`value="..."`, `checked`, selected option). Fill only the
empty fields unless asked to change one. Passwords show as `value="••••"`; never ask for or
echo the real value.

## Things that are easy to get wrong

- Coordinates are a last resort. Snapshots and refs already know where things are.
- `edge_type` sends real key events one character at a time (use it for autocomplete
  widgets); `edge_fill` replaces the value in one step and is faster for plain inputs.
- `<select>` needs `edge_select`, not `edge_fill`.
- Hidden file inputs still appear in the snapshot with `hidden`; `edge_upload` on their ref or on
  the styled button next to them works. Paths must be absolute; from WSL, `/mnt/<drive>/...`
  paths translate to Windows form automatically.
- Coordinates never scroll: a ref click scrolls its target into view, an explicit `x`/`y`
  clicks exactly where given. A target hidden under a sticky overlay fails with
  `click_covered` instead of misclicking.
- `edge_tab_close` needs an explicit `tabId`. The bridge will not guess which tab to close.
- `edge_eval` runs JavaScript with full page access. Use it for reading state you cannot get
  from a snapshot, not as a substitute for clicks (page code sees `isTrusted: false` events).
- A native `confirm()`/`alert()` is accepted automatically by default. Set
  `edge_dialog {policy: "manual"}` when the user must decide; the next action then returns
  `dialog_open` until you call `edge_dialog` with `accept` or `dismiss`.

## Errors

Every failure is `{success: false, code, error}`. Codes you will meet most:

| code | meaning | what to do |
|---|---|---|
| `stale_snapshot`, `stale_ref` | refs are from an old page state | snapshot again |
| `target_not_found` | nothing matched within the timeout | snapshot, pick a ref |
| `tab_not_found` | the pinned tab is gone | tokenless and session: expect sticky `tab_closed` until `switch`/`new` |
| `tab_not_scriptable` | the tab shows a browser page (`edge://`, `chrome://`, `view-source:`, an extension page) that extensions can't script | ask the user which tab they mean and `edge_tab_switch` to it, or `edge_navigate` this tab first |
| `extension_offline` | Edge is closed or the extension is off | tell the user to open Edge and check the popup |
| `extension_outdated` | the extension predates the daemon | tell the user to update it |
| `dialog_open` | a native dialog is waiting | `edge_dialog` accept or dismiss |
| `timeout` | the wait condition never held, or the browser didn't answer in time | after `edge_wait`, report what `inflight` or `url` shows; after anything else, check the page before retrying, because the action may have run |
| `daemon_unreachable` | nothing answers on the daemon port | `edge-bridge daemon start`, then retry |
| `connection_lost` | the daemon got the command, then the connection dropped before an answer came back (daemon restart or crash) | check the page before retrying, because the action may have run; run `edge-bridge status` if it keeps happening |
| `tab_busy` | another call holds the tab lock | backoff 250ms·2ⁿ+jitter ≤4×, then surface |
| `tab_closed` | the session or tokenless pin points at a closed tab | `switch` or `new` to re-pin |
| `stale_ref` | ref/frame no longer valid | ladder runs automatically; on `exhausted_fallback`, snapshot fresh |
| `unknown_session` | bad/expired token (incl. after daemon restart) | client re-mints once and re-pins your last tab; if repeated, `session start` |
| `click_covered` | sticky overlay covers the target | dismiss it or scroll the target clear, then retry |
| `file_not_found` | upload path missing | check the path; the error names both the given and translated form |
| `file_access_required` | extension lacks file URL access | tell the user to turn on "Allow access to file URLs" for Edge Agent Bridge in edge://extensions > Details |
| `bad_name` | session name outside `[A-Za-z0-9_-]{1,40}` | pick a conforming name |
| `no_log` | daemon never started here | `edge-bridge daemon start` |
| `focus_lost` | target never took focus | snapshot, check overlays, coords click |
| `focus_unverifiable` | frame context unresolvable | retry, coords-only click, or abort |
| `focus_stolen` | focus moved mid-type | resume with `remaining` |
| `deadline_exceeded` | extension passed `deadlineMs` | retry (locks/deadlines reset), split long writes |
| `write_mismatch` | readback differs after retry | report expected vs readback, stop |
| `exhausted_fallback` | ref→text→scan→coords all failed | read `tried`, snapshot, new approach |
| `history_failed` | history query failed in the extension | report `error`, retry once |
| `group_failed` | tab-group call failed in the extension | report `error`, check tab IDs |

## Setup (once per machine)

```
pip install edge-agent-bridge
edge-bridge setup          # registers the MCP server with the agent clients it finds
edge-bridge status         # daemon, extension, and pairing state
edge-bridge doctor         # when anything fails: checks everything, prints fixes
```

The extension comes from the Edge Add-ons store ("Edge Agent Bridge"); developers can load
`edge-bridge extension path` unpacked instead.

## Sessions: one tab per agent (v2.1.0+)

CLI invocations are stateless, so each agent holds a daemon-side session pinned to one tab:

```
edge-bridge session list             # what exists before you mint another
edge-bridge session start --name agent-a   # prints sessionToken=<uuid>
export EDGE_BRIDGE_SESSION=<uuid>    # or pass --session <uuid> per call
edge-bridge switch 1459              # pin this session to your tab
edge-bridge session status           # shows pin state
edge-bridge session stop             # drop it server-side
edge-bridge session prune            # drop closed-tab sessions
```

Forgetting `--tab` is harmless inside a session: the daemon injects your pinned tab. An explicit `--tab` is a one-shot override and never changes the pin. Only `switch` and `new` re-pin. MCP clients get an implicit session per process automatically. If your tab closes, the session goes sticky `tab_closed` — every tab-scoped call fails until `switch`/`new`. A daemon restart no longer strands you: the client re-mints and re-pins your last tab (if that tab is gone, the next call resolves the active tab like a fresh session). There is no shared default session: two agents MUST use distinct tokens or they share one pin.

Multi-agent etiquette: own tab each. Per-tab locks serialize writers as defense-in-depth, not as an excuse to share a tab.

## History and tab groups (v2.2.0+)

`edge_history_search {text, maxResults?, startTime?, endTime?}` searches titles and URLs
(`startTime`/`endTime` are ms since epoch); `edge_history_delete {url}` removes one entry.
`edge_group_list {windowId?}` lists tab groups; `edge_group_move {tabIds, groupId?, title?, color?}`
moves tabs into an existing group, or a new one when `groupId` is omitted;
`edge_group_ungroup {tabIds}` removes tabs from their groups. CLI mirrors:
`edge-bridge history search "query"`, `history delete <url>`, `group list [--window-id N]`,
`group move <tab ids> [--group-id N] [--title T] [--color C]`, `group ungroup <tab ids>`.

## Verified writes: `match`, `write_mismatch`, `focus_stolen`

`edge_fill`/`edge_type` verify every write. Results carry `written` (expected full value), `readback` (value read back after 100ms), and `match`:

- `match: true` — written value confirmed.
- `match: false` — the readback differs from `written`. Results also carry `field` (`input`, `textarea`, `contenteditable`, ...). A `fill` is retried once as a clearing fill unless `field` is `contenteditable`; a second mismatch returns `write_mismatch` with `{expected, readback, attempts: 2}`. `type` and contenteditable fills are never retried: they return `match: false` with `written` and `readback` and leave the field alone, because `type` assumes the caret sat at the end and a clearing fill would wipe the editor's markup. The CLI prints `Warning (write_mismatch): wrote ..., read back ...` to stderr and exits 1, so `&&` chains stop. Compare the two values; if the text is wrong, report both and STOP.
- Contenteditable comparisons ignore leading and trailing newlines and treat U+00A0 as a space.
- `match: "unknown"` — the value could not be read back (element gone or not readable), or the extension predates readback (check `edge-bridge status`: old `extension_version` means expected degradation, new version means broken readback). A retry that comes back unknown is returned as unknown, not `write_mismatch`. The client warns once about an old extension and continues unverified; the CLI prints `Warning: write not verified` to stderr and exits 0.

Focus is asserted before every write (≤3 tries) and rechecked after the 1st char and every 8 chars while typing:

- `focus_lost` — target never took focus. Snapshot, check overlays, try a coords click.
- `focus_unverifiable` — the target frame vanished mid-write. Retry, coords-only click, or abort.
- `focus_stolen` — the human (or page) moved focus mid-type. Resume by typing `remaining` (exactly `typedSoFar` chars already landed).
- At most 8 chars can land off-target on a steal (sampling residual, inherent).

## The fallback ladder and `tab_busy`

Ref-taking actions (`click`, `dblclick`, `rightclick`, `hover`, `fill`, `type` with target, `select`, `check-radio`, `upload`, `drag`) automatically retry ref → text/selector → fresh `elements` scan → coords from the scan match (exact name, else case-insensitive name, and only when exactly one element matches; substring matches never click), with at most 2 extra scans per call. A scan success reports `ladder.matched: {name, ref}` for the element acted on. Explicit `--x/--y` skips the ladder; `--no-fallback` disables it. Total failure returns `exhausted_fallback` with `tried: [{step, outcome}]` — read it, snapshot fresh, try a new approach.

`tab_busy` means another writer holds your tab's lock (the daemon waits up to 10s for it, never past the call's own timeout). Back off 250ms·2ⁿ with jitter, ≤4 retries, then surface or use another tab.
