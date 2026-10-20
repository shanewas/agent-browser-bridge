import base64
import json
import os
import socket
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _attach_second_cdp(profile, url_substring):
    port_file = Path(profile) / "DevToolsActivePort"
    for _ in range(50):
        if port_file.exists():
            break
        time.sleep(0.1)
    port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as resp:
        targets = json.loads(resp.read().decode("utf-8"))
    target = next((t for t in targets if url_substring in t.get("url", "")), None)
    assert target is not None, f"No target matching {url_substring} in {targets}"
    ws_url = target["webSocketDebuggerUrl"]
    path = ws_url.split(f":{port}", 1)[1]
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: 127.0.0.1:{port}\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n")
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp += chunk
    assert b"101" in resp, f"upgrade failed: {resp!r}"
    return sock


def test_actions_with_second_cdp_client_attached(client, edge, pages):
    csp_url = f"{pages}/csp.html"
    # Inactive, so a captureVisibleTab fallback would have to activate it and say so.
    nav_res = client("tab_new", {"url": csp_url, "active": False})
    assert nav_res["success"], nav_res
    tid = nav_res["tab"]["id"]

    try:
        second_client = _attach_second_cdp(edge.profile, "csp.html")
        try:
            # 1. eval runs via Runtime.evaluate on CDP, succeeding even under script-src 'self'
            eval_res = client("eval", {"code": "1 + 1", "tabId": tid})
            assert eval_res["success"] is True, eval_res
            assert eval_res["result"] == 2

            # 2. native click succeeds and dispatches trusted CDP input event
            click_res = client("click", {"x": 50, "y": 50, "tabId": tid})
            assert click_res["success"] is True, click_res
            assert click_res.get("native") is True, click_res

            # Check that button click fired
            clicked = client("eval", {"code": "window.__clicked", "tabId": tid})
            assert clicked.get("result") is True, clicked

            # 3. screenshot succeeds via CDP without activating/switching tabs
            shot_res = client("screenshot", {"tabId": tid})
            assert shot_res["success"] is True, shot_res
            assert shot_res.get("activated") is not True and "native" not in shot_res, shot_res
        finally:
            second_client.close()
    finally:
        client("tab_close", {"tabId": tid})
