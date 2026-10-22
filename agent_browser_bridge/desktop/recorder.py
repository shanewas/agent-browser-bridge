"""Record a person's clicks and keys, with a shot per click, and write them up as a skill.

The low-level hooks run on their own thread and only enqueue; a worker thread grabs the
screenshots and writes the files, so a hook callback never trips the 300 ms limit after which
Windows drops it. Everything appends to events.jsonl as it happens, so a crash loses nothing.
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import json
import queue
import re
import threading
import time
from pathlib import Path

from . import inputs as _inputs
from .errors import ActionError
from .overlay import NullOverlay

MAX_EVENTS = 2000
MAX_SECONDS = 1800
VIEW_EDGE = 1280
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
#: IME plumbing keys: the composed text never reaches a keyboard hook, so these only add noise.
_IME_VKS = {0x1C, 0x1D, 0xE5, 0xF3, 0xF4}
_KEY_NAMES: dict[int, str] = {}
for _name, _vk in _inputs._NAMED_KEYS.items():
    _KEY_NAMES.setdefault(_vk, _name)
_KEY_NAMES.update({0x6A: "*", 0x6B: "plus", 0x6C: ",", 0x6D: "minus", 0x6E: "period", 0x6F: "slash"})
_BUTTON_STEP = {"left": "click", "right": "right-click", "middle": "middle-click"}
_COUNT_STEP = {2: "double-", 3: "triple-"}
WHEEL_DELTA = 120


def key_name(vk: int) -> str:
    """The name `desktop_key` accepts for a virtual-key code."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return chr(vk - 0x30)
    return _KEY_NAMES.get(vk, f"vk{vk:02x}")


class Recorder:
    def __init__(self, screen, home, hooks_factory=None, overlay=None, clock=time.monotonic):
        self.screen = screen
        self.home = Path(home)
        self.hooks_factory = hooks_factory or Hooks
        self.overlay = overlay if overlay is not None else NullOverlay()
        self.clock = clock
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._hooks = None
        self._active: dict | None = None
        self._last: dict | None = None

    def start(self, name: str | None, describe: str | None) -> dict:
        with self._lock:
            if self._active:
                raise ActionError(f"a recording is already running: {self._active['name']}")
        if isinstance(self.overlay, NullOverlay):
            raise ActionError("recording needs the on-screen overlay, which is off here "
                              "(EDGE_BRIDGE_NO_OVERLAY, or the strip could not be created); "
                              "the person must be able to see that keys are being kept")
        name = name or _dt.datetime.now().strftime("rec-%Y%m%d-%H%M%S")
        if not NAME_RE.match(name):
            raise ActionError("name must be 1 to 40 letters, digits, _ or -")
        folder = self.home / "recordings" / name
        if folder.exists():
            raise ActionError(f"{folder} already exists; pick another name")
        display = tuple(self.screen.primary_monitor().size)
        folder.mkdir(parents=True)
        active = {"name": name, "dir": folder, "describe": describe, "started": self.clock(),
                  "events": [], "shots": 0, "display": display, "wheel": {},
                  "file": open(folder / "events.jsonl", "a", encoding="utf-8")}
        with self._lock:
            self._active = active
        try:
            self._worker = threading.Thread(target=self._drain, name="recorder", daemon=True)
            self._worker.start()
            self._hooks = self.hooks_factory(self._sink)
            self._hooks.start()
            self.overlay.record(f"Recording {name}: clicks and keys are being saved. "
                                "Stop: agent-bridge desktop record stop")
        except Exception:
            # Nothing half-started may linger: the next start must be a clean one.
            with self._lock:
                self._active = None
            active["file"].close()
            self._queue.put(None)
            self._worker = self._hooks = None
            for path in folder.iterdir():
                path.unlink()
            folder.rmdir()
            raise
        return {"recording": True, "name": name, "dir": str(folder)}

    def stop(self) -> dict:
        with self._lock:
            if not self._active:
                raise ActionError("no recording is running")
        self._hooks.stop()
        self.flush()
        # The worker may have finished the recording at the event limit while we waited.
        result = self._finish(None) or self._last
        if self._worker is not None:
            self._worker.join(5.0)
            self._worker = None
        return result

    def status(self) -> dict:
        with self._lock:
            active = self._active
        if not active:
            return {"recording": False}
        return {"recording": True, "name": active["name"], "events": len(active["events"]),
                "seconds": round(self.clock() - active["started"], 3)}

    def close(self) -> None:
        with self._lock:
            running = self._active is not None
        if running:
            self.stop()

    def flush(self) -> None:
        """Wait until every queued event has been written; tests and stop() use it."""
        self._queue.join()

    # -- the hook side: any thread, must return at once ------------------------------------

    def _sink(self, kind: str, data: dict) -> None:
        self._queue.put((self.clock(), kind, dict(data)))

    # -- the worker ------------------------------------------------------------------------

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                self._handle(*item)
            except Exception as exc:  # noqa: BLE001 - one bad event must not end the recording
                print(f"recorder: dropped a {item[1]} event: {type(exc).__name__}: {exc}", flush=True)
            finally:
                self._queue.task_done()

    def _handle(self, t: float, kind: str, data: dict) -> None:
        with self._lock:
            active = self._active
        if not active:
            return
        event = {"t": round(t - active["started"], 3), "kind": kind}
        if kind == "click":
            x, y = int(data["display"][0]), int(data["display"][1])
            monitor = self._monitor_at(x, y)
            scale = min(1.0, VIEW_EDGE / max(monitor.size))
            from .executor import fit
            image = fit(self.screen.grab(monitor.box, cursor=False), VIEW_EDGE, fast=True)
            active["shots"] += 1
            shot = f"{active['shots']:03d}.jpg"
            image.convert("RGB").save(active["dir"] / shot, "JPEG", quality=85)
            event.update(button=data["button"], display=[x, y], monitor=monitor.index,
                         view=[round((x - monitor.origin[0]) * scale), round((y - monitor.origin[1]) * scale)],
                         window=data.get("window", ""), shot=shot)
        elif kind == "wheel":
            direction = data["direction"]
            if "delta" in data:
                # Precision touchpads send many small deltas; a notch is 120 of them.
                total = active["wheel"].get(direction, 0) + abs(int(data["delta"]))
                amount, active["wheel"][direction] = divmod(total, WHEEL_DELTA)
                if amount == 0:
                    return
            else:
                amount = int(data["amount"])
            event.update(direction=direction, amount=amount,
                         display=list(data["display"]), window=data.get("window", ""))
        elif kind == "key":
            vk = int(data["vk"])
            if vk in _MODIFIER_VKS or vk in _IME_VKS:
                return
            event.update(vk=vk, name=data.get("char") or key_name(vk), modifiers=list(data.get("modifiers", [])),
                         window=data.get("window", ""))
        else:
            return
        active["events"].append(event)
        active["file"].write(json.dumps(event) + "\n")
        active["file"].flush()
        if len(active["events"]) >= MAX_EVENTS:
            self._finish(f"{MAX_EVENTS} events reached")
        elif t - active["started"] >= MAX_SECONDS:
            self._finish(f"{MAX_SECONDS // 60} minutes reached")

    def _monitor_at(self, x: int, y: int):
        for m in self.screen.monitors():
            if m.origin[0] <= x < m.origin[0] + m.size[0] and m.origin[1] <= y < m.origin[1] + m.size[1]:
                return m
        return self.screen.primary_monitor()

    def _finish(self, reason: str | None) -> dict | None:
        with self._lock:
            active, self._active = self._active, None
        if not active:
            return None
        self._hooks.stop()
        active["file"].close()
        skill = active["dir"] / "SKILL.md"
        skill.write_text(write_skill(active["name"], active["describe"], active["display"], active["events"]),
                         encoding="utf-8")
        self.overlay.record(None)
        if reason:
            self.overlay.show(f"Recording {active['name']} stopped: {reason}", "notify", 6)
        self._queue.put(None)
        self._last = {"recording": False, "name": active["name"], "dir": str(active["dir"]), "skill": str(skill),
                      "events": len(active["events"]), "shots": active["shots"]}
        return self._last


