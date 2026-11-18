"""Spotlight wiring on the Python side: daemon feature/pause sets and the
client methods' payload shapes."""
from agent_browser_bridge import bridge
from agent_browser_bridge.client import EdgeClient


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
