"""Tests for edge-bridge doctor, logs, and MCP registration status."""
import json
import os
import shutil
import sys
import sysconfig
import time
import pytest
from tests.fake_extension import FakeExtension
from tests.support import free_port
from tests.support import run_cli


def _stub_env(tmp_path, daemon):
    """Staged PATH: tmp stub launcher first, scripts dir present, ambient kept."""
    launcher_name = "edge-bridge.exe" if sys.platform == "win32" else "edge-bridge"
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / launcher_name
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    path = os.pathsep.join([str(stub_dir), sysconfig.get_path("scripts"),
                            os.environ.get("PATH", "")])
    return {"EDGE_BRIDGE_HOME": str(daemon.home),
            "EDGE_BRIDGE_PORT": str(daemon.port), "PATH": path,
            "EDGE_BRIDGE_TEST_ELEVATED": "0"}


def _doctor_json_in_process(monkeypatch, tmp_path, capsys):
    """Run _handle_doctor in-process (daemon down) for monkeypatched checks."""
    from agent_browser_bridge import cli
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(home))
    port = free_port()
    monkeypatch.setenv("EDGE_BRIDGE_PORT", str(port))
    rc = cli._handle_doctor(port, True)
    body = json.loads(capsys.readouterr().out)
    return rc, {c["name"]: c for c in body["checks"]}


def _tabs_handler(action, params):
    if action == "tabs":
        return {"success": True, "tabs": [{"id": 1, "title": "T", "url": "http://x/", "active": True}]}
    if action == "ping":
        return {"success": True, "fileAccess": True}
    return {"success": True}


def test_doctor_healthy_tree(daemon, tmp_path):
    ext = FakeExtension(daemon.port, handler=_tabs_handler).connect().run()
    time.sleep(0.3)
    env = _stub_env(tmp_path, daemon)
    code, out, err = run_cli(["doctor"], env=env)
    assert code == 0, out
    assert "FAIL" not in out
    for name in ("daemon", "token", "extension", "versions", "tabs", "file-access", "pairing", "pid",
                 "launcher", "path", "elevation", "mcp"):
        assert f"[ok  ] {name}:" in out
    ext.close()


def test_doctor_json_shape_hides_token(daemon, tmp_path):
    ext = FakeExtension(daemon.port, handler=_tabs_handler).connect().run()
    time.sleep(0.3)
    env = _stub_env(tmp_path, daemon)
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 0, out
    body = json.loads(out)
    assert body["success"] is True
    assert [c["name"] for c in body["checks"]] == [
        "daemon", "token", "extension", "versions", "tabs", "file-access", "pairing", "pid",
        "launcher", "path", "elevation", "mcp"]
    token = (daemon.home / "token").read_text(encoding="utf-8").strip()
    assert token not in out
    ext.close()


def test_doctor_file_access(daemon, tmp_path):
    def no_file_access(action, params):
        if action == "tabs":
            return {"success": True, "tabs": [{"id": 1, "title": "T", "url": "http://x/", "active": True}]}
        if action == "ping":
            return {"success": True, "fileAccess": False}
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=no_file_access).connect().run()
    time.sleep(0.3)
    env = _stub_env(tmp_path, daemon)
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 1
    body = json.loads(out)
    checks = {c["name"]: c for c in body["checks"]}
    assert checks["file-access"]["ok"] is False
    assert "turned off" in checks["file-access"]["detail"]
    assert "Allow access to file URLs" in checks["file-access"]["fix"]
    ext.close()


def test_doctor_file_access_unknown_on_older_extension(daemon, tmp_path):
    # A 2.5.0-or-older extension's ping has no fileAccess field; that is not a failure.
    def old_ping(action, params):
        if action == "tabs":
            return {"success": True, "tabs": [{"id": 1, "title": "T", "url": "http://x/", "active": True}]}
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=old_ping).connect().run()
    time.sleep(0.3)
    env = _stub_env(tmp_path, daemon)
    code, out, err = run_cli(["--json", "doctor"], env=env)
    checks = {c["name"]: c for c in json.loads(out)["checks"]}
    assert checks["file-access"]["ok"] is True, checks["file-access"]
    assert "unknown" in checks["file-access"]["detail"]
    ext.close()


