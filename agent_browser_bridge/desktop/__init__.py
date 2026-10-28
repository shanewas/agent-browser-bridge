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
import subprocess
import sys
import time

from .errors import ActionError, Aborted
from .overlay import NullOverlay, make_overlay

ACTIONS = ("screen", "cursor", "window", "shot", "click", "move", "drag", "scroll", "type", "key",
           "hold", "wait", "notify", "record", "windows", "focus", "launch", "clipboard")
#: Read-only verbs agents call in tight loops; the overlay stays quiet for them.
QUIET = {"screen", "cursor", "window", "windows"}
NOTIFY_MAX_CHARS = 200
DEFAULT_MAX_EDGE = 1280
MAX_DURATION = 100
MAX_EDGE_LIMIT = 16384
MAX_SCROLL = 1000
MAX_WAYPOINTS = 500
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
    if action in ("desktop_focus", "desktop_launch") and _is_number(params.get("waitSeconds")):
        return max(0.0, float(params["waitSeconds"]))
    if action == "desktop_type" and isinstance(params.get("text"), str):
        return len(params["text"]) * TYPE_SECONDS_PER_CHAR
    if action == "desktop_drag" and isinstance(params.get("through"), list) and params["through"]:
        # Eight 10 ms moves per segment; Python 3.10 on Windows sleeps a 15.6 ms tick each time.
        return (len(params["through"]) + 1) * 0.125
    return 0.0


class BadParams(ValueError):
    pass


class WindowNotFound(ActionError):
    code = "window_not_found"


def _default_launcher(command, args):
    return subprocess.Popen([command, *args], shell=False, close_fds=True)


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


def _points(params: dict, key: str, limit: int) -> list:
    value = params.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise BadParams(f"{key} must be a list of up to {limit} [x, y] points")
    return [_point({key: p}, key) for p in value]


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


