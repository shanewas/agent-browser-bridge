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

## OS file dialog via the desktop tools (last resort, Windows)

When no DOM input exists at all (custom dropzone with no `<input type=file>`), let the page open
the OS dialog and drive it with the desktop half (`pip install "agent-browser-bridge[desktop]"`):

```python
from agent_browser_bridge import Edge
edge = Edge(tab_id=1361124478)
edge.click("Upload resume")                                   # trusted click opens the OS dialog
shot = edge.send("desktop_shot")                              # look: is the dialog up, where is the name box?
edge.send("desktop_type", {"text": r"C:\Users\me\cv.docx"})  # the name box has focus when the dialog opens
edge.send("desktop_key", {"combo": "Return"})
shot = edge.send("desktop_shot")                              # confirm the dialog closed
```

Results are plain dicts and never raise: off Windows the code is `desktop_unsupported`, without
Pillow `desktop_unavailable`. Coordinates for any `desktop_click` are view pixels of the last
`desktop_shot`; take a fresh shot after every change on screen.

## Tight captcha chains

Captcha tokens expire in ~2 minutes. Chain solve→submit in one `run` call
instead of separate CLI invocations:

```bash
agent-bridge run steps.json --tab 1361124478
```

where `steps.json` is `[{action, params}, ...]` (use `-` for stdin).
