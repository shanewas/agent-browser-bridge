# Recipes

Short playbooks for flows that cost real sessions. All assume a pinned
`--tab` or `--session` so a tab the user switches to never receives commands.

## Shadow-DOM upload (Salesforce Lightning, custom elements)

`upload` pierces open shadow roots and temp-reveals hidden inputs itself:

```bash
agent-bridge upload "Choose File" C:\path\cv.docx --tab 1361124478
```

Rules that still hold: target the label or button near the input; JS
`.click()` never opens a native dialog, only a trusted bridge click does.
If `upload` reports `no_file_input`, the input lives in a closed shadow
root or is created on click — click the button first, then retry.

## Long JS without shell quoting

```bash
agent-bridge eval --file probe.js --tab 1361124478
cat probe.js | agent-bridge eval --file - --tab 1361124478
```

`--file` also avoids the 8 KiB CLI arg ceilings on some shells.

## OS file dialog via doppelhand (last resort)

When no DOM input exists at all (custom dropzone with no `<input type=file>`):

```python
from agent_browser_bridge import Edge, Doppelhand
edge = Edge(tab_id=1361124478)
edge.click("Upload resume")          # trusted click opens the OS dialog
dh = Doppelhand(token="...")         # or DOPPELHAND_TOKEN env
dh.type_file_and_confirm("cv.docx")  # types name, presses Return
```

Windows only: the doppelhand server runs on Windows. Calls return a result
dict and never raise; a missing server returns `doppelhand_unreachable`, a server
that doesn't answer in time `doppelhand_timeout`.

Doppelhand mutating calls require POST (the client does this); coords are in
view space from `/screen`. Verify with `shot()` after any coord click.

## Tight captcha chains

Captcha tokens expire in ~2 minutes. Chain solve→submit in one `run` call
instead of separate CLI invocations:

```bash
agent-bridge run steps.json --tab 1361124478
```

where `steps.json` is `[{action, params}, ...]` (use `-` for stdin).
