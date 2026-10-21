"""The desktop half of the daemon: screenshots, mouse and keyboard on Windows.

`Desktop.handle("desktop_<verb>", params)` validates the params, builds an executor whose
view space the coordinates are written in, runs the action and answers a result dict.
It never raises. The Win32 and Pillow layers load on the first action, so the daemon
starts on every platform and a machine without the `desktop` extra answers
`desktop_unavailable` instead of failing to import.

Coordinates arrive in view pixels: the pixel space of the last screenshot of that
display, which is smaller than the display. `space: "display"` pins the scale to 1.
"""
from __future__ import annotations

import base64
import io
import sys

from .errors import ActionError, Aborted

ACTIONS = ("screen", "cursor", "shot", "click", "move", "drag", "scroll", "type", "key",
           "hold", "wait")
DEFAULT_MAX_EDGE = 1280
MAX_DURATION = 100
MAX_EDGE_LIMIT = 16384
MAX_SCROLL = 1000
REGION_NOTE = "read this image, do not take click coordinates from it"
#: Typing pauses 5 ms per character, and time.sleep rounds up to the 15.6 ms timer tick on
#: Python 3.10 under Windows, so the budget per character is generous on purpose.
TYPE_SECONDS_PER_CHAR = 0.02


def estimate_seconds(action: str, params: dict) -> float:
    """How long a desktop action will occupy the pointer or keyboard, from its params alone.

    The client sizes its timeout from this and the daemon refuses an action that would
    outlive the call's deadline, so both sides agree on the number.
    """
    if not isinstance(params, dict):
        return 0.0
    if action in ("desktop_wait", "desktop_hold") and _is_number(params.get("seconds")):
        return max(0.0, float(params["seconds"]))
    if action == "desktop_type" and isinstance(params.get("text"), str):
        return len(params["text"]) * TYPE_SECONDS_PER_CHAR
    return 0.0


class BadParams(ValueError):
    pass


def _failure(code: str, error: str) -> dict:
    return {"success": False, "code": code, "error": error}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _point(params: dict, key: str, required: bool = True):
    value = params.get(key)
    if value is None:
        if required:
            raise BadParams(f"{key} is required: [x, y] in view pixels")
        return None
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_number(n) for n in value)):
        raise BadParams(f"{key} must be [x, y], got {value!r}")
    return [value[0], value[1]]


def _whole(params: dict, key: str, default, lo: int, hi: int):
    value = params.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise BadParams(f"{key} must be a whole number from {lo} to {hi}, got {value!r}")
    return value


def _number(params: dict, key: str, lo: float, hi: float) -> float:
    value = params.get(key)
    if not _is_number(value) or not lo <= value <= hi:
        raise BadParams(f"{key} must be a number from {lo:g} to {hi:g}, got {value!r}")
    return float(value)


def _choice(params: dict, key: str, allowed: tuple, default):
    value = params.get(key)
    if value is None:
        return default
    if value not in allowed:
        raise BadParams(f"{key} must be one of {', '.join(allowed)}, got {value!r}")
    return value


def _flag(params: dict, key: str, default: bool) -> bool:
    value = params.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BadParams(f"{key} must be true or false, got {value!r}")
    return value


def _text(params: dict, key: str, required: bool = True):
    value = params.get(key)
    if value is None:
        if required:
            raise BadParams(f"{key} is required")
        return None
    if not isinstance(value, str) or (required and not value):
        raise BadParams(f"{key} must be a string, got {value!r}")
    return value


def _region(params: dict):
    value = params.get("region")
    if value is None:
        return None
    if not (isinstance(value, (list, tuple)) and len(value) == 4 and all(_is_number(n) for n in value)):
        raise BadParams(f"region must be [x, y, width, height], got {value!r}")
    left, top, width, height = (int(n) for n in value)
    if width <= 0 or height <= 0:
        raise BadParams(f"region has no area: {width}x{height}")
    return [left, top, width, height]


def _layout_of(monitors) -> list:
    return [[m.index, list(m.origin), list(m.size)] for m in monitors]


