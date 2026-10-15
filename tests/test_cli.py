"""Tests for edge_agent_bridge.cli."""
import io
import json
from pathlib import Path
from edge_agent_bridge import cli, config
from edge_agent_bridge.client import Edge
from tests.fake_extension import FakeExtension
from tests.support import free_port, run_cli


def test_cli_json_ping_exits_0(daemon, fake_ext):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "ping"], env=env)
    assert code == 0
    data = json.loads(out)
    assert data["success"] is True
    assert data["message"] == "pong"


def test_cli_json_tab_not_found_exits_1(daemon):
    def handler(action, params):
        return {"success": False, "code": "tab_not_found", "error": "Tab 999 no longer exists"}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "tab", "--tab", "999"], env=env)
    assert code == 1
    data = json.loads(out)
    assert data["success"] is False
    assert data["code"] == "tab_not_found"
    ext.close()


def test_cli_unreachable_port_exits_2(monkeypatch, tmp_path):
    port = free_port()
    env = {"EDGE_BRIDGE_HOME": str(tmp_path), "EDGE_BRIDGE_PORT": str(port)}
    code, out, err = run_cli(["--json", "ping"], env=env)
    assert code == 2
    data = json.loads(out)
    assert data["success"] is False
    assert data["code"] in ("daemon_unreachable", "missing_token")


def test_cli_close_without_tab_exits_3(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["close"], env=env)
    assert code == 3


def test_cli_status_human_contains_extension_and_version(daemon, fake_ext):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["status"], env=env)
    assert code == 0
    assert "Extension:" in out
    assert fake_ext.version in out


def test_cli_screenshot_writes_the_file(daemon, fake_ext, tmp_path):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    out_file = tmp_path / "shot.jpg"
    code, out, err = run_cli(["--json", "screenshot", str(out_file)], env=env)
    assert code == 0, err
    data = json.loads(out)
    assert data["path"] == str(out_file.resolve())
    assert out_file.exists() and out_file.stat().st_size > 0


def test_cli_session_start_status_stop(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "session", "start"], env=env)
    assert code == 0
    token = json.loads(out)["sessionToken"]
    code, out, err = run_cli(["--json", "session", "status", "--session", token], env=env)
    assert code == 0 and json.loads(out)["tabClosed"] is False
    code, out, err = run_cli(["--json", "session", "stop", "--session", token], env=env)
    assert code == 0 and json.loads(out)["success"] is True
    code, out, err = run_cli(["--json", "session", "status", "--session", token], env=env)
    assert code == 1 and json.loads(out)["code"] == "unknown_session"


def test_cli_session_env_resolution_and_pin_routing(daemon):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        tid = params.get("tabId", 5)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    _, out, _ = run_cli(["--json", "session", "start"], env=env)
    token = json.loads(out)["sessionToken"]
    env["EDGE_BRIDGE_SESSION"] = token
    code, out, err = run_cli(["--json", "switch", "5"], env=env)
    assert code == 0
    code, out, err = run_cli(["--json", "click", "Save"], env=env)
    assert code == 0
    clicks = [p for a, p in seen if a == "click"]
    assert clicks and clicks[-1].get("tabId") == 5
    ext.close()


def test_cli_no_fallback_flag_accepted(daemon):
    def handler(action, params):
        return {"success": True, "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "click", "Save", "--no-fallback"], env=env)
    assert code == 0, err
    ext.close()


def test_cli_status_shows_real_pid(daemon, fake_ext):
    import re
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["status"], env=env)
    assert code == 0
    assert "PID None" not in out
    assert re.search(r"\(PID \d+\)", out)
    code, out, err = run_cli(["daemon", "status"], env=env)
    assert code == 0
    assert "PID None" not in out
    assert re.search(r"\(PID \d+\)", out)


