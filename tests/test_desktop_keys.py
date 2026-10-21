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