def _merge(events: list[dict]) -> list[dict]:
    """Two quick clicks on one spot are a double-click; consecutive wheel notches add up."""
    merged: list[dict] = []
    for event in events:
        last = merged[-1] if merged else None
        if (last and event["kind"] == "click" == last["kind"] and last.get("count", 1) < 3
                and event["button"] == last["button"] and event["window"] == last["window"]
                and event["t"] - last["t"] <= 0.5
                and abs(event["view"][0] - last["view"][0]) <= 3 and abs(event["view"][1] - last["view"][1]) <= 3):
            last["count"] = last.get("count", 1) + 1
            last["t"] = event["t"]
        elif last and event["kind"] == "wheel" == last["kind"] and event["direction"] == last["direction"]:
            last["amount"] += event["amount"]
        else:
            merged.append(dict(event))
    return merged


def _plain(text: str) -> str:
    """One line with no Markdown emphasis or code marks, so a window title cannot reshape a step."""
    return re.sub(r"\s+", " ", text.replace("*", "").replace("`", "")).strip()


def write_skill(name: str, describe: str | None, display: tuple[int, int], events: list[dict]) -> str:
    """Events in, Markdown out. Printable keys between other events collapse into one Type step."""
    scale = min(1.0, VIEW_EDGE / max(display))
    description = describe or "Recorded on the desktop, " + _dt.date.today().isoformat()
    lines = ["---", f"name: {name}", f"description: {json.dumps(description, ensure_ascii=False)}",
             "---", "", f"# {name}", "",
             f"Recorded on the Windows desktop; the primary display is {display[0]}×{display[1]}. Each step "
             f"names its display: take the shot with `monitor: N` and the default `maxEdge`, so coordinates "
             f"are view pixels of a {VIEW_EDGE}-long-edge view of that display (scale {scale:.4f} on the "
             "primary), the space `desktop_shot` answers by default. To replay: take a fresh `desktop_shot`, "
             "check that `window` names the same application as the step, then run the step with "
             "`agent-bridge desktop <verb>` or the matching `desktop_*` tool, and shoot again before the "
             "next one.", "",
             "Typed text below is reproduced verbatim. Scrub anything secret (a password typed during the "
             "demonstration) before keeping or sharing this file.", "", "## Steps", ""]
    steps: list[str] = []
    typed: list[str] = []

    def flush_typing():
        if typed:
            text = "".join(typed).replace("\\", "\\\\").replace('"', '\\"')
            steps.append(f'Type "{text}".')
            typed.clear()

    for event in _merge(events):
        kind = event["kind"]
        if kind == "key":
            modifiers = list(event.get("modifiers", []))
            others = [m for m in modifiers if m != "shift"]
            key = event["name"]
            printable = len(key) == 1 or key == "space"
            if printable and not others:
                char = " " if key == "space" else key
                if "shift" not in modifiers:
                    typed.append(char)
                    continue
                if char.isalpha():
                    typed.append(char.upper())
                    continue
            flush_typing()
            if key.startswith("vk"):
                steps.append(f"Press {key} (a key desktop_key does not know; edit this step).")
            else:
                steps.append(f"Press {'+'.join([*modifiers, key])}.")
        elif kind == "click":
            flush_typing()
            x, y = event["view"]
            verb = _COUNT_STEP.get(event.get("count", 1), "") + _BUTTON_STEP.get(event["button"], "click")
            steps.append(f"In **{_plain(event['window'])}** (display {event.get('monitor', 1)}), {verb} at "
                         f"({x}, {y}). ![step {len(steps) + 1}]({event['shot']})")
        elif kind == "wheel":
            flush_typing()
            steps.append(f"Scroll {event['direction']} {event['amount']}.")
    flush_typing()
    lines += [f"{i}. {step}" for i, step in enumerate(steps, 1)] or ["(no events were recorded)"]
    return "\n".join(lines) + "\n"