class Desktop:
    def __init__(self, screen=None, inputs=None, frames=None):
        self.screen = screen
        self.inputs = inputs
        self.frames = frames
        self._executor_module = None
        # (max_edge, monitor index, layout) of the last full shot; replaces doppelhand's
        # session file. A changed layout makes the note worthless, so it is checked each use.
        self._noted: tuple[int, int, list] | None = None

    def availability(self) -> dict | None:
        """None when desktop actions can run here, else the failure every action answers."""
        if self._executor_module is not None:
            return None
        injected = self.screen is not None and self.inputs is not None
        if not injected and sys.platform != "win32":
            return _failure("desktop_unsupported", "desktop control runs on Windows only")
        try:
            import PIL  # noqa: F401
        except ImportError:
            return _failure("desktop_unavailable",
                            "Pillow is not installed; run: pip install agent-browser-bridge[desktop]")
        from . import executor
        if not injected:
            from . import duplication, inputs, screen
            self.screen = self.screen or screen
            self.inputs = self.inputs or inputs
            if self.frames is None:
                self.frames = duplication.FrameSource()
        self._executor_module = executor
        return None

    def handle(self, action: str, params: dict, remaining: float | None = None) -> dict:
        """Run one action. `remaining` is what is left of the call's deadline, in seconds."""
        failure = self.availability()
        if failure:
            return failure
        verb = action[len("desktop_"):] if action.startswith("desktop_") else ""
        if verb not in ACTIONS:
            known = ", ".join("desktop_" + name for name in ACTIONS)
            return _failure("unknown_action", f"no such desktop action: {action}; actions: {known}")
        if not isinstance(params, dict):
            params = {}
        need = estimate_seconds(action, params)
        if remaining is not None and need > remaining:
            # Running on would keep the keyboard busy after the caller has given up.
            return _failure("timeout", f"{action} needs about {need:g} s but only {max(0.0, remaining):g} s "
                                       "remain of the call's timeout; split it into shorter calls")
        try:
            # Attachment to the input desktop is per thread, and the daemon answers each
            # request on its own thread.
            self.screen.attach_input_desktop()
            return {"success": True, **getattr(self, "_do_" + verb)(params)}
        except BadParams as exc:
            return _failure("bad_params", str(exc))
        except Aborted as exc:
            return _failure("desktop_aborted", str(exc))
        except ActionError as exc:
            return _failure("desktop_error", str(exc))
        except Exception as exc:  # noqa: BLE001 - the contract is a dict, always
            return _failure("desktop_error", f"{type(exc).__name__}: {exc}")

    def close(self) -> None:
        frames, self.frames = self.frames, None
        if frames is not None:
            frames.close()

    def _pick_monitor(self, monitors, wanted):
        if wanted in (None, "primary"):
            return next((m for m in monitors if m.primary), monitors[0])
        if wanted == "all":
            return self.screen.virtual_monitor()
        if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted >= 1:
            for monitor in monitors:
                if monitor.index == wanted:
                    return monitor
            raise BadParams(f"there is no monitor {wanted}; attached: {[m.index for m in monitors]}")
        raise BadParams(f"monitor must be a number from 1, 'primary' or 'all', got {wanted!r}; "
                        f"attached: {[m.index for m in monitors]}")

    def _executor(self, params: dict):
        """The executor whose view space the params are written in, plus the space name."""
        monitors = self.screen.monitors()
        noted_edge = noted_monitor = None
        if self._noted is not None and self._noted[2] == _layout_of(monitors):
            noted_edge, noted_monitor = self._noted[0], self._noted[1]
        wanted = params.get("monitor")
        monitor = self._pick_monitor(monitors, noted_monitor if wanted is None else wanted)
        space = _choice(params, "space", ("view", "display"), "view")
        max_edge = _whole(params, "maxEdge", None, 1, MAX_EDGE_LIMIT)
        if space == "display":
            max_edge = max(monitor.size)
        else:
            max_edge = max_edge or noted_edge or DEFAULT_MAX_EDGE
        executor = self._executor_module.Executor(
            max_edge=max_edge, monitor=monitor, screen=self.screen, keyboard=self.inputs,
            cursor=_flag(params, "cursor", True), frames=self.frames)
        return executor, space

    @staticmethod
    def _space(executor, space: str) -> dict:
        return {"monitor": executor.monitor.index, "view": list(executor.view_size),
                "display": list(executor.screen_size), "scale": round(executor.scale, 6),
                "space": space}

    def _do_screen(self, params: dict) -> dict:
        executor, space = self._executor(params)
        return {"monitors": [m.describe() for m in self.screen.monitors()],
                **self._space(executor, space)}

    def _do_cursor(self, params: dict) -> dict:
        executor, _ = self._executor(params)
        x, y = self.inputs.cursor_position()
        on = next((m.index for m in self.screen.monitors()
                   if m.origin[0] <= x < m.origin[0] + m.size[0]
                   and m.origin[1] <= y < m.origin[1] + m.size[1]), None)
        return {"view": list(executor.to_view(x, y)), "display": [x, y],
                "monitor": executor.monitor.index, "pointer_on_monitor": on,
                "scale": round(executor.scale, 6)}

    def _do_shot(self, params: dict) -> dict:
        region = _region(params)
        fast = _flag(params, "fast", False)
        executor, space = self._executor(params)
        if region:
            image = self._executor_module.fit(executor.frame(executor.crop_box(*region)),
                                              executor.max_edge, fast=fast)
        else:
            image = executor.capture(fast=fast)
        buffer = io.BytesIO()
        if fast:
            image.save(buffer, format="JPEG", quality=85)
            fmt = "jpeg"
        else:
            image.save(buffer, format="PNG")
            fmt = "png"
        payload = {"dataUrl": f"data:image/{fmt};base64,"
                              + base64.b64encode(buffer.getvalue()).decode("ascii"),
                   "format": fmt, **self._space(executor, space)}
        if region:
            # A crop has its own origin, so points read off it are not screen points.
            payload["region"] = region
            payload["space"] = "region"
            payload["note"] = REGION_NOTE
        else:
            # The virtual monitor has index 0, which is not a valid request; note it as "all".
            noted_monitor = "all" if executor.monitor.index == 0 else executor.monitor.index
            self._noted = (executor.max_edge, noted_monitor, _layout_of(self.screen.monitors()))
        payload["image"] = list(image.size)
        payload["cursor_drawn"] = executor.cursor
        payload["source"] = executor.last_source
        return payload

    def _do_click(self, params: dict) -> dict:
        at = _point(params, "at")
        button = _choice(params, "button", ("left", "right", "middle"), "left")
        count = _whole(params, "count", 1, 1, 3)
        modifiers = _text(params, "modifiers", required=False)
        if count == 1:
            member = f"{button}_click"
        elif button != "left":
            raise BadParams("only the left button has a double or triple click")
        else:
            member = "double_click" if count == 2 else "triple_click"
        executor, _ = self._executor(params)
        executor.dispatch(member, {"coordinate": at, "text": modifiers})
        return {"action": member, "at": at}

    def _do_move(self, params: dict) -> dict:
        at = _point(params, "at")
        executor, _ = self._executor(params)
        executor.dispatch("mouse_move", {"coordinate": at})
        return {"action": "mouse_move", "at": at}

    def _do_drag(self, params: dict) -> dict:
        start, end = _point(params, "from"), _point(params, "to")
        modifiers = _text(params, "modifiers", required=False)
        executor, _ = self._executor(params)
        executor.dispatch("left_click_drag", {"start_coordinate": start, "coordinate": end,
                                              "text": modifiers})
        return {"action": "left_click_drag", "from": start, "to": end}

    def _do_scroll(self, params: dict) -> dict:
        direction = _choice(params, "direction", ("up", "down", "left", "right"), None)
        if direction is None:
            raise BadParams("direction is required: up, down, left or right")
        amount = _whole(params, "amount", 3, 1, MAX_SCROLL)
        at = _point(params, "at", required=False)
        modifiers = _text(params, "modifiers", required=False)
        executor, _ = self._executor(params)
        dispatch = {"scroll_direction": direction, "scroll_amount": amount, "text": modifiers}
        if at:
            dispatch["coordinate"] = at
        executor.dispatch("scroll", dispatch)
        return {"action": "scroll", "direction": direction, "amount": amount}

    def _do_type(self, params: dict) -> dict:
        text = _text(params, "text")
        executor, _ = self._executor(params)
        executor.dispatch("type", {"text": text})
        return {"action": "type", "characters": len(text)}

    def _do_key(self, params: dict) -> dict:
        combo = _text(params, "combo")
        repeat = params.get("repeat", 1)
        if not isinstance(repeat, int) or isinstance(repeat, bool):
            raise BadParams(f"repeat must be a whole number, got {repeat!r}")
        repeat = min(100, max(1, repeat))  # the computer-use toolset's own ceiling
        executor, _ = self._executor(params)
        executor.dispatch("key", {"text": combo, "repeat": repeat})
        return {"action": "key", "combo": combo, "repeat": repeat}

    def _do_hold(self, params: dict) -> dict:
        combo = _text(params, "combo")
        seconds = _number(params, "seconds", 0, MAX_DURATION)
        executor, _ = self._executor(params)
        executor.dispatch("hold_key", {"text": combo, "duration": seconds})
        return {"action": "hold_key", "combo": combo, "seconds": seconds}

    def _do_wait(self, params: dict) -> dict:
        seconds = _number(params, "seconds", 0, MAX_DURATION)
        executor, _ = self._executor(params)
        executor.dispatch("wait", {"duration": seconds})
        return {"action": "wait", "seconds": seconds}
