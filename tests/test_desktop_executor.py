import base64
import io

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from agent_browser_bridge.desktop import executor as executor_module
from agent_browser_bridge.desktop.errors import ActionError, Aborted
from agent_browser_bridge.desktop.executor import Executor
from tests.desktop_fakes import FakeKeyboard, FakeScreen


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    monkeypatch.setattr(executor_module, "SETTLE_SECONDS", 0)


@pytest.fixture
def parts():
    screen, keyboard = FakeScreen(), FakeKeyboard()
    return Executor(max_edge=1280, screen=screen, keyboard=keyboard), screen, keyboard


def test_view_is_scaled_down_to_max_edge(parts):
    ex, _, _ = parts
    assert ex.view_size == (1280, 720)
    assert ex.scale == pytest.approx(1280 / 1920)


def test_a_click_lands_on_the_matching_physical_pixel(parts):
    ex, _, keyboard = parts
    ex.dispatch("left_click", {"coordinate": [640, 360]})
    assert ("move", 960, 540) in keyboard.calls
    assert ("click", "left", 1) in keyboard.calls


def test_a_point_off_the_view_is_refused_rather_than_clamped(parts):
    ex, _, keyboard = parts
    with pytest.raises(ActionError) as caught:
        ex.dispatch("left_click", {"coordinate": [99999, 99999]})
    assert "outside" in str(caught.value)
    assert keyboard.calls == []


def test_the_far_edge_of_the_view_still_counts_as_on_screen(parts):
    ex, _, keyboard = parts
    ex.dispatch("left_click", {"coordinate": [1280, 720]})
    assert ("move", 1919, 1079) in keyboard.calls


def test_a_drag_that_leaves_the_view_is_refused(parts):
    ex, _, keyboard = parts
    with pytest.raises(ActionError):
        ex.dispatch("left_click_drag", {"start_coordinate": [10, 10],
                                        "coordinate": [4000, 10]})
    assert keyboard.calls == []


def test_zoom_outside_the_view_is_refused(parts):
    ex, _, _ = parts
    with pytest.raises(ActionError):
        ex.dispatch("zoom", {"region": [5000, 5000, 5100, 5100]})


def test_screenshot_is_a_png_of_the_view_size(parts):
    ex, _, _ = parts
    block = ex.dispatch("screenshot")[0]
    assert block["source"]["media_type"] == "image/png"
    image = Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))
    assert image.size == (1280, 720)


def test_zoom_grabs_the_region_in_physical_pixels(parts):
    ex, screen, _ = parts
    ex.dispatch("zoom", {"region": [0, 0, 640, 360]})
    assert screen.grabs[-1] == ((0, 0, 960, 540), True)


def test_modifiers_wrap_the_click(parts):
    ex, _, keyboard = parts
    ex.dispatch("left_click", {"coordinate": [10, 10], "text": "ctrl"})
    order = [call[0] for call in keyboard.calls]
    assert order.index("modifiers_down") < order.index("click") < order.index("modifiers_up")


def test_double_and_triple_click_carry_their_count(parts):
    ex, _, keyboard = parts
    ex.dispatch("double_click", {})
    ex.dispatch("triple_click", {})
    assert ("click", "left", 2) in keyboard.calls
    assert ("click", "left", 3) in keyboard.calls


def test_drag_uses_both_coordinates(parts):
    ex, _, keyboard = parts
    ex.dispatch("left_click_drag", {"start_coordinate": [0, 0], "coordinate": [640, 360]})
    assert ("drag", (0, 0), (960, 540), "left", []) in keyboard.calls


def test_drag_without_a_start_is_rejected(parts):
    ex, _, _ = parts
    with pytest.raises(ActionError):
        ex.dispatch("left_click_drag", {"coordinate": [1, 1]})


def test_cursor_position_is_reported_in_view_pixels(parts):
    ex, _, keyboard = parts
    keyboard.cursor = (960, 540)
    assert ex.dispatch("cursor_position")[0]["text"] == "X=640, Y=360"


def test_key_repeat_is_capped(parts):
    ex, _, keyboard = parts
    ex.dispatch("key", {"text": "Down", "repeat": 5000})
    assert ("press", "Down", 100) in keyboard.calls


def test_wait_and_hold_reject_a_silly_duration(parts):
    ex, _, _ = parts
    with pytest.raises(ActionError):
        ex.dispatch("wait", {"duration": 4000})
    with pytest.raises(ActionError):
        ex.dispatch("hold_key", {"text": "shift", "duration": -1})


def test_a_malformed_number_is_a_tool_error_not_a_crash(parts):
    ex, _, _ = parts
    with pytest.raises(ActionError):
        ex.dispatch("scroll", {"scroll_direction": "down", "scroll_amount": "lots"})
    with pytest.raises(ActionError):
        ex.dispatch("key", {"text": "Down", "repeat": "many"})


def test_button_down_honours_a_coordinate_when_one_is_sent(parts):
    ex, _, keyboard = parts
    ex.dispatch("left_mouse_down", {"coordinate": [640, 360]})
    assert keyboard.calls == [("move", 960, 540), ("mouse_down", "left")]


def test_unknown_action_is_an_action_error(parts):
    ex, _, _ = parts
    with pytest.raises(ActionError):
        ex.dispatch("lick_screen", {})


def test_escape_held_down_stops_everything(parts):
    ex, _, keyboard = parts
    keyboard.abort = True
    with pytest.raises(Aborted):
        ex.dispatch("left_click", {"coordinate": [1, 1]})
    assert keyboard.calls == []


def test_fit_never_enlarges():
    small = Image.new("RGB", (400, 300))
    assert executor_module.fit(small, 1280) is small


def test_a_second_monitor_gets_its_own_view_and_its_own_offset():
    from tests.desktop_fakes import three_monitors

    screen, keyboard = FakeScreen(layout=three_monitors()), FakeKeyboard()
    left = Executor(max_edge=1280, monitor=three_monitors()[0], screen=screen,
                    keyboard=keyboard)
    assert left.view_size == (1280, 720)
    # The top left of the left-hand display sits at a negative virtual coordinate.
    assert left.to_physical(0, 0) == (-1920, 0)
    assert left.to_view(-1920, 0) == (0, 0)

    left.dispatch("left_click", {"coordinate": [640, 360]})
    assert ("move", -960, 540) in keyboard.calls


def test_displays_are_numbered_by_horizontal_position():
    """Monitors at different heights are common, and the number an agent is told has to
    match what someone at the desk would count from the left."""
    from agent_browser_bridge.desktop.screen import number_left_to_right

    found = [((3840, -200), (1920, 1080), False),   # right, mounted high
             ((0, 0), (1920, 1080), True),          # middle, the primary
             ((1920, 500), (1920, 1080), False)]    # between them, sitting low
    assert [m.origin[0] for m in number_left_to_right(found)] == [0, 1920, 3840]
    assert [m.index for m in number_left_to_right(found)] == [1, 2, 3]


def test_the_pointer_is_drawn_into_captures_by_default():
    screen, keyboard = FakeScreen(), FakeKeyboard()
    Executor(screen=screen, keyboard=keyboard).screenshot()
    assert screen.grabs[-1] == ((0, 0, 1920, 1080), True)

    Executor(screen=screen, keyboard=keyboard, cursor=False).screenshot()
    assert screen.grabs[-1] == ((0, 0, 1920, 1080), False)