def test_doctor_daemon_down(tmp_path):
    port = free_port()
    home = tmp_path / "empty_home"
    home.mkdir()
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port)}
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 1
    body = json.loads(out)
    assert body["success"] is False
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["daemon"]["ok"] is False
    assert "edge-bridge daemon start" in by_name["daemon"]["fix"]
    assert by_name["token"]["ok"] is False
    code, out, err = run_cli(["doctor"], env=env)
    assert code == 1 and "[FAIL] daemon:" in out


def test_doctor_stale_pid(tmp_path):
    port = free_port()
    home = tmp_path / "stale_home"
    home.mkdir()
    (home / "bridge.pid").write_text("99999999", encoding="utf-8")
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port)}
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 1
    by_name = {c["name"]: c for c in json.loads(out)["checks"]}
    assert by_name["pid"]["ok"] is False
    assert "stale pid file" in by_name["pid"]["detail"]
    assert "edge-bridge daemon restart" in by_name["pid"]["fix"]


def test_doctor_outdated_extension(daemon):
    ext = FakeExtension(daemon.port, version="1.0.0", handler=_tabs_handler).connect().run()
    time.sleep(0.3)
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["doctor"], env=env)
    assert code == 1
    assert "[FAIL] versions:" in out
    assert "microsoftedge.microsoft.com" in out
    ext.close()


def test_doctor_no_extension(daemon):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    code, out, err = run_cli(["doctor"], env=env)
    assert code == 1
    assert "[FAIL] extension:" in out


def test_logs_tail_and_json(daemon, fake_ext):
    env = {"EDGE_BRIDGE_HOME": str(daemon.home), "EDGE_BRIDGE_PORT": str(daemon.port)}
    run_cli(["ping"], env=env)
    code, out, err = run_cli(["logs", "--tail", "5"], env=env)
    assert code == 0, err
    assert len(out.splitlines()) >= 1
    code, out, err = run_cli(["--json", "logs", "--tail", "5"], env=env)
    assert code == 0
    body = json.loads(out)
    assert body["success"] is True and isinstance(body["lines"], list)


def test_logs_missing(tmp_path):
    port = free_port()
    home = tmp_path / "nolog_home"
    home.mkdir()
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port)}
    code, out, err = run_cli(["--json", "logs"], env=env)
    assert code == 1
    assert json.loads(out)["code"] == "no_log"


def test_registration_status_reads_json_configs(tmp_path):
    from agent_browser_bridge.setup import registration_status
    cursor_cfg = tmp_path / "cursor-mcp.json"
    cursor_cfg.write_text(json.dumps({"mcpServers": {"edge": {"command": "edge-bridge", "args": ["mcp"]}}}),
                          encoding="utf-8")
    broken = tmp_path / "vscode-mcp.json"
    broken.write_text("{oops", encoding="utf-8")
    detected = {
        "cli": {"claude": "/usr/bin/claude", "gemini": None},
        "json": {
            "cursor": {"path": cursor_cfg, "found": True},
            "vscode": {"path": broken, "found": True},
            "windsurf": {"path": tmp_path / "nope.json", "found": False},
        },
    }
    assert registration_status(detected) == {
        "claude": "detected", "gemini": "absent", "cursor": "registered",
        "vscode": "detected", "windsurf": "absent"}


