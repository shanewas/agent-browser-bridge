"""Presence pill and takeover: daemon gate, control protocol, labels, clients."""
import json
import threading
import time
import types

from agent_browser_bridge import config
from agent_browser_bridge.bridge import (
    BridgeRequestHandler, BridgeState, HeldCommand, _clean_agent_label,
)
from agent_browser_bridge.client import LADDER_RETRY_CODES


def _state(tmp_path):
    return BridgeState(port=0, home=tmp_path, token="t")


def _handler(state):
    h = object.__new__(BridgeRequestHandler)
    h.server = types.SimpleNamespace(state=state)
    return h


class _Ws:
    closed = False
    grace_timer = None

    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(json.loads(text))
        return True


def _active(state, tmp_path):
    ws = _Ws()
    state.ext, state.ext_accepted = ws, True
    return ws


# ---- label cleaning ---------------------------------------------------------

def test_clean_agent_label():
    assert _clean_agent_label("Claude Code") == "Claude Code"
    assert _clean_agent_label("  Sigil  ") == "Sigil"
    assert _clean_agent_label("x" * 50) == "x" * 40
    assert _clean_agent_label("") is None
    assert _clean_agent_label("   ") is None
    assert _clean_agent_label(None) is None
    assert _clean_agent_label(42) is None
    assert _clean_agent_label("\x00\x07") is None


# ---- pause map --------------------------------------------------------------

def test_pause_blocks_content_not_metadata(tmp_path):
    state = _state(tmp_path)
    with state.lock:
        state.set_tab_pause_locked(7, True)
        assert state.pause_blocked_locked("click", tab_id=7)["code"] == "user_takeover"
        assert state.pause_blocked_locked("snapshot", tab_id=7)["code"] == "user_takeover"
        assert state.pause_blocked_locked("text", tab_id=7)["code"] == "user_takeover"
        assert state.pause_blocked_locked("dialog", tab_id=7)["code"] == "user_takeover"
        assert state.pause_blocked_locked("tabs", tab_id=7) is None
        assert state.pause_blocked_locked("ping", tab_id=7) is None
        assert state.pause_blocked_locked("status", tab_id=7) is None
        assert state.pause_blocked_locked("click", tab_id=8) is None


def test_pause_expiry(tmp_path):
    state = _state(tmp_path)
    with state.lock:
        state.paused_tabs[7] = time.time() - 1
        assert state.pause_blocked_locked("click", tab_id=7) is None
        assert 7 not in state.paused_tabs


def test_session_and_desktop_pauses(tmp_path):
    state = _state(tmp_path)
    with state.lock:
        state.paused_sessions.add("tok1")
        assert state.pause_blocked_locked("click", tab_id=99, session_token="tok1")
        assert state.pause_blocked_locked("click", tab_id=99, session_token="tok2") is None
        state.desktop_paused = True
        assert state.pause_blocked_locked("desktop_click")["code"] == "user_takeover"
        assert state.pause_blocked_locked("click", tab_id=3) is None


# ---- control protocol ---------------------------------------------------------

def test_control_takeover_acks_and_pauses(tmp_path):
    state = _state(tmp_path)
    ws = _active(state, tmp_path)
    h = _handler(state)
    h._handle_ws_json({"control": "takeover", "id": "c1", "tabId": 5, "paused": True}, ws)
    assert state.paused_tabs.get(5, 0) > time.time()
    assert ws.sent == [{"control_ack": "c1", "tabId": 5, "paused": True}]
    h._handle_ws_json({"control": "takeover", "id": "c2", "tabId": 5, "paused": False}, ws)
    assert 5 not in state.paused_tabs


def test_control_snapshot_replaces(tmp_path):
    state = _state(tmp_path)
    ws = _active(state, tmp_path)
    h = _handler(state)
    h._handle_ws_json({"control": "takeover", "id": "c1", "tabId": 5, "paused": True}, ws)
    h._handle_ws_json({"control": "snapshot", "id": "c2", "pausedTabs": [9]}, ws)
    assert set(state.live_paused_tabs_locked()) == {9}


def test_control_frames_validated(tmp_path):
    state = _state(tmp_path)
    ws = _active(state, tmp_path)
    h = _handler(state)
    h._handle_ws_json({"control": "takeover", "id": "c1", "tabId": "5", "paused": True}, ws)
    h._handle_ws_json({"control": "takeover", "id": "c2", "tabId": 5, "paused": "yes"}, ws)
    h._handle_ws_json({"control": "snapshot", "id": "c3", "pausedTabs": "5"}, ws)
    assert state.paused_tabs == {}
    assert ws.sent == []


