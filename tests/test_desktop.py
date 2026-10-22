"""The Desktop facade: validated desktop_* actions over fake screen and input layers."""
import sys

import pytest

pytest.importorskip("PIL")

from agent_browser_bridge.desktop import Desktop, executor  # noqa: E402
from agent_browser_bridge.desktop.screen import Monitor  # noqa: E402
from tests.desktop_fakes import FakeKeyboard, FakeOverlay, FakeScreen, three_monitors  # noqa: E402


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    monkeypatch.setattr(executor, "SETTLE_SECONDS", 0)


@pytest.fixture
def keyboard():
    return FakeKeyboard()


@pytest.fixture
def overlay():
    return FakeOverlay()


@pytest.fixture
def desk(keyboard, overlay):
    return Desktop(screen=FakeScreen(layout=three_monitors()), inputs=keyboard, overlay=overlay)


def test_screen_reports_the_coordinate_space(desk):
    r = desk.handle("desktop_screen", {})
    assert r["success"] and r["view"] == [1280, 720] and r["display"] == [1920, 1080]
    assert r["space"] == "view" and [m["monitor"] for m in r["monitors"]] == [1, 2, 3]


def test_a_click_is_scaled_from_view_space(desk, keyboard):
    r = desk.handle("desktop_click", {"at": [640, 360]})
    assert r["success"] and r["action"] == "left_click" and ("move", 960, 540) in keyboard.calls


def test_display_space_takes_coordinates_as_they_are(desk, keyboard):
    desk.handle("desktop_click", {"at": [960, 540], "space": "display"})
    assert ("move", 960, 540) in keyboard.calls


def test_shot_returns_a_png_data_url_and_names_its_space(desk):
    r = desk.handle("desktop_shot", {})
    assert r["dataUrl"].startswith("data:image/png;base64,") and r["format"] == "png"
    assert r["space"] == "view" and r["image"] == [1280, 720] and r["cursor_drawn"] is True
    assert r["source"] == "gdi"


def test_fast_shot_is_jpeg(desk):
    r = desk.handle("desktop_shot", {"fast": True})
    assert r["dataUrl"].startswith("data:image/jpeg;base64,") and r["format"] == "jpeg"


def test_a_region_shot_says_not_to_click_from_it(desk):
    r = desk.handle("desktop_shot", {"region": [0, 0, 640, 360]})
    assert r["space"] == "region" and r["region"] == [0, 0, 640, 360]
    assert "do not take click" in r["note"]


def test_the_view_and_monitor_of_the_last_shot_are_reused(desk):
    desk.handle("desktop_shot", {"monitor": 3, "maxEdge": 960})
    r = desk.handle("desktop_screen", {})
    assert r["monitor"] == 3 and r["view"] == [960, 540]


def test_a_region_shot_does_not_change_the_remembered_space(desk):
    desk.handle("desktop_shot", {"maxEdge": 960})
    desk.handle("desktop_shot", {"region": [0, 0, 10, 10], "maxEdge": 400})
    assert desk.handle("desktop_screen", {})["view"] == [960, 540]


def test_moving_a_display_throws_the_remembered_space_away(desk):
    desk.handle("desktop_shot", {"monitor": 3, "maxEdge": 960})
    desk.screen._monitors = [Monitor(1, (0, 0), (1920, 1080), True)]
    r = desk.handle("desktop_screen", {})
    assert r["monitor"] == 1 and r["view"] == [1280, 720]


def test_a_click_off_the_view_is_refused_with_no_driver_call(desk, keyboard):
    r = desk.handle("desktop_click", {"at": [99999, 99999]})
    assert r["success"] is False and r["code"] == "desktop_error" and "outside" in r["error"]
    assert keyboard.calls == []


def test_only_the_left_button_double_clicks(desk):
    r = desk.handle("desktop_click", {"at": [1, 1], "button": "right", "count": 2})
    assert r["code"] == "bad_params"


def test_a_backwards_scroll_amount_is_refused(desk):
    assert desk.handle("desktop_scroll", {"direction": "down", "amount": 0})["code"] == "bad_params"


def test_the_reported_repeat_is_the_one_that_ran(desk, keyboard):
    r = desk.handle("desktop_key", {"combo": "Return", "repeat": 500})
    assert r["repeat"] == 100 and ("press", "Return", 100) in keyboard.calls


