"""Client library for Edge Agent Bridge.

Provides the Edge class (and EdgeClient alias), session pinning, connection pooling,
and convenience methods for all browser actions.
"""
import base64
import http.client
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

from . import config


LADDER_ACTIONS = frozenset({
    "click", "dblclick", "rightclick", "hover", "fill", "type",
    "select", "check_radio", "upload", "drag",
})
LADDER_RETRY_CODES = frozenset({"stale_ref", "stale_snapshot", "target_not_found"})
WRITE_ACTIONS = frozenset({"fill", "type"})
REPIN_ACTIONS = frozenset({"tab_switch", "switch_tab", "tab_new"})
# The daemon's deadline outlasts the browser's own wait by this much, and the client's socket outlasts
# the daemon's deadline by the same, so each layer's timeout answer arrives before the caller gives up.
RESPONSE_MARGIN_SEC = 5


def _browser_wait_ms(action: str, params: dict) -> float:
    """How long a call asks the browser to wait or sleep: its `timeout`, or a batch's sleeps and step timeouts."""
    steps = params.get("steps") if action == "batch" else [dict(params, action=action)]
    total = 0.0
    for step in steps if isinstance(steps, list) else ():
        if isinstance(step, dict):
            value = step.get("ms") if step.get("action") in ("sleep", "wait_ms") else step.get("timeout")
            if isinstance(value, (int, float)):
                total += max(0.0, value)
    return total


def _peer_closed(sock) -> bool:
    """Whether the daemon closed this idle keep-alive socket: between calls it turns readable only then."""
    with selectors.DefaultSelector() as sel:
        sel.register(sock, selectors.EVENT_READ)
        return bool(sel.select(0))


def _wsl_to_windows(path_str: str) -> str:
    """Rewrite a WSL /mnt/<drive>/... path to Windows form (C:\\...).

    Shape-only: a native Windows path never matches, so this runs on every
    platform and leaves anything else untouched.
    """
    if (len(path_str) > 7 and path_str.startswith("/mnt/")
            and (("a" <= path_str[5] <= "z") or ("A" <= path_str[5] <= "Z"))
            and path_str[6] == "/"):
        return path_str[5].upper() + ":\\" + path_str[7:].replace("/", "\\")
    return path_str


def score_element_match(entries, text):
    """The scan rung's match: an exact name, else a case-insensitive name,
    and only when exactly one element matches. Substring matches never
    count: clicking a different element whose name merely contains the
    target text is the failure the ladder exists to avoid (issue #19)."""
    def name(e):
        return e.get("text") or e.get("name") or ""
    entries = entries or []
    exact = [e for e in entries if name(e) == text]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    tl = (text or "").lower()
    if not tl:
        return None
    folded = [e for e in entries if name(e).lower() == tl]
    return folded[0] if len(folded) == 1 else None


def _probe_status(port: int) -> dict | None:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        conn.request("GET", "/status")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return data
    except Exception:
        return None


