import sys

import pytest

from agent_browser_bridge.desktop import inputs
from agent_browser_bridge.desktop.errors import ActionError

VK_CONTROL, VK_SHIFT, VK_RETURN, VK_DOWN = 0x11, 0x10, 0x0D, 0x28


def test_named_keys_resolve():
    assert inputs.resolve_key("Return")[0] == VK_RETURN
    assert inputs.resolve_key("Down")[0] == VK_DOWN
    assert inputs.resolve_key("F5")[0] == 0x74


def test_combination_splits_into_modifiers_and_key():
    modifiers, vk = inputs.parse_combo("ctrl+shift+s")
    assert modifiers[:2] == [VK_CONTROL, VK_SHIFT]
    assert vk == ord("S")


def test_trailing_plus_is_the_plus_key():
    modifiers, vk = inputs.parse_combo("ctrl++")
    assert modifiers == [VK_CONTROL]
    assert vk == inputs.resolve_key("plus")[0]


@pytest.mark.skipif(sys.platform != "win32", reason="VkKeyScanW")
def test_a_shifted_character_implies_shift():
    modifiers, _ = inputs.parse_combo("!")
    assert VK_SHIFT in modifiers


def test_unknown_names_are_rejected():
    with pytest.raises(ActionError):
        inputs.parse_combo("hyper+k")
    with pytest.raises(ActionError):
        inputs.resolve_key("Wingding")


def test_arrow_keys_are_flagged_extended():
    # Applications that read scan codes see Down as the numeric keypad's 2 without it.
    assert VK_DOWN in inputs._EXTENDED


def test_astral_characters_become_surrogate_pairs():
    assert len(inputs._code_units("a")) == 1
    assert len(inputs._code_units("\U0001F600")) == 2


def test_cursor_move_failure_names_the_scheduled_task_case(monkeypatch):
    pytest.importorskip("PIL")

    class NoCursor:
        def SetCursorPos(self, x, y):
            return 0

    monkeypatch.setattr(inputs, "_user32", NoCursor())
    monkeypatch.setattr("agent_browser_bridge.desktop.screen.attach_input_desktop", lambda: True)
    with pytest.raises(ActionError, match="scheduled task"):
        inputs.move(1, 2)


class RecordingUser32:
    """Keeps every input event; the virtual desktop is 1920x1080 at the origin."""

    def __init__(self):
        self.events = []

    def SetCursorPos(self, x, y):
        self.events.append(("cursor", x, y))
        return 1

    def GetSystemMetrics(self, index):
        return {76: 0, 77: 0, 78: 1920, 79: 1080}[index]

    def SendInput(self, count, array, size):
        for i in range(count):
            self.events.append(("input", array[i].mi.dwFlags, array[i].mi.dx, array[i].mi.dy))
        return count


def test_drag_moves_are_absolute_input_events_not_cursor_jumps(monkeypatch):
    # WinUI apps (Paint, Settings) read raw pointer input: SetCursorPos between the press and
    # the release draws nothing, so the stroke must be SendInput moves in absolute coordinates.
    stub = RecordingUser32()
    monkeypatch.setattr(inputs, "_user32", stub)
    monkeypatch.setattr(inputs.time, "sleep", lambda s: None)
    inputs.drag((0, 0), (100, 100), through=[(50, 0)])
    flags = [e[1] for e in stub.events if e[0] == "input"]
    moves = [e for e in stub.events if e[0] == "input" and e[1] & inputs.MOUSEEVENTF_MOVE]
    assert flags[0] == inputs.MOUSEEVENTF_LEFTDOWN and flags[-1] == inputs.MOUSEEVENTF_LEFTUP
    assert len(moves) == 16
    assert all(m[1] & inputs.MOUSEEVENTF_ABSOLUTE and m[1] & inputs.MOUSEEVENTF_VIRTUALDESK for m in moves)
    assert (moves[-1][2], moves[-1][3]) == (round(100 * 65535 / 1919), round(100 * 65535 / 1079))
    assert moves[7][2] == round(50 * 65535 / 1919) and moves[7][3] == 0
    # The release lands on the exact pixel: absolute moves can be a pixel off after rounding.
    assert stub.events[-2] == ("cursor", 100, 100)


class StubUser32:
    """Refuses the first `blocked` events, the way a background desktop does."""

    def __init__(self, blocked):
        self.blocked = blocked
        self.batches = []

    def SendInput(self, count, array, size):
        self.batches.append(count)
        allowed = max(0, count - self.blocked)
        self.blocked = max(0, self.blocked - count)
        return allowed


def test_refused_input_is_retried_without_repeating_what_got_through(monkeypatch):
    pytest.importorskip("PIL")
    stub = StubUser32(blocked=1)
    monkeypatch.setattr(inputs, "_user32", stub)
    monkeypatch.setattr("agent_browser_bridge.desktop.screen.attach_input_desktop", lambda: True)

    inputs._send(inputs._key_event(0x41, up=False), inputs._key_event(0x41, up=True))

    # Three of four events would mean one key press sent twice.
    assert stub.batches == [2, 1]


def test_input_that_stays_blocked_is_an_error(monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setattr(inputs, "_user32", StubUser32(blocked=99))
    monkeypatch.setattr("agent_browser_bridge.desktop.screen.attach_input_desktop", lambda: False)
    with pytest.raises(ActionError):
        inputs._send(inputs._key_event(0x41, up=False))


def test_modifier_string_parses():
    assert inputs.parse_modifiers("ctrl+alt") == [VK_CONTROL, 0x12]
    assert inputs.parse_modifiers(None) == []


def test_ascii_fallback_resolves_letters_without_user32(monkeypatch):
    monkeypatch.setattr(inputs, "_user32", None)
    assert inputs.resolve_key("a") == (0x41, [])
    assert inputs.resolve_key("A") == (0x41, [0x10])
    assert inputs.resolve_key("7") == (0x37, [])
    with pytest.raises(ActionError):
        inputs.resolve_key("!")