def test_registration_status_new_entry_and_stale_interpreter(tmp_path):
    from agent_browser_bridge.setup import registration_status
    good_cfg = tmp_path / "cursor-mcp.json"
    good_cfg.write_text(json.dumps({"mcpServers": {"edge": {"command": sys.executable, "args": ["-m", "agent_browser_bridge", "mcp"]}}}),
                        encoding="utf-8")
    stale_cfg = tmp_path / "windsurf-mcp.json"
    stale_cfg.write_text(json.dumps({"mcpServers": {"edge": {"command": str(tmp_path / "deleted_python"), "args": ["-m", "agent_browser_bridge", "mcp"]}}}),
                         encoding="utf-8")
    detected = {
        "cli": {},
        "json": {
            "cursor": {"path": good_cfg, "found": True},
            "windsurf": {"path": stale_cfg, "found": True},
        },
    }
    assert registration_status(detected) == {"cursor": "registered", "windsurf": "stale"}


def test_doctor_mcp_check_fails_on_stale_registration(monkeypatch, tmp_path, capsys):
    from agent_browser_bridge import cli
    stale_cfg = tmp_path / "cursor-mcp.json"
    stale_cfg.write_text(json.dumps({"mcpServers": {"edge": {"command": str(tmp_path / "deleted_venv" / "python"), "args": ["-m", "agent_browser_bridge", "mcp"]}}}),
                         encoding="utf-8")
    detected = {
        "cli": {},
        "json": {"cursor": {"path": stale_cfg, "found": True}},
    }
    monkeypatch.setattr("agent_browser_bridge.setup.registration_status", lambda: {"cursor": "stale"})
    monkeypatch.setattr("agent_browser_bridge.cli._is_elevated", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    rc, by_name = _doctor_json_in_process(monkeypatch, tmp_path, capsys)
    assert rc == 1
    assert by_name["mcp"]["ok"] is False
    assert "cursor=stale" in by_name["mcp"]["detail"]
    assert "run edge-bridge setup to re-register" in by_name["mcp"]["fix"]



def test_doctor_launcher_stale_install(tmp_path):
    scripts = sysconfig.get_path("scripts")
    launcher_name = "edge-bridge.exe" if sys.platform == "win32" else "edge-bridge"
    if os.path.exists(os.path.join(scripts, launcher_name)):
        pytest.skip("launcher installed in scripts dir; stale state unstaging")
    port = free_port()
    home = tmp_path / "stale_home"
    home.mkdir()
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port),
           "PATH": scripts, "EDGE_BRIDGE_TEST_ELEVATED": "0"}
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 1
    by_name = {c["name"]: c for c in json.loads(out)["checks"]}
    assert by_name["launcher"]["ok"] is False
    assert "stale install" in by_name["launcher"]["detail"]
    assert by_name["launcher"]["fix"] == "py -m pip install --force-reinstall edge-agent-bridge"
    assert by_name["path"]["ok"] is True


def test_doctor_path_red_when_scripts_dir_off_path(tmp_path):
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    launcher_name = "edge-bridge.exe" if sys.platform == "win32" else "edge-bridge"
    stub = stub_dir / launcher_name
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    port = free_port()
    home = tmp_path / "offpath_home"
    home.mkdir()
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port),
           "PATH": str(stub_dir), "EDGE_BRIDGE_TEST_ELEVATED": "0"}
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 1
    by_name = {c["name"]: c for c in json.loads(out)["checks"]}
    assert by_name["launcher"]["ok"] is True
    assert by_name["path"]["ok"] is False
    assert sysconfig.get_path("scripts") in by_name["path"]["detail"]
    assert by_name["path"]["fix"] == "edge-bridge setup --fix-path"


def test_doctor_path_green_with_trailing_separator(tmp_path):
    # The python.org installer writes the entry as "C:\Python313\Scripts\", with the separator.
    port = free_port()
    home = tmp_path / "trailing_home"
    home.mkdir()
    env = {"EDGE_BRIDGE_HOME": str(home), "EDGE_BRIDGE_PORT": str(port),
           "PATH": sysconfig.get_path("scripts") + os.sep, "EDGE_BRIDGE_TEST_ELEVATED": "0"}
    code, out, err = run_cli(["--json", "doctor"], env=env)
    by_name = {c["name"]: c for c in json.loads(out)["checks"]}
    assert by_name["path"]["ok"] is True, by_name["path"]


