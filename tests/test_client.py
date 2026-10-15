"""Tests for edge_agent_bridge.client."""
import http.client
import json
import os
import threading
import time
from pathlib import Path
import pytest
from edge_agent_bridge import config
from edge_agent_bridge.client import Edge, EdgeClient, send_cmd, ensure_bridge_running
from tests.fake_extension import FakeExtension
from tests.support import DaemonHandle, free_port


@pytest.fixture(autouse=True)
def _setup_home(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))


def test_keepalive_reuse(daemon, fake_ext, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    edge = Edge(port=daemon.port, auto_start=False)
    r1 = edge.send("ping")
    conn1 = edge._conn
    assert r1.get("success") is True
    r2 = edge.send("ping")
    conn2 = edge._conn
    assert r2.get("success") is True
    assert conn1 is conn2
    edge.close()
    assert edge._conn is None


def test_reconnect_after_daemon_restart(daemon, fake_ext, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    edge = Edge(port=daemon.port, auto_start=False)
    r1 = edge.send("ping")
    assert r1.get("success") is True

    # Restart daemon
    daemon.restart()
    # Reconnect fake extension to restarted daemon
    ext2 = FakeExtension(daemon.port).connect().run()
    deadline = time.time() + 5
    while time.time() < deadline and not daemon.status().get("websocket_active"):
        time.sleep(0.05)

    r2 = edge.send("ping")
    assert r2.get("success") is True
    edge.close()
    ext2.close()


def test_daemon_unreachable():
    port = free_port()
    edge = Edge(port=port, auto_start=False)
    r = edge.send("ping")
    assert r["success"] is False
    assert r["code"] == "daemon_unreachable"
    edge.close()


def test_missing_token(daemon, monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path / "empty_home"))
    edge = Edge(port=daemon.port, auto_start=False)
    r = edge.send("ping")
    assert r["success"] is False
    assert r["code"] == "missing_token"
    assert str(config.token_path()) in r["error"]
    edge.close()


def test_pinning_and_pin_drop(daemon, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    # Set up fake extension with scripted tab responses
    responses = {
        "tab": {"success": True, "tab": {"id": 7, "title": "Test", "url": "http://test"}},
        "click": {"success": True, "tab": {"id": 7, "title": "Test", "url": "http://test"}},
        "tab_close": {"success": True},
        "nav": {"success": True, "tab": {"id": 8, "title": "Nav", "url": "http://nav"}},
    }
    captured_commands = []

    def handler(action, params):
        captured_commands.append((action, dict(params)))
        if action == "drop_test":
            return {"success": False, "code": "tab_not_found", "error": "tab 7 closed"}
        return responses.get(action, {"success": True})

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)

    edge = Edge(port=daemon.port, pin=True, auto_start=False)
    assert edge.tab_id is None

    # First command: resolves active tab (id: 7)
    edge.send("tab")
    assert edge.tab_id == 7
    assert "tabId" not in captured_commands[0][1]

    # Second command: should automatically include tabId: 7
    edge.send("click", {"selector": "button"})
    assert captured_commands[1][1].get("tabId") == 7

    # Tab close of pinned tab (7) drops pin
    edge.send("tab_close", {"tabId": 7})
    assert edge.tab_id is None

    # Next command sends without pinned tabId, re-pins if response contains tab
    edge.send("nav", {"url": "http://nav"})
    assert edge.tab_id == 8

    # When a command returns tab_not_found, pin is dropped
    edge.send("drop_test")
    assert edge.tab_id is None

    edge.close()
    ext.close()


def test_failed_result_does_not_pin(daemon, monkeypatch):
    # A refusal names the tab it refused; pinning to it would send every later call there.
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    seen = []

    def handler(action, params):
        seen.append(dict(params))
        if action == "snapshot":
            return {"success": False, "code": "tab_not_scriptable", "error": "Tab 9 shows edge://newtab/",
                    "tab": {"id": 9, "title": "New tab", "url": "edge://newtab/"}}
        return {"success": True, "tab": {"id": 7, "title": "T", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, pin=True, auto_start=False)
    assert edge.send("snapshot")["code"] == "tab_not_scriptable"
    assert edge.tab_id is None and edge._last_tab_id is None
    edge.send("tab")
    assert "tabId" not in seen[-1]
    assert edge.tab_id == 7
    edge.close()
    ext.close()


def test_ensure_bridge_running_waits_for_websocket_active(daemon):
    # Daemon is running, but no extension connected yet
    # ensure_bridge_running with wait_for_extension=True should wait until extension connects
    assert not daemon.status().get("websocket_active")

    def connect_later():
        time.sleep(0.5)
        ext = FakeExtension(daemon.port).connect().run()
        # Keep alive for 2 seconds
        time.sleep(2)
        ext.close()

    t = threading.Thread(target=connect_later)
    t.start()

    res = ensure_bridge_running(port=daemon.port, wait_for_extension=True, timeout=5.0)
    assert res is True
    t.join()


def test_screenshot_writes_the_file(daemon, monkeypatch, tmp_path):
    # The extension returns the image under dataUrl; reading "data" silently wrote nothing.
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    png = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="

    def handler(action, params):
        if action == "screenshot":
            return {"success": True, "dataUrl": png, "format": "jpeg",
                    "tab": {"id": 3, "title": "t", "url": "http://t"}}
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    out = tmp_path / "shots" / "page.jpg"
    edge = Edge(port=daemon.port, auto_start=False)
    res = edge.screenshot(str(out))
    edge.close()
    ext.close()

    assert res.get("success") is True
    assert out.exists() and out.read_bytes().startswith(b"\xff\xd8")
    assert res["path"] == str(out.resolve())


def test_explicit_tab_id_repins(daemon, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))

    def handler(action, params):
        tid = params.get("tabId", 7)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, pin=True, auto_start=False)
    edge.send("tab")
    assert edge.tab_id == 7
    edge.send("click", {"tabId": 11, "selector": "b"})
    assert edge.tab_id == 11
    edge.close()
    ext.close()


