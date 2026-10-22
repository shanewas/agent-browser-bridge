"""Stand-ins for the screen and the input driver, so tests never move a real cursor."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

from PIL import Image

from agent_browser_bridge.desktop import inputs
from agent_browser_bridge.desktop.screen import Monitor


class FakeScreen:
    def __init__(self, size=(1920, 1080), layout=None):
        self.size = size
        self._monitors = layout or [Monitor(1, (0, 0), size, True)]
        self.grabs = []

    def monitors(self):
        return list(self._monitors)

    def primary_monitor(self):
        return next(m for m in self._monitors if m.primary)

    def virtual_monitor(self):
        boxes = [m.box for m in self._monitors]
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[0] + b[2] for b in boxes)
        bottom = max(b[1] + b[3] for b in boxes)
        return Monitor(0, (left, top), (right - left, bottom - top), False)

    def screen_size(self):
        return self.size

    def attach_input_desktop(self):
        return True

    def cursor_overlay(self):
        return None

    def foreground(self):
        return {"title": "Untitled - Paint", "pid": 4242, "process": "mspaint.exe"}

    def grab(self, region=None, cursor=False):
        self.grabs.append((region, cursor))
        width, height = self.size if region is None else (region[2], region[3])
        return Image.new("RGB", (width, height), "white")


class FakeOverlay:
    def __init__(self):
        self.calls = []

    def show(self, text, kind="action", seconds=None):
        self.calls.append(("show", text, kind, seconds))

    def idle(self):
        self.calls.append(("idle",))

    def record(self, text):
        self.calls.append(("record", text))

    def close(self):
        self.calls.append(("close",))


class FakeHooks:
    """Stands in for the low-level hooks: tests push the events a person would make."""

    instances = []

    def __init__(self, sink):
        self.sink, self.running = sink, False
        FakeHooks.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def push(self, kind, **data):
        self.sink(kind, data)


def three_monitors():
    """The layout that caught the single-display assumption: primary in the middle,
    one display to its left at a negative offset, one to its right."""
    return [Monitor(1, (-1920, 0), (1920, 1080), False),
            Monitor(2, (0, 0), (1920, 1080), True),
            Monitor(3, (1920, 0), (1920, 1080), False)]


class FakeKeyboard:
    def __init__(self):
        self.calls = []
        self.abort = False
        self.cursor = (0, 0)

    def abort_requested(self):
        return self.abort

    def cursor_position(self):
        return self.cursor

    def move(self, x, y):
        self.cursor = (x, y)
        self.calls.append(("move", x, y))

    def click(self, button="left", count=1):
        self.calls.append(("click", button, count))

    def mouse_down(self, button="left"):
        self.calls.append(("mouse_down", button))

    def mouse_up(self, button="left"):
        self.calls.append(("mouse_up", button))

    def drag(self, start, end, button="left", through=()):
        self.calls.append(("drag", start, end, button, list(through)))

    def scroll(self, direction, amount):
        self.calls.append(("scroll", direction, amount))

    def type_text(self, text):
        self.calls.append(("type_text", text))

    # Key strings are parsed for real, so a fake run rejects what a real one would.
    def press(self, combo, repeat=1):
        inputs.parse_combo(combo)
        self.calls.append(("press", combo, repeat))

    def hold(self, combo, duration):
        inputs.parse_combo(combo)
        self.calls.append(("hold", combo, duration))

    @contextlib.contextmanager
    def modifiers_held(self, text):
        inputs.parse_modifiers(text)
        self.calls.append(("modifiers_down", text))
        try:
            yield
        finally:
            self.calls.append(("modifiers_up", text))
