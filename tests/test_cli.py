"""Tests for edge_agent_bridge.cli."""
import io
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
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


def test_cli_unreachable_port_autostarts_instead_of_exits_2(monkeypatch, tmp_path):
    # Since issue #30 the CLI starts the daemon on first use, so a port with
    # no daemon answers extension_offline (exit 1), not daemon_unreachable.
    port = free_port()
    env = {"EDGE_BRIDGE_HOME": str(tmp_path), "EDGE_BRIDGE_PORT": str(port)}
    try:
        code, out, err = run_cli(["--json", "ping"], env=env)
        assert code == 1
        data = json.loads(out)
        assert data["success"] is False
        assert data["code"] == "extension_offline"
    finally:
        run_cli(["daemon", "stop"], env=env)


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


def test_cli_type_mismatch_warns_and_exits_1(daemon):
    def handler(action, params):
        if action == "type":
            return {"success": True, "written": "56", "readback": "5", "match": False,
                    "field": "contenteditable", "tab": {"id": 1, "title": "t", "url": "http://t"}}
        return {"success": True, "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["type", "56"], env=env)
    ext.close()
    assert code == 1, err
    assert out.startswith("OK"), out
    assert "Warning (write_mismatch): wrote '56', read back '5'" in err, err

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


def test_cli_daemon_status_prints_pid_from_status_not_file(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    pid = daemon.status()["pid"]
    (daemon.home / "bridge.pid").write_text("1", encoding="utf-8")
    code, out, err = run_cli(["daemon", "status"], env=env)
    assert code == 0, err
    assert f"(PID {pid})" in out


@pytest.fixture
def bystander():
    """A live process that is not the daemon, standing in for a recycled PID or a hung daemon."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait(timeout=5)


def test_cli_daemon_stop_leaves_stale_pid_process_alive(tmp_path, bystander):
    # A crash leaves bridge.pid behind and the OS recycles the PID; stop killed whatever owned it (#5).
    (tmp_path / "bridge.pid").write_text(str(bystander.pid), encoding="utf-8")
    env = {"EDGE_BRIDGE_HOME": str(tmp_path), "EDGE_BRIDGE_PORT": str(free_port())}
    code, out, err = run_cli(["daemon", "stop"], env=env)
    assert code == 0, err
    assert "stale" in out
    assert not (tmp_path / "bridge.pid").exists()
    assert bystander.poll() is None


def test_cli_daemon_stop_kills_the_daemon_that_owns_the_pid(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["daemon", "stop"], env=env)
    assert code == 0, err
    assert "Stopped daemon" in out
    daemon.proc.wait(timeout=10)


def test_cli_daemon_stop_refuses_daemon_without_status_pid(tmp_path, bystander):
    # A pre-fix daemon answers /status without "pid"; that proves a bridge runs, not that bridge.pid names it.
    import http.server
    import threading

    class OldStatus(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"bridge_running": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OldStatus)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        (tmp_path / "bridge.pid").write_text(str(bystander.pid), encoding="utf-8")
        env = {"EDGE_BRIDGE_HOME": str(tmp_path), "EDGE_BRIDGE_PORT": str(srv.server_address[1])}
        code, out, err = run_cli(["daemon", "stop"], env=env)
        assert code == 1
        assert "--force" in err
        assert (tmp_path / "bridge.pid").exists()
        assert bystander.poll() is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_daemon_stop_hung_daemon_needs_force(tmp_path, bystander):
    # Alive PID plus a port that accepts but never answers: plain stop keeps bridge.pid so --force can still kill.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    try:
        (tmp_path / "bridge.pid").write_text(str(bystander.pid), encoding="utf-8")
        env = {"EDGE_BRIDGE_HOME": str(tmp_path), "EDGE_BRIDGE_PORT": str(listener.getsockname()[1])}
        started = time.time()
        code, out, err = run_cli(["daemon", "stop"], env=env)
        assert code == 1
        assert "--force" in err
        assert time.time() - started < 15
        assert (tmp_path / "bridge.pid").exists()
        assert bystander.poll() is None
        code, out, err = run_cli(["daemon", "stop", "--force"], env=env)
        assert code == 0, err
        bystander.wait(timeout=10)
        assert not (tmp_path / "bridge.pid").exists()
    finally:
        listener.close()


def test_cli_daemon_restart_fails_while_another_daemon_answers(daemon, bystander):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    (daemon.home / "bridge.pid").write_text(str(bystander.pid), encoding="utf-8")
    code, out, err = run_cli(["daemon", "restart"], env=env)
    assert code == 1
    assert f"PID {daemon.status()['pid']} still answers" in err
    assert bystander.poll() is None


def test_cli_daemon_restart_without_pid_file(daemon):
    # bridge.pid is gone but the daemon answers: /status names its PID, so restart needs no --force.
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    old_pid = daemon.status()["pid"]
    (daemon.home / "bridge.pid").unlink()
    try:
        code, out, err = run_cli(["daemon", "restart"], env=env)
        assert code == 0, err
        assert f"Stopped daemon (PID {old_pid})" in out
        daemon.proc.wait(timeout=10)
        new_pid = daemon.status()["pid"]
        assert new_pid != old_pid
    finally:
        run_cli(["daemon", "stop"], env=env)


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


def test_cli_run_tab_forwarded_to_steps(daemon, tmp_path):
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "tab": {"id": 77, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps([
        {"action": "snapshot", "params": {}},
        {"action": "snapshot", "params": {"tabId": 9}},
    ]), encoding="utf-8")
    code, out, err = run_cli(["run", str(steps), "--tab", "77"], env=env)
    assert code == 0, err
    assert seen[0][1].get("tabId") == 77, seen
    assert seen[1][1].get("tabId") == 9, seen
    ext.close()


def test_cli_eval_text_screenshot_print_results(daemon):
    def handler(action, params):
        if action == "eval":
            return {"success": True, "result": 42, "tab": {"id": 1, "title": "t", "url": "http://t"}}
        if action == "text":
            return {"success": True, "text": "hello world", "tab": {"id": 1, "title": "t", "url": "http://t"}}
        if action == "screenshot":
            return {"success": True, "dataUrl": "data:image/jpeg;base64,AAAA",
                    "tab": {"id": 1, "title": "t", "url": "http://t"}}
        return {"success": True, "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["eval", "6*7"], env=env)
    assert code == 0 and out.strip() == "42", (code, out, err)
    code, out, err = run_cli(["text", "main"], env=env)
    assert code == 0 and out.strip() == "hello world", (code, out, err)
    code, out, err = run_cli(["screenshot"], env=env)
    assert code == 0 and "27 bytes" in out, (code, out, err)
    ext.close()


def test_cli_repl_is_removed():
    # The repl subcommand is gone (issue #26): argparse rejects it with exit 2.
    code, out, err = run_cli(["repl"])
    assert code == 2 and "invalid choice" in err, (code, out, err)


def test_cli_stdin_read_as_utf8_bytes(daemon):
    # Issue #25: stdin is read as UTF-8 bytes, not the console code page.
    import os as _os, subprocess as _sp, sys as _sys
    from pathlib import Path as _P
    seen = []

    def handler(action, params):
        seen.append((action, dict(params)))
        return {"success": True, "match": True, "tab": {"id": 1, "title": "t", "url": "http://t"}}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    env = {k: v for k, v in _os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update({"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)})
    root = _P(__file__).resolve().parents[1]

    def run_bytes(args, payload):
        r = _sp.run([_sys.executable, "-u", "-m", "edge_agent_bridge.cli"] + args,
                    cwd=root, env=env, input=payload, capture_output=True)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    text = "bullet \u2022 emoji \U0001F600"
    steps = json.dumps([{"action": "fill", "params": {"target": "x", "text": text}}]).encode("utf-8")
    code, out, err = run_bytes(["run", "-"], steps)
    assert code == 0, (code, out, err)
    fills = [prm for a, prm in seen if a == "fill"]
    assert fills and fills[-1].get("text") == text, seen

    code, out, err = run_bytes(["eval", "--file", "-"], "var s = \'\u2022\';".encode("utf-8"))
    assert code == 0, (code, out, err)
    evals = [prm for a, prm in seen if a == "eval"]
    assert evals and "\u2022" in evals[-1].get("code", ""), seen

    code, out, err = run_bytes(["run", "-"], b"\xff\xfe not utf-8")
    assert code == 3, (code, out, err)

    # PowerShell 5 pipes and Notepad files start with a BOM.
    code, out, err = run_bytes(["run", "-"], b"\xef\xbb\xbf" + steps)
    assert code == 0, (code, out, err)

    script = daemon.home / "bom.js"
    script.write_bytes(b"\xef\xbb\xbf1 + 1")
    code, out, err = run_bytes(["eval", "--file", str(script)], b"")
    assert code == 0, (code, out, err)
    assert [prm for a, prm in seen if a == "eval"][-1]["code"] == "1 + 1"

    script.write_bytes(b"var s = '\x95';")
    code, out, err = run_bytes(["--json", "eval", "--file", str(script)], b"")
    assert code == 3, (code, out, err)
    assert json.loads(out)["code"] == "bad_params"
    ext.close()


def test_cli_autostarts_daemon_but_status_does_not(tmp_path):
    # Issue #30: browser commands start the daemon on first use; status must not.
    import urllib.request as _u
    from tests.support import free_port as _fp

    def probe(port):
        try:
            with _u.urlopen(f"http://127.0.0.1:{port}/status", timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    port = _fp()
    home = tmp_path / "home1"
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port)}
    try:
        code, out, err = run_cli(["--json", "tabs"], env=env)
        body = json.loads(out)
        assert body["code"] == "extension_offline", (code, out, err)
        assert probe(port) and probe(port)["bridge_running"] is True
        ext = FakeExtension(port, handler=lambda a, p: {"success": True, "tabs": []}).connect().run()
        try:
            time.sleep(0.3)
            code, out, err = run_cli(["--json", "tabs"], env=env)
            assert code == 0 and json.loads(out)["success"] is True, (code, out, err)
        finally:
            ext.close()
    finally:
        run_cli(["daemon", "stop"], env=env)

    port2 = _fp()
    env2 = {"EDGE_BRIDGE_HOME": str(tmp_path / "home2"), "EDGE_BRIDGE_PORT": str(port2)}
    for args in (["status"], ["session", "list"], ["session", "status", "--session", "t"],
                 ["session", "stop", "--session", "t"], ["session", "prune"]):
        run_cli(args, env=env2)
        assert probe(port2) is None, args


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


def test_cli_check_radio_dispatches_ref_selector_target(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setenv("EDGE_BRIDGE_PORT", str(free_port()))
    monkeypatch.delenv("EDGE_BRIDGE_SESSION", raising=False)
    config.ensure_token()
    calls = []

    class RecordingConnection:
        sock = None

        def request(self, method, url, body=None, headers=None):
            calls.append((url, json.loads(body)))

        def getresponse(self):
            return io.BytesIO(b'{"success": true}')

        def close(self):
            pass

    monkeypatch.setattr(Edge, "_get_connection", lambda self, timeout=None: RecordingConnection())

    assert cli.main(["--json", "check-radio", "--ref", "e5"]) == 0
    assert calls[-1][0] == "/exec"
    assert calls[-1][1]["action"] == "check_radio"
    assert calls[-1][1]["params"]["ref"] == "e5"

    assert cli.main(["--json", "check-radio", "--selector", "#agree"]) == 0
    assert calls[-1][1]["params"]["selector"] == "#agree"

    assert cli.main(["--json", "check-radio", "Option A"]) == 0
    assert calls[-1][1]["params"]["target"] == "Option A"