def test_cli_status_session_line(daemon):
    def handler(action, params):
        tid = params.get("tabId", 7)
        return {"success": True, "tab": {"id": tid, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    _, out, _ = run_cli(["--json", "session", "start", "--new", "--name", "cli-a"], env=env)
    token = json.loads(out)["sessionToken"]
    code, out, err = run_cli(["status", "--session", token], env=env)
    assert code == 0 and "Session cli-a: unpinned" in out
    run_cli(["switch", "5", "--session", token], env=env)
    code, out, err = run_cli(["status", "--session", token], env=env)
    assert "Session cli-a: pinned to tab 5" in out
    ext.close()


def test_cli_session_named_list(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    _, out, _ = run_cli(["--json", "session", "start", "--name", "alpha"], env=env)
    assert json.loads(out)["name"] == "alpha"
    run_cli(["--json", "session", "start", "--name", "beta"], env=env)
    code, out, err = run_cli(["--json", "session", "list"], env=env)
    assert code == 0
    names = [s["name"] for s in json.loads(out)["sessions"]]
    assert names == ["alpha", "beta"]
    code, out, err = run_cli(["session", "list"], env=env)
    assert code == 0 and "alpha" in out and "beta" in out
    code, out, err = run_cli(["session", "prune"], env=env)
    assert code == 0 and "Pruned 0" in out


def test_cli_snapshot_flags_forwarded(daemon):
    seen = []

    def handler(action, params):
        seen.append(dict(params))
        return {"success": True, "text": "page", "refs": 0,
                "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "snapshot", "--compact", "--max-nodes", "50"], env=env)
    assert code == 0, err
    assert seen[-1].get("mode") == "compact"
    assert seen[-1].get("maxNodes") == 50
    code, out, err = run_cli(["snapshot"], env=env)
    assert code == 0 and out == "page"
    ext.close()


def test_cli_upload_missing_file(daemon, tmp_path):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "upload", "e1", str(tmp_path / "nope.pdf")], env=env)
    assert code == 1
    data = json.loads(out)
    assert data["code"] == "file_not_found"


def test_cli_upload_existing_file_forwarded(daemon, tmp_path):
    seen = []

    def handler(action, params):
        seen.append(dict(params))
        return {"success": True, "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    f = tmp_path / "up.pdf"
    f.write_bytes(b"%PDF")
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "upload", "e3", str(f)], env=env)
    assert code == 0, err
    assert seen[-1]["files"] == [str(Path(str(f)).resolve())]
    ext.close()


def test_cli_run_steps_and_exit_codes(daemon, fake_ext, tmp_path):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps([{"action": "ping"}, {"action": "tab"}]), encoding="utf-8")
    code, out, err = run_cli(["run", str(steps)], env=env)
    assert code == 0, err
    lines = [json.loads(ln) for ln in out.splitlines()]
    assert [ln["step"] for ln in lines] == [0, 1]
    assert all(ln["success"] for ln in lines)

    steps.write_text(json.dumps([{"action": "ping"}, {"action": "click", "params": {"target": "x"}}]),
                     encoding="utf-8")
    code, out, err = run_cli(["run", str(steps)], env=env)
    assert code == 1
    assert len(out.splitlines()) == 2

    steps.write_text(json.dumps([{"nonsense": True}, {"action": "ping"}]), encoding="utf-8")
    code, out, err = run_cli(["run", str(steps), "--stop-on-error"], env=env)
    assert code == 1
    assert len(out.splitlines()) == 1


def test_cli_run_usage_errors(daemon, tmp_path):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["run", str(tmp_path / "missing.json")], env=env)
    assert code == 3
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, out, err = run_cli(["run", str(bad)], env=env)
    assert code == 3
    bad.write_text("[]", encoding="utf-8")
    code, out, err = run_cli(["run", str(bad)], env=env)
    assert code == 3


def test_cli_run_stdin(daemon, fake_ext):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["run", "-"], env=env, input_text=json.dumps([{"action": "ping"}]))
    assert code == 0, err
    assert json.loads(out)["success"] is True


def test_cli_eval_file_reads_code_from_file(daemon, tmp_path):
    seen = {}

    def handler(action, params):
        seen["code"] = params.get("code")
        return {"success": True, "result": 1}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    js = tmp_path / "probe.js"
    js.write_text("1 + 1", encoding="utf-8")
    code, out, err = run_cli(["--json", "eval", "--file", str(js)], env=env)
    assert code == 0, err
    assert seen["code"] == "1 + 1"
    ext.close()


def test_cli_eval_file_stdin(daemon):
    seen = {}

    def handler(action, params):
        seen["code"] = params.get("code")
        return {"success": True, "result": 2}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "eval", "--file", "-"], env=env, input_text="2 + 2")
    assert code == 0, err
    assert seen["code"] == "2 + 2"
    ext.close()


def test_cli_eval_without_code_or_file_exits_3(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "eval"], env=env)
    assert code == 3
    import json as _json
    assert _json.loads(out)["code"] == "bad_params"


def test_cli_eval_missing_file_exits_3(daemon, tmp_path):
    import json as _json
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["--json", "eval", "--file", str(tmp_path / "nope.js")], env=env)
    assert code == 3
    assert _json.loads(out)["code"] == "file_not_found"


def test_cli_wait_asks_the_daemon_for_its_whole_timeout(tmp_path, monkeypatch):
    # The CLI sent wait with the client's 20 s default, so the daemon cut a longer --timeout short.
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setenv("EDGE_BRIDGE_PORT", str(free_port()))
    monkeypatch.delenv("EDGE_BRIDGE_SESSION", raising=False)
    config.ensure_token()
    bodies = []

    class RecordingConnection:
        sock = None

        def request(self, method, url, body=None, headers=None):
            bodies.append(json.loads(body))

        def getresponse(self):
            return io.BytesIO(b'{"success": true}')

        def close(self):
            pass

    monkeypatch.setattr(Edge, "_get_connection", lambda self, timeout=None: RecordingConnection())
    assert cli.main(["--json", "wait", "--text", "X", "--timeout", "60000"]) == 0
    assert bodies[0]["timeout"] == 65, bodies