def test_doctor_elevation_red_when_elevated(monkeypatch, tmp_path, capsys):
    from agent_browser_bridge import cli
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_is_elevated", lambda: True)
    rc, by_name = _doctor_json_in_process(monkeypatch, tmp_path, capsys)
    assert rc == 1
    assert by_name["elevation"]["ok"] is False
    assert "per OS user" in by_name["elevation"]["detail"]
    assert "unelevated" in by_name["elevation"]["detail"]
    assert by_name["elevation"]["fix"] == "use a normal non-admin PowerShell"


def test_doctor_elevation_green_when_unelevated(monkeypatch, tmp_path, capsys):
    from agent_browser_bridge import cli
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_is_elevated", lambda: False)
    rc, by_name = _doctor_json_in_process(monkeypatch, tmp_path, capsys)
    assert by_name["elevation"] == {"name": "elevation", "ok": True,
                                    "detail": "running unelevated", "fix": ""}


@pytest.mark.skipif(sys.platform == "win32", reason="off-windows behavior only")
def test_doctor_elevation_not_applicable_off_windows(monkeypatch, tmp_path, capsys):
    rc, by_name = _doctor_json_in_process(monkeypatch, tmp_path, capsys)
    assert by_name["elevation"] == {"name": "elevation", "ok": True,
                                    "detail": "not applicable", "fix": ""}


def test_daemon_version_drift_fails_doctor_and_warns_in_status(tmp_path):
    from tests.support import DaemonHandle
    from agent_browser_bridge import __version__
    port = free_port()
    daemon = DaemonHandle(port, tmp_path).start(extra_env={"EDGE_BRIDGE_TEST_DAEMON_VERSION": "2.4.0"})
    try:
        env = _stub_env(tmp_path, daemon)

        # A stale daemon is the fix to show even while the extension is disconnected.
        code, out, err = run_cli(["--json", "doctor"], env=env)
        checks = {c["name"]: c for c in json.loads(out)["checks"]}
        assert "differs from daemon 2.4.0" in checks["versions"]["detail"]
        assert "edge-bridge daemon restart" in checks["versions"]["fix"]

        ext = FakeExtension(port, version="2.5.0", handler=_tabs_handler).connect().run()
        try:
            time.sleep(0.3)
            code, out, err = run_cli(["status"], env=env)
            assert code == 0
            assert f"CLI {__version__}, daemon 2.4.0, extension 2.5.0" in out
            assert "edge-bridge daemon restart" in out

            code, out, err = run_cli(["--json", "doctor"], env=env)
            assert code == 1
            checks = {c["name"]: c for c in json.loads(out)["checks"]}
            assert checks["versions"]["ok"] is False
            assert "differs from daemon 2.4.0" in checks["versions"]["detail"]
            assert "edge-bridge daemon restart" in checks["versions"]["fix"]
        finally:
            ext.close()
    finally:
        daemon.stop()


def test_doctor_extension_newer_than_daemon_passes(daemon, tmp_path):
    ext = FakeExtension(daemon.port, version="2.9.9", handler=_tabs_handler).connect().run()
    time.sleep(0.3)
    env = _stub_env(tmp_path, daemon)
    code, out, err = run_cli(["--json", "doctor"], env=env)
    assert code == 0
    checks = {c["name"]: c for c in json.loads(out)["checks"]}
    assert checks["versions"]["ok"] is True
    assert "extension newer than daemon" in checks["versions"]["detail"]
    ext.close()


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


def test_doctor_reports_rejected_instance(daemon, tmp_path):
    ext1 = FakeExtension(daemon.port, instance="inst-1", handler=_tabs_handler).connect().run()
    assert _wait_active(daemon)
    ext2 = FakeExtension(daemon.port, instance="inst-2").connect().run()
    assert _wait_closed(ext2) == 4002
    env = _stub_env(tmp_path, daemon)
    code, out, err = run_cli(["doctor"], env=env)
    assert code == 0, out
    assert "another browser rejected: instance inst-2" in out
    ext1.close()
    ext2.close()

