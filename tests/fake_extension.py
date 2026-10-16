"""A stdlib WebSocket client that behaves like the Edge extension."""
import base64
import json
import os
import socket
import struct
import threading
import time


class FakeExtension:
    def __init__(self, port, version="2.0.0", token=None, send_hello=True, handler=None,
                 origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                 file_access=True):
        self.port = port
        self.version = version
        self.token = token
        self.send_hello = send_hello
        self.handler = handler or self.default_handler
        self.origin = origin
        self.file_access = file_access
        self.sock = None
        self.received = []
        self.closed_with = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def default_handler(self, action, params):
        if action == "ping":
            return {"success": True, "message": "pong", "version": self.version, "fileAccess": self.file_access}
        if action in ("get_active_tab", "tab"):
            return {"success": True, "tab": {"id": 1, "title": "T", "url": "http://x/"}}
        if action == "screenshot":
            return {"success": True, "dataUrl": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                    "format": "jpeg", "tab": {"id": 1, "title": "T", "url": "http://x/"}}
        if action == "sleep":
            time.sleep(float(params.get("ms", 0)) / 1000.0)
            return {"success": True}
        return {"success": False, "code": "unknown_action", "error": f"Unknown action: {action}"}

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        headers = [
            "GET /ws HTTP/1.1", f"Host: 127.0.0.1:{self.port}", "Upgrade: websocket",
            "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
        ]
        if self.origin is not None:
            headers.append(f"Origin: {self.origin}")
        self.sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            resp += chunk
        status = resp.split(b" ", 2)[1] if b" " in resp else b"000"
        if status != b"101":
            self.sock.close()
            raise ConnectionError(f"upgrade rejected: {resp[:80]!r}")
        if self.send_hello:
            hello = {"version": self.version, "id": "fakeextensionid"}
            if self.token is not None:
                hello["token"] = self.token
            self.send_text(json.dumps({"hello": hello}))
        return self

    def send_frame(self, opcode, payload):
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n <= 0xFFFF:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(header) + mask + masked)

    def send_text(self, text):
        self.send_frame(1, text.encode("utf-8"))

    def send_raw_text(self, text):
        self.send_text(text)

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _read_frame(self):
        hdr = self._read_exact(2)
        if not hdr:
            return None, None
        opcode = hdr[0] & 0x0F
        n = hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read_exact(8))[0]
        payload = self._read_exact(n) if n else b""
        return opcode, payload

    def run(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                opcode, payload = self._read_frame()
            except socket.timeout:
                continue
            except OSError:
                break
            if opcode is None:
                break
            if opcode == 8:
                self.closed_with = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                break
            if opcode == 9:
                self.send_frame(10, payload)
                continue
            if opcode != 1:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except ValueError:
                continue
            self.received.append(msg)
            if msg.get("ping"):
                self.send_text(json.dumps({"pong": True}))
            elif "id" in msg and "action" in msg:
                try:
                    result = self.handler(msg["action"], msg.get("params") or {})
                except Exception as e:  # a raising test handler must surface as a result, not kill the thread
                    result = {"success": False, "code": "handler_error", "error": repr(e)}
                self.send_text(json.dumps({"id": msg["id"], "result": result}))

    def close(self):
        self._stop.set()
        try:
            self.send_frame(8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=2)