def test_cursor_is_reported_in_both_spaces(desk, keyboard):
    keyboard.cursor = (2880, 540)  # on monitor 3, which starts at x=1920
    r = desk.handle("desktop_cursor", {})
    assert r["display"] == [2880, 540] and r["pointer_on_monitor"] == 3 and r["monitor"] == 2


def test_an_unknown_key_is_a_desktop_error(desk):
    assert desk.handle("desktop_key", {"combo": "ctrl+nosuchkey"})["code"] == "desktop_error"


def test_every_display_at_once_is_one_wide_view(desk):
    r = desk.handle("desktop_shot", {"monitor": "all"})
    assert r["display"] == [5760, 1080] and r["view"] == [1280, 240] and r["monitor"] == 0


def test_the_pointer_is_left_out_on_request(desk):
    r = desk.handle("desktop_shot", {"cursor": False})
    assert r["cursor_drawn"] is False and desk.screen.grabs[-1][1] is False


def test_drag_scroll_type_and_hold_reach_the_driver(desk, keyboard):
    assert desk.handle("desktop_drag", {"from": [0, 0], "to": [10, 10]})["action"] == "left_click_drag"
    assert desk.handle("desktop_scroll", {"direction": "up", "amount": 2, "at": [5, 5]})["amount"] == 2
    assert desk.handle("desktop_type", {"text": "hi"})["characters"] == 2
    assert desk.handle("desktop_hold", {"combo": "shift", "seconds": 0})["action"] == "hold_key"
    assert desk.handle("desktop_wait", {"seconds": 0})["action"] == "wait"
    names = [c[0] for c in keyboard.calls]
    assert names.count("drag") == 1 and "scroll" in names and ("type_text", "hi") in keyboard.calls


@pytest.mark.parametrize("action,params", [
    ("desktop_click", {"at": "640,360"}), ("desktop_click", {"at": [1, 2, 3]}),
    ("desktop_click", {}), ("desktop_drag", {"from": [1, 1]}),
    ("desktop_click", {"at": [1, 1], "button": "back"}), ("desktop_click", {"at": [1, 1], "count": 4}),
    ("desktop_shot", {"region": [0, 0, 0, 5]}), ("desktop_shot", {"region": "0,0,5,5"}),
    ("desktop_shot", {"monitor": 0}), ("desktop_shot", {"monitor": "left"}), ("desktop_shot", {"monitor": 9}),
    ("desktop_scroll", {"direction": "sideways"}), ("desktop_scroll", {"direction": "up", "amount": "3"}),
    ("desktop_type", {}), ("desktop_type", {"text": 5}),
    ("desktop_key", {"combo": 5}), ("desktop_key", {}), ("desktop_wait", {"seconds": 101}),
    ("desktop_wait", {}), ("desktop_hold", {"combo": "a", "seconds": -1}), ("desktop_hold", {"combo": "a"}),
    ("desktop_click", {"at": [1, 1], "space": "world"}), ("desktop_click", {"at": [1, 1], "maxEdge": 0}),
    ("desktop_click", {"at": [1, 1], "modifiers": 7}), ("desktop_shot", {"fast": "yes"}),
])
def test_bad_params_are_refused_before_anything_moves(desk, keyboard, action, params):
    r = desk.handle(action, params)
    assert r["success"] is False and r["code"] == "bad_params", r
    assert keyboard.calls == []


def test_an_unknown_monitor_names_the_attached_ones(desk):
    assert "[1, 2, 3]" in desk.handle("desktop_shot", {"monitor": 9})["error"]


def test_client_side_extras_are_ignored(desk):
    r = desk.handle("desktop_click", {"at": [1, 1], "tabId": 7, "highlight": False, "fallback": True})
    assert r["success"]


def test_an_unknown_desktop_action(desk):
    r = desk.handle("desktop_fly", {})
    assert r["code"] == "unknown_action" and "desktop_shot" in r["error"]


def test_escape_held_answers_aborted(desk, keyboard):
    keyboard.abort = True
    assert desk.handle("desktop_click", {"at": [1, 1]})["code"] == "desktop_aborted"


