"""Spotlight wiring on the Python side: daemon feature/pause sets and the
client methods' payload shapes."""
import json

from agent_browser_bridge import bridge
from agent_browser_bridge.bridge import BridgeState
from agent_browser_bridge.client import EdgeClient
from tests.fake_extension import FakeExtension
from tests.support import run_cli


def test_spotlight_feature_advertised():
    assert "spotlight" in bridge.FEATURES


def test_pause_allow_only_covers_clear_and_status():
    # Draws must stay blocked while the user has the tab; clearing the agent's
    # own overlay and reading its status are metadata-level and stay allowed.
    assert "spotlight_clear" in bridge.PAUSE_ALLOW_ACTIONS
    assert "spotlight_status" in bridge.PAUSE_ALLOW_ACTIONS
    assert "spotlight" not in bridge.PAUSE_ALLOW_ACTIONS


def _client_with_capture():
    client = EdgeClient.__new__(EdgeClient)
    calls = []

    def fake_send(action, params=None, timeout=None):
        calls.append((action, params or {}))
        return {"success": True}

    client.send = fake_send
    return client, calls


def test_client_spotlight_payload():
    client, calls = _client_with_capture()
    client.spotlight(target="Save", mode="ring", label="About to click Save", tab_id=7)
    assert calls == [("spotlight", {"tabId": 7, "target": "Save", "mode": "ring",
                                    "label": "About to click Save"})]


def test_client_spotlight_point_payload_omits_unset():
    client, calls = _client_with_capture()
    client.spotlight(x=10, y=20, label="The spike")
    assert calls == [("spotlight", {"x": 10, "y": 20, "label": "The spike"})]


def test_client_spotlight_clear_and_status():
    client, calls = _client_with_capture()
    client.spotlight_clear(tab_id=3)
    client.spotlight_status()
    assert calls == [("spotlight_clear", {"tabId": 3}), ("spotlight_status", {})]


def test_spotlight_draw_on_paused_tab_is_tab_paused(tmp_path):
    # The daemon gate answers a draw on a paused tab with the tab_paused code
    # the guide promises, not the generic takeover code other actions get.
    state = BridgeState(port=0, home=tmp_path, token="test")
    state.set_tab_pause_locked(7, True)
    assert state.pause_blocked_locked("spotlight", tab_id=7)["code"] == "tab_paused"
    assert state.pause_blocked_locked("click", tab_id=7)["code"] == "user_takeover"
    assert state.pause_blocked_locked("spotlight_clear", tab_id=7) is None
    assert state.pause_blocked_locked("spotlight", tab_id=8) is None


def test_cli_spotlight_rect_media_time_and_scale_factor(daemon):
    seen = {}

    def handler(action, params):
        seen[action] = params
        return {"success": True, "mode_effective": "ring"}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    try:
        env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
        code, out, err = run_cli(
            ["--json", "spotlight", "show",
             "--rect", '{"x": 1, "y": 2, "width": 30, "height": 40}',
             "--media-time", "12.5", "--scale-factor", "2"],
            env=env)
        assert code == 0, err
        params = seen["spotlight"]
        assert params["rect"] == {"x": 1, "y": 2, "width": 30, "height": 40}
        assert params["media_time"] == 12.5
        assert params["scale_factor"] == 2.0
    finally:
        ext.close()
