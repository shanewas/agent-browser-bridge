import http.client
import json
import struct
import threading
import time
import pytest
from tests.fake_extension import FakeExtension
from agent_browser_bridge import config
from tests.support import DaemonHandle, free_port


def _wait_active(daemon, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if daemon.status().get("websocket_active"):
            return True
        time.sleep(0.05)
    return False


def _wait_closed(ext, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline and ext.closed_with is None:
        time.sleep(0.05)
    return ext.closed_with


def test_result_arriving_after_timeout_is_dropped(tmp_path):
    # The result lands in state.results after the wait has already timed out: the daemon must
    # answer timeout and pop it, or every late result stays in memory for the daemon's lifetime.
    from agent_browser_bridge.bridge import BridgeRequestHandler, BridgeState

    state = BridgeState(port=0, home=tmp_path, token="t")

    class LateExtension:
        closed = False

        def send_text(self, text):
            state.results[json.loads(text)["id"]] = {"success": True}
            return True

    state.ext, state.ext_accepted = LateExtension(), True
    res, code = BridgeRequestHandler._dispatch_once(None, state, "ping", {}, time.time(), False)
    assert (code, res["code"]) == (504, "timeout")
    assert state.results == {} and state.pending == {}


def test_malformed_extension_results_are_dropped(tmp_path):
    # A scalar result or a non-string id must not complete (or crash) a pending request.
    import types

    from agent_browser_bridge.bridge import BridgeRequestHandler, BridgeState

    state = BridgeState(port=0, home=tmp_path, token="t")

    class Ws:
        closed = False
        grace_timer = None

    ws = Ws()
    state.ext, state.ext_accepted = ws, True
    event = threading.Event()
    state.pending["cmd1"] = event
    handler = object.__new__(BridgeRequestHandler)
    handler.server = types.SimpleNamespace(state=state)
    handler._handle_ws_json({"id": "cmd1", "result": "scalar"}, ws)
    handler._handle_ws_json({"id": ["cmd1"], "result": {"success": True}}, ws)
    handler._handle_ws_json({"id": 123, "result": {"success": True}}, ws)
    assert not event.is_set()
    assert state.pending == {"cmd1": event} and state.results == {}


def test_bad_tab_id_in_result_does_not_crash_pin_maintenance(tmp_path):
    from agent_browser_bridge.bridge import BridgeRequestHandler, BridgeState

    state = BridgeState(port=0, home=tmp_path, token="t")
    res = {"success": True, "tab": {"id": "x", "title": "t", "url": "http://t"}}
    BridgeRequestHandler._after_result(None, state, None, None, "tab_switch", {}, None, res)
    assert state.cli_pin is None
    token = state.session_create_locked("s")
    BridgeRequestHandler._after_result(None, state, token, state.sessions[token],
                                       "tab_switch", {}, None, res)
    assert state.sessions[token]["tabId"] is None


def test_status_reports_running(daemon):
    st = daemon.status()
    assert st["bridge_running"] is True
    assert st["pid"] == int((daemon.home / "bridge.pid").read_text(encoding="utf-8"))


def test_second_daemon_on_busy_port_exits_1(daemon):
    # Windows SO_REUSEADDR let a second daemon bind the live port and overwrite bridge.pid (#5).
    import os
    import subprocess
    import sys
    from tests.support import ROOT
    first_pid = (daemon.home / "bridge.pid").read_text(encoding="utf-8")
    env = dict(os.environ, EDGE_BRIDGE_HOME=str(daemon.home), EDGE_BRIDGE_PORT=str(daemon.port))
    res = subprocess.run([sys.executable, "-u", "-m", "agent_browser_bridge.bridge", "--port", str(daemon.port)],
                         cwd=ROOT, env=env, capture_output=True, timeout=30)
    assert res.returncode == 1
    assert "Daemon crashed:" in (daemon.home / "bridge.log").read_text(encoding="utf-8")
    assert (daemon.home / "bridge.pid").read_text(encoding="utf-8") == first_pid
    assert daemon.status()["bridge_running"] is True


def test_ping_roundtrip_via_fake_extension(daemon, fake_ext):
    code, body = daemon.exec("ping")
    assert code == 200 and body["success"] is True and body["message"] == "pong"


def test_exec_requires_token(daemon, fake_ext):
    assert daemon.exec("ping", token=None)[0] == 401
    code, body = daemon.exec("ping", token="0" * 64)
    assert code == 401 and body["code"] == "bad_token"


def test_exec_rejects_browser_context(daemon, fake_ext):
    code, body = daemon.exec("ping", headers={"Origin": "http://evil.example"})
    assert code == 403 and body["code"] == "browser_context"
    code, body = daemon.exec("ping", headers={"Sec-Fetch-Mode": "cors"})
    assert code == 403 and body["code"] == "browser_context"


def test_exec_malformed_bodies_get_json_errors(daemon, fake_ext):
    # Non-ASCII token header must answer 401, not drop the connection (issue #29).
    code, body = daemon.exec("ping", token="t\u00f6k\u00e9n")
    assert code == 401 and body["code"] == "bad_token", (code, body)
    # A non-string action must answer 400 bad_body.
    code, body = daemon._request("POST", "/exec", {"action": {}, "params": {}},
                                 headers={config.TOKEN_HEADER: daemon.token})
    assert code == 400 and body["code"] == "bad_body", (code, body)
    # A batch whose steps is not a list must answer 400 bad_body.
    code, body = daemon.exec("batch", {"steps": 5})
    assert code == 400 and body["code"] == "bad_body", (code, body)
    # The daemon still answers the next valid request.
    code, body = daemon.exec("ping")
    assert code == 200 and body["success"] is True, (code, body)


def test_batch_step_without_a_string_action_is_bad_params(daemon, fake_ext):
    # A missing or non-string step action crashed the handler instead of answering an error.
    for steps in ([{}], [{"action": ["ping"]}], [{"action": "ping"}, {"action": 5}]):
        code, body = daemon.exec("batch", {"steps": steps})
        assert code == 200 and body["success"] is False and body["code"] == "bad_params", (steps, code, body)
        assert "stoppedAt" in body and len(body["results"]) == len(steps), (steps, body)
    code, body = daemon.exec("ping")
    assert code == 200 and body["success"] is True, (code, body)


def test_batch_non_object_step_is_bad_params(daemon, fake_ext):
    for steps, result_count in (([42], 1), ([{"action": "ping"}, 42, {"action": "ping"}], 2)):
        code, body = daemon.exec("batch", {"steps": steps})
        assert code == 200 and body["success"] is False and body["code"] == "bad_params", (steps, code, body)
        assert "stoppedAt" in body and len(body["results"]) == result_count, (steps, body)
        assert body["results"][-1]["success"] is False and body["results"][-1]["code"] == "bad_params", body
    code, body = daemon.exec("ping")
    assert code == 200 and body["success"] is True, (code, body)


def test_pairing_flags_are_mutually_exclusive(monkeypatch):
    from agent_browser_bridge import bridge

    monkeypatch.setattr(bridge, "run_server", lambda **kwargs: None)
    monkeypatch.setattr(bridge.config, "read_config", lambda: {})
    writes = []
    monkeypatch.setattr(bridge.config, "write_config", lambda cfg: writes.append(dict(cfg)))
    with pytest.raises(SystemExit) as exc:
        bridge.main(["--require-pairing", "--no-pairing"])
    assert exc.value.code == 2
    assert bridge.main(["--require-pairing"]) == 0 and writes[-1]["pairing"] == "1"
    assert bridge.main(["--no-pairing"]) == 0 and writes[-1]["pairing"] == "0"


def test_bad_host_rejected(daemon):
    code, body = daemon._request("GET", "/status", headers={"Host": "evil.example:80"})
    assert code == 421 and body["code"] == "bad_host"


def test_ws_requires_extension_origin(daemon):
    with pytest.raises(ConnectionError):
        FakeExtension(daemon.port, origin=None).connect()
    with pytest.raises(ConnectionError):
        FakeExtension(daemon.port, origin="https://page.example").connect()


def test_status_fields(daemon, fake_ext):
    st = daemon.status()
    for k in ("bridge_running", "daemon_version", "extension_version", "extension_outdated",
              "extension_connected", "websocket_active", "last_seen_seconds_ago", "pending", "pairing_required"):
        assert k in st
    from agent_browser_bridge import __version__
    assert st["extension_version"] == __version__ and st["extension_outdated"] is False and st["websocket_active"] is True


def test_poll_and_result_gone(daemon):
    assert daemon._request("GET", "/poll?client=sw")[0] == 404
    assert daemon._request("POST", "/result", {"id": "x"})[0] == 404


def test_unauthenticated_api_routes_gone(daemon, fake_ext):
    # /api/eval reached the extension with no token at all, and /api/tabs and /api/status
    # answered unauthenticated. Only /exec, /status and /ws are routes.
    assert daemon._request("POST", "/api/eval", {"code": "1+1"})[0] == 404
    assert daemon._request("GET", "/api/tabs")[0] == 404
    assert daemon._request("GET", "/api/status")[0] == 404
    assert not any(m.get("action") == "eval" for m in fake_ext.received)


def test_no_cors_headers(daemon):
    c = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=5)
    c.request("GET", "/status")
    r = c.getresponse()
    r.read()
    c.close()
    assert r.getheader("Access-Control-Allow-Origin") is None


def test_hello_records_version_and_pong_answers_ping(daemon):
    ext = FakeExtension(daemon.port, version="2.3.4").connect().run()
    time.sleep(0.3)
    assert daemon.status()["extension_version"] == "2.3.4"
    ext.close()


def test_heartbeat_ping_sent(daemon, fake_ext, monkeypatch):
    time.sleep(2.5)
    assert any(m.get("ping") for m in fake_ext.received)


def test_no_pong_closes_socket(daemon):
    ext = FakeExtension(daemon.port).connect()          # not run(): never answers pings
    ext.sock.settimeout(8)
    deadline = time.time() + 8
    closed = False
    while time.time() < deadline:
        op, payload = ext._read_frame()
        if op == 8 or op is None:
            closed = True
            break
    assert closed
    assert daemon.status()["websocket_active"] is False


def test_oversized_frame_closes_1009(daemon):
    ext = FakeExtension(daemon.port).connect()
    hdr = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", 17 * 1024 * 1024) + b"\x00\x00\x00\x00"
    ext.sock.sendall(hdr)
    ext.sock.settimeout(5)
    op, payload = ext._read_frame()
    assert op == 8 and struct.unpack("!H", payload[:2])[0] == 1009


def test_offline_fails_fast_then_succeeds_after_connect(daemon):
    start = time.time()
    code, body = daemon.exec("ping", timeout=6)
    assert code == 504 and body["code"] == "extension_offline"
    assert time.time() - start < 2.0
    ext = FakeExtension(daemon.port).connect().run()
    assert _wait_active(daemon)
    code, body = daemon.exec("ping", timeout=6)
    assert code == 200 and body["success"] is True
    ext.close()


def test_offline_command_times_out_with_extension_offline(daemon):
    code, body = daemon.exec("ping", timeout=1)
    assert code == 504 and body["code"] == "extension_offline"
    assert daemon.status()["pending"] == 0


def test_unaccepted_socket_fails_fast_until_grace_passes(daemon):
    ext = FakeExtension(daemon.port, send_hello=False, version="1.1.2").connect().run()
    code, body = daemon.exec("ping", timeout=6)
    assert code == 504 and body["code"] == "extension_offline"
    time.sleep(1.2)   # pairing-off grace timer accepts the socket
    code, body = daemon.exec("ping", timeout=6)
    assert code == 200 and body["success"] is True
    ext.close()


def test_grace_candidate_does_not_displace_live_extension(daemon):
    real = FakeExtension(daemon.port, version="2.5.0").connect().run()
    assert _wait_active(daemon)
    lurker = FakeExtension(daemon.port, send_hello=False).connect().run()
    time.sleep(1.5)   # longer than the pairing-off grace window
    status = daemon.status()
    assert status["websocket_active"] is True
    assert status["extension_version"] == "2.5.0"
    assert real.closed_with is None
    code, body = daemon.exec("ping", timeout=3)
    assert code == 200 and body["success"] is True and body["version"] == "2.5.0"
    assert not any("action" in m for m in lurker.received)
    lurker.close()
    real.close()


def test_grace_accepts_when_no_extension_connected(daemon):
    ext = FakeExtension(daemon.port, send_hello=False).connect().run()
    time.sleep(1.2)   # pairing-off grace timer accepts the socket
    assert daemon.status()["websocket_active"] is True
    code, body = daemon.exec("ping", timeout=3)
    assert code == 200 and body["success"] is True
    ext.close()


def test_prefixed_routes_are_not_real_routes(daemon):
    assert daemon._request("GET", "/status")[0] == 200
    assert daemon._request("GET", "/status?verbose=1")[0] == 200
    for method, path in (("GET", "/statusx"), ("GET", "/wsx"), ("GET", "/status-anything")):
        code, body = daemon._request(method, path)
        assert code == 404 and body["code"] == "not_found", (path, code, body)
    code, body = daemon._request("POST", "/execx", {"action": "ping", "params": {}},
                                 headers={config.TOKEN_HEADER: daemon.token})
    assert code == 404 and body["code"] == "not_found", (code, body)
    code, body = daemon._request("POST", "/exec-anything", {"action": "ping", "params": {}},
                                 headers={config.TOKEN_HEADER: daemon.token})
    assert code == 404 and body["code"] == "not_found", (code, body)


def test_inflight_timeout_and_late_result_dropped(daemon):
    ext = FakeExtension(daemon.port).connect().run()
    assert _wait_active(daemon)
    code, body = daemon.exec("sleep", {"ms": 2500}, timeout=1)
    assert code == 504 and body["code"] == "timeout"
    time.sleep(2)                       # late result arrives, nobody waits
    assert daemon.status()["pending"] == 0
    ext.close()


def test_pending_command_fails_fast_when_extension_drops(daemon):
    # Issue #23: a dropped socket must fail in-flight commands at once.
    import threading as _t

    def handler(action, params):
        time.sleep(5)
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    assert _wait_active(daemon)
    outcome = {}

    def call():
        outcome["r"] = daemon.exec("click", {"target": "x"}, timeout=20)

    th = _t.Thread(target=call)
    th.start()
    time.sleep(0.5)
    ext.close()
    th.join(timeout=8)
    assert not th.is_alive(), "caller still waiting after the socket dropped"
    code, body = outcome["r"]
    assert body["code"] == "extension_offline", (code, body)
    assert daemon.status()["pending"] == 0


def test_pending_command_fails_when_extension_is_replaced(daemon):
    # A command in flight on the replaced socket can never be answered; it must
    # fail extension_offline at replacement time, not at its own timeout.
    def handler(action, params):
        time.sleep(5)
        return {"success": True}

    old = FakeExtension(daemon.port, handler=handler).connect().run()
    assert _wait_active(daemon)
    outcome = {}

    def call():
        outcome["r"] = daemon.exec("click", {"target": "x"}, timeout=20)

    th = threading.Thread(target=call)
    th.start()
    deadline = time.time() + 3
    while time.time() < deadline and not any("action" in m for m in old.received):
        time.sleep(0.02)
    new = FakeExtension(daemon.port).connect().run()
    th.join(timeout=8)
    assert not th.is_alive(), "caller still waiting after its socket was replaced"
    code, body = outcome["r"]
    assert body["code"] == "extension_offline", (code, body)
    assert daemon.status()["pending"] == 0
    new.close()
    old.close()


def test_extension_outdated_rewrite(daemon):
    ext = FakeExtension(daemon.port, send_hello=False).connect().run()   # behaves like 1.x
    time.sleep(1.2)
    code, body = daemon.exec("snapshot", timeout=3)
    assert code == 200 and body["code"] == "extension_outdated" and "2.0.0" in body["error"]
    assert daemon.status()["extension_outdated"] is True
    ext.close()


def test_extension_outdated_rewrite_for_older_v2_extension(daemon):
    def handler(action, params):
        return {"success": False, "code": "unknown_action", "error": "Unknown action: future_cmd"}

    ext = FakeExtension(daemon.port, version="2.4.0", handler=handler).connect().run()
    time.sleep(1.2)
    code, body = daemon.exec("future_cmd", timeout=3)
    assert code == 200 and body["code"] == "extension_outdated"
    assert "2.4.0" in body["error"]
    assert daemon.status()["extension_outdated"] is True
    ext.close()


def test_extension_outdated_clears_when_a_current_extension_connects(daemon):
    # The flag stayed set after the user updated the extension, until the daemon restarted.
    old = FakeExtension(daemon.port, send_hello=False).connect().run()
    time.sleep(1.2)
    daemon.exec("snapshot", timeout=3)
    assert daemon.status()["extension_outdated"] is True
    old.close()
    ext = FakeExtension(daemon.port).connect().run()
    deadline = time.time() + 5
    while daemon.status()["extension_version"] != ext.version and time.time() < deadline:
        time.sleep(0.05)
    status = daemon.status()
    assert status["extension_version"] == ext.version and status["extension_outdated"] is False, status
    ext.close()


@pytest.fixture
def paired_daemon(tmp_path):
    (tmp_path / "config").write_text("pairing=1\n", encoding="utf-8")
    handle = DaemonHandle(free_port(), tmp_path).start()
    assert handle.status()["pairing_required"] is True
    yield handle
    handle.stop()


def test_bad_pairing_token_does_not_disconnect_paired_extension(paired_daemon):
    real = FakeExtension(paired_daemon.port, version="2.5.0", token=paired_daemon.token).connect().run()
    assert _wait_active(paired_daemon)

    intruder = FakeExtension(paired_daemon.port, version="9.9.9", token="0" * 64).connect().run()
    assert _wait_closed(intruder) == 4001

    status = paired_daemon.status()
    assert status["websocket_active"] is True
    assert status["extension_version"] == "2.5.0"
    assert real.closed_with is None
    code, body = paired_daemon.exec("ping", timeout=3)
    assert code == 200 and body["success"] is True and body["version"] == "2.5.0"
    intruder.close()
    real.close()


def test_unpaired_socket_without_hello_does_not_displace_paired_extension(paired_daemon):
    real = FakeExtension(paired_daemon.port, token=paired_daemon.token).connect().run()
    assert _wait_active(paired_daemon)
    lurker = FakeExtension(paired_daemon.port, send_hello=False).connect().run()
    time.sleep(1.5)                     # longer than the pairing-off grace window
    assert paired_daemon.status()["websocket_active"] is True
    code, body = paired_daemon.exec("ping", timeout=3)
    assert code == 200 and body["success"] is True
    assert not any("action" in m for m in lurker.received)
    lurker.close()
    real.close()


def test_results_from_unaccepted_socket_are_ignored(paired_daemon):
    release = threading.Event()

    def slow(action, params):
        release.wait(5)
        return {"success": True, "from": "real"}

    real = FakeExtension(paired_daemon.port, token=paired_daemon.token, handler=slow).connect().run()
    assert _wait_active(paired_daemon)
    lurker = FakeExtension(paired_daemon.port, send_hello=False).connect()

    out = {}
    t = threading.Thread(target=lambda: out.update(r=paired_daemon.exec("ping", timeout=5)))
    t.start()
    deadline = time.time() + 3
    while time.time() < deadline and not any("action" in m for m in real.received):
        time.sleep(0.02)
    cmd = next(m for m in real.received if "action" in m)
    lurker.send_text(json.dumps({"id": cmd["id"], "result": {"success": True, "from": "lurker"}}))
    time.sleep(0.3)
    release.set()
    t.join(timeout=10)
    code, body = out["r"]
    assert code == 200 and body["from"] == "real"
    lurker.close()
    real.close()


def test_new_hello_replaces_stale_connection_without_pairing(daemon):
    old = FakeExtension(daemon.port, version="2.0.0").connect().run()
    assert _wait_active(daemon)
    new = FakeExtension(daemon.port, version="2.0.1").connect().run()
    deadline = time.time() + 3
    while time.time() < deadline and daemon.status()["extension_version"] != "2.0.1":
        time.sleep(0.05)
    assert daemon.status()["extension_version"] == "2.0.1"
    assert _wait_closed(old) is not None
    code, body = daemon.exec("ping", timeout=3)
    assert code == 200 and body["version"] == "2.0.1"
    new.close()
    old.close()


def test_different_instance_rejected_with_4002(daemon):
    ext1 = FakeExtension(daemon.port, instance="inst-1").connect().run()
    assert _wait_active(daemon)
    st = daemon.status()
    assert st.get("rejected_instance") is None
    assert st["extension_instance"] == "inst-1" and "instance" not in st

    ext2 = FakeExtension(daemon.port, instance="inst-2").connect().run()
    assert _wait_closed(ext2) == 4002
    assert daemon.status()["websocket_active"] is True
    st2 = daemon.status()
    assert st2.get("rejected_instance") == "inst-2"

    # First extension remains connected and functional
    code, body = daemon.exec("ping", timeout=3)
    assert code == 200 and body["success"] is True
    ext1.close()
    ext2.close()


def test_same_instance_replaces_old_socket(daemon):
    old = FakeExtension(daemon.port, version="2.0.0", instance="inst-1").connect().run()
    assert _wait_active(daemon)
    new = FakeExtension(daemon.port, version="2.0.1", instance="inst-1").connect().run()
    deadline = time.time() + 3
    while time.time() < deadline and daemon.status()["extension_version"] != "2.0.1":
        time.sleep(0.05)
    assert daemon.status()["extension_version"] == "2.0.1"
    assert _wait_closed(old) is not None
    code, body = daemon.exec("ping", timeout=3)
    assert code == 200 and body["version"] == "2.0.1"
    new.close()
    old.close()



def _desktop_state(tmp_path):
    pytest.importorskip("PIL")
    from agent_browser_bridge.bridge import BridgeState
    from agent_browser_bridge.desktop import Desktop
    from tests.desktop_fakes import FakeKeyboard, FakeScreen, three_monitors
    state = BridgeState(port=0, home=tmp_path, token="t")
    keyboard = FakeKeyboard()
    state.desktop = Desktop(screen=FakeScreen(layout=three_monitors()), inputs=keyboard)
    return state, keyboard


def test_desktop_actions_run_in_the_daemon_not_the_extension(tmp_path, monkeypatch):
    from agent_browser_bridge.bridge import BridgeRequestHandler
    state, keyboard = _desktop_state(tmp_path)
    from agent_browser_bridge.desktop import executor
    monkeypatch.setattr(executor, "SETTLE_SECONDS", 0)
    res, code = BridgeRequestHandler._dispatch_once(None, state, "desktop_click", {"at": [640, 360]},
                                                    time.time() + 5, False)
    assert code == 200 and res["success"] and ("move", 960, 540) in keyboard.calls
    assert state.pending == {}


def test_desktop_busy_when_the_lock_is_held(tmp_path):
    from agent_browser_bridge.bridge import BridgeRequestHandler
    state, _ = _desktop_state(tmp_path)
    state.desktop_lock.acquire()
    try:
        res, code = BridgeRequestHandler._dispatch_once(None, state, "desktop_screen", {}, time.time() + 0.2, False)
    finally:
        state.desktop_lock.release()
    assert (code, res["code"]) == (200, "desktop_busy")


def test_desktop_actions_are_not_tab_scoped():
    from agent_browser_bridge.bridge import _is_tab_scoped
    assert _is_tab_scoped("click") and not _is_tab_scoped("desktop_click") and not _is_tab_scoped("ping")


def test_desktop_screen_through_the_real_daemon(daemon):
    code, body = daemon.exec("desktop_screen")
    assert code == 200
    if body["success"]:
        assert isinstance(body["monitors"], list) and body["space"] == "view"
    else:
        assert body["code"] in {"desktop_unsupported", "desktop_unavailable", "desktop_error"}, body


def test_desktop_action_needs_the_token(daemon):
    assert daemon.exec("desktop_click", {"at": [1, 1]}, token=None)[0] == 401


def test_a_desktop_step_in_a_batch_runs_in_the_daemon(daemon, fake_ext):
    code, body = daemon.exec("batch", {"steps": [{"action": "desktop_wait", "seconds": 0}, {"action": "ping"}]})
    assert code == 200
    first = body["results"][0]
    if first.get("success"):
        assert first["action"] == "wait" and body["results"][1]["message"] == "pong"
    else:
        assert first["code"].startswith("desktop_") and body["stoppedAt"] == "desktop_wait"


def test_unknown_action_from_a_supported_extension_is_not_outdated(daemon):
    # Daemon 4.0.0 with the 3.0.0 store extension: the floor is MIN_EXTENSION_VERSION, not the daemon version.
    def handler(action, params):
        return {"success": False, "code": "unknown_action", "error": "Unknown action: future_cmd"}

    ext = FakeExtension(daemon.port, version="3.0.0", handler=handler).connect().run()
    time.sleep(1.2)
    code, body = daemon.exec("future_cmd", timeout=3)
    assert code == 200 and body["code"] == "unknown_action", body
    assert daemon.status()["extension_outdated"] is False
    ext.close()


def test_a_desktop_wait_past_the_call_deadline_is_refused(tmp_path):
    from agent_browser_bridge.bridge import BridgeRequestHandler
    state, keyboard = _desktop_state(tmp_path)
    res, code = BridgeRequestHandler._dispatch_once(None, state, "desktop_wait", {"seconds": 5}, time.time() + 1, False)
    assert (code, res["code"]) == (200, "timeout") and keyboard.calls == []