def test_an_unexpected_failure_still_answers_a_dict(desk, keyboard, monkeypatch):
    monkeypatch.setattr(keyboard, "click", lambda *a, **k: 1 / 0)
    r = desk.handle("desktop_click", {"at": [1, 1]})
    assert r["code"] == "desktop_error" and "ZeroDivisionError" in r["error"]


def test_close_releases_the_frames_once():
    class Frames:
        closed = 0

        def frame(self, monitor):
            return None

        def close(self):
            self.closed += 1

    frames = Frames()
    d = Desktop(screen=FakeScreen(), inputs=FakeKeyboard(), frames=frames)
    d.close()
    d.close()
    assert frames.closed == 1


def test_unsupported_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    r = Desktop().handle("desktop_screen", {})
    assert r == {"success": False, "code": "desktop_unsupported",
                 "error": "desktop control runs on Windows only"}


def test_unavailable_without_pillow(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "PIL", None)
    r = Desktop().handle("desktop_screen", {})
    assert r["code"] == "desktop_unavailable" and "pip install agent-browser-bridge[desktop]" in r["error"]


def test_a_call_after_an_all_monitors_shot_keeps_the_wide_view(desk, keyboard):
    desk.handle("desktop_shot", {"monitor": "all"})
    r = desk.handle("desktop_click", {"at": [10, 10]})
    assert r["success"], r
    assert desk.handle("desktop_screen", {})["monitor"] == 0
    assert ("move", -1920 + 45, 45) in keyboard.calls


@pytest.mark.parametrize("monitor", [0, -1, "left"])
def test_a_bad_monitor_value_names_the_attached_ones(desk, monitor):
    r = desk.handle("desktop_shot", {"monitor": monitor})
    assert r["code"] == "bad_params" and "[1, 2, 3]" in r["error"]


@pytest.mark.parametrize("action,params", [
    ("desktop_wait", {"seconds": 5}), ("desktop_hold", {"combo": "shift", "seconds": 5}),
    ("desktop_type", {"text": "x" * 500}),
])
def test_an_action_longer_than_the_remaining_deadline_is_refused(desk, keyboard, action, params):
    r = desk.handle(action, params, remaining=1.0)
    assert r["code"] == "timeout" and "split" in r["error"], r
    assert keyboard.calls == []


def test_a_short_action_runs_within_the_deadline(desk, keyboard):
    assert desk.handle("desktop_type", {"text": "ok"}, remaining=1.0)["success"]


def test_shot_reports_the_foreground_window(desk):
    r = desk.handle("desktop_shot", {})
    assert r["window"] == {"title": "Untitled - Paint", "pid": 4242, "process": "mspaint.exe"}


def test_drag_through_waypoints_is_one_stroke(desk, keyboard):
    r = desk.handle("desktop_drag", {"from": [0, 0], "to": [10, 10], "through": [[2, 2], [5, 5]]})
    assert r["success"] and r["through"] == 2
    drags = [c for c in keyboard.calls if c[0] == "drag"]
    assert drags == [("drag", (0, 0), (15, 15), "left", [(3, 3), (8, 8)])]


def test_a_waypoint_off_the_view_is_refused_before_the_pen_goes_down(desk, keyboard):
    r = desk.handle("desktop_drag", {"from": [0, 0], "to": [10, 10], "through": [[99999, 5]]})
    assert r["code"] == "desktop_error" and "outside" in r["error"]
    assert keyboard.calls == []


@pytest.mark.parametrize("through", [[[1]], "2,2", [[1, 2, 3]], [["a", "b"]]])
def test_through_must_be_a_list_of_points(desk, keyboard, through):
    r = desk.handle("desktop_drag", {"from": [0, 0], "to": [10, 10], "through": through})
    assert r["code"] == "bad_params" and keyboard.calls == []


def test_estimate_counts_waypoints():
    from agent_browser_bridge.desktop import estimate_seconds
    # 31 segments of eight 10 ms sleeps, which Python 3.10 on Windows rounds up to the 15.6 ms tick.
    assert estimate_seconds("desktop_drag", {"from": [0, 0], "to": [1, 1], "through": [[1, 1]] * 30}) == 3.875
    assert estimate_seconds("desktop_drag", {"from": [0, 0], "to": [1, 1]}) == 0.0


def test_window_action_reports_without_capturing(desk):
    r = desk.handle("desktop_window", {})
    assert r["success"] and r["window"]["title"] == "Untitled - Paint"
    assert desk.screen.grabs == []


