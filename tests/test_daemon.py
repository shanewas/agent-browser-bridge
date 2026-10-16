import http.client
import json
import struct
import threading
import time
import pytest
from tests.fake_extension import FakeExtension
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


def test_status_reports_running(daemon):
    st = daemon.status()
    assert st["bridge_running"] is True


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
    assert st["extension_version"] == "2.0.0" and st["extension_outdated"] is False and st["websocket_active"] is True


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


def test_inflight_timeout_and_late_result_dropped(daemon):
    ext = FakeExtension(daemon.port).connect().run()
    assert _wait_active(daemon)
    code, body = daemon.exec("sleep", {"ms": 2500}, timeout=1)
    assert code == 504 and body["code"] == "timeout"
    time.sleep(2)                       # late result arrives, nobody waits
    assert daemon.status()["pending"] == 0
    ext.close()


def test_extension_outdated_rewrite(daemon):
    ext = FakeExtension(daemon.port, send_hello=False).connect().run()   # behaves like 1.x
    time.sleep(1.2)
    code, body = daemon.exec("snapshot", timeout=3)
    assert code == 200 and body["code"] == "extension_outdated" and "2.0.0" in body["error"]
    assert daemon.status()["extension_outdated"] is True
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

