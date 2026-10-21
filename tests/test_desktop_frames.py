"""The held-frame path: what happens when duplication works, and when it will not."""

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from agent_browser_bridge.desktop import executor as executor_module
from agent_browser_bridge.desktop.errors import ActionError
from agent_browser_bridge.desktop.executor import Executor
from tests.desktop_fakes import FakeKeyboard, FakeScreen, three_monitors


class FakeFrames:
    """Stands in for held duplications, including displays that refuse to be captured."""

    def __init__(self, refuse=(), broken=()):
        self.refuse = set(refuse)
        self.broken = set(broken)
        self.asked = []

    def frame(self, monitor):
        self.asked.append(monitor.index)
        if monitor.index in self.broken:
            raise ActionError("frame source fell over")
        if monitor.index in self.refuse:
            return None
        image = Image.new("RGB", monitor.size, "white")
        image.putpixel((7, 9), (10, 20, 30))  # something to identify the frame by
        return image


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(executor_module, "SETTLE_SECONDS", 0)


def build(frames=None, monitor=None, cursor=False):
    screen = FakeScreen(layout=three_monitors())
    return Executor(max_edge=1280, monitor=monitor or three_monitors()[1],
                    screen=screen, keyboard=FakeKeyboard(), cursor=cursor,
                    frames=frames), screen


def test_a_held_frame_is_used_instead_of_asking_the_screen():
    frames = FakeFrames()
    ex, screen = build(frames)
    image = ex.frame()
    assert image.getpixel((7, 9)) == (10, 20, 30)
    assert screen.grabs == []  # GDI never touched
    assert frames.asked == [2]
    assert ex.last_source == "duplication"


def test_a_display_that_refuses_falls_back_to_the_slow_path():
    ex, screen = build(FakeFrames(refuse=[2]))
    ex.frame()
    assert screen.grabs == [((0, 0, 1920, 1080), False)]
    assert ex.last_source == "gdi"


def test_with_no_frame_source_at_all_the_slow_path_is_used():
    ex, screen = build(None)
    ex.frame()
    assert screen.grabs and ex.last_source == "gdi"


def test_a_region_is_cropped_out_of_the_held_frame():
    """A crop off a frame already in hand costs nothing, where GDI would capture again."""
    frames = FakeFrames()
    ex, screen = build(frames, monitor=three_monitors()[2])  # origin at 1920,0
    crop = ex.frame((1920 + 100, 50, 200, 120))
    assert crop.size == (200, 120)
    assert screen.grabs == []


def test_the_pointer_is_pasted_onto_a_held_frame():
    pointer = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    ex, _ = build(FakeFrames(), cursor=True)
    ex.screen.cursor_overlay = lambda: (pointer, (300, 400))
    image = ex.frame()
    assert image.getpixel((305, 405)) == (255, 0, 0)


def test_a_pointer_that_cannot_be_drawn_leaves_the_frame_alone():
    ex, _ = build(FakeFrames(), cursor=True)
    ex.screen.cursor_overlay = lambda: None
    assert ex.frame().getpixel((7, 9)) == (10, 20, 30)


def test_the_frame_source_gives_up_on_a_display_that_keeps_failing():
    """One refusal must not cost a capture on every later screenshot."""
    from agent_browser_bridge.desktop.duplication import FrameSource

    import agent_browser_bridge.desktop.duplication as duplication

    source = FrameSource()
    monitor = three_monitors()[0]
    attempts = []

    def refuse(_monitor):
        attempts.append(_monitor.index)
        raise ActionError("refused")

    original = duplication.Duplicator
    duplication.Duplicator = refuse
    try:
        assert source.frame(monitor) is None
        assert source.frame(monitor) is None
        assert attempts == [monitor.index], "a refused display was tried twice"
        assert monitor.index in source._refused
    finally:
        duplication.Duplicator = original


def test_interface_ids_are_built_without_ole32(monkeypatch):
    """Off Windows there is no ole32; the IIDs must come from the stdlib so the module imports."""
    import uuid
    import agent_browser_bridge.desktop.duplication as duplication
    monkeypatch.setattr(duplication, "_ole32", None)
    guid = duplication._guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c")
    assert bytes(guid) == uuid.UUID("54ec77fa-1377-44e6-8c32-88fd5f44c84c").bytes_le
    assert bytes(duplication.IID_IDXGIDevice) == bytes(guid)