def ensure_bridge_running(port=None, wait_for_extension=True, timeout=12.0) -> bool:
    """Ensure the bridge daemon is running, spawning it if necessary."""
    p = port or config.port()
    status = _probe_status(p)
    if status and status.get("bridge_running"):
        if not wait_for_extension or status.get("websocket_active"):
            return True

    if status is None:
        cmd = [sys.executable, "-u", "-m", "edge_agent_bridge.bridge", "--port", str(p)]
        env = dict(os.environ)
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS"):
                flags |= subprocess.DETACHED_PROCESS
            try:
                subprocess.Popen(cmd, env=env, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _probe_status(p)
        if status and status.get("bridge_running"):
            if not wait_for_extension or status.get("websocket_active"):
                return True
        time.sleep(0.1)
    return False


class Edge:
    """Persistent client for Edge Agent Bridge with connection pooling and session pinning."""

    def __init__(
        self,
        tab_id=None,
        port=None,
        timeout=20,
        highlight=True,
        pin=True,
        auto_start=True,
        token=None,
        session_token=None,
    ):
        self.tab_id = tab_id
        self.port = port or config.port()
        self.timeout = timeout
        self.highlight = highlight
        self.pin = pin
        self.auto_start = auto_start
        self.token = token
        self.session_token = session_token
        self._ref_texts = {}
        self._degraded_warned = False
        self._reminting = False
        self._last_tab_id = None
        self._conn: http.client.HTTPConnection | None = None

    def _get_connection(self, timeout: float) -> http.client.HTTPConnection:
        # A request written to a socket the daemon already closed is lost after it leaves, and
        # nothing tells whether it ran, so such a socket is replaced before anything is sent.
        if self._conn is not None and self._conn.sock is not None and _peer_closed(self._conn.sock):
            self.close()
        if self._conn is None:
            self._conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        self._conn.timeout = timeout
        if self._conn.sock is not None:
            self._conn.sock.settimeout(timeout)
        return self._conn

    def _send_once(self, action: str, params: dict | None = None, timeout: int | None = None) -> dict:
        """Send an action to the daemon over loopback HTTP POST /exec."""
        if self.auto_start:
            status = _probe_status(self.port)
            if status is None or not status.get("bridge_running"):
                ensure_bridge_running(port=self.port, wait_for_extension=False, timeout=6.0)

        token = self.token or config.read_token()
        if token is None:
            status = _probe_status(self.port)
            if status is None or not status.get("bridge_running"):
                return {
                    "success": False,
                    "code": "daemon_unreachable",
                    "error": f"Cannot reach bridge daemon on 127.0.0.1:{self.port}",
                }
            return {
                "success": False,
                "code": "missing_token",
                "error": f"Missing bridge token at {config.token_path()}",
            }

        p = dict(params or {})
        if self.pin and self.tab_id is not None and self.session_token is None and "tabId" not in p:
            p["tabId"] = self.tab_id
        if not self.highlight and "highlight" not in p:
            p["highlight"] = False

        asked_s = _browser_wait_ms(action, p) / 1000
        if asked_s + RESPONSE_MARGIN_SEC > config.MAX_CALL_TIMEOUT_SEC:
            return {
                "success": False,
                "code": "bad_params",
                "error": f"{action} asks the browser to wait {asked_s:g} s, but one call can wait at most "
                         f"{config.MAX_CALL_TIMEOUT_SEC - RESPONSE_MARGIN_SEC:g} s; split it into shorter calls",
            }
        t = max(config.call_timeout(timeout or self.timeout), asked_s + RESPONSE_MARGIN_SEC)
        body = {"action": action, "params": p, "timeout": t}
        if self.session_token is not None:
            body["sessionToken"] = self.session_token
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Host": f"127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            config.TOKEN_HEADER: token,
        }

        wait_s = t + RESPONSE_MARGIN_SEC
        for attempt in range(2):
            conn = self._get_connection(wait_s)
            try:
                conn.request("POST", "/exec", body=payload, headers=headers)
                break
            except (OSError, http.client.HTTPException) as e:
                # The daemon never got the whole request, so a second try on a fresh connection can't run it twice.
                self.close()
                if attempt == 1:
                    return {
                        "success": False,
                        "code": "daemon_unreachable",
                        "error": f"Cannot reach bridge daemon on 127.0.0.1:{self.port}: {e}",
                    }
        # The daemon may run the command from here on, so a failure is reported and never retried.
        try:
            raw = conn.getresponse().read().decode("utf-8", errors="replace")
        except TimeoutError:
            self.close()
            return {
                "success": False,
                "code": "timeout",
                "error": f"No answer to {action} within {wait_s:g} s; it may have run in the browser",
            }
        except (OSError, http.client.HTTPException) as e:
            self.close()
            return {
                "success": False,
                "code": "connection_lost",
                "error": f"Lost the connection to the daemon after sending {action}; it may have run in the browser: {e}",
            }
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {"success": False, "code": "bad_response", "error": raw}

        # Session pinning maintenance
        if self.pin and self.session_token is None:
            if action in ("tab_close", "close_tab") and p.get("tabId") == self.tab_id:
                self.tab_id = None
            if data.get("code") == "tab_not_found":
                self.tab_id = None
            # A failure can name the tab it refused; pinning to that tab would strand every later call.
            tab_info = data.get("tab") if data.get("success") else None
            if isinstance(tab_info, dict) and tab_info.get("id") is not None:
                repin = action in REPIN_ACTIONS or "tabId" in (params or {})
                if repin or self.tab_id is None:
                    self.tab_id = tab_info["id"]

        # Remember the tab for session re-mint re-pin. One-shot overrides (an
        # explicit tabId on a non-repin action) don't count: the server pin
        # stays where it was, so the remembered tab must too.
        if (params or {}).get("tabId") is None or action in REPIN_ACTIONS:
            tab_seen = data.get("tab") if data.get("success") else None
            if isinstance(tab_seen, dict) and tab_seen.get("id") is not None:
                try:
                    self._last_tab_id = int(tab_seen["id"])
                except (TypeError, ValueError):
                    pass

        return data

    def send(self, action: str, params: dict | None = None, timeout: int | None = None) -> dict:
        p = dict(params or {})
        fallback = p.pop("fallback", True)
        data = self._send_once(action, p, timeout)
        if data.get("code") == "unknown_session" and self.session_token and not self._reminting:
            self._reminting = True
            try:
                mint = self._send_once("session_start", {})
                if mint.get("success") and mint.get("sessionToken"):
                    self.session_token = mint["sessionToken"]
                    if self._last_tab_id is not None:
                        repin = self._send_once("tab_switch", {"tabId": self._last_tab_id}, timeout)
                        if not repin.get("success"):
                            # Tab is gone; stay unpinned so the retry resolves
                            # the active tab like a fresh session.
                            self._last_tab_id = None
                    data = self._send_once(action, p, timeout)
            finally:
                self._reminting = False
        if action == "elements" and data.get("success"):
            for entry in data.get("elements") or []:
                ref = entry.get("ref")
                text = entry.get("text") or entry.get("name")
                if ref and text:
                    self._ref_texts[ref] = text
            while len(self._ref_texts) > 1000:
                self._ref_texts.pop(next(iter(self._ref_texts)))
        if action in LADDER_ACTIONS and fallback is not False and not self._has_explicit_coords(action, p):
            data = self._ladder(action, p, data, timeout)
        if action in WRITE_ACTIONS and isinstance(data, dict):
            data = self._verify_write(action, p, data, timeout)
        return data

    @staticmethod
    def _has_explicit_coords(action, params) -> bool:
        if action == "drag":
            for end in (params.get("from"), params.get("to")):
                if isinstance(end, dict) and "x" in end and "y" in end:
                    return True
            return False
        return "x" in params and "y" in params

    def _ladder(self, action, params, first, timeout):
        if first.get("success") or first.get("code") not in LADDER_RETRY_CODES:
            return first
        tried = [{"step": "ref" if "ref" in params else "target", "outcome": first.get("code")}]
        if action == "drag":
            return self._ladder_drag(params, tried, timeout)
        locator_text = params.get("target") or params.get("selector")
        if action not in WRITE_ACTIONS:
            locator_text = locator_text or params.get("text")
        if locator_text is None and "ref" in params:
            locator_text = self._ref_texts.get(params["ref"])
        if locator_text is not None:
            rung2 = {}
            for k, v in params.items():
                if k in ("ref", "selector", "target"):
                    continue
                if k == "text" and action not in WRITE_ACTIONS:
                    continue
                rung2[k] = v
            rung2["target"] = locator_text
            second = self._send_once(action, rung2, timeout)
            tried.append({"step": "text", "outcome": "ok" if second.get("success") else second.get("code")})
            if second.get("success"):
                second["ladder"] = {"rung": "text", "tried": tried}
                return second
            if second.get("code") not in LADDER_RETRY_CODES:
                return second
        else:
            tried.append({"step": "text", "outcome": "no text target"})
            return {"success": False, "code": "exhausted_fallback",
                    "error": f"{action} target could not be re-resolved", "tried": tried}
        scan_params = {"tabId": params["tabId"]} if "tabId" in params else {}
        match = None
        for _ in range(2):
            scan = self._send_once("elements", scan_params, timeout)
            if not scan.get("success"):
                tried.append({"step": "scan", "outcome": scan.get("code")})
                break
            match = score_element_match(scan.get("elements"), locator_text)
            if match is not None:
                tried.append({"step": "scan", "outcome": "match"})
                break
            tried.append({"step": "scan", "outcome": "0 matches"})
        if match is None:
            return {"success": False, "code": "exhausted_fallback",
                    "error": f"{action} target could not be re-resolved", "tried": tried}
        rung4 = {}
        for k, v in params.items():
            if k in ("ref", "selector", "target", "x", "y"):
                continue
            if k == "text" and action not in WRITE_ACTIONS:
                continue
            rung4[k] = v
        rung4["x"] = match["x"]
        rung4["y"] = match["y"]
        final = self._send_once(action, rung4, timeout)
        tried.append({"step": "coords", "outcome": "ok" if final.get("success") else final.get("code")})
        if final.get("success"):
            final["ladder"] = {"rung": "coords", "tried": tried,
                               "matched": {"name": match.get("text") or match.get("name") or "",
                                           "ref": match.get("ref")}}
            return final
        return {"success": False, "code": "exhausted_fallback",
                "error": f"{action} target could not be re-resolved", "tried": tried}

    def _ladder_drag(self, params, tried, timeout):
        tried.append({"step": "text", "outcome": "n/a for drag"})
        scan_params = {"tabId": params["tabId"]} if "tabId" in params else {}
        coords = {}
        for key in ("from", "to"):
            end = params.get(key)
            if isinstance(end, str):
                scan = self._send_once("elements", scan_params, timeout)
                if not scan.get("success"):
                    tried.append({"step": "scan", "outcome": scan.get("code")})
                    break
                match = score_element_match(scan.get("elements"), end)
                if match is None:
                    tried.append({"step": "scan", "outcome": "0 matches"})
                    break
                tried.append({"step": "scan", "outcome": "match"})
                coords[key] = match
        if len(coords) != 2:
            return {"success": False, "code": "exhausted_fallback",
                    "error": "drag endpoints could not be re-resolved", "tried": tried}
        rung = {k: v for k, v in params.items() if k not in ("from", "to")}
        rung["fromX"] = coords["from"]["x"]
        rung["fromY"] = coords["from"]["y"]
        rung["toX"] = coords["to"]["x"]
        rung["toY"] = coords["to"]["y"]
        final = self._send_once("drag", rung, timeout)
        tried.append({"step": "coords", "outcome": "ok" if final.get("success") else final.get("code")})
        if final.get("success"):
            final["ladder"] = {"rung": "coords", "tried": tried}
            return final
        return {"success": False, "code": "exhausted_fallback",
                "error": "drag endpoints could not be re-resolved", "tried": tried}

    def _verify_write(self, action, params, data, timeout):
        if not data.get("success"):
            return data
        if data.get("match") is True:
            return data
        if "match" not in data and "readback" not in data or data.get("match") == "unknown":
            data["match"] = "unknown"
            if not self._degraded_warned:
                self._degraded_warned = True
                print("edge-bridge: extension predates verified writes (no readback); continuing unverified",
                      file=sys.stderr)
                data["notice"] = "extension predates verified writes; match unknown"
            return data
        # A clearing fill would wipe rich-editor markup, and type's expected value guesses the caret
        # position, so neither is retried.
        if action == "type" or data.get("field") == "contenteditable":
            return data
        expected = data.get("written")
        if expected is None:
            expected = params.get("text", "")
        retry_params = {k: v for k, v in params.items() if k != "text"}
        retry_params["text"] = expected
        retry_params["clear"] = True
        retry = self._send_once("fill", retry_params, timeout)
        if retry.get("success") and retry.get("match") in (True, "unknown"):
            retry["retried"] = True
            return retry
        return {"success": False, "code": "write_mismatch",
                "error": f"Write verification failed after retry: expected {expected!r}, read back {retry.get('readback')!r}",
                "expected": expected, "readback": retry.get("readback"), "attempts": 2}

    def tab(self) -> dict:
        return self.send("tab")

    def tabs(self) -> dict:
        return self.send("tabs")

    def ping(self) -> dict:
        return self.send("ping")

    def tab_switch(self, tab_id=None) -> dict:
        p = {}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("tab_switch", p)

    def tab_new(self, url="about:blank", group=None, window=False, active=True) -> dict:
        p = {"url": url, "window": window, "active": active}
        if group is not None:
            p["group"] = group
        return self.send("tab_new", p)

    def tab_close(self, tab_id=None) -> dict:
        p = {}
        target_id = tab_id if tab_id is not None else self.tab_id
        if target_id is not None:
            p["tabId"] = target_id
        return self.send("tab_close", p)

    def nav(self, url: str, wait: str = "load", tab_id=None) -> dict:
        p = {"url": url, "wait": wait}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("nav", p)

    def back(self, wait: str = "load", tab_id=None) -> dict:
        p = {"wait": wait}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("back", p)

    def forward(self, wait: str = "load", tab_id=None) -> dict:
        p = {"wait": wait}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("forward", p)

    def reload(self, wait: str = "load", tab_id=None) -> dict:
        p = {"wait": wait}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("reload", p)

    def snapshot(self, mode: str = "interactive", frames: bool = True, tab_id=None, max_nodes: int = 400) -> dict:
        p = {"mode": mode, "frames": frames, "maxNodes": max_nodes}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("snapshot", p)

    def elements(self, filter: str | None = None, tab_id=None) -> dict:
        p = {}
        if filter:
            p["filter"] = filter
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("elements", p)

    def _resolve_target_params(self, target, selector, text, ref, x, y, button, modifiers, tab_id):
        p = {"button": button}
        if ref is not None:
            p["ref"] = ref
        elif selector is not None:
            p["selector"] = selector
        elif text is not None:
            p["text"] = text
        elif target is not None:
            p["target"] = target
        elif x is not None and y is not None:
            p["x"] = x
            p["y"] = y
        if modifiers:
            p["modifiers"] = modifiers
        if tab_id is not None:
            p["tabId"] = tab_id
        return p

    def click(self, target=None, selector=None, text=None, ref=None, x=None, y=None, button="left", modifiers=None, tab_id=None, fallback=True) -> dict:
        p = self._resolve_target_params(target, selector, text, ref, x, y, button, modifiers, tab_id)
        if fallback is False:
            p["fallback"] = False
        return self.send("click", p)

    def dblclick(self, target=None, selector=None, text=None, ref=None, x=None, y=None, button="left", modifiers=None, tab_id=None, fallback=True) -> dict:
        p = self._resolve_target_params(target, selector, text, ref, x, y, button, modifiers, tab_id)
        if fallback is False:
            p["fallback"] = False
        return self.send("dblclick", p)

    def rightclick(self, target=None, selector=None, text=None, ref=None, x=None, y=None, tab_id=None, fallback=True) -> dict:
        p = self._resolve_target_params(target, selector, text, ref, x, y, "right", None, tab_id)
        if fallback is False:
            p["fallback"] = False
        return self.send("rightclick", p)

    def hover(self, target=None, selector=None, text=None, ref=None, x=None, y=None, tab_id=None, fallback=True) -> dict:
        p = self._resolve_target_params(target, selector, text, ref, x, y, "left", None, tab_id)
        if fallback is False:
            p["fallback"] = False
        return self.send("hover", p)

    def drag(self, from_target, to_target, steps: int = 10, tab_id=None, fallback=True) -> dict:
        p = {"from": from_target, "to": to_target, "steps": steps}
        if tab_id is not None:
            p["tabId"] = tab_id
        if fallback is False:
            p["fallback"] = False
        return self.send("drag", p)

    def fill(self, target=None, text: str = "", ref=None, append: bool = False, clear: bool = True, tab_id=None, fallback=True) -> dict:
        p = {"text": text, "append": append, "clear": clear}
        if ref is not None:
            p["ref"] = ref
        elif target is not None:
            p["target"] = target
        if tab_id is not None:
            p["tabId"] = tab_id
        if fallback is False:
            p["fallback"] = False
        return self.send("fill", p)

    def type(self, text: str, delay: int = 20, ref=None, target=None, tab_id=None, fallback=True) -> dict:
        p = {"text": text, "delay": delay}
        if ref is not None:
            p["ref"] = ref
        elif target is not None:
            p["target"] = target
        if tab_id is not None:
            p["tabId"] = tab_id
        if fallback is False:
            p["fallback"] = False
        return self.send("type", p)

    def key(self, key: str, tab_id=None) -> dict:
        p = {"key": key}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("key", p)

    def check_radio(self, target=None, selector: str = None, text: str = None, value: str = None, tab_id=None, fallback=True) -> dict:
        """Check a radio button or checkbox by selector, label text, or value attribute."""
        p = {}
        if selector is not None:
            p["selector"] = selector
        elif target is not None:
            p["target"] = target
        if text is not None:
            p["text"] = text
        if value is not None:
            p["value"] = str(value)
        if tab_id is not None:
            p["tabId"] = tab_id
        if fallback is False:
            p["fallback"] = False
        return self.send("check_radio", p)

    def select(self, target=None, ref=None, value=None, label=None, tab_id=None, fallback=True) -> dict:
        p = {}
        if ref is not None:
            p["ref"] = ref
        elif target is not None:
            p["target"] = target
        if value is not None:
            p["value"] = value
        if label is not None:
            p["label"] = label
        if tab_id is not None:
            p["tabId"] = tab_id
        if fallback is False:
            p["fallback"] = False
        return self.send("select", p)

    def upload(self, target=None, ref=None, files=None, tab_id=None, fallback=True) -> dict:
        raw = []
        if files:
            raw = list(files) if isinstance(files, (list, tuple)) else [files]
        file_list = []
        for f in raw:
            resolved = str(Path(f).resolve())
            if not Path(resolved).exists():
                translated = _wsl_to_windows(resolved)
                return {"success": False, "code": "file_not_found",
                        "error": f"File not found: {f} (as {translated}). "
                                 "Pass an existing file, or a Windows-style path when driving Windows Edge."}
            file_list.append(_wsl_to_windows(resolved))
        p = {"files": file_list}
        if ref is not None:
            p["ref"] = ref
        elif target is not None:
            p["target"] = target
        if tab_id is not None:
            p["tabId"] = tab_id
        if fallback is False:
            p["fallback"] = False
        return self.send("upload", p)

    def history_search(self, text: str = "", max_results: int = 20, start_time=None, end_time=None) -> dict:
        p = {"text": text, "maxResults": max_results}
        if start_time is not None:
            p["startTime"] = start_time
        if end_time is not None:
            p["endTime"] = end_time
        return self.send("history_search", p)

    def history_delete(self, url: str) -> dict:
        return self.send("history_delete", {"url": url})

    def group_list(self, window_id=None) -> dict:
        p = {}
        if window_id is not None:
            p["windowId"] = window_id
        return self.send("group_list", p)

    def group_move(self, tab_ids, group_id=None, title=None, color=None) -> dict:
        ids = list(tab_ids) if isinstance(tab_ids, (list, tuple)) else [tab_ids]
        p = {"tabIds": ids}
        if group_id is not None:
            p["groupId"] = group_id
        if title is not None:
            p["title"] = title
        if color is not None:
            p["color"] = color
        return self.send("group_move", p)

    def group_ungroup(self, tab_ids) -> dict:
        ids = list(tab_ids) if isinstance(tab_ids, (list, tuple)) else [tab_ids]
        return self.send("group_ungroup", {"tabIds": ids})

    def scroll(self, x=None, y=None, ref=None, target=None, tab_id=None) -> dict:
        p = {}
        if x is not None:
            p["x"] = x
        if y is not None:
            p["y"] = y
        if ref is not None:
            p["ref"] = ref
        elif target is not None:
            p["target"] = target
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("scroll", p)

    def wait(self, text=None, selector=None, ref=None, url=None, load=False, idle=False, timeout=5000, tab_id=None) -> dict:
        p = {"timeout": timeout}
        if text:
            p["text"] = text
        elif selector:
            p["selector"] = selector
        elif ref:
            p["ref"] = ref
        elif url:
            p["url"] = url
        elif load:
            p["load"] = True
        elif idle:
            p["idle"] = True
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("wait", p)

    def text(self, target=None, ref=None, tab_id=None) -> dict:
        p = {}
        if ref is not None:
            p["ref"] = ref
        elif target is not None:
            p["target"] = target
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("text", p)

    def eval(self, code: str, tab_id=None) -> dict:
        p = {"code": code}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("eval", p)

    def screenshot(self, path=None, format: str = "jpeg", quality: int = 80, clip=None, of=None, tab_id=None) -> dict:
        p = {"format": format, "quality": quality}
        if clip is not None:
            p["clip"] = clip
        if of is not None:
            p["of"] = of
        if tab_id is not None:
            p["tabId"] = tab_id
        res = self.send("screenshot", p)
        if path and res.get("success") and res.get("dataUrl"):
            data_str = res["dataUrl"]
            if "," in data_str:
                data_str = data_str.split(",", 1)[1]
            img_bytes = base64.b64decode(data_str)
            out_p = Path(path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(img_bytes)
            res["path"] = str(out_p.resolve())
        return res

    def console(self, clear: bool = False, tab_id=None) -> dict:
        p = {"clear": clear}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("console", p)

    def dialog(self, policy: str = "accept", prompt_text=None, tab_id=None) -> dict:
        p = {"policy": policy}
        if prompt_text is not None:
            p["promptText"] = prompt_text
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("dialog", p)

    def batch(self, steps: list, tab_id=None) -> dict:
        p = {"steps": steps}
        if tab_id is not None:
            p["tabId"] = tab_id
        return self.send("batch", p)

    def status(self) -> dict:
        for _ in range(2):
            try:
                conn = self._get_connection(config.call_timeout(self.timeout))
                conn.request("GET", "/status")
                data = json.loads(conn.getresponse().read().decode("utf-8"))
                data["success"] = bool(data.get("bridge_running"))
                return data
            except (OSError, http.client.HTTPException, ValueError) as e:
                self.close()
                error = e
        return {
            "success": False,
            "code": "daemon_unreachable",
            "error": f"Cannot reach bridge daemon on 127.0.0.1:{self.port}: {error}",
        }

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


EdgeClient = Edge


def send_cmd(action: str, params: dict | None = None, timeout: int = 20) -> dict:
    with Edge(pin=False, timeout=timeout) as client:
        return client.send(action, params, timeout=timeout)
