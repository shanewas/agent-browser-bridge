#!/usr/bin/env python3
"""
Edge Agent Bridge daemon.
Runs locally on 127.0.0.1:18999 (or configured port).
Bridges commands between local agents and the Microsoft Edge extension over WebSocket.
"""
import argparse
import base64
import collections
import hashlib
import hmac
import http.server
import json
import logging
import logging.handlers
import os
import socket
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

from . import __version__, config

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_BODY_BYTES = 16 * 1024 * 1024  # 16 MB
MAX_FRAME_PAYLOAD = 16 * 1024 * 1024  # 16 MB

LOCK_EXEMPT_ACTIONS = frozenset({
    "ping", "status", "tabs", "daemon", "session_start",
    "session_status", "session_stop", "session_list", "session_prune",
    "mcp-config", "extension",
    "history_search", "history_delete",
    "group_list", "group_move", "group_ungroup",
})
PIN_MUTATING_ACTIONS = frozenset({"tab_switch", "switch_tab", "tab_new"})
CLOSING_ACTIONS = frozenset({"tab_close", "close_tab"})
DISPATCH_BUDGET_SEC = 12.0
LOCK_IDLE_EVICT_SEC = 3600.0

logger = logging.getLogger("edge_bridge")


def setup_logging(home_dir: Path):
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    log_file = home_dir / "bridge.log"
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class WebSocketConnection:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.closed = False
        self._write_lock = threading.Lock()
        self.grace_timer: threading.Timer | None = None

    def send_frame(self, opcode: int, data: bytes) -> bool:
        if self.closed:
            return False
        length = len(data)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))
        payload = bytes(header) + data
        with self._write_lock:
            try:
                self.sock.sendall(payload)
                return True
            except Exception:
                self.closed = True
                return False

    def send_text(self, text: str) -> bool:
        return self.send_frame(1, text.encode("utf-8"))

    def send_ping(self, data: bytes = b"") -> bool:
        return self.send_frame(9, data)

    def send_pong(self, data: bytes = b"") -> bool:
        return self.send_frame(10, data)

    def close_with_code(self, code: int = 1000, reason: str = ""):
        if not self.closed:
            try:
                payload = struct.pack("!H", code) + reason.encode("utf-8")
                self.send_frame(8, payload)
            except Exception:
                pass
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass

    def close(self):
        self.close_with_code(1000)


class HeldCommand:
    def __init__(self, cmd_id: str, payload: dict, event: threading.Event, deadline: float):
        self.id = cmd_id
        self.payload = payload
        self.event = event
        self.deadline = deadline


