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
    from edge_agent_bridge import cli
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
    from edge_agent_bridge.setup import registration_status
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


def test_doctor_elevation_red_when_elevated(monkeypatch, tmp_path, capsys):
    from edge_agent_bridge import cli
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
    from edge_agent_bridge import cli
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
