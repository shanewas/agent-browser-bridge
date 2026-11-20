"""Tests for group_list / group_move / group_ungroup (param building + CLI parsing)."""
import json
from tests.fake_extension import FakeExtension
from tests.support import recording_edge, run_cli


def test_group_list_param_building():
    edge, seen = recording_edge()
    edge.group_list()
    assert seen["action"] == "group_list"
    assert seen["params"] == {}


def test_group_list_window_id():
    edge, seen = recording_edge()
    edge.group_list(window_id=3)
    assert seen["action"] == "group_list"
    assert seen["params"] == {"windowId": 3}


def test_group_move_param_building():
    edge, seen = recording_edge()
    edge.group_move([1, 2], group_id=7, title="Work", color="blue")
    assert seen["action"] == "group_move"
    assert seen["params"] == {"tabIds": [1, 2], "groupId": 7, "title": "Work", "color": "blue"}


def test_group_move_single_tab_id():
    edge, seen = recording_edge()
    edge.group_move(5)
    assert seen["action"] == "group_move"
    assert seen["params"] == {"tabIds": [5]}


def test_group_ungroup_param_building():
    edge, seen = recording_edge()
    edge.group_ungroup([1, 2])
    assert seen["action"] == "group_ungroup"
    assert seen["params"] == {"tabIds": [1, 2]}


def test_cli_group_list_sends_params(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "count": 0, "groups": []}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "group", "list"], env=env)
    assert code == 0, err
    assert json.loads(out)["success"] is True
    lists = [p for a, p in seen if a == "group_list"]
    assert lists, seen
    assert "windowId" not in lists[-1]
    ext.close()


def test_cli_group_list_window_id(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "count": 0, "groups": []}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "group", "list", "--window-id", "3"], env=env)
    assert code == 0, err
    lists = [p for a, p in seen if a == "group_list"]
    assert lists, seen
    assert lists[-1].get("windowId") == 3
    ext.close()


def test_cli_group_move_sends_params(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "groupId": 7, "tabIds": params.get("tabIds")}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(
        ["--json", "group", "move", "1", "2", "--group-id", "7", "--title", "Work", "--color", "blue"],
        env=env,
    )
    assert code == 0, err
    assert json.loads(out)["success"] is True
    moves = [p for a, p in seen if a == "group_move"]
    assert moves, seen
    assert moves[-1].get("tabIds") == [1, 2]
    assert moves[-1].get("groupId") == 7
    assert moves[-1].get("title") == "Work"
    assert moves[-1].get("color") == "blue"
    ext.close()


def test_cli_group_ungroup_sends_params(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "tabIds": params.get("tabIds")}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "group", "ungroup", "1", "2"], env=env)
    assert code == 0, err
    assert json.loads(out)["success"] is True
    ungroups = [p for a, p in seen if a == "group_ungroup"]
    assert ungroups, seen
    assert ungroups[-1].get("tabIds") == [1, 2]
    ext.close()


def test_cli_group_move_without_tabs_exits_3(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["group", "move"], env=env)
    assert code == 3


def test_cli_group_ungroup_without_tabs_exits_3(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["group", "ungroup"], env=env)
    assert code == 3


def test_cli_group_move_noninteger_tabs_exits_3(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["group", "move", "abc"], env=env)
    assert code == 3
    assert "integers" in err


def test_cli_group_ungroup_noninteger_tabs_exits_3(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["group", "ungroup", "1", "xyz"], env=env)
    assert code == 3
    assert "integers" in err


def test_cli_group_move_noninteger_tabs_json(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "group", "move", "abc"], env=env)
    assert code == 3
    body = json.loads(out)
    assert body["success"] is False
    assert body["code"] == "bad_params"
    assert "integers" in body["error"]


# ---- takeover isolation: group ops must not move a tab the user holds ----

def test_group_move_blocked_when_a_target_tab_is_paused(tmp_path):
    from agent_browser_bridge.bridge import BridgeState

    state = BridgeState(port=0, home=tmp_path, token="test-token")
    with state.lock:
        state.set_tab_pause_locked(7, True)
        blocked = state.pause_blocked_locked("group_move", tab_ids=[6, 7])
        assert blocked is not None and blocked["code"] == "user_takeover"
        blocked = state.pause_blocked_locked("group_ungroup", tab_ids=[7])
        assert blocked is not None and blocked["code"] == "user_takeover"
        # Untouched tabs group freely; listing groups is metadata and stays open.
        assert state.pause_blocked_locked("group_move", tab_ids=[6]) is None
        assert state.pause_blocked_locked("group_list", tab_ids=[7]) is None


def test_group_move_allowed_after_pause_expires(tmp_path):
    import time

    from agent_browser_bridge.bridge import BridgeState

    state = BridgeState(port=0, home=tmp_path, token="test-token")
    with state.lock:
        state.paused_tabs[7] = time.time() - 1
        assert state.pause_blocked_locked("group_move", tab_ids=[7]) is None
        assert 7 not in state.paused_tabs