class Hooks:
    """WH_MOUSE_LL and WH_KEYBOARD_LL on their own thread, feeding `sink(kind, data)`."""

    WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
    WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x201, 0x204, 0x207
    WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x20A, 0x20E
    WM_KEYDOWN, WM_SYSKEYDOWN, WM_QUIT = 0x100, 0x104, 0x12
    LLMHF_INJECTED, LLKHF_INJECTED = 0x1, 0x10
    _BUTTONS = {0x201: "left", 0x204: "right", 0x207: "middle"}
    _MODS = (("ctrl", 0x11), ("shift", 0x10), ("alt", 0x12), ("super", 0x5B), ("super", 0x5C))

    def __init__(self, sink):
        self.sink = sink
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self.error: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hooks", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0):
            self.error = "the hook thread did not start"
        if self.error:
            self.stop()
            raise ActionError(f"could not install the input hooks: {self.error}")

    def stop(self) -> None:
        if self._thread and self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)
            self._thread.join(3.0)
        self._thread = None

    def _run(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        def window_title():
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 256)
            return buf.value

        def modifiers():
            held = [name for name, vk in self._MODS if user32.GetAsyncKeyState(vk) & 0x8000]
            return list(dict.fromkeys(held))

        def on_mouse(code, wparam, lparam):
            if code >= 0:
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not info.flags & self.LLMHF_INJECTED:  # the bridge's own clicks are not the person's
                    if wparam in self._BUTTONS:
                        self.sink("click", {"button": self._BUTTONS[wparam], "display": [info.pt.x, info.pt.y],
                                            "window": window_title()})
                    elif wparam in (self.WM_MOUSEWHEEL, self.WM_MOUSEHWHEEL):
                        delta = ctypes.c_short(info.mouseData >> 16).value
                        if wparam == self.WM_MOUSEWHEEL:
                            direction = "up" if delta > 0 else "down"
                        else:
                            direction = "right" if delta > 0 else "left"
                        self.sink("wheel", {"direction": direction, "delta": delta,
                                            "display": [info.pt.x, info.pt.y], "window": window_title()})
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def on_key(code, wparam, lparam):
            if code >= 0 and wparam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not info.flags & self.LLKHF_INJECTED:
                    data = {"vk": info.vkCode, "modifiers": modifiers(), "window": window_title()}
                    if info.vkCode == 0xE7 and info.scanCode:  # VK_PACKET carries a Unicode unit
                        data["char"] = chr(info.scanCode)
                    self.sink("key", data)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._procs = (HOOKPROC(on_mouse), HOOKPROC(on_key))  # kept alive while hooked
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        handles = [user32.SetWindowsHookExW(self.WH_MOUSE_LL, self._procs[0], None, 0),
                   user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._procs[1], None, 0)]
        if not all(handles):
            self.error = f"SetWindowsHookEx failed (error {ctypes.get_last_error()})"
        self._ready.set()
        if self.error:
            for handle in handles:
                if handle:
                    user32.UnhookWindowsHookEx(handle)
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for handle in handles:
                if handle:
                    user32.UnhookWindowsHookEx(handle)
