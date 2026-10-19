"""E2E fixtures: a v2 daemon on a random port, a headless Edge with a port-patched copy of the
extension in its own profile, and ephemeral HTTP servers for fixture pages.

The owner's live daemon on 18999 and their unpacked extension are never touched."""
import base64
import functools
import hashlib
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.support import DaemonHandle, edge_binary, free_port  # noqa: E402
from edge_agent_bridge import config  # noqa: E402

PAGES = Path(__file__).resolve().parent / "pages"


class EdgeProcess:
    def __init__(self, binary, profile, extension, port):
        self.binary, self.profile, self.extension, self.port = binary, profile, extension, port
        self.proc = None

    def start(self):
        args = [
            self.binary, "--headless=new", f"--user-data-dir={self.profile}",
            f"--load-extension={self.extension}", f"--disable-extensions-except={self.extension}",
            "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
            "--window-size=1280,900", "about:blank",
        ]
        if sys.platform.startswith("linux") or (hasattr(os, "geteuid") and os.geteuid() == 0):
            args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return self

    def stop(self):
        if not self.proc:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


@pytest.fixture(scope="session")
def e2e_daemon(tmp_path_factory):
    home = tmp_path_factory.mktemp("eab-home")
    handle = DaemonHandle(free_port(), home).start()
    yield handle
    handle.stop()


@pytest.fixture(scope="session")
def staged_extension(tmp_path_factory, e2e_daemon):
    dst = tmp_path_factory.mktemp("eab-ext") / "extension"
    shutil.copytree(config.extension_dir(), dst, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("background.js", "popup.js"):
        f = dst / name
        f.write_text(f.read_text(encoding="utf-8").replace("18999", str(e2e_daemon.port)), encoding="utf-8")
    return dst


@pytest.fixture(scope="session")
def edge(tmp_path_factory, e2e_daemon, staged_extension):
    binary = edge_binary()
    if not binary:
        if os.environ.get("EDGE_BRIDGE_E2E_REQUIRED"):
            pytest.fail("Microsoft Edge not found and EDGE_BRIDGE_E2E_REQUIRED is set")
        pytest.skip("Microsoft Edge not found")
    profile = tmp_path_factory.mktemp("eab-profile")
    proc = EdgeProcess(binary, profile, staged_extension, e2e_daemon.port).start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if e2e_daemon.status().get("websocket_active"):
            break
        time.sleep(0.25)
    else:
        err = proc.proc.stderr.read().decode(errors="replace")[-1500:] if proc.proc.poll() is not None else ""
        proc.stop()
        pytest.fail("extension never connected to the test daemon: " + err)
    yield proc
    proc.stop()


class _PageHandler(http.server.SimpleHTTPRequestHandler):
    """Serves tests/e2e/pages plus two endpoints the wait tests need: /slow?ms=N answers after
    N ms, and /ws accepts a WebSocket and holds it open until the client goes away."""
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".html": "text/html; charset=utf-8"}

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/slow" or self.path.startswith("/slow?"):
            ms = int((self.path.split("ms=", 1)[1] if "ms=" in self.path else "500").split("&")[0])
            time.sleep(ms / 1000.0)
            body = b"slow done"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/ws") and self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.close_connection = True
            self.connection.settimeout(60)
            try:
                while self.connection.recv(4096):
                    pass
            except OSError:
                pass
            return
        super().do_GET()


def _serve_pages():
    handler = functools.partial(_PageHandler, directory=str(PAGES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="session")
def pages():
    server = _serve_pages()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def pages2():
    server = _serve_pages()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(e2e_daemon, edge):
    def call(action, params=None, timeout=15):
        p = dict(params or {})
        p.setdefault("highlight", False)
        code, body = e2e_daemon.exec(action, p, timeout=timeout)
        if isinstance(body, dict):
            body.setdefault("http_status", code)
        return body
    return call
