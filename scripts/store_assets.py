"""Regenerate the store promo tiles and screenshots (Pillow, dev only).

    python scripts/store_assets.py

Writes four RGB PNGs into store-assets/ at the exact sizes the stores ask for.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "store-assets"
FONTS = Path(r"C:\Windows\Fonts")

BG = "#0b0b0c"
CARD = "#141416"
BORDER = "#26262a"
TEXT = "#f2f2f3"
MUTED = "#8b8b93"
ACCENT = "#4cc2ff"
GREEN = "#3fb950"
ICON_BG = "#000000"
TITLE = "Agent Browser Bridge"


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


BOLD = lambda s: font("segoeuib.ttf", s)  # noqa: E731
BODY = lambda s: font("segoeui.ttf", s)  # noqa: E731
MONO = lambda s: font("consola.ttf", s)  # noqa: E731


def canvas(w, h):
    img = Image.new("RGB", (w, h), BG)
    return img, ImageDraw.Draw(img)


def icon(img, x, y, size):
    box = Image.new("RGB", (size, size), ICON_BG)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=255)
    glyph = Image.open(ASSETS / "icon_128x128.png").convert("RGB").resize((size * 2 // 3, size * 2 // 3))
    box.paste(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
    img.paste(box, (x, y), mask)


def card(d, x, y, w, h):
    d.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=CARD, outline=BORDER)


def lines(d, x, y, text, f, fill, gap):
    for ln in text.split("\n"):
        d.text((x, y), ln, font=f, fill=fill)
        y += gap
    return y


def tile_small():
    img, d = canvas(440, 280)
    icon(img, 36, 44, 84)
    lines(d, 136, 56, "Agent Browser\nBridge", BOLD(30), TEXT, 36)
    d.rectangle((36, 160, 88, 163), fill=ACCENT)
    lines(d, 36, 182, "Let any AI agent drive the browser\nyou already have open.", BODY(17), MUTED, 24)
    img.save(ASSETS / "promo_tile_small_440x280.png")


def tile_large():
    img, d = canvas(1400, 560)
    icon(img, 80, 70, 120)
    d.text((238, 86), TITLE, font=BOLD(46), fill=TEXT)
    d.text((238, 142), "Drive the browser you already have open, from any AI agent.", font=BODY(22), fill=MUTED)
    cards = [
        ("Snapshot, then act by ref", "The page arrives as a tree of roles,\nnames and values with stable ids.\nAgents click e5, not fuzzy text."),
        ("MCP, CLI or Python", "One daemon behind all three.\nagent-bridge setup registers the\nserver with the clients you have."),
        ("Real input, real tab", "CDP dispatches trusted mouse and\nkeyboard events into the tab you\nare looking at, with your session."),
        ("Nothing leaves the machine", "Loopback only, token on every\ncommand, no telemetry. MIT."),
    ]
    for i, (head, body) in enumerate(cards):
        x = 80 + i * 316
        card(d, x, 250, 296, 230)
        d.rectangle((x + 24, 250, x + 76, 253), fill=ACCENT)
        d.text((x + 24, 286), head, font=BOLD(18), fill=TEXT)
        lines(d, x + 24, 328, body, BODY(15), MUTED, 24)
    img.save(ASSETS / "promo_tile_large_1400x560.png")


def chrome_frame(d, title, url):
    d.rectangle((0, 0, 1280, 76), fill="#111113")
    d.rounded_rectangle((16, 16, 210, 62), radius=6, fill="#1b1b1e")
    d.text((34, 32), title, font=BODY(15), fill=TEXT)
    d.rounded_rectangle((226, 18, 1056, 60), radius=6, fill="#161618", outline=BORDER)
    d.text((246, 32), url, font=BODY(15), fill=MUTED)


def terminal(d, x, y, w, h, label):
    card(d, x, y, w, h)
    for i in range(3):
        d.ellipse((x + 18 + i * 16, y + 16, x + 28 + i * 16, y + 26), fill="#3a3a40")
    d.text((x + 72, y + 13), label, font=BODY(14), fill=MUTED)
    d.line((x, y + 40, x + w, y + 40), fill=BORDER)


def screenshot_one():
    img, d = canvas(1280, 800)
    chrome_frame(d, "Files - Example App", "http://localhost:8080/files")
    d.text((50, 122), "Read the page as refs, then act on them", font=BOLD(26), fill=TEXT)
    d.text((50, 158), "One snapshot names every control. The agent clicks e5, never a guessed coordinate.", font=BODY(16), fill=MUTED)
    terminal(d, 50, 200, 740, 552, "agent-bridge")
    text = (
        "$ agent-bridge daemon status\n"
        "Daemon: running on 127.0.0.1:18999 (PID 24188)\n"
        "Extension: 3.0.0 (connected)\n\n"
        "$ agent-bridge snapshot\n"
        'tab 1459 "Files - Example App" http://localhost:8080/files\n'
        'page "Files - Example App" url=http://localhost:8080/files\n'
        '- heading "Files" level=1\n'
        '- textbox "Search" [e1] value=""\n'
        '- button "Upload" [e2]\n'
        '- file "Choose file" [e3] hidden\n'
        '- table "Files"\n'
        '  - row "12080 spec.pdf 2026-09-01" [e4]\n'
        '    - button "Open" [e5]\n\n'
        "$ agent-bridge fill e1 spec\n"
        "$ agent-bridge click e5\n"
        "$ agent-bridge wait --text Opened"
    )
    y = 256
    for ln in text.split("\n"):
        d.text((74, y), ln, font=MONO(15 if ln.startswith("$") else 14), fill=TEXT if ln.startswith("$") else MUTED)
        y += 21
    card(d, 826, 200, 404, 270)
    d.text((850, 230), TITLE, font=BOLD(18), fill=TEXT)
    d.ellipse((850, 268, 860, 278), fill=GREEN)
    d.text((870, 264), "Connected", font=BODY(15), fill=GREEN)
    d.text((850, 308), "Active tab", font=BODY(13), fill=MUTED)
    d.text((850, 332), "Files - Example App (1459)", font=BODY(15), fill=TEXT)
    d.text((850, 380), "Transport", font=BODY(13), fill=MUTED)
    d.text((850, 402), "ws://127.0.0.1:18999/ws", font=MONO(13), fill=TEXT)
    card(d, 826, 494, 404, 258)
    d.text((850, 524), "Refs beat text matching", font=BOLD(18), fill=TEXT)
    lines(d, 850, 560, "e1, e2, e5 stay valid until the page\nnavigates. A ref that no longer\nresolves fails loudly with\nstale_ref instead of clicking the\nwrong element.\n\nRefs inside an iframe carry their\nframe: f31e1.", BODY(14), MUTED, 23)
    img.save(ASSETS / "screenshot1_snapshot_refs_1280x800.png")


def screenshot_two():
    img, d = canvas(1280, 800)
    chrome_frame(d, "Files - Example App", "http://localhost:8080/files")
    d.text((50, 122), "One command wires it into your agent", font=BOLD(26), fill=TEXT)
    d.text((50, 158), "The MCP server exposes 34 edge_* tools over stdio. Setup writes the config for you.", font=BODY(16), fill=MUTED)
    terminal(d, 50, 200, 590, 552, "setup")
    left = [
        ("$ agent-bridge setup", TEXT), ("Found: Claude Code, Cursor, VS Code", MUTED),
        ("Added edge -> agent-bridge mcp", GREEN), ("", MUTED),
        ("$ agent-bridge mcp-config --client claude", TEXT), ("claude mcp add edge -- agent-bridge mcp", MUTED), ("", MUTED),
        ("$ agent-bridge mcp-config --client cursor", TEXT), ("{", MUTED), ('  "mcpServers": {', MUTED), ('    "edge": {', MUTED),
        ('      "command": "agent-bridge",', MUTED), ('      "args": ["mcp"]', MUTED), ("    }", MUTED), ("  }", MUTED), ("}", MUTED),
    ]
    y = 256
    for ln, col in left:
        d.text((74, y), ln, font=MONO(15 if ln.startswith("$") else 14), fill=col)
        y += 21
    terminal(d, 676, 200, 554, 552, "agent")
    calls = [
        ("edge_snapshot {}", "28 refs on Files - Example App"),
        ('edge_fill {ref: "e1", text: "spec"}', "filled, tab 1459"),
        ('edge_click {ref: "e5"}', "isTrusted click at (612, 344)"),
        ('edge_wait {text: "Opened"}', "matched in 240 ms"),
        ("edge_console {}", "2 entries, 0 exceptions"),
        ('edge_screenshot {of: "e4"}', "jpeg 41 KB"),
    ]
    y = 256
    for call, res in calls:
        d.text((700, y), call, font=MONO(14), fill=ACCENT)
        d.text((712, y + 21), res, font=MONO(13), fill=MUTED)
        y += 63
    img.save(ASSETS / "screenshot2_mcp_setup_1280x800.png")


if __name__ == "__main__":
    tile_small()
    tile_large()
    screenshot_one()
    screenshot_two()
    print("wrote 4 images to", ASSETS)
