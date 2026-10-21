"""Tests for agent_browser_bridge.mcp server."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from tests.fake_extension import FakeExtension
from tests.support import free_port

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mcp_proc(daemon, fake_ext):
    env = dict(
        os.environ,
        EDGE_BRIDGE_HOME=str(daemon.home),
        EDGE_BRIDGE_PORT=str(daemon.port),
        PYTHONUNBUFFERED="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_browser_bridge.mcp"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    yield proc
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def send_rpc(proc, msg):
    line = json.dumps(msg).encode("utf-8") + b"\n"
    proc.stdin.write(line)
    proc.stdin.flush()
    resp_line = proc.stdout.readline().decode("utf-8").strip()
    return json.loads(resp_line) if resp_line else None


def test_mcp_initialize(mcp_proc):
    resp = send_rpc(
        mcp_proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        },
    )
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2025-11-25"
    assert resp["result"]["capabilities"]["tools"] == {}
    assert resp["result"]["serverInfo"]["name"] == "edge-agent-bridge"


def test_mcp_notifications_and_ping(mcp_proc):
    # notifications/initialized returns nothing
    line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8") + b"\n"
    mcp_proc.stdin.write(line)
    mcp_proc.stdin.flush()

    # ping returns {}
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert resp["id"] == 2
    assert resp["result"] == {}


def test_mcp_tools_list_34_tools(mcp_proc):
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert len(tools) == 34
    for t in tools:
        assert t["name"].startswith("edge_")
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"


def test_mcp_tools_call_status(mcp_proc):
    resp = send_rpc(
        mcp_proc,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "edge_status", "arguments": {}},
        },
    )
    assert resp["id"] == 4
    assert resp["result"]["isError"] is False
    content = resp["result"]["content"]
    assert len(content) >= 1
    assert content[0]["type"] == "text"
    assert "bridge_running" in content[0]["text"]


def test_mcp_tools_call_pinning(daemon):
    captured_commands = []

    def handler(action, params):
        captured_commands.append((action, dict(params)))
        return {"success": True, "tab": {"id": 42, "title": "PinTab", "url": "http://pin"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)

    env = dict(
        os.environ,
        EDGE_BRIDGE_HOME=str(daemon.home),
        EDGE_BRIDGE_PORT=str(daemon.port),
        PYTHONUNBUFFERED="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_browser_bridge.mcp"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # First call: edge_tab
    r1 = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "edge_tab", "arguments": {}},
        },
    )
    assert r1["result"]["isError"] is False

    # Second call: edge_tab - should have pinned tabId: 42
    r2 = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "edge_tab", "arguments": {}},
        },
    )
    assert r2["result"]["isError"] is False
    assert captured_commands[1][1].get("tabId") == 42

    proc.terminate()
    proc.wait(timeout=2)
    ext.close()


def test_mcp_unknown_tool_and_malformed(mcp_proc):
    # Unknown tool
    r1 = send_rpc(
        mcp_proc,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
    )
    assert r1["error"]["code"] == -32602

    # Malformed line
    mcp_proc.stdin.write(b"this is not json\n")
    mcp_proc.stdin.flush()
    resp_line = mcp_proc.stdout.readline().decode("utf-8").strip()
    r2 = json.loads(resp_line)
    assert r2["error"]["code"] == -32700


def test_mcp_non_object_lines_get_invalid_request(mcp_proc):
    # Valid JSON that is not an object must not kill the server (issue #27).
    for raw in (b"[]\n", b"42\n", b'"x"\n'):
        mcp_proc.stdin.write(raw)
        mcp_proc.stdin.flush()
        resp = json.loads(mcp_proc.stdout.readline().decode("utf-8").strip())
        assert resp["id"] is None, resp
        assert resp["error"]["code"] == -32600, resp
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert resp["id"] == 9 and resp["result"] == {}


def test_mcp_tool_failure_is_error(daemon):
    def handler(action, params):
        return {"success": False, "code": "target_not_found", "error": "Element not found"}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)

    env = dict(
        os.environ,
        EDGE_BRIDGE_HOME=str(daemon.home),
        EDGE_BRIDGE_PORT=str(daemon.port),
        PYTHONUNBUFFERED="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_browser_bridge.mcp"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    r = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "edge_click", "arguments": {"target": "#missing"}},
        },
    )
    assert r["result"]["isError"] is True
    assert "target_not_found" in r["result"]["content"][0]["text"]

    proc.terminate()
    proc.wait(timeout=2)
    ext.close()


def test_screenshot_returns_an_image_block(mcp_proc):
    # The extension returns the image under dataUrl; reading "data" fell through to a JSON dump.
    send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25"}})
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "edge_screenshot", "arguments": {}}})
    content = resp["result"]["content"]
    assert resp["result"].get("isError") is not True
    image = [c for c in content if c["type"] == "image"]
    assert len(image) == 1
    assert image[0]["mimeType"] == "image/jpeg"
    assert not image[0]["data"].startswith("data:")


def test_request_id_zero_gets_a_response(mcp_proc):
    # JSON-RPC allows id 0; a falsy check treated it as a notification and the client hung.
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                               "params": {"protocolVersion": "2025-11-25"}})
    assert resp is not None and resp["id"] == 0
    assert resp["result"]["serverInfo"]["name"] == "edge-agent-bridge"


def test_tools_list_matches_the_router(mcp_proc):
    send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25"}})
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    listed = {t["name"] for t in resp["result"]["tools"]}
    # Every listed tool must route to something rather than fall through to "Unknown tool".
    for i, name in enumerate(sorted(listed), start=10):
        r = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                "params": {"name": name, "arguments": {}}})
        assert "error" not in r, f"{name} is listed but not routed: {r.get('error')}"


def test_snapshot_schema_has_compact_and_max_nodes(mcp_proc):
    resp = send_rpc(mcp_proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    snap = next(t for t in resp["result"]["tools"] if t["name"] == "edge_snapshot")
    props = snap["inputSchema"]["properties"]
    assert props["mode"]["enum"] == ["interactive", "full", "compact"]
    assert props["maxNodes"]["type"] == "integer"


def test_snapshot_forwards_mode_and_max_nodes(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "text": "page", "refs": 0, "truncated": False, "total": 0,
                "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.2)
    env = dict(
        os.environ,
        EDGE_BRIDGE_HOME=str(daemon.home),
        EDGE_BRIDGE_PORT=str(daemon.port),
        PYTHONUNBUFFERED="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_browser_bridge.mcp"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        r = send_rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                            "params": {"name": "edge_snapshot",
                                       "arguments": {"mode": "compact", "maxNodes": 50}}})
        assert r["result"]["isError"] is False
        snaps = [p for a, p in seen if a == "snapshot"]
        assert snaps[-1].get("mode") == "compact"
        assert snaps[-1].get("maxNodes") == 50
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        ext.close()


def test_mcp_session_is_named(daemon, mcp_proc):
    names = []
    for _ in range(25):
        time.sleep(0.1)
        _, body = daemon.exec("session_list")
        if body.get("success"):
            names = [s["name"] for s in body.get("sessions", [])]
            if "mcp" in names:
                break
    assert "mcp" in names