def test_group_pause_gate_sees_coerced_tab_ids(tmp_path):
    from agent_browser_bridge.bridge import BridgeState, _param_tab_ids

    state = BridgeState(port=0, home=tmp_path, token="test-token")
    with state.lock:
        state.set_tab_pause_locked(7, True)
        state.set_tab_pause_locked(1, True)
        # The worker coerces tabIds with Number(): each of these shapes
        # names tab 7 by the time chrome.tabs.group runs, so the gate
        # must see tab 7 in every one of them.
        for params in ({"tabIds": ["7"]}, {"tabIds": [7.0]}, {"tabIds": 7},
                       {"tabId": 7}, {"tabIds": [6, "7"]}):
            blocked = state.pause_blocked_locked(
                "group_move", tab_ids=_param_tab_ids(params))
            assert blocked is not None and blocked["code"] == "user_takeover", params
        # Number(true) is 1 in the worker.
        blocked = state.pause_blocked_locked(
            "group_move", tab_ids=_param_tab_ids({"tabIds": [True]}))
        assert blocked is not None and blocked["code"] == "user_takeover"
        # Shapes that name no paused tab pass.
        for params in ({"tabIds": ["x"]}, {"tabIds": [8]}, {"tabIds": []}, {}):
            assert state.pause_blocked_locked(
                "group_move", tab_ids=_param_tab_ids(params)) is None, params


def test_group_move_on_paused_tab_blocked_end_to_end(daemon):
    import time as _time

    def handler(action, params):
        return {"success": True, "groupId": 7, "tabIds": params.get("tabIds")}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    try:
        ext.send_text(json.dumps(
            {"control": "takeover", "id": "tg1", "tabId": 7, "paused": True}))
        # The pause lands asynchronously; poll until the gate answers.
        result = {}
        deadline = _time.time() + 10
        while _time.time() < deadline:
            code, out, err = run_cli(["--json", "group", "move", "7"], env=env)
            result = json.loads(out) if out else {}
            if result.get("code") == "user_takeover":
                break
            _time.sleep(0.2)
        assert result.get("code") == "user_takeover", result
        # A tab the user has not taken over still groups fine.
        code, out, err = run_cli(["--json", "group", "move", "8"], env=env)
        assert json.loads(out)["success"] is True, out
    finally:
        ext.close()


def test_group_pause_gate_mirrors_worker_coercion(tmp_path):
    from agent_browser_bridge.bridge import BridgeState, _param_tab_ids

    state = BridgeState(port=0, home=tmp_path, token="test-token")
    with state.lock:
        state.set_tab_pause_locked(7, True)
        # Shapes the worker's Number() coercion turns into tab 7, verified
        # against Node: nested single-element arrays stringify first, and
        # radix-prefixed strings parse.
        for params in ({"tabIds": [[7]]}, {"tabIds": [6, [7]]},
                       {"tabIds": ["0x7"]}, {"tabIds": ["0b111"]},
                       {"tabIds": ["0o7"]}, {"tabIds": [" 7 "]}):
            blocked = state.pause_blocked_locked(
                "group_move", tab_ids=_param_tab_ids(params))
            assert blocked is not None and blocked["code"] == "user_takeover", params
        # Shapes JS rejects (signed radix, numeric underscores) name no
        # tab in the worker either, so the gate must not invent one.
        for params in ({"tabIds": ["-0x7"]}, {"tabIds": ["1_0"]},
                       {"tabIds": [6, [8]]}):
            assert state.pause_blocked_locked(
                "group_move", tab_ids=_param_tab_ids(params)) is None, params
        # A several-hundred-digit integer is Infinity in JS, not an
        # exception: the gate answers, it does not throw.
        assert _param_tab_ids({"tabIds": [10 ** 400]}) is None


def test_group_move_in_batch_blocked_on_paused_tab(daemon):
    import time as _time

    def handler(action, params):
        return {"success": True, "groupId": 7, "tabIds": params.get("tabIds")}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    try:
        ext.send_text(json.dumps(
            {"control": "takeover", "id": "tg2", "tabId": 7, "paused": True}))
        # The pause lands asynchronously; poll a direct move until the
        # gate answers, then exercise the batch path.
        deadline = _time.time() + 10
        result = {}
        while _time.time() < deadline:
            code, body = daemon.exec("group_move", {"tabIds": [7]})
            result = body
            if result.get("code") == "user_takeover":
                break
            _time.sleep(0.2)
        assert result.get("code") == "user_takeover", result
        code, body = daemon.exec(
            "batch", {"steps": [{"action": "group_move", "tabIds": [7]}]})
        assert body.get("success") is False, body
        assert body.get("stoppedAt") == "group_move", body
        assert body["results"][0].get("code") == "user_takeover", body
        code, body = daemon.exec(
            "batch", {"steps": [{"action": "group_move", "tabIds": [8]}]})
        assert body.get("success") is True, body
    finally:
        ext.close()