class BridgeState:
    def __init__(self, port: int, home: Path, token: str, pairing_required: bool = False,
                 heartbeat_sec: float = 20.0, pong_timeout_sec: float = 45.0,
                 lock_timeout_sec: float = 10.0):
        self.port = port
        self.home = home
        self.token = token
        self.pairing_required = pairing_required
        self.heartbeat_sec = heartbeat_sec
        self.pong_timeout_sec = pong_timeout_sec

        self.lock = threading.RLock()
        self.ext: WebSocketConnection | None = None
        self.ext_version: str | None = None
        self.ext_accepted: bool = False
        self.extension_outdated: bool = False

        self.held: collections.deque[HeldCommand] = collections.deque()
        self.pending: dict[str, threading.Event] = {}
        self.results: dict[str, dict] = {}
        self.lock_timeout_sec = lock_timeout_sec
        self.sessions: dict[str, dict] = {}
        self.tab_locks: dict[int, list] = {}

        self.last_seen: float = 0.0
        self.last_pong: float = 0.0
        self.last_ping_sent: float = 0.0

    def mark_seen(self):
        self.last_seen = time.time()

    def mark_pong(self):
        self.last_pong = time.monotonic()
        self.mark_seen()

    def is_active_locked(self, ws: WebSocketConnection) -> bool:
        return self.ext is ws and self.ext_accepted and not ws.closed

    def accept_extension_locked(self, ws: WebSocketConnection, version: str | None):
        """Make ws the extension connection. Called only once ws has passed hello/pairing (or the
        pairing-off grace window), so a socket that never authenticates cannot displace the live one."""
        if ws.grace_timer:
            ws.grace_timer.cancel()
            ws.grace_timer = None
        if ws.closed:
            return
        old = self.ext
        if old is not ws:
            if old is not None:
                old.close()
            self.ext = ws
            now = time.monotonic()
            self.last_pong = now
            self.last_ping_sent = now
        self.ext_version = version
        self.ext_accepted = True
        self.mark_seen()
        self._drain_held_locked()

    def _drain_held_locked(self):
        now = time.time()
        while self.held:
            item = self.held.popleft()
            if now < item.deadline and self.ext and not self.ext.closed:
                self.pending[item.id] = item.event
                self.ext.send_text(json.dumps(item.payload))

    def on_ws_closed(self, ws: WebSocketConnection):
        with self.lock:
            if ws.grace_timer:
                ws.grace_timer.cancel()
                ws.grace_timer = None
            if self.ext is ws:
                self.ext = None
                self.ext_accepted = False

    def session_create_locked(self, name: str | None = None) -> str:
        token = str(uuid.uuid4())
        if not name:
            name = "s-" + token[:8]
        self.sessions[token] = {"tabId": None, "tabClosed": False, "warned": set(),
                                "name": name, "created": time.time()}
        return token

    def tab_lock_locked(self, tab_id: int) -> threading.Lock:
        now = time.monotonic()
        entry = self.tab_locks.get(tab_id)
        if entry is None:
            for tid, (_, used) in list(self.tab_locks.items()):
                if now - used > LOCK_IDLE_EVICT_SEC:
                    del self.tab_locks[tid]
            entry = [threading.Lock(), now]
            self.tab_locks[tab_id] = entry
        entry[1] = now
        return entry[0]

    def tab_lock_evict_locked(self, tab_id: int) -> None:
        self.tab_locks.pop(tab_id, None)


def _heartbeat_worker(state: BridgeState, stop_event: threading.Event):
    while not stop_event.is_set():
        time.sleep(0.1)
        to_close = None
        with state.lock:
            ws = state.ext
            if ws and not ws.closed:
                now = time.monotonic()
                if state.last_ping_sent > 0 and (now - state.last_pong > state.pong_timeout_sec):
                    logger.warning("Extension pong timeout (%.1fs); closing socket", now - state.last_pong)
                    to_close = ws
                elif now - state.last_ping_sent >= state.heartbeat_sec:
                    state.last_ping_sent = now
                    ws.send_text('{"ping": true}')

        if to_close:
            to_close.close()
            state.on_ws_closed(to_close)


def _valid_session_name(name) -> bool:
    if not isinstance(name, str) or not 1 <= len(name) <= 40:
        return False
    return all("a" <= c <= "z" or "A" <= c <= "Z" or "0" <= c <= "9" or c in "_-" for c in name)


def _strip_session_token(obj) -> None:
    if isinstance(obj, dict):
        obj.pop("sessionToken", None)
        for v in obj.values():
            _strip_session_token(v)
    elif isinstance(obj, list):
        for v in obj:
            _strip_session_token(v)


class BridgeRequestHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, format, *args):
        logger.info("%s %s", self.address_string(), format % args)

    def _respond(self, status: int, data: dict | list | None = None) -> bool:
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            if body:
                self.wfile.write(body)
                self.wfile.flush()
            return True
        except OSError:
            return False

    def _check_host(self) -> bool:
        host_header = self.headers.get("Host", "")
        port = self.server.state.port
        allowed = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
            "127.0.0.1",
            "localhost",
            "[::1]",
        }
        return host_header.lower() in allowed

    def _is_browser_request(self) -> bool:
        if self.headers.get("Origin"):
            return True
        for h in self.headers:
            if h.lower().startswith("sec-fetch-"):
                return True
        return False

    def _read_body(self):
        raw = self.headers.get("Content-Length", "0")
        try:
            length = int(raw)
        except (TypeError, ValueError):
            return None, "Malformed Content-Length"
        if length < 0 or length > MAX_BODY_BYTES:
            return None, f"Body too large (max {MAX_BODY_BYTES} bytes)"
        if length == 0:
            return {}, None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")), None
        except Exception as e:
            return None, f"Malformed JSON body: {e}"

    def _read_exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.connection.recv(n - len(buf))
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def do_GET(self):
        if not self._check_host():
            self._respond(421, {"success": False, "code": "bad_host", "error": "Invalid host"})
            return

        state = self.server.state

        if self.path.startswith("/status"):
            state.mark_seen()
            with state.lock:
                ext_connected = bool(state.ext and not state.ext.closed)
                ws_active = bool(state.ext and not state.ext.closed and state.ext_accepted)
                ext_ver = state.ext_version
                ext_outdated = state.extension_outdated
                pending_count = len(state.pending) + len(state.held)
                session_count = len(state.sessions)
                last_seen = state.last_seen

            self._respond(200, {
                "bridge_running": True,
                "daemon_version": __version__,
                "extension_version": ext_ver,
                "extension_outdated": ext_outdated,
                "extension_connected": ext_connected,
                "websocket_active": ws_active,
                "last_seen_seconds_ago": round(time.time() - last_seen, 1) if last_seen > 0 else None,
                "pending": pending_count,
                "sessions": session_count,
                "pairing_required": state.pairing_required,
                "data_dir": str(config.home()),
            })
            return

        if self.path.startswith("/ws"):
            upgrade = self.headers.get("Upgrade", "").lower()
            conn_hdr = self.headers.get("Connection", "").lower()
            if upgrade != "websocket" or "upgrade" not in conn_hdr:
                self._respond(400, {"success": False, "code": "bad_request", "error": "Expected WebSocket Upgrade"})
                return

            origin = self.headers.get("Origin", "")
            if not origin or not (origin.startswith("chrome-extension://") or origin.startswith("extension://")):
                self._respond(403, {"success": False, "code": "forbidden", "error": "Origin not allowed"})
                return

            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self._respond(400, {"success": False, "code": "bad_request", "error": "Missing Sec-WebSocket-Key"})
                return

            accept_str = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()

            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_str)
            self.end_headers()

            # The Origin header is spoofable by any local process, so the new socket stays a
            # candidate and does not touch the live extension connection until it is accepted.
            ws_conn = WebSocketConnection(self.connection)
            if not state.pairing_required:
                # Extensions older than 2.0 send no hello; without pairing they are accepted
                # after a short grace window.
                def grace_timeout():
                    with state.lock:
                        if state.ext is not ws_conn:
                            state.accept_extension_locked(ws_conn, None)
                with state.lock:
                    ws_conn.grace_timer = threading.Timer(1.0, grace_timeout)
                    ws_conn.grace_timer.daemon = True
                    ws_conn.grace_timer.start()

            self.close_connection = True
            self.connection.settimeout(60.0)
            self._run_ws_loop(ws_conn)
            return

        if self.path.startswith("/poll") or self.path.startswith("/result"):
            self._respond(404, {"success": False, "code": "not_found", "error": "/poll and /result are removed in v2; use WebSocket /ws"})
            return

        self._respond(404, {"success": False, "code": "not_found", "error": "Unknown path"})

    def _run_ws_loop(self, ws_conn: WebSocketConnection):
        msg_buffer = bytearray()
        msg_opcode = None
        state = self.server.state

        try:
            while not ws_conn.closed:
                hdr = self._read_exact(2)
                if not hdr:
                    break
                fin = (hdr[0] >> 7) & 1
                opcode = hdr[0] & 0x0F
                has_mask = (hdr[1] >> 7) & 1
                length = hdr[1] & 0x7F
                if length == 126:
                    ext = self._read_exact(2)
                    if not ext:
                        break
                    length = struct.unpack("!H", ext)[0]
                elif length == 127:
                    ext = self._read_exact(8)
                    if not ext:
                        break
                    length = struct.unpack("!Q", ext)[0]

                mask = None
                if has_mask:
                    mask = self._read_exact(4)
                    if not mask:
                        break

                if length > MAX_FRAME_PAYLOAD:
                    logger.warning("Frame payload %d exceeds 16MB cap; closing 1009", length)
                    ws_conn.close_with_code(1009, "Message too big")
                    break

                payload = self._read_exact(length) if length > 0 else b""
                if payload is None:
                    break

                if has_mask and mask and payload:
                    m = mask * (len(payload) // 4) + mask[:len(payload) % 4]
                    payload = (int.from_bytes(payload, "big") ^ int.from_bytes(m, "big")).to_bytes(len(payload), "big")

                with state.lock:
                    active = state.is_active_locked(ws_conn)
                    if active:
                        state.mark_seen()

                if opcode == 9:  # Ping
                    ws_conn.send_pong(payload)
                elif opcode == 10:  # Pong
                    if active:
                        state.mark_pong()
                elif opcode == 8:  # Close
                    break
                else:
                    if opcode != 0:
                        msg_opcode = opcode
                        msg_buffer = bytearray(payload)
                    else:
                        msg_buffer.extend(payload)

                    if fin:
                        if msg_opcode == 1:
                            try:
                                data = json.loads(msg_buffer.decode("utf-8"))
                                self._handle_ws_json(data, ws_conn)
                            except Exception as e:
                                logger.debug("Failed to handle WS text: %s", e)
                        msg_buffer = bytearray()
                        msg_opcode = None
        finally:
            ws_conn.close()
            state.on_ws_closed(ws_conn)

    def _handle_ws_json(self, data: dict, ws_conn: WebSocketConnection):
        state = self.server.state
        if not isinstance(data, dict):
            return

        if "hello" in data and isinstance(data["hello"], dict):
            hello = data["hello"]
            version = hello.get("version")
            if not isinstance(version, str):
                version = None
            if state.pairing_required:
                token = hello.get("token")
                if not isinstance(token, str) or not hmac.compare_digest(
                        token.encode("utf-8"), state.token.encode("utf-8")):
                    # Closing only this socket: the accepted extension, if any, stays connected.
                    logger.warning("Pairing token mismatch from extension hello; closing 4001")
                    ws_conn.close_with_code(4001, "pairing_failed")
                    return
            with state.lock:
                state.accept_extension_locked(ws_conn, version)

        with state.lock:
            if not state.is_active_locked(ws_conn):
                # Pongs and results from a socket that has not been accepted are ignored.
                return

        if data.get("pong") is True:
            state.mark_pong()

        if "id" in data and "result" in data:
            cmd_id = data["id"]
            result = data["result"]
            with state.lock:
                if cmd_id in state.pending and state.is_active_locked(ws_conn):
                    is_v1 = False
                    if state.ext_version is None:
                        is_v1 = True
                    else:
                        try:
                            major = int(state.ext_version.split(".")[0])
                            if major < 2:
                                is_v1 = True
                        except (ValueError, IndexError):
                            is_v1 = True
                    err = result.get("error") if isinstance(result, dict) else ""
                    code = result.get("code") if isinstance(result, dict) else ""
                    if is_v1 and ((isinstance(err, str) and err.startswith("Unknown action")) or code == "unknown_action"):
                        state.extension_outdated = True
                        result = {
                            "success": False,
                            "code": "extension_outdated",
                            "error": f"Extension {state.ext_version or 'older than 2.0.0'} is connected; daemon {__version__} needs extension 2.0.0 or newer. Update it from the Edge Add-ons store, or reload the unpacked extension from {config.extension_dir()}."
                        }
                    state.results[cmd_id] = result
                    ev = state.pending.pop(cmd_id)
                    ev.set()

    def do_POST(self):
        if not self._check_host():
            self._respond(421, {"success": False, "code": "bad_host", "error": "Invalid host"})
            return

        state = self.server.state

        if self.path.startswith("/exec"):
            if self._is_browser_request():
                self._respond(403, {"success": False, "code": "browser_context", "error": "/exec is not reachable from browser context"})
                return

            token_hdr = self.headers.get(config.TOKEN_HEADER)
            if not token_hdr:
                self._respond(401, {"success": False, "code": "missing_token", "error": "X-Bridge-Token header required"})
                return
            if not hmac.compare_digest(token_hdr, state.token):
                self._respond(401, {"success": False, "code": "bad_token", "error": "Invalid token"})
                return

            req, err = self._read_body()
            if err is not None or not isinstance(req, dict):
                self._respond(400, {"success": False, "code": "bad_body", "error": err or "Invalid JSON object"})
                return

            action = req.get("action")
            if not action:
                self._respond(400, {"success": False, "code": "bad_body", "error": "Missing action field"})
                return

            params = req.get("params") or {}
            if not isinstance(params, dict):
                self._respond(400, {"success": False, "code": "bad_body", "error": "params must be an object"})
                return
            try:
                timeout = float(req.get("timeout", 20))
            except (TypeError, ValueError):
                timeout = 20.0
            timeout = max(1.0, min(timeout, 120.0))
            session_token = req.get("sessionToken")

            if action == "session_start":
                name = params.get("name")
                if name is not None and not _valid_session_name(name):
                    self._respond(200, {"success": False, "code": "bad_name",
                                        "error": "Invalid session name: use 1-40 chars of A-Z a-z 0-9 _ -."})
                    return
                with state.lock:
                    token = state.session_create_locked(name)
                    sess_name = state.sessions[token]["name"]
                self._respond(200, {"success": True, "sessionToken": token, "name": sess_name})
                return

            if action in ("session_status", "session_stop"):
                with state.lock:
                    sess = state.sessions.get(session_token) if session_token else None
                    if sess is None:
                        self._respond(200, {"success": False, "code": "unknown_session",
                                            "error": "Unknown or expired sessionToken. Mint one with session_start."})
                        return
                    if action == "session_stop":
                        del state.sessions[session_token]
                        self._respond(200, {"success": True, "stopped": True})
                        return
                    now = time.time()
                    self._respond(200, {"success": True, "name": sess.get("name", "s-?"),
                                        "tabId": sess["tabId"], "tabClosed": sess["tabClosed"],
                                        "age_s": int(now - sess.get("created", now))})
                    return

            if action in ("session_list", "session_prune"):
                now = time.time()
                with state.lock:
                    if action == "session_prune":
                        closed = [t for t, s in state.sessions.items() if s.get("tabClosed")]
                        for t in closed:
                            del state.sessions[t]
                        self._respond(200, {"success": True, "dropped": len(closed)})
                        return
                    items = [{"name": s.get("name", "s-?"), "tabId": s.get("tabId"),
                              "tabClosed": s.get("tabClosed", False),
                              "age_s": int(now - s.get("created", now))}
                             for s in state.sessions.values()]
                self._respond(200, {"success": True, "sessions": items})
                return

            session = None
            if session_token is not None:
                with state.lock:
                    session = state.sessions.get(session_token)
                if session is None:
                    self._respond(200, {"success": False, "code": "unknown_session",
                                        "error": "Unknown or expired sessionToken. Mint one with session_start."})
                    return

            tab_scoped = action not in LOCK_EXEMPT_ACTIONS
            if session is not None and tab_scoped and action not in PIN_MUTATING_ACTIONS:
                with state.lock:
                    closed = session["tabClosed"]
                if closed:
                    self._respond(200, {"success": False, "code": "tab_closed",
                                        "error": "Session tab is closed. Use switch or new to re-pin."})
                    return

            if action == "batch":
                self._exec_batch(state, session_token, session, params, timeout)
                return

            _strip_session_token(params)
            forward_tab = self._resolve_forward_tab(state, session, params, tab_scoped)
            res, status_code = self._dispatch_guarded(state, action, params, timeout, tab_scoped, forward_tab)
            self._after_result(state, session_token, session, action, params, forward_tab, res)
            if not self._respond(status_code, res):
                self._best_effort_cancel(state, getattr(self, "_last_cmd_id", None))
            return

        if self.path.startswith("/poll") or self.path.startswith("/result"):
            self._respond(404, {"success": False, "code": "not_found", "error": "/poll and /result are removed in v2; use WebSocket /ws"})
            return

        self._respond(404, {"success": False, "code": "not_found", "error": "Unknown path"})

    def _resolve_forward_tab(self, state, session, params, tab_scoped):
        if not tab_scoped:
            return None
        explicit = params.get("tabId")
        if explicit is not None:
            try:
                return int(explicit)
            except (TypeError, ValueError):
                return None
        if session is not None:
            with state.lock:
                pin = session["tabId"]
            if pin is not None:
                params["tabId"] = pin
                return int(pin)
        return None

    def _dispatch_guarded(self, state, action, params, timeout, tab_scoped, forward_tab):
        tab_lock = None
        if forward_tab is not None:
            with state.lock:
                tab_lock = state.tab_lock_locked(forward_tab)
            if not tab_lock.acquire(timeout=state.lock_timeout_sec):
                return ({"success": False, "code": "tab_busy", "tabId": forward_tab,
                         "error": f"Tab {forward_tab} is busy; another call holds its lock"}, 200)
        try:
            return self._dispatch_once(state, action, params, timeout, tab_scoped)
        finally:
            if tab_lock is not None:
                tab_lock.release()

    def _dispatch_once(self, state, action, params, timeout, tab_scoped):
        fwd_params = dict(params)
        if tab_scoped:
            fwd_params["deadlineMs"] = int((time.time() + DISPATCH_BUDGET_SEC) * 1000)
        cmd_id = str(uuid.uuid4())
        self._last_cmd_id = cmd_id
        event = threading.Event()
        deadline = time.time() + timeout
        cmd_payload = {"id": cmd_id, "action": action, "params": fwd_params}
        with state.lock:
            live = bool(state.ext and not state.ext.closed and state.ext_accepted)
            if live:
                state.pending[cmd_id] = event
                if not state.ext.send_text(json.dumps(cmd_payload)):
                    state.pending.pop(cmd_id, None)
                    live = False
        if not live:
            return ({"success": False, "code": "extension_offline",
                     "error": "Edge extension is not connected. Open Edge and check the Edge Agent Bridge popup shows Connected."}, 504)
        rem = max(0.01, deadline - time.time())
        finished = event.wait(timeout=rem)
        with state.lock:
            if finished:
                res = state.results.pop(cmd_id, None)
                if res is not None:
                    return (res, 200)
            state.pending.pop(cmd_id, None)
            return ({"success": False, "code": "timeout",
                     "error": "Command timed out waiting for Edge extension response"}, 504)

    def _after_result(self, state, session_token, session, action, params, forward_tab, res):
        if not isinstance(res, dict):
            return
        if forward_tab is not None and (res.get("code") == "tab_not_found"
                                        or (action in CLOSING_ACTIONS and res.get("success"))):
            with state.lock:
                state.tab_lock_evict_locked(forward_tab)
        if session is None or session_token is None:
            return
        with state.lock:
            sess = state.sessions.get(session_token)
            if sess is None:
                return
            tab_info = res.get("tab") if isinstance(res.get("tab"), dict) else None
            if action in PIN_MUTATING_ACTIONS and res.get("success") and tab_info is not None \
                    and tab_info.get("id") is not None:
                sess["tabId"] = int(tab_info["id"])
                sess["tabClosed"] = False
            elif sess.get("tabId") is None and res.get("success") and tab_info is not None \
                    and tab_info.get("id") is not None:
                sess["tabId"] = int(tab_info["id"])
            if forward_tab is not None and forward_tab == sess.get("tabId"):
                if res.get("code") == "tab_not_found" \
                        or (action in CLOSING_ACTIONS and res.get("success")):
                    sess["tabClosed"] = True
                    if "tab_closed" not in sess["warned"]:
                        sess["warned"].add("tab_closed")
                        logger.warning("Session tab %s closed; session is now sticky tab_closed", forward_tab)

    def _best_effort_cancel(self, state, cmd_id):
        if not cmd_id:
            return
        try:
            with state.lock:
                ws = state.ext
            if ws and not ws.closed:
                ws.send_text(json.dumps({"cancel": cmd_id}))
        except Exception:
            pass

    def _exec_batch(self, state, session_token, session, params, timeout):
        steps = params.get("steps") or []
        _strip_session_token(params)
        results = []
        top_tab = params.get("tabId")
        per_step_timeout = max(1.0, timeout / max(1, len(steps)))
        for step in steps:
            if not isinstance(step, dict):
                continue
            s_action = step.get("action")
            if s_action in ("sleep", "wait_ms"):
                try:
                    time.sleep(max(0.0, float(step.get("ms", 100)) / 1000.0))
                except (TypeError, ValueError):
                    pass
                results.append({"action": "sleep", "success": True, "ms": step.get("ms", 100)})
                continue
            if s_action == "batch":
                # The extension runs a nested batch's steps without its per-tab checks.
                err = "a batch step can't be another batch"
                results.append({"success": False, "code": "bad_params", "error": err})
                self._respond(200, {"success": False, "code": "bad_params", "error": err,
                                    "stoppedAt": "batch", "results": results})
                return
            s_params = {k: v for k, v in step.items() if k != "action"}
            if "tabId" not in s_params and top_tab is not None:
                s_params["tabId"] = top_tab
            s_tab_scoped = s_action not in LOCK_EXEMPT_ACTIONS
            forward_tab = self._resolve_forward_tab(state, session, s_params, s_tab_scoped)
            res, _ = self._dispatch_guarded(state, s_action, s_params, per_step_timeout, s_tab_scoped, forward_tab)
            self._after_result(state, session_token, session, s_action, s_params, forward_tab, res)
            results.append(res)
            if res and res.get("success") is False and step.get("stopOnError", True) is not False:
                self._respond(200, {"success": False, "code": res.get("code") or "step_failed",
                                    "error": res.get("error"), "stoppedAt": s_action, "results": results})
                return
        self._respond(200, {"success": True, "count": len(results), "results": results})


class BridgeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, state: BridgeState):
        self.state = state
        super().__init__(server_address, RequestHandlerClass)


