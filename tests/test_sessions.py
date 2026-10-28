import time
from tests.fake_extension import FakeExtension


def test_session_start_status_stop_roundtrip(daemon):
    code, body = daemon.exec("session_start", session_token=None)
    assert code == 200 and body["success"] is True
    token = body["sessionToken"]
    assert isinstance(token, str) and len(token) >= 32
    code, st = daemon.exec("session_status", session_token=token)
    assert code == 200 and st["success"] is True and st["tabId"] is None and st["tabClosed"] is False
    code, stop = daemon.exec("session_stop", session_token=token)
    assert code == 200 and stop["success"] is True
    code, st2 = daemon.exec("session_status", session_token=token)
    assert code == 200 and st2["code"] == "unknown_session"


def test_unknown_token_rejected_never_unpinned(daemon, fake_ext):
    n_before = len(fake_ext.received)
    code, body = daemon.exec("click", {"target": "x"}, session_token="bogus-token")
    assert code == 200 and body["code"] == "unknown_session"
    assert len(fake_ext.received) == n_before


def test_pin_inject_and_one_shot_override(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        tid = params.get("tabId", 5)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    _, start = daemon.exec("session_start")
    token = start["sessionToken"]
    _, sw = daemon.exec("tab_switch", {"tabId": 5}, session_token=token)
    assert sw["success"] is True
    _, st = daemon.exec("session_status", session_token=token)
    assert st["tabId"] == 5
    daemon.exec("click", {"target": "b"}, session_token=token)
    assert seen[-1][1].get("tabId") == 5
    daemon.exec("click", {"target": "b", "tabId": 9}, session_token=token)
    assert seen[-1][1].get("tabId") == 9
    _, st2 = daemon.exec("session_status", session_token=token)
    assert st2["tabId"] == 5
    ext.close()


def test_cli_pin_follows_switch_and_new(daemon):
    # Issue #17: tokenless calls stick to the tab picked by switch/new.
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        if action == "tab_new":
            return {"success": True, "tab": {"id": 12, "title": "t", "url": "http://t"}}
        tid = params.get("tabId", 5)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    _, sw = daemon.exec("tab_switch", {"tabId": 5})
    assert sw["success"] is True
    daemon.exec("click", {"target": "b"})
    assert seen[-1][1].get("tabId") == 5, seen
    assert daemon.status().get("pin") == 5
    daemon.exec("click", {"target": "b", "tabId": 9})
    assert seen[-1][1].get("tabId") == 9, seen
    daemon.exec("click", {"target": "b"})
    assert seen[-1][1].get("tabId") == 5, seen
    _, nw = daemon.exec("tab_new", {"url": "http://x", "background": True})
    assert nw["success"] is True
    daemon.exec("snapshot")
    assert seen[-1][1].get("tabId") == 12, seen
    ext.close()


def test_cli_pin_closed_until_switch_and_sessions_ignore_it(daemon):
    seen = []
    gone = []

    def handler(action, params):
        seen.append((action, dict(params)))
        if action == "click" and params.get("tabId") == 5 and gone:
            return {"success": False, "code": "tab_not_found", "error": "gone"}
        tid = params.get("tabId", 7)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    daemon.exec("tab_switch", {"tabId": 5})
    gone.append(True)
    _, r1 = daemon.exec("click", {"target": "b"})
    assert r1["code"] == "tab_not_found", r1
    _, r2 = daemon.exec("click", {"target": "b"})
    assert r2["code"] == "tab_closed", r2
    # A session call ignores the tokenless pin entirely.
    _, start = daemon.exec("session_start")
    token = start["sessionToken"]
    _, r3 = daemon.exec("click", {"target": "b"}, session_token=token)
    assert r3["success"] is True and "tabId" not in seen[-1][1], seen
    # switch re-pins and clears the closed state.
    daemon.exec("tab_switch", {"tabId": 7})
    _, r4 = daemon.exec("click", {"target": "b"})
    assert r4["success"] is True and seen[-1][1].get("tabId") == 7, seen
    ext.close()


def test_cli_pin_does_not_inject_into_tab_close(daemon):
    # tab_close requires an explicit tabId; tokenless pin must not be auto-injected.
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        if action == "tab_close" and not params.get("tabId"):
            return {"success": False, "code": "tab_required", "error": "tab_close requires an explicit tabId"}
        return {"success": True, "tab": {"id": params.get("tabId", 5), "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    daemon.exec("tab_switch", {"tabId": 5})
    assert daemon.status().get("pin") == 5
    _, r = daemon.exec("tab_close", {})
    assert r["success"] is False and r["code"] == "tab_required", r
    assert "tabId" not in seen[-1][1], seen
    ext.close()


def test_tokenless_legacy_unchanged(daemon):
    seen = []

    def handler(action, params):
        seen.append(dict(params))
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    daemon.exec("click", {"target": "b", "tabId": 3})
    assert seen[-1].get("tabId") == 3
    daemon.exec("click", {"target": "b"})
    assert "tabId" not in seen[-1]
    ext.close()


def test_session_token_stripped_before_forward(daemon, fake_ext):
    daemon.exec("click", {"target": "b", "sessionToken": "smuggled"})
    fwd = [m for m in fake_ext.received if m.get("action") == "click"][-1]
    assert "sessionToken" not in fwd.get("params", {})
    assert "sessionToken" not in fwd


def test_tab_closed_sticky_until_switch(daemon):
    def handler(action, params):
        if action in ("tab_switch", "switch_tab"):
            tid = params.get("tabId", 7)
            return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}
        if params.get("tabId") == 5:
            return {"success": False, "code": "tab_not_found", "error": "Tab 5 no longer exists"}
        tid = params.get("tabId", 7)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    _, start = daemon.exec("session_start")
    token = start["sessionToken"]
    daemon.exec("tab_switch", {"tabId": 5}, session_token=token)
    n_before = len(ext.received)
    code, body = daemon.exec("click", {"target": "b"}, session_token=token)
    assert body["code"] == "tab_not_found"
    _, st = daemon.exec("session_status", session_token=token)
    assert st["tabClosed"] is True
    code, body = daemon.exec("click", {"target": "b"}, session_token=token)
    assert code == 200 and body["code"] == "tab_closed"
    assert len(ext.received) == n_before + 1  # sticky call never forwarded
    _, sw = daemon.exec("tab_switch", {"tabId": 7}, session_token=token)
    assert sw["success"] is True
    _, st2 = daemon.exec("session_status", session_token=token)
    assert st2["success"] is True and st2["tabId"] == 7 and st2["tabClosed"] is False
    assert st2["name"].startswith("s-") and st2["age_s"] >= 0
    code, body = daemon.exec("click", {"target": "b"}, session_token=token)
    assert body["success"] is True
    ext.close()


def test_batch_expanded_daemon_side_with_pin(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        tid = params.get("tabId", 5)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    _, start = daemon.exec("session_start")
    token = start["sessionToken"]
    daemon.exec("tab_switch", {"tabId": 5}, session_token=token)
    steps = [
        {"action": "click", "target": "a", "sessionToken": "smuggled"},
        {"action": "sleep", "ms": 50},
        {"action": "click", "target": "b", "tabId": 9},
    ]
    code, body = daemon.exec("batch", {"steps": steps}, session_token=token, timeout=10)
    assert code == 200 and body["success"] is True and body["count"] == 3
    assert [a for a, _ in seen[-2:]] == ["click", "click"]
    assert seen[-2][1].get("tabId") == 5 and "sessionToken" not in seen[-2][1]
    assert seen[-1][1].get("tabId") == 9  # one-shot inside batch, no pin change
    _, st = daemon.exec("session_status", session_token=token)
    assert st["tabId"] == 5
    ext.close()


def test_batch_nested_params_match_flat(daemon):
    # Issue #20: {action, params} steps must forward the same params as flat steps.
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "tab": {"id": 5, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    steps = [
        {"action": "click", "params": {"ref": "e1"}},
        {"action": "click", "ref": "e1"},
        {"action": "click", "params": {"ref": "e9", "text": "inner"}, "ref": "e2"},
    ]
    code, body = daemon.exec("batch", {"steps": steps}, timeout=10)
    assert code == 200 and body["success"] is True, body
    # tabId comes from the tokenless pin after the first step (issue #17), not from the step.
    forwarded = [{k: v for k, v in prm.items() if k not in ("deadlineMs", "tabId")} for _, prm in seen]
    assert forwarded[0] == forwarded[1] == {"ref": "e1"}, forwarded
    assert forwarded[2].get("ref") == "e2" and forwarded[2].get("text") == "inner", forwarded
    ext.close()


def test_batch_first_slow_step_keeps_remaining_budget(daemon):
    # One deadline for the whole batch: a slow first step must not shrink the rest.
    def handler(action, params):
        if params.get("target") == "slow":
            time.sleep(2)
        return {"success": True, "tab": {"id": 5, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    steps = [{"action": "click", "target": "slow"}] + [{"action": "ping"}] * 3
    code, body = daemon.exec("batch", {"steps": steps}, timeout=6)
    assert code == 200 and body["success"] is True and body["count"] == 4, body
    ext.close()


def test_batch_stops_on_error_with_shape(daemon):
    def handler(action, params):
        if params.get("target") == "boom":
            return {"success": False, "code": "target_not_found", "error": "nope"}
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    steps = [{"action": "click", "target": "ok"}, {"action": "click", "target": "boom"},
             {"action": "click", "target": "never"}]
    code, body = daemon.exec("batch", {"steps": steps}, timeout=10)
    assert code == 200 and body["success"] is False and body["stoppedAt"] == "click"
    assert len(body["results"]) == 2
    ext.close()


def test_batch_rejects_nested_batch_step(daemon):
    # The extension runs a nested batch's steps without its per-tab checks, so the daemon refuses it.
    seen = []

    def handler(action, params):
        seen.append(action)
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    steps = [{"action": "batch", "steps": [{"action": "eval", "code": "1"}]}]
    code, body = daemon.exec("batch", {"steps": steps}, timeout=10)
    assert code == 200 and body["success"] is False and body["code"] == "bad_params", body
    assert body["stoppedAt"] == "batch"
    assert "batch" not in seen
    ext.close()


def test_batch_stops_when_its_time_runs_out(daemon):
    # A batch had no overall deadline: its sleeps ran outside it, and later steps were still
    # dispatched after the caller had stopped waiting.
    seen = []

    def handler(action, params):
        seen.append(action)
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    steps = [{"action": "sleep", "ms": 3000}, {"action": "click", "target": "Save"}]
    start = time.monotonic()
    code, body = daemon.exec("batch", {"steps": steps}, timeout=1)
    elapsed = time.monotonic() - start
    ext.close()
    assert code == 200 and body["success"] is False and body["code"] == "timeout", body
    assert body["stoppedAt"] == "sleep" and "did not run" in body["error"], body
    assert "click" not in seen and elapsed < 2, (seen, elapsed)


def test_named_session_start_status(daemon):
    import re
    code, start = daemon.exec("session_start", {"name": "agent-a"})
    assert code == 200 and start["success"] is True and start["name"] == "agent-a"
    _, st = daemon.exec("session_status", session_token=start["sessionToken"])
    assert st["name"] == "agent-a" and st["age_s"] >= 0
    _, anon = daemon.exec("session_start")
    assert re.fullmatch(r"s-[0-9a-f]{8}", anon["name"])


def test_bad_session_name_rejected(daemon):
    for bad in ["has space", "bang!", "", "x" * 41, "semi;colon", "uni–dash", 123]:
        code, body = daemon.exec("session_start", {"name": bad})
        assert code == 200 and body["code"] == "bad_name", bad


def test_session_list_hides_tokens(daemon):
    import json
    _, a = daemon.exec("session_start", {"name": "first"})
    _, b = daemon.exec("session_start", {"name": "second"})
    code, body = daemon.exec("session_list")
    assert code == 200 and body["success"] is True
    assert [s["name"] for s in body["sessions"]] == ["first", "second"]
    assert "sessionToken" not in json.dumps(body)
    assert a["sessionToken"] not in json.dumps(body) and b["sessionToken"] not in json.dumps(body)


def test_session_prune_drops_closed_only(daemon):
    def handler(action, params):
        if action in ("tab_switch", "switch_tab"):
            tid = params.get("tabId", 7)
            return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}
        if params.get("tabId") == 5:
            return {"success": False, "code": "tab_not_found", "error": "gone"}
        tid = params.get("tabId", 7)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    _, doomed = daemon.exec("session_start", {"name": "doomed"})
    _, kept = daemon.exec("session_start", {"name": "kept"})
    daemon.exec("tab_switch", {"tabId": 5}, session_token=doomed["sessionToken"])
    daemon.exec("tab_switch", {"tabId": 7}, session_token=kept["sessionToken"])
    daemon.exec("click", {"target": "b"}, session_token=doomed["sessionToken"])
    _, st = daemon.exec("session_status", session_token=doomed["sessionToken"])
    assert st["tabClosed"] is True
    code, body = daemon.exec("session_prune")
    assert code == 200 and body == {"success": True, "dropped": 1}
    _, listed = daemon.exec("session_list")
    assert [s["name"] for s in listed["sessions"]] == ["kept"]
    _, gone = daemon.exec("session_status", session_token=doomed["sessionToken"])
    assert gone["code"] == "unknown_session"
    ext.close()


def test_status_reports_data_dir(daemon):
    st = daemon.status()
    assert st["data_dir"] == str(daemon.home)


def test_deadline_ms_forwarded_for_tab_scoped_only(daemon, fake_ext):
    import time as _t
    before = int(_t.time() * 1000)
    daemon.exec("click", {"target": "b", "tabId": 3}, timeout=5)
    daemon.exec("ping")
    fwd_click = [m for m in fake_ext.received if m.get("action") == "click"][-1]
    fwd_ping = [m for m in fake_ext.received if m.get("action") == "ping"][-1]
    assert "deadlineMs" in fwd_click["params"]
    assert before + 3500 < fwd_click["params"]["deadlineMs"] < before + 5500
    assert "deadlineMs" not in fwd_ping["params"]

    before10 = int(_t.time() * 1000)
    daemon.exec("click", {"target": "b", "tabId": 3}, timeout=10)
    fwd_click10 = [m for m in fake_ext.received if m.get("action") == "click"][-1]
    assert "deadlineMs" in fwd_click10["params"]
    assert before10 + 8500 < fwd_click10["params"]["deadlineMs"] < before10 + 10500


# --- Session idle expiry (audit finding 12, daemon half) ---

def _expiry_state(tmp_path, ttl=100.0):
    from agent_browser_bridge.bridge import BridgeState
    return BridgeState(port=0, home=tmp_path, token="t", session_idle_ttl_sec=ttl)


def _expiry_session(state, name="idle"):
    with state.lock:
        return state.session_create_locked(name)


def _backdate(state, token, when):
    with state.lock:
        state.sessions[token]["lastActivity"] = when


def test_idle_session_expired_on_next_prune(tmp_path):
    state = _expiry_state(tmp_path)
    token = _expiry_session(state)
    _backdate(state, token, 1_000.0)
    with state.lock:
        dropped = state.session_prune_locked(now=1_101.0)
    assert dropped == 1
    with state.lock:
        assert token not in state.sessions


def test_idle_session_expired_on_next_list(tmp_path):
    state = _expiry_state(tmp_path)
    old = _expiry_session(state, "old")
    fresh = _expiry_session(state, "fresh")
    _backdate(state, old, 1_000.0)
    _backdate(state, fresh, 1_050.0)
    with state.lock:
        items = state.session_list_locked(now=1_101.0)
    assert [s["name"] for s in items] == ["fresh"]
    with state.lock:
        assert old not in state.sessions and fresh in state.sessions


def test_session_with_recent_activity_survives_expiry(tmp_path):
    state = _expiry_state(tmp_path)
    token = _expiry_session(state)
    _backdate(state, token, 1_000.0)
    with state.lock:
        dropped = state.session_prune_locked(now=1_099.0)
        items = state.session_list_locked(now=1_099.0)
    assert dropped == 0 and [s["name"] for s in items] == ["idle"]
    with state.lock:
        assert token in state.sessions


def test_dispatch_refreshes_activity_and_extends_life(tmp_path):
    import json as _json
    from agent_browser_bridge.bridge import BridgeRequestHandler

    state = _expiry_state(tmp_path, ttl=100.0)
    token = _expiry_session(state)
    _backdate(state, token, time.time() - 90)

    class ImmediateExtension:
        closed = False

        def send_text(self, text):
            cmd = _json.loads(text)
            state.results[cmd["id"]] = {"success": True,
                                         "tab": {"id": 5, "title": "t", "url": "http://t"}}
            state.pending[cmd["id"]].set()
            return True

    state.ext, state.ext_accepted = ImmediateExtension(), True
    handler = object.__new__(BridgeRequestHandler)
    res, code = handler._dispatch_guarded(state, "click", {"target": "b"},
                                          time.time() + 5, True, None, session_token=token)
    assert code == 200 and res["success"] is True
    # Without the dispatch refresh the session would be 140 s idle at this clock.
    with state.lock:
        dropped = state.session_prune_locked(now=time.time() + 50)
        assert token in state.sessions
    assert dropped == 0


def test_session_idle_ttl_env_override(monkeypatch):
    from agent_browser_bridge import config
    assert config.DEFAULT_SESSION_IDLE_TTL_SEC == 24 * 60 * 60
    monkeypatch.delenv("EDGE_BRIDGE_SESSION_IDLE_TTL_S", raising=False)
    assert config.session_idle_ttl_seconds() == 24 * 60 * 60
    monkeypatch.setenv("EDGE_BRIDGE_SESSION_IDLE_TTL_S", "42")
    assert config.session_idle_ttl_seconds() == 42.0
    for bad in ("not-a-number", "0", "-5", ""):
        monkeypatch.setenv("EDGE_BRIDGE_SESSION_IDLE_TTL_S", bad)
        assert config.session_idle_ttl_seconds() == 24 * 60 * 60, bad


def test_inflight_session_is_not_expired(tmp_path):
    import json as _json
    import threading as _threading
    from agent_browser_bridge.bridge import BridgeRequestHandler

    state = _expiry_state(tmp_path, ttl=100.0)
    token = _expiry_session(state)
    started, release = _threading.Event(), _threading.Event()

    class BlockingExtension:
        closed = False

        def send_text(self, text):
            cmd = _json.loads(text)
            event = state.pending[cmd["id"]]
            started.set()

            def respond():
                release.wait(timeout=10)
                state.results[cmd["id"]] = {"success": True}
                event.set()

            _threading.Thread(target=respond, daemon=True).start()
            return True

    state.ext, state.ext_accepted = BlockingExtension(), True
    handler = object.__new__(BridgeRequestHandler)
    outcome = {}
    thread = _threading.Thread(
        target=lambda: outcome.update(
            r=handler._dispatch_guarded(state, "click", {"target": "b"},
                                        time.time() + 10, True, None, session_token=token)))
    thread.start()
    try:
        assert started.wait(timeout=5)
        _backdate(state, token, 1_000.0)
        with state.lock:
            dropped = state.session_prune_locked(now=10_000.0)
        assert dropped == 0
        with state.lock:
            assert token in state.sessions
    finally:
        release.set()
        thread.join(timeout=10)
    assert outcome["r"][1] == 200 and outcome["r"][0]["success"] is True
    # Once nothing is in flight, the same idle session expires.
    with state.lock:
        dropped = state.session_prune_locked(now=10_000.0)
    assert dropped == 1
    with state.lock:
        assert token not in state.sessions


def test_idle_expiry_in_running_daemon(tmp_path):
    from tests.support import DaemonHandle, free_port
    handle = DaemonHandle(free_port(), tmp_path).start(
        extra_env={"EDGE_BRIDGE_SESSION_IDLE_TTL_S": "0.2"})
    try:
        _, start = handle.exec("session_start", {"name": "short-lived"})
        assert start["success"] is True
        deadline = time.time() + 5
        listed = {"sessions": ["pending"]}
        while time.time() < deadline and listed["sessions"]:
            _, listed = handle.exec("session_list")
            if listed["sessions"]:
                time.sleep(0.05)
        assert listed["sessions"] == []
        _, st = handle.exec("session_status", session_token=start["sessionToken"])
        assert st["code"] == "unknown_session"
    finally:
        handle.stop()