def test_an_action_shows_the_overlay_then_idles(desk, overlay):
    desk.handle("desktop_click", {"at": [1, 1]})
    assert overlay.calls == [("show", "agent-bridge: desktop_click", "action", None), ("idle",)]


def test_a_failed_action_still_idles_the_overlay(desk, overlay):
    desk.handle("desktop_click", {"at": [99999, 1]})
    assert overlay.calls[-1] == ("idle",)


@pytest.mark.parametrize("action", ["desktop_screen", "desktop_cursor", "desktop_window"])
def test_quiet_actions_leave_the_overlay_alone(desk, overlay, action):
    assert desk.handle(action, {})["success"]
    assert overlay.calls == []


def test_notify_shows_the_text_for_the_seconds_asked(desk, overlay):
    r = desk.handle("desktop_notify", {"text": "hello", "seconds": 2})
    assert r["success"] and r["action"] == "notify" and r["seconds"] == 2.0
    assert overlay.calls == [("show", "hello", "notify", 2.0), ("idle",)]


def test_notify_defaults_to_four_seconds(desk, overlay):
    assert desk.handle("desktop_notify", {"text": "x" * 200})["seconds"] == 4.0
    assert desk.handle("desktop_notify", {"text": "y", "seconds": 0.5})["success"]
    assert desk.handle("desktop_notify", {"text": "y", "seconds": 30})["success"]


@pytest.mark.parametrize("params", [{}, {"text": ""}, {"text": "x" * 201}, {"text": 5},
                                    {"text": "a", "seconds": 31}, {"text": "a", "seconds": 0.1}])
def test_notify_bad_params(desk, overlay, params):
    assert desk.handle("desktop_notify", params)["code"] == "bad_params"
    assert ("show", params.get("text"), "notify", params.get("seconds")) not in overlay.calls


def test_close_closes_the_overlay(desk, overlay):
    desk.close()
    assert ("close",) in overlay.calls


class _StubRecorder:
    def __init__(self):
        self.calls = []

    def start(self, name, describe):
        self.calls.append(("start", name, describe))
        return {"recording": True, "name": name or "rec-x", "dir": "d"}

    def stop(self):
        self.calls.append(("stop",))
        return {"recording": False}

    def status(self):
        return {"recording": False}


def test_record_ops_reach_the_recorder_and_status_is_quiet(keyboard, overlay):
    rec = _StubRecorder()
    d = Desktop(screen=FakeScreen(), inputs=keyboard, overlay=overlay, recorder=rec)
    assert d.handle("desktop_record", {"op": "status"}) == {"success": True, "recording": False}
    assert overlay.calls == []
    r = d.handle("desktop_record", {"op": "start", "name": "demo", "describe": "x"})
    assert r["success"] and r["recording"] and rec.calls == [("start", "demo", "x")]
    assert d.handle("desktop_record", {"op": "stop"})["success"] and rec.calls[-1] == ("stop",)


def test_actions_leave_the_banner_alone_while_recording(keyboard, overlay):
    rec = _StubRecorder()
    rec.status = lambda: {"recording": True, "name": "demo", "events": 0, "seconds": 1.0}
    d = Desktop(screen=FakeScreen(), inputs=keyboard, overlay=overlay, recorder=rec)
    assert d.handle("desktop_click", {"at": [1, 1]})["success"]
    assert overlay.calls == []
    assert d.handle("desktop_notify", {"text": "still allowed"})["success"]
    assert overlay.calls[0] == ("show", "still allowed", "notify", 4.0)


@pytest.mark.parametrize("params", [{}, {"op": "pause"}, {"op": "start", "name": 5},
                                    {"op": "start", "describe": "x" * 201}, {"op": "start", "describe": "a\nb"}])
def test_record_bad_params(keyboard, overlay, params):
    d = Desktop(screen=FakeScreen(), inputs=keyboard, overlay=overlay, recorder=_StubRecorder())
    assert d.handle("desktop_record", params)["code"] == "bad_params"


def test_fakes_never_get_a_real_overlay(keyboard):
    from agent_browser_bridge.desktop.overlay import NullOverlay
    d = Desktop(screen=FakeScreen(), inputs=keyboard)
    d.handle("desktop_screen", {})
    assert isinstance(d.overlay, NullOverlay)
