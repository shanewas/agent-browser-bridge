"""The overlay's message state, without a window: the thread is stubbed out."""
import pytest

from agent_browser_bridge.desktop import overlay as ov


@pytest.fixture
def strip(monkeypatch):
    monkeypatch.setattr(ov.Overlay, "_window_loop", lambda self: None)
    return ov.Overlay()


def test_a_notice_during_a_recording_keeps_the_recording_visible(strip):
    strip.record("Recording demo: clicks and keys are being saved. Stop: agent-bridge desktop record stop")
    strip.show("form submitted, please check", "notify", 10)
    assert strip._kind == "record"
    assert strip._text.startswith("Recording demo") and "form submitted" in strip._text


def test_a_notice_outside_a_recording_is_blue(strip):
    strip.show("hello", "notify", 2)
    assert strip._kind == "notify" and strip._text == "hello"
    strip.record(None)
    strip.show("again", "notify", 2)
    assert strip._kind == "notify"