def test_snapshot_sends_max_nodes(daemon, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    seen = []

    def handler(action, params):
        seen.append(dict(params))
        return {"success": True, "text": "page", "refs": 0,
                "tab": {"id": 3, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, auto_start=False)
    edge.snapshot()
    assert seen[-1].get("maxNodes") == 400
    edge.snapshot(max_nodes=50)
    assert seen[-1].get("maxNodes") == 50
    edge.snapshot(max_nodes=0)
    assert seen[-1].get("maxNodes") == 0
    edge.close()
    ext.close()


def test_wsl_to_windows_table():
    from edge_agent_bridge.client import _wsl_to_windows
    assert _wsl_to_windows("/mnt/c/tmp/spec.pdf") == "C:\\tmp\\spec.pdf"
    assert _wsl_to_windows("/mnt/D/a/b") == "D:\\a\\b"
    assert _wsl_to_windows("C:\\tmp\\spec.pdf") == "C:\\tmp\\spec.pdf"
    assert _wsl_to_windows("/tmp/spec.pdf") == "/tmp/spec.pdf"
    assert _wsl_to_windows("/mnt/c") == "/mnt/c"
    assert _wsl_to_windows("/mnt/1/x") == "/mnt/1/x"
    assert _wsl_to_windows("") == ""


def test_upload_missing_file_errors_locally(tmp_path):
    from edge_agent_bridge.client import _wsl_to_windows
    edge = Edge(port=1, auto_start=False)
    missing = str(tmp_path / "nope.pdf")
    r = edge.upload(ref="e1", files=[missing])
    assert r["success"] is False and r["code"] == "file_not_found"
    assert missing in r["error"]
    wsl = "/mnt/c/nope/nonexistent.pdf"
    r2 = edge.upload(ref="e1", files=[wsl])
    assert r2["code"] == "file_not_found"
    assert wsl in r2["error"]
    assert _wsl_to_windows(str(Path(wsl).resolve())) in r2["error"]
    edge.close()


def test_upload_forwards_existing_file(daemon, monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    f = tmp_path / "up.pdf"
    f.write_bytes(b"%PDF")
    seen = []

    def handler(action, params):
        seen.append(dict(params))
        return {"success": True, "tab": {"id": 3, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, auto_start=False)
    r = edge.upload(ref="e3", files=[str(f)])
    assert r["success"] is True
    assert seen[-1]["files"] == [str(Path(str(f)).resolve())]
    edge.close()
    ext.close()


def _restart_with_ext(daemon, handler):
    daemon.restart()
    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    deadline = time.time() + 5
    while time.time() < deadline and not daemon.status().get("websocket_active"):
        time.sleep(0.05)
    return ext


def test_session_remint_repins_remembered_tab(daemon, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        if action in ("tab_switch", "switch_tab"):
            tid = params.get("tabId", 7)
            return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}
        tid = params.get("tabId")
        return {"success": True, "tab": {"id": tid or 9, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, auto_start=False)
    mint = edge.send("session_start", {})
    assert mint["success"] is True
    edge.session_token = mint["sessionToken"]
    edge.send("tab_switch", {"tabId": 5})
    assert edge._last_tab_id == 5
    old_token = edge.session_token

    ext2 = _restart_with_ext(daemon, handler)
    r = edge.send("click", {"target": "b"})
    assert r["success"] is True
    assert edge.session_token != old_token
    switches = [p for a, p in seen if a in ("tab_switch", "switch_tab")]
    assert switches[-1].get("tabId") == 5
    clicks = [p for a, p in seen if a == "click"]
    assert clicks[-1].get("tabId") == 5
    edge.close()
    ext.close()
    ext2.close()


def test_one_shot_override_keeps_remembered_tab(daemon, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))

    def handler(action, params):
        tid = params.get("tabId", 7)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, auto_start=False)
    mint = edge.send("session_start", {})
    edge.session_token = mint["sessionToken"]
    edge.send("tab_switch", {"tabId": 5})
    edge.send("click", {"target": "b", "tabId": 9})
    assert edge._last_tab_id == 5
    edge.close()
    ext.close()


def test_remint_falls_back_to_active_tab_when_remembered_tab_gone(daemon, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(daemon.home))
    closed = []
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        if action in ("tab_switch", "switch_tab"):
            tid = params.get("tabId", 7)
            if tid == 5 and closed:
                return {"success": False, "code": "tab_not_found", "error": "gone"}
            return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}
        tid = params.get("tabId")
        return {"success": True, "tab": {"id": tid or 9, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    edge = Edge(port=daemon.port, auto_start=False)
    mint = edge.send("session_start", {})
    edge.session_token = mint["sessionToken"]
    edge.send("tab_switch", {"tabId": 5})

    closed.append(True)
    ext2 = _restart_with_ext(daemon, handler)
    r = edge.send("click", {"target": "b"})
    assert r["success"] is True
    clicks = [p for a, p in seen if a == "click"]
    assert "tabId" not in clicks[-1]
    assert edge._last_tab_id == 9
    edge.close()
    ext.close()
    ext2.close()