def _selector(params: dict):
    value = params.get("window")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BadParams(f"window must be an object with title, process or pid, got {value!r}")
    selector = {}
    for key in ("title", "process"):
        if key in value and value[key] is not None:
            if not isinstance(value[key], str) or not value[key]:
                raise BadParams(f"window {key} must be a non-empty string, got {value[key]!r}")
            selector[key] = value[key]
    if "pid" in value and value["pid"] is not None:
        pid = value["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise BadParams(f"window pid must be a positive whole number, got {pid!r}")
        selector["pid"] = pid
    if not selector:
        raise BadParams("window needs at least one of title, process or pid")
    return selector


def _inline_selector(params: dict, required: bool) -> dict:
    selector = {}
    for key in ("title", "process"):
        if params.get(key) is not None:
            if not isinstance(params[key], str) or not params[key]:
                raise BadParams(f"{key} must be a non-empty string, got {params[key]!r}")
            selector[key] = params[key]
    if params.get("pid") is not None:
        pid = params["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise BadParams(f"pid must be a positive whole number, got {pid!r}")
        selector["pid"] = pid
    if required and not selector:
        raise BadParams("at least one of title, process or pid is required")
    return selector


def _wait_seconds(params: dict, default: float) -> float:
    if params.get("waitSeconds") is None:
        return default
    return _number(params, "waitSeconds", 0, 30)


def _layout_of(monitors) -> list:
    return [[m.index, list(m.origin), list(m.size)] for m in monitors]


class Desktop:
    def __init__(self, screen=None, inputs=None, frames=None, overlay=None, recorder=None,
                 clipboard=None, launcher=None):
        self.screen = screen
        self.inputs = inputs
        self.frames = frames
        self.recorder = recorder
        self.clipboard = clipboard
        self.launcher = launcher
        # The real strip is only built alongside the real screen, so fakes never open a window.
        self.overlay = overlay if overlay is not None else NullOverlay()
        self._overlay_given = overlay is not None
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
        from . import clipboard as clipboard_module
        from . import executor
        self.clipboard = self.clipboard or clipboard_module
        self.launcher = self.launcher or _default_launcher
        if not injected:
            from . import duplication, inputs, screen
            self.screen = self.screen or screen
            self.inputs = self.inputs or inputs
            if self.frames is None:
                self.frames = duplication.FrameSource()
            if not self._overlay_given:
                self.overlay = make_overlay()
            if self.recorder is None:
                from .. import config
                from .recorder import Recorder
                self.recorder = Recorder(self.screen, home=config.home(), overlay=self.overlay)
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
        loud = verb not in QUIET and not (verb == "record" and params.get("op") == "status")
        if loud and verb != "notify" and self.recorder is not None and self.recorder.status().get("recording"):
            loud = False  # the red banner stays up for the whole recording
        if loud and verb != "notify":
            self.overlay.show(f"agent-bridge: {action}")
        try:
            # Attachment to the input desktop is per thread, and the daemon answers each
            # request on its own thread.
            self.screen.attach_input_desktop()
            return {"success": True, **getattr(self, "_do_" + verb)(params)}
        except BadParams as exc:
            return _failure("bad_params", str(exc))
        except WindowNotFound as exc:
            return _failure("window_not_found", str(exc))
        except Aborted as exc:
            return _failure("desktop_aborted", str(exc))
        except ActionError as exc:
            return _failure("desktop_error", str(exc))
        except Exception as exc:  # noqa: BLE001 - the contract is a dict, always
            return _failure("desktop_error", f"{type(exc).__name__}: {exc}")
        finally:
            if loud:
                self.overlay.idle()

    def close(self) -> None:
        frames, self.frames = self.frames, None
        if frames is not None:
            frames.close()
        if self.recorder is not None and hasattr(self.recorder, "close"):
            self.recorder.close()
        self.overlay.close()

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
        selector = _selector(params)
        if selector is not None:
            if params.get("monitor") is not None:
                raise BadParams("give either monitor or window, not both")
            info = self.screen.find_window(**selector)
            if info is None:
                raise BadParams(f"no window matches {selector!r}; call desktop_windows to see what is open")
            from .screen import Monitor
            rect = info["rect"]
            monitor = Monitor(0, (rect[0], rect[1]), (rect[2], rect[3]), False)
            space = _choice(params, "space", ("view", "display"), "view")
            max_edge = _whole(params, "maxEdge", None, 1, MAX_EDGE_LIMIT)
            if space == "display":
                max_edge = max(monitor.size)
            else:
                max_edge = max_edge or DEFAULT_MAX_EDGE
            executor = self._executor_module.Executor(
                max_edge=max_edge, monitor=monitor, screen=self.screen, keyboard=self.inputs,
                cursor=_flag(params, "cursor", True), frames=self.frames)
            executor.window = info
            return executor, space
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
        executor.window = None
        return executor, space

    @staticmethod
    def _space(executor, space: str) -> dict:
        result = {"monitor": executor.monitor.index, "view": list(executor.view_size),
                  "display": list(executor.screen_size), "scale": round(executor.scale, 6),
                  "space": space}
        window = getattr(executor, "window", None)
        if window is not None:
            result["window"] = window
        return result

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

    def _do_window(self, params: dict) -> dict:
        return {"action": "window", "window": self.screen.foreground()}

    def _do_windows(self, params: dict) -> dict:
        return {"action": "windows", "windows": self.screen.windows()}

    def _do_focus(self, params: dict) -> dict:
        selector = _inline_selector(params, required=True)
        wait = _wait_seconds(params, 5.0)
        deadline = time.monotonic() + wait
        info = None
        while True:
            info = self.screen.find_window(**selector)
            if info is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if info is None:
            raise WindowNotFound(f"no window matches {selector!r}; call desktop_windows to see what is open")
        focused = self.screen.focus_window(info["hwnd"])
        return {"action": "focus", "window": info, "focused": focused}

    def _do_launch(self, params: dict) -> dict:
        command = _text(params, "command")
        args = params.get("args")
        if args is None:
            args = []
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise BadParams(f"args must be a list of strings, got {args!r}")
        wait = _wait_seconds(params, 10.0)
        selector = _inline_selector(params, required=False)
        proc = self.launcher(command, args)
        pid = proc.pid
        deadline = time.monotonic() + wait
        info = None
        while True:
            if selector:
                info = self.screen.find_window(**selector)
            else:
                info = next((w for w in self.screen.windows() if w.get("pid") == pid), None)
            if info is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        return {"action": "launch", "pid": pid, "window": info, "found": info is not None}

    def _do_clipboard(self, params: dict) -> dict:
        op = _choice(params, "op", ("get", "set", "paste"), None)
        if op is None:
            raise BadParams("op is required: get, set or paste")
        if op == "get":
            text = self.clipboard.get_text()
            return {"action": "clipboard", "op": "get", "text": text[:100_000],
                    "characters": len(text), "truncated": len(text) > 100_000}
        text = _text(params, "text")
        if len(text) > 200_000:
            raise BadParams("text must be at most 200000 characters")
        # Paste puts long or non-Latin text in one step instead of typing it character by character.
        self.clipboard.set_text(text)
        if op == "paste":
            self.inputs.press("ctrl+v")
        return {"action": "clipboard", "op": op, "characters": len(text)}

    def _do_notify(self, params: dict) -> dict:
        text = _text(params, "text")
        if not text or len(text) > NOTIFY_MAX_CHARS:
            raise BadParams(f"text must be 1 to {NOTIFY_MAX_CHARS} characters")
        seconds = _number(params, "seconds", 0.5, 30.0) if "seconds" in params else 4.0
        self.overlay.show(text, "notify", seconds)
        return {"action": "notify", "seconds": seconds}

    def _do_record(self, params: dict) -> dict:
        op = _choice(params, "op", ("start", "stop", "status"), None)
        if op is None:
            raise BadParams("op is required: start, stop or status")
        if self.recorder is None:
            raise ActionError("recording is not available here")
        if op == "start":
            name = _text(params, "name", required=False)
            describe = _text(params, "describe", required=False)
            if describe is not None and (len(describe) > NOTIFY_MAX_CHARS or "\n" in describe or "\r" in describe):
                raise BadParams(f"describe must be one line of at most {NOTIFY_MAX_CHARS} characters")
            return self.recorder.start(name, describe)
        if op == "stop":
            return self.recorder.stop()
        return self.recorder.status()

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
            if getattr(executor, "window", None) is None:
                # The virtual monitor has index 0, which is not a valid request; note it as "all".
                noted_monitor = "all" if executor.monitor.index == 0 else executor.monitor.index
                self._noted = (executor.max_edge, noted_monitor, _layout_of(self.screen.monitors()))
        payload["image"] = list(image.size)
        payload["cursor_drawn"] = executor.cursor
        payload["source"] = executor.last_source
        # Keys go to whatever is in front, so a shot says what that is.
        if getattr(executor, "window", None) is None:
            payload["window"] = self.screen.foreground()
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
        through = _points(params, "through", MAX_WAYPOINTS)
        modifiers = _text(params, "modifiers", required=False)
        executor, _ = self._executor(params)
        executor.dispatch("left_click_drag", {"start_coordinate": start, "coordinate": end,
                                              "through": through, "text": modifiers})
        return {"action": "left_click_drag", "from": start, "to": end, "through": len(through)}

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
