"""Tab title activity label: daemon storage, cleaning, forwarding."""
import json

from agent_browser_bridge import config
from agent_browser_bridge.bridge import _clean_doing_label, _doing_label_rejected


def _exec_raw(daemon, body):
    return daemon._request("POST", "/exec", body,
                           headers={config.TOKEN_HEADER: daemon.token})[1]


def _frames_for(ext, action):
    return [m for m in ext.received if m.get("action") == action]


def _ok_handler(action, params):
    if action == "set_doing":
        return {"success": True}
    if action in ("get_active_tab", "tab"):
        return {"success": True, "tab": {"id": 1, "title": "T", "url": "http://x/"}}
    if action == "ping":
        return {"success": True, "message": "pong"}
    return {"success": False, "code": "unknown_action",
            "error": f"Unknown action: {action}"}


def test_clean_doing_label():
    assert _clean_doing_label("Deploying the cache rule") == "Deploying the cache rule"
    assert _clean_doing_label("x" * 80) == "x" * 60
    assert _clean_doing_label("   ") is None
    assert _clean_doing_label(None) is None
    assert _clean_doing_label(7) is None
    assert _doing_label_rejected("see https://evil.example now") is True
    assert _doing_label_rejected("visit www.evil.example") is True
    assert _doing_label_rejected("Deploying the cache rule") is False


def test_feature_advertised(daemon, fake_ext):
    assert "tab_title" in daemon.status().get("features", [])


def test_session_start_stores_doing(daemon, fake_ext):
    res = _exec_raw(daemon, {"action": "session_start",
                             "params": {"name": "t1", "agent": "Sigil",
                                        "doing": "Deploying the cache rule"}})
    assert res["success"] and res["doing"] == "Deploying the cache rule"
    stok = res["sessionToken"]
    res = _exec_raw(daemon, {"action": "session_status", "sessionToken": stok})
    assert res["doing"] == "Deploying the cache rule"
    # A link in the label is rejected at mint.
    res = _exec_raw(daemon, {"action": "session_start",
                             "params": {"doing": "go to https://evil.example"}})
    assert res["success"] is False and res["code"] == "label_rejected"


def test_set_doing_forwards_and_updates_session(daemon, fake_ext):
    fake_ext.handler = _ok_handler
    res = _exec_raw(daemon, {"action": "session_start",
                             "params": {"name": "t2", "agent": "Sigil"}})
    stok = res["sessionToken"]
    # Pin the session to a tab with a normal command first.
    res = _exec_raw(daemon, {"action": "get_active_tab", "sessionToken": stok})
    assert res["success"], res
    res = _exec_raw(daemon, {"action": "set_doing", "sessionToken": stok,
                             "params": {"doing": "Deploying the cache rule"}})
    assert res["success"] and res["doing"] == "Deploying the cache rule", res
    frames = _frames_for(fake_ext, "set_doing")
    assert frames and frames[-1].get("doing") == "Deploying the cache rule", frames
    # The label now rides an ordinary command's frame.
    fake_ext.received.clear()
    res = _exec_raw(daemon, {"action": "ping", "sessionToken": stok})
    assert res["success"]
    frames = _frames_for(fake_ext, "ping")
    assert frames and frames[-1].get("doing") == "Deploying the cache rule", frames
    # Clearing forwards an explicit null, so the worker can tell a clear
    # from a frame that simply does not mention the label.
    res = _exec_raw(daemon, {"action": "set_doing", "sessionToken": stok,
                             "params": {"doing": ""}})
    assert res["success"] and res["doing"] is None, res
    frames = _frames_for(fake_ext, "set_doing")
    assert frames and "doing" in frames[-1] and frames[-1]["doing"] is None, frames
    res = _exec_raw(daemon, {"action": "session_status", "sessionToken": stok})
    assert res["doing"] is None


def test_set_doing_rejects_links_and_bad_types(daemon, fake_ext):
    res = _exec_raw(daemon, {"action": "set_doing",
                             "params": {"doing": "open www.evil.example"}})
    assert res["success"] is False and res["code"] == "label_rejected"
    res = _exec_raw(daemon, {"action": "set_doing", "params": {"doing": 7}})
    assert res["success"] is False and res["code"] == "bad_params"
    # No session, no tab: stored nowhere, still a clean success - the
    # caller's own client keeps the label and stamps later requests.
    res = _exec_raw(daemon, {"action": "set_doing",
                             "params": {"doing": "Reading mail"}})
    assert res["success"] and res["doing"] == "Reading mail"


def test_batch_set_doing_step_updates_session(daemon, fake_ext):
    fake_ext.handler = _ok_handler
    res = _exec_raw(daemon, {"action": "session_start",
                             "params": {"name": "t3", "agent": "Sigil"}})
    stok = res["sessionToken"]
    res = _exec_raw(daemon, {"action": "get_active_tab", "sessionToken": stok})
    assert res["success"], res
    fake_ext.received.clear()
    res = _exec_raw(daemon, {"action": "batch", "sessionToken": stok,
                             "params": {"steps": [
                                 {"action": "set_doing", "doing": "Batch label"},
                                 {"action": "ping"},
                             ]}})
    assert res["success"], res
    frames = _frames_for(fake_ext, "set_doing")
    assert frames and frames[-1].get("doing") == "Batch label", frames
    frames = _frames_for(fake_ext, "ping")
    assert frames and frames[-1].get("doing") == "Batch label", frames
    res = _exec_raw(daemon, {"action": "session_status", "sessionToken": stok})
    assert res["doing"] == "Batch label"


def test_set_doing_forward_failure_keeps_session(daemon, fake_ext):
    def failing(action, params):
        if action == "set_doing":
            return {"success": False, "code": "boom", "error": "nope"}
        return _ok_handler(action, params)
    fake_ext.handler = failing
    res = _exec_raw(daemon, {"action": "session_start",
                             "params": {"name": "t4", "agent": "Sigil",
                                        "doing": "Original label"}})
    stok = res["sessionToken"]
    res = _exec_raw(daemon, {"action": "get_active_tab", "sessionToken": stok})
    assert res["success"], res
    res = _exec_raw(daemon, {"action": "set_doing", "sessionToken": stok,
                             "params": {"doing": "New label"}})
    assert res["success"] is False, res
    res = _exec_raw(daemon, {"action": "session_status", "sessionToken": stok})
    assert res["doing"] == "Original label"