def run_server(host="127.0.0.1", port=None, home=None, require_pairing=None) -> None:
    if port is None:
        port = config.port()
    if home is None:
        home = config.home()
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)

    if require_pairing is None:
        cfg = config.read_config()
        require_pairing = (cfg.get("pairing") == "1")

    token = config.ensure_token()
    setup_logging(home)

    state = BridgeState(
        port=port,
        home=home,
        token=token,
        pairing_required=require_pairing,
        heartbeat_sec=config.heartbeat_seconds(),
        pong_timeout_sec=config.pong_timeout_seconds(),
        lock_timeout_sec=config.lock_timeout_seconds(),
    )

    stop_event = threading.Event()
    server = BridgeServer((host, port), BridgeRequestHandler, state=state)

    hb_thread = threading.Thread(target=_heartbeat_worker, args=(state, stop_event), daemon=True)
    hb_thread.start()

    pid_file = config.pid_path()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    import atexit
    atexit.register(lambda: pid_file.unlink(missing_ok=True))
    logger.info("Starting Edge Agent Bridge daemon v%s on %s:%d (pid %d)", __version__, host, port, os.getpid())

    try:
        server.serve_forever()
    finally:
        stop_event.set()
        try:
            server.server_close()
        except Exception:
            pass
        with state.lock:
            if state.ext:
                try:
                    state.ext.close()
                except Exception:
                    pass
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        logger.info("Daemon stopped")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Edge Agent Bridge daemon")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    parser.add_argument("--require-pairing", action="store_true", help="Require pairing token")
    parser.add_argument("--no-pairing", action="store_true", help="Disable pairing token")
    args = parser.parse_args(argv)

    pairing = None
    if args.require_pairing or args.no_pairing:
        pairing = bool(args.require_pairing)
        cfg = config.read_config()
        cfg["pairing"] = "1" if pairing else "0"
        config.write_config(cfg)

    try:
        run_server(port=args.port, require_pairing=pairing)
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        logger.error("Daemon crashed: %s", e, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
