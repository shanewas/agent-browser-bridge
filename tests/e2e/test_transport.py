import json
import threading
import time

import pytest

from agent_browser_bridge import config

pytestmark = pytest.mark.e2e


def test_hello_version_matches_manifest(e2e_daemon, edge):
    manifest = json.loads((config.extension_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert e2e_daemon.status()["extension_version"] == manifest["version"]


def test_ping_and_tab_shape(client, pages):
    r = client("nav", {"url": pages + "/blank.html"})
    assert r["success"] and r["tab"]["url"].endswith("/blank.html")
    assert set(r["tab"]) >= {"id", "title", "url"}
    r = client("ping")
    assert r["success"] and r["version"]


def test_tab_not_found_code(client):
    r = client("tab", {"tabId": 999999})
    assert r["success"] is False and r["code"] == "tab_not_found"


def test_tab_close_requires_tab_id(client):
    r = client("tab_close")
    assert r["success"] is False and r["code"] == "tab_required"


def test_commands_on_one_tab_serialize(client, pages):
    client("nav", {"url": pages + "/blank.html"})
    client("eval", {"code": "window.__order = []"})

    def go(n):
        delay = 300 - n * 100
        client("eval", {"code": f"new Promise(r => setTimeout(() => {{ window.__order.push({n}); r(1) }}, {delay}))"})

    threads = [threading.Thread(target=go, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
        time.sleep(0.05)
    for t in threads:
        t.join()
    assert client("eval", {"code": "window.__order"})["result"] == [0, 1, 2]


def test_reconnect_after_daemon_restart(e2e_daemon, edge, client):
    e2e_daemon.restart()
    deadline = time.time() + 15
    while time.time() < deadline and not e2e_daemon.status().get("websocket_active"):
        time.sleep(0.25)
    assert e2e_daemon.status()["websocket_active"] is True
    assert client("ping")["success"] is True
