"""The overlay's message state, without a window: the thread is stubbed out."""
import ctypes
import sys
import time

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


def test_idle_keeps_a_notice_shorter_than_the_fade(strip):
    strip.show("quick", "notify", 0.5)
    strip.idle()
    assert strip._until <= time.monotonic() + 0.5


@pytest.mark.skipif(sys.platform != "win32", reason="a real Win32 window class")
def test_closing_the_strip_unregisters_its_window_class():
    from ctypes import wintypes
    overlay = ov.Overlay()
    if not overlay._hwnd:
        pytest.skip("no window on this desktop")
    overlay.close()
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    # 0 means the class is already gone; anything else means this call just removed it.
    assert user32.UnregisterClassW(ov.CLASS_NAME, kernel32.GetModuleHandleW(None)) == 0
