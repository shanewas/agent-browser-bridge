"""The recorder: a person's clicks and keys become events, shots and a SKILL.md."""
import json

import pytest

pytest.importorskip("PIL")

from agent_browser_bridge.desktop.recorder import (  # noqa: E402
    MAX_EVENTS, Recorder, write_skill,
)
from tests.desktop_fakes import FakeHooks, FakeOverlay, FakeScreen  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def parts(tmp_path):
    FakeHooks.instances.clear()
    screen, overlay, clock = FakeScreen(), FakeOverlay(), Clock()
    rec = Recorder(screen, home=tmp_path, hooks_factory=FakeHooks, overlay=overlay, clock=clock)
    return rec, screen, overlay, clock, tmp_path


def _hooks():
    return FakeHooks.instances[-1]


def test_start_creates_the_directory_and_the_banner(parts):
    rec, _, overlay, _, home = parts
    r = rec.start("demo", "fill the form")
    assert r == {"recording": True, "name": "demo", "dir": str(home / "recordings" / "demo")}
    assert (home / "recordings" / "demo").is_dir() and _hooks().running
    assert overlay.calls[-1][0] == "record" and "demo" in overlay.calls[-1][1]
    assert "record stop" in overlay.calls[-1][1]


def test_default_name_is_a_timestamp(parts):
    rec = parts[0]
    assert rec.start(None, None)["name"].startswith("rec-")


@pytest.mark.parametrize("name", ["a b", "x" * 41, "../up", "名前"])
def test_bad_names_are_refused(parts, name):
    from agent_browser_bridge.desktop.errors import ActionError
    with pytest.raises(ActionError):
        parts[0].start(name, None)


