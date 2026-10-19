"""Tests for edge_agent_bridge.doppelhand against a fake HTTP server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from edge_agent_bridge.doppelhand import Doppelhand


class Handler(BaseHTTPRequestHandler):
    calls = []

    def _reply(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Handler.calls.append(("GET", self.path, self.headers.get("X-Doppelhand-Token")))
        if self.path == "/screen":
            self._reply({"ok": True, "view": [1280, 720]})
        elif self.path == "/shot":
            self._reply({"ok": True, "path": "C:\\shot.png"})
        else:
            self._reply({"ok": False, "error": "use POST"})

    def do_POST(self):
        Handler.calls.append(("POST", self.path, self.headers.get("X-Doppelhand-Token")))
        self._reply({"ok": True, "action": "posted", "path": self.path})

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    Handler.calls = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_reads_use_get_with_token(server):
    dh = Doppelhand(base_url=server, token="tok")
    assert dh.screen() == {"ok": True, "view": [1280, 720]}
    assert dh.shot()["path"] == "C:\\shot.png"
    assert Handler.calls[0] == ("GET", "/screen", "tok")
    assert Handler.calls[1] == ("GET", "/shot", "tok")


def test_writes_use_post_with_query_args(server):
    dh = Doppelhand(base_url=server, token="tok")
    dh.click(232, 400)
    dh.type("a b.docx")
    dh.key("Return")
    paths = [c[1] for c in Handler.calls]
    assert paths[0] == "/click?at=232,400"
    assert paths[1] == "/type?text=a%20b.docx"
    assert paths[2] == "/key?combo=Return"
    assert all(c[0] == "POST" for c in Handler.calls)


def test_type_file_and_confirm_chains(server):
    dh = Doppelhand(base_url=server)
    r = dh.type_file_and_confirm("cv.docx")
    assert r["typed"]["ok"] and r["pressed"]["ok"]
    assert len(Handler.calls) == 2


def test_unreachable_server_returns_result_not_raise():
    # Nothing listens on this port: calls return a failure dict (issue #31).
    dh = Doppelhand(base_url="http://127.0.0.1:1", timeout=2)
    r = dh.screen()
    assert r["success"] is False and r["code"] == "doppelhand_unreachable", r


def test_http_error_returns_result_not_raise():
    class ErrHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), ErrHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = Doppelhand(base_url=f"http://127.0.0.1:{httpd.server_port}", timeout=2).screen()
    finally:
        httpd.shutdown()
    assert r["success"] is False and r["code"] == "doppelhand_http_503", r


def test_css_to_view_center():
    x, y = Doppelhand.css_to_view({"x": 100, "y": 50, "width": 200, "height": 100}, (1000, 500), (1280, 720))
    assert (x, y) == (256, 144)