def test_control_from_unaccepted_socket_ignored(tmp_path):
    state = _state(tmp_path)
    _active(state, tmp_path)
    stranger = _Ws()
    h = _handler(state)
    h._handle_ws_json({"control": "takeover", "id": "c1", "tabId": 5, "paused": True}, stranger)
    assert state.paused_tabs == {}


# ---- gates at dispatch --------------------------------------------------------

def test_guarded_recheck_refuses_paused_tab(tmp_path):
    state = _state(tmp_path)
    ws = _active(state, tmp_path)
    h = _handler(state)
    with state.lock:
        state.set_tab_pause_locked(4, True)
    res, code = h._dispatch_guarded(state, "click", {"tabId": 4}, time.time() + 5, True, 4)
    assert code == 200 and res["code"] == "user_takeover"
    assert ws.sent == []  # nothing reached the extension


def test_held_command_drain_respects_pause(tmp_path):
    state = _state(tmp_path)
    ws = _active(state, tmp_path)
    event = threading.Event()
    payload = {"id": "h1", "action": "click", "params": {"tabId": 6}}
    state.held.append(HeldCommand("h1", payload, event, time.time() + 30))
    with state.lock:
        state.set_tab_pause_locked(6, True)
        state._drain_held_locked()
    assert event.is_set()
    assert state.results["h1"]["code"] == "user_takeover"
    assert ws.sent == []


# ---- client / MCP contract ----------------------------------------------------

def test_user_takeover_is_never_a_ladder_retry():
    assert "user_takeover" not in LADDER_RETRY_CODES


def test_edge_agent_label_sources(monkeypatch):
    from agent_browser_bridge.client import Edge
    monkeypatch.delenv("AGENT_BRIDGE_AGENT_NAME", raising=False)
    assert Edge(auto_start=False).agent is None
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_NAME", "Env Agent")
    assert Edge(auto_start=False).agent == "Env Agent"
    assert Edge(auto_start=False, agent="Sigil").agent == "Sigil"


def test_mcp_initialize_captures_client_name_and_instructions():
    from agent_browser_bridge import mcp
    edge = types.SimpleNamespace(agent=None)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "Claude Code"}}}
    out = mcp.handle(msg, edge)
    assert edge.agent == "Claude Code"
    assert "user_takeover" in out["result"]["instructions"]


def test_mcp_client_name_does_not_override_explicit_label():
    from agent_browser_bridge import mcp
    edge = types.SimpleNamespace(agent="Sigil")
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "Other"}}}
    mcp.handle(msg, edge)
    assert edge.agent == "Sigil"


# ---- end to end through a live daemon -----------------------------------------

def _exec_raw(daemon, body):
    return daemon._request("POST", "/exec", body,
                           headers={config.TOKEN_HEADER: daemon.token})[1]


def test_live_takeover_flow(daemon, fake_ext):
    fake_ext.send_text(json.dumps({"control": "takeover", "id": "t1", "tabId": 1, "paused": True}))
    deadline = time.time() + 5
    ack = None
    while time.time() < deadline:
        for m in fake_ext.received:
            if m.get("control_ack") == "t1":
                ack = m
                break
        if ack:
            break
        time.sleep(0.05)
    assert ack == {"control_ack": "t1", "tabId": 1, "paused": True}

    sent_before = len([m for m in fake_ext.received if m.get("action") == "click"])
    _, res = daemon.exec("click", {"tabId": 1})
    assert res["code"] == "user_takeover"
    assert "Do not retry" in res["error"]
    sent_after = len([m for m in fake_ext.received if m.get("action") == "click"])
    assert sent_after == sent_before  # the command never reached the extension

    _, res = daemon.exec("tabs", {})
    assert res.get("code") != "user_takeover"  # metadata still answers

    status = daemon.status()
    assert "banner" in status["features"] and "takeover" in status["features"]
    assert status["paused_tabs"] == 1


def test_live_desktop_pause(daemon, fake_ext):
    fake_ext.send_text(json.dumps({"control": "desktop", "id": "d1", "paused": True}))
    deadline = time.time() + 5
    while time.time() < deadline and not daemon.status().get("desktop_paused"):
        time.sleep(0.05)
    assert daemon.status()["desktop_paused"] is True
    _, res = daemon.exec("desktop_click", {"x": 1, "y": 1})
    assert res["code"] == "user_takeover"


def test_live_session_agent_label(daemon, fake_ext):
    res = _exec_raw(daemon, {"action": "session_start",
                             "params": {"name": "pilltest", "agent": "Sigil"}})
    assert res["success"] and res["agent"] == "Sigil"
    stok = res["sessionToken"]
    res = _exec_raw(daemon, {"action": "session_status", "sessionToken": stok})
    assert res["agent"] == "Sigil" and res["paused"] is False
    res = _exec_raw(daemon, {"action": "session_start", "params": {"agent": "x" * 60}})
    assert res["success"] and res["agent"] == "x" * 40