def test_a_click_saves_a_shot_and_an_event_line(parts):
    rec, screen, _, clock, home = parts
    rec.start("demo", None)
    clock.now += 2.5
    _hooks().push("click", button="left", display=[960, 540], window="Untitled - Paint")
    rec.flush()
    line = json.loads((home / "recordings" / "demo" / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line == {"t": 2.5, "kind": "click", "button": "left", "display": [960, 540], "monitor": 1,
                    "view": [640, 360], "window": "Untitled - Paint", "shot": "001.jpg"}
    assert (home / "recordings" / "demo" / "001.jpg").stat().st_size > 0
    assert screen.grabs == [((0, 0, 1920, 1080), False)]


def test_keys_and_wheel_are_events_without_shots(parts):
    rec, screen, _, _, home = parts
    rec.start("demo", None)
    _hooks().push("key", vk=0x41, modifiers=[], window="Notepad")
    _hooks().push("wheel", direction="down", amount=3, display=[10, 10], window="Notepad")
    rec.flush()
    lines = [json.loads(l) for l in (home / "recordings" / "demo" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lines[0]["kind"] == "key" and lines[0]["name"] == "a" and "shot" not in lines[0]
    assert lines[1] == {"t": 0.0, "kind": "wheel", "direction": "down", "amount": 3, "display": [10, 10], "window": "Notepad"}
    assert screen.grabs == []


def test_a_unicode_packet_key_keeps_its_character(parts):
    rec, _, _, _, home = parts
    rec.start("demo", None)
    _hooks().push("key", vk=0xE7, char="é", modifiers=[], window="x")
    rec.flush()
    line = json.loads((home / "recordings" / "demo" / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["name"] == "é"


def test_stop_writes_the_skill_and_reports_counts(parts):
    rec, _, overlay, _, home = parts
    rec.start("demo", "save the file")
    _hooks().push("click", button="left", display=[100, 100], window="Notepad")
    _hooks().push("key", vk=0x53, modifiers=["ctrl"], window="Notepad")
    r = rec.stop()
    skill = home / "recordings" / "demo" / "SKILL.md"
    assert r == {"recording": False, "name": "demo", "dir": str(home / "recordings" / "demo"),
                 "skill": str(skill), "events": 2, "shots": 1}
    text = skill.read_text(encoding="utf-8")
    assert text.startswith('---\nname: demo\ndescription: "save the file"\n---\n')
    assert "1. In **Notepad** (display 1), click at (67, 67). ![step 1](001.jpg)" in text
    assert "2. Press ctrl+s." in text
    assert not _hooks().running and overlay.calls[-1] == ("record", None)
    assert rec.status() == {"recording": False}


def test_status_while_recording(parts):
    rec, _, _, clock, _ = parts
    rec.start("demo", None)
    clock.now += 3
    _hooks().push("key", vk=0x42, modifiers=[], window="x")
    rec.flush()
    assert rec.status() == {"recording": True, "name": "demo", "events": 1, "seconds": 3.0}


def test_second_start_and_existing_directory_are_refused(parts, tmp_path):
    from agent_browser_bridge.desktop.errors import ActionError
    rec = parts[0]
    rec.start("demo", None)
    with pytest.raises(ActionError, match="already"):
        rec.start("other", None)
    rec.stop()
    with pytest.raises(ActionError, match="exists"):
        rec.start("demo", None)
    with pytest.raises(ActionError, match="no recording"):
        rec.stop()


def test_the_event_limit_stops_the_recording(parts):
    rec, _, overlay, _, home = parts
    rec.start("demo", None)
    for _ in range(MAX_EVENTS):
        _hooks().push("key", vk=0x41, modifiers=[], window="x")
    rec.flush()
    assert rec.status() == {"recording": False} and not _hooks().running
    assert (home / "recordings" / "demo" / "SKILL.md").exists()
    assert any(c[0] == "show" and "stopped" in c[1] for c in overlay.calls)


def test_the_time_limit_stops_the_recording_without_an_event(parts, monkeypatch):
    import time
    from agent_browser_bridge.desktop import recorder as mod
    monkeypatch.setattr(mod, "POLL_SECONDS", 0.01, raising=False)
    rec, _, overlay, clock, home = parts
    rec.start("demo", None)
    clock.now += mod.MAX_SECONDS
    deadline = time.time() + 2
    while rec.status()["recording"] and time.time() < deadline:
        time.sleep(0.01)
    assert rec.status() == {"recording": False} and not _hooks().running
    assert (home / "recordings" / "demo" / "SKILL.md").exists()
    assert any(c[0] == "show" and "minutes" in c[1] for c in overlay.calls)


def test_a_failing_skill_write_still_ends_the_worker(parts, monkeypatch):
    def broken(*_args):
        raise OSError("disk full")
    rec = parts[0]
    rec.start("demo", None)
    worker = rec._worker
    monkeypatch.setattr("agent_browser_bridge.desktop.recorder.write_skill", broken)
    with pytest.raises(OSError, match="disk full"):
        rec.stop()
    worker.join(2.0)
    assert not worker.is_alive() and not _hooks().running
    assert rec.status() == {"recording": False}


def test_write_skill_collapses_typing_and_names_combos():
    events = [
        {"t": 0, "kind": "click", "button": "left", "display": [1, 1], "monitor": 1, "view": [1, 1],
         "window": "Untitled - Paint", "shot": "001.jpg"},
        {"t": 1, "kind": "key", "vk": 0x48, "name": "h", "modifiers": [], "window": "Untitled - Paint"},
        {"t": 2, "kind": "key", "vk": 0x49, "name": "i", "modifiers": ["shift"], "window": "Untitled - Paint"},
        {"t": 3, "kind": "key", "vk": 0x20, "name": "space", "modifiers": [], "window": "Untitled - Paint"},
        {"t": 4, "kind": "key", "vk": 0x0D, "name": "return", "modifiers": [], "window": "Untitled - Paint"},
        {"t": 5, "kind": "key", "vk": 0x53, "name": "s", "modifiers": ["ctrl"], "window": "Untitled - Paint"},
        {"t": 6, "kind": "wheel", "direction": "down", "amount": 2, "display": [50, 50], "window": "Untitled - Paint"},
        {"t": 7, "kind": "click", "button": "right", "display": [3, 3], "monitor": 1, "view": [2, 2],
         "window": "Settings", "shot": "002.jpg"},
    ]
    text = write_skill("demo", None, (1920, 1080), events)
    assert 'description: "Recorded on the desktop' in text
    assert "1. In **Untitled - Paint** (display 1), click at (1, 1). ![step 1](001.jpg)" in text
    assert '2. Type "hI ".' in text
    assert "3. Press return." in text
    assert "4. Press ctrl+s." in text
    assert "5. Scroll down 2." in text
    assert "6. In **Settings** (display 1), right-click at (2, 2). ![step 6](002.jpg)" in text
    assert "1920×1080" in text and "1280" in text and "desktop_shot" in text and "monitor" in text
    assert "secret" in text  # typed text is reproduced verbatim; the header says to scrub it


def _key(name, vk, mods=(), t=0):
    return {"t": t, "kind": "key", "vk": vk, "name": name, "modifiers": list(mods), "window": "w"}


def _click(x, y, t, button="left", shot="001.jpg"):
    return {"t": t, "kind": "click", "button": button, "display": [x, y], "monitor": 1, "view": [x, y],
            "window": "w", "shot": shot}


def test_shift_stays_in_combos_and_on_non_letters():
    text = write_skill("d", "x", (1920, 1080), [
        _key("tab", 0x09, ["shift"]), _key("s", 0x53, ["ctrl", "shift"]), _key("end", 0x23, ["shift"]),
        _key("1", 0x31, ["shift"]), _key("a", 0x41, ["shift"])])
    assert "1. Press shift+tab." in text and "2. Press ctrl+shift+s." in text
    assert "3. Press shift+end." in text and "4. Press shift+1." in text and '5. Type "A".' in text


def test_two_quick_clicks_at_one_spot_are_a_double_click():
    text = write_skill("d", "x", (1920, 1080), [
        _click(100, 100, 1.0), _click(101, 100, 1.3, shot="002.jpg"), _click(300, 300, 2.5, shot="003.jpg")])
    assert "1. In **w** (display 1), double-click at (100, 100). ![step 1](001.jpg)" in text
    assert "2. In **w** (display 1), click at (300, 300). ![step 2](003.jpg)" in text


def test_consecutive_wheel_events_merge():
    events = [{"t": i, "kind": "wheel", "direction": "down", "amount": 1, "display": [1, 1], "window": "w"} for i in range(5)]
    events.append({"t": 9, "kind": "wheel", "direction": "up", "amount": 2, "display": [1, 1], "window": "w"})
    text = write_skill("d", "x", (1920, 1080), events)
    assert "1. Scroll down 5." in text and "2. Scroll up 2." in text


def test_touchpad_wheel_deltas_accumulate_into_notches(parts):
    rec, _, _, _, home = parts
    rec.start("demo", None)
    for _ in range(3):
        _hooks().push("wheel", direction="down", delta=40, display=[1, 1], window="w")
    _hooks().push("wheel", direction="down", delta=100, display=[1, 1], window="w")
    _hooks().push("wheel", direction="down", delta=260, display=[1, 1], window="w")
    rec.flush()
    lines = [json.loads(l) for l in (home / "recordings" / "demo" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [l["amount"] for l in lines] == [1, 3]


def test_numpad_keys_type_digits_and_ime_keys_are_dropped(parts):
    from agent_browser_bridge.desktop.recorder import key_name
    assert key_name(0x65) == "5" and key_name(0x6B) == "plus" and key_name(0x6E) == "period"
    rec, _, _, _, home = parts
    rec.start("demo", None)
    _hooks().push("key", vk=0xE5, modifiers=[], window="w")
    _hooks().push("key", vk=0xF3, modifiers=[], window="w")
    _hooks().push("key", vk=0x65, modifiers=[], window="w")
    rec.flush()
    lines = (home / "recordings" / "demo" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["name"] == "5"


def test_unknown_keys_are_flagged_in_the_skill():
    text = write_skill("d", "x", (1920, 1080), [_key("vkf5", 0xF5)])
    assert "1. Press vkf5 (a key desktop_key does not know; edit this step)." in text


def test_describe_with_a_colon_is_valid_frontmatter():
    text = write_skill("d", 'Expense claim: file "one" expense', (1920, 1080), [])
    assert text.splitlines()[2] == 'description: "Expense claim: file \\"one\\" expense"'


def test_titles_and_typed_text_cannot_break_the_step_list():
    events = [{"t": 0, "kind": "click", "button": "left", "display": [1, 1], "monitor": 1, "view": [1, 1],
               "window": "evil\ntitle **bold** `x`", "shot": "001.jpg"},
              _key('"', 0xE7, t=1)]
    text = write_skill("d", "x", (1920, 1080), events)
    assert "1. In **evil title bold x** (display 1), click at (1, 1)." in text
    assert '2. Type "\\"".' in text


def test_steps_on_a_second_display_say_so():
    event = {"t": 0, "kind": "click", "button": "left", "display": [2000, 10], "monitor": 2, "view": [53, 7],
             "window": "w", "shot": "001.jpg"}
    text = write_skill("d", "x", (1920, 1080), [event])
    assert "(display 2), click at (53, 7)" in text


def test_stop_after_the_limit_finished_returns_that_result(parts):
    rec, _, _, _, home = parts
    rec.start("demo", None)
    for _ in range(MAX_EVENTS):
        _hooks().push("key", vk=0x41, modifiers=[], window="x")
    r = rec.stop()
    assert r["recording"] is False and r["skill"].endswith("SKILL.md") and r["events"] == MAX_EVENTS


def test_a_failed_start_leaves_nothing_behind(tmp_path):
    from agent_browser_bridge.desktop.errors import ActionError

    class BrokenHooks:
        def __init__(self, sink):
            pass

        def start(self):
            raise ActionError("hooks refused")

        def stop(self):
            pass

    rec = Recorder(FakeScreen(), home=tmp_path, hooks_factory=BrokenHooks, overlay=FakeOverlay())
    with pytest.raises(ActionError, match="hooks refused"):
        rec.start("demo", None)
    assert rec.status() == {"recording": False} and not (tmp_path / "recordings" / "demo").exists()
    FakeHooks.instances.clear()
    rec.hooks_factory = FakeHooks
    assert rec.start("demo", None)["recording"]


def test_recording_needs_a_real_overlay(tmp_path):
    from agent_browser_bridge.desktop.errors import ActionError
    from agent_browser_bridge.desktop.overlay import NullOverlay
    rec = Recorder(FakeScreen(), home=tmp_path, hooks_factory=FakeHooks, overlay=NullOverlay())
    with pytest.raises(ActionError, match="overlay"):
        rec.start("demo", None)
    assert not (tmp_path / "recordings" / "demo").exists()
