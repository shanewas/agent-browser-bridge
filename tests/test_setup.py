"""Tests for agent_browser_bridge.setup."""
import json
import os
import shutil
import sys
from pathlib import Path
import pytest
from agent_browser_bridge import setup

def test_mcp_config_snippets():
    cursor_snip = setup.get_mcp_config("cursor")
    data = json.loads(cursor_snip)
    assert "edge" in data["mcpServers"]
    assert data["mcpServers"]["edge"]["command"] == sys.executable
    assert data["mcpServers"]["edge"]["args"] == ["-m", "agent_browser_bridge", "mcp"]

    claude_snip = setup.get_mcp_config("claude")
    assert claude_snip == f"claude mcp add --scope user edge -- {sys.executable} -m agent_browser_bridge mcp"

    gemini_snip = setup.get_mcp_config("gemini")
    assert gemini_snip == f"gemini mcp add --scope user edge {sys.executable} -m agent_browser_bridge mcp"

    codex_snip = setup.get_mcp_config("codex")
    assert codex_snip == f"codex mcp add edge -- {sys.executable} -m agent_browser_bridge mcp"


def test_vscode_uses_the_servers_key():
    # VS Code reads mcp.json under "servers"; writing "mcpServers" there registers nothing.
    data = json.loads(setup.get_mcp_config("vscode"))
    assert list(data) == ["servers"]
    assert data["servers"]["edge"] == {"command": sys.executable, "args": ["-m", "agent_browser_bridge", "mcp"]}


def test_merge_into_vscode_keeps_its_own_key(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps({"servers": {"other": {"command": "x"}}, "inputs": []}), encoding="utf-8")
    ok, _ = setup.merge_json_config(target, "vscode")
    assert ok
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["servers"]["edge"] == {"command": sys.executable, "args": ["-m", "agent_browser_bridge", "mcp"]}
    assert data["servers"]["other"] == {"command": "x"}
    assert data["inputs"] == []
    assert "mcpServers" not in data


def test_merge_creates_vscode_file_with_servers_key(tmp_path):
    target = tmp_path / "nested" / "mcp.json"
    ok, _ = setup.merge_json_config(target, "vscode")
    assert ok
    assert list(json.loads(target.read_text(encoding="utf-8"))) == ["servers"]
    assert not list(target.parent.glob("*.tmp"))


def test_merge_json_config_existing_and_backup(tmp_path):
    target = tmp_path / "mcp.json"
    original = {"mcpServers": {"existing": {"command": "other"}}, "theme": "dark"}
    target.write_text(json.dumps(original, indent=2), encoding="utf-8")

    ok, msg = setup.merge_json_config(target)
    assert ok is True

    # Backup file created
    baks = list(tmp_path.glob("mcp.json.bak.*"))
    assert len(baks) == 1
    assert json.loads(baks[0].read_text(encoding="utf-8")) == original

    # Updated content preserves other keys
    updated = json.loads(target.read_text(encoding="utf-8"))
    assert updated["theme"] == "dark"
    assert updated["mcpServers"]["existing"] == {"command": "other"}
    assert updated["mcpServers"]["edge"] == {"command": sys.executable, "args": ["-m", "agent_browser_bridge", "mcp"]}


def test_merge_json_config_unparsable_left_untouched(tmp_path):
    target = tmp_path / "corrupt.json"
    corrupt_content = "this is not json {"
    target.write_text(corrupt_content, encoding="utf-8")

    ok, msg = setup.merge_json_config(target)
    assert ok is False
    assert "invalid" in msg.lower() or "error" in msg.lower() or "parse" in msg.lower()

    # Content unchanged
    assert target.read_text(encoding="utf-8") == corrupt_content
    # No backup created
    baks = list(tmp_path.glob("corrupt.json.bak.*"))
    assert len(baks) == 0


def test_detect_clients(monkeypatch, tmp_path):
    # Fake which
    which_map = {"claude": "/bin/claude", "gemini": None, "codex": "/bin/codex"}
    fake_which = lambda cmd: which_map.get(cmd)

    # Fake home dirs
    fake_home = tmp_path / "home"
    cursor_dir = fake_home / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    detected = setup.detect_clients(env={}, path_which=fake_which)
    assert detected["cli"]["claude"] == "/bin/claude"
    assert detected["cli"]["codex"] == "/bin/codex"
    assert detected["cli"]["gemini"] is None
    assert detected["json"]["cursor"]["found"] is True
    assert detected["json"]["windsurf"]["found"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="non-win32 refusal only")
def test_setup_fix_path_refuses_off_windows():
    from tests.support import run_cli
    code, out, err = run_cli(["setup", "--fix-path"])
    assert code == 3
    assert "only supported on Windows" in out


class _FakeKey:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        return self.fake

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self, path_value=None, reg_type=2):
        self.values = {}
        if path_value is not None:
            self.values["Path"] = (path_value, reg_type)
        self.writes = []

    def OpenKey(self, hive, subkey, reserved=0, access=0):
        assert hive == self.HKEY_CURRENT_USER
        assert subkey == "Environment"
        return _FakeKey(self)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, key, name, reserved, typ, value):
        self.writes.append((name, typ, value))
        self.values[name] = (value, typ)


class _FakeUser32:
    def __init__(self):
        self.calls = []

    def SendMessageTimeoutW(self, *args):
        self.calls.append(args)
        return 1


def _stage_windows(monkeypatch, path_value=None):
    import ctypes
    from types import SimpleNamespace
    fake_reg = _FakeWinreg(path_value)
    fake_u32 = _FakeUser32()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake_reg)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=fake_u32), raising=False)
    return fake_reg, fake_u32


def test_fix_path_appends_and_broadcasts(monkeypatch, capsys):
    import sysconfig
    fake_reg, fake_u32 = _stage_windows(monkeypatch, "C:\\Windows")
    rc = setup.fix_path(yes=True)
    assert rc == 0
    scripts = sysconfig.get_path("scripts")
    assert fake_reg.writes == [("Path", _FakeWinreg.REG_EXPAND_SZ, f"C:\\Windows;{scripts}")]
    assert len(fake_u32.calls) == 1
    assert "Environment" in fake_u32.calls[0]
    assert "Reopen your terminal" in capsys.readouterr().out


def test_fix_path_dedup_is_case_insensitive(monkeypatch, capsys):
    import sysconfig
    scripts = sysconfig.get_path("scripts")
    fake_reg, fake_u32 = _stage_windows(monkeypatch, f"C:\\Windows;{scripts.upper()}")
    rc = setup.fix_path(yes=True)
    assert rc == 0
    assert fake_reg.writes == []
    assert fake_u32.calls == []
    assert "already on the user PATH" in capsys.readouterr().out


def test_fix_path_missing_value_writes_scripts_only(monkeypatch, capsys):
    import sysconfig
    fake_reg, fake_u32 = _stage_windows(monkeypatch, None)
    rc = setup.fix_path(yes=True)
    assert rc == 0
    assert fake_reg.writes == [("Path", _FakeWinreg.REG_EXPAND_SZ, sysconfig.get_path("scripts"))]


def test_fix_path_refuses_overlong_path(monkeypatch, capsys):
    fake_reg, fake_u32 = _stage_windows(monkeypatch, "x" * 2047)
    rc = setup.fix_path(yes=True)
    assert rc == 1
    assert "2047" in capsys.readouterr().out
    assert fake_reg.writes == []
    assert fake_u32.calls == []


def test_fix_path_confirms_before_writing(monkeypatch, capsys):
    fake_reg, fake_u32 = _stage_windows(monkeypatch, "C:\\Windows")
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert setup.fix_path() == 0
    assert len(fake_reg.writes) == 1
    assert len(fake_u32.calls) == 1


def test_fix_path_abort_writes_nothing(monkeypatch, capsys):
    fake_reg, fake_u32 = _stage_windows(monkeypatch, "C:\\Windows")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert setup.fix_path() == 0
    assert "Aborted" in capsys.readouterr().out
    assert fake_reg.writes == []
    assert fake_u32.calls == []


def test_setup_cli_runs_detected_path_and_user_scope(monkeypatch):
    gemini_cmd = r"C:\Users\tester\AppData\Roaming\npm\gemini.CMD"
    claude_bin = "/usr/local/bin/claude"
    detected = {
        "cli": {"claude": claude_bin, "gemini": gemini_cmd, "codex": None},
        "json": {},
    }
    monkeypatch.setattr(setup, "detect_clients", lambda: detected)
    executed = []

    def fake_run(cmd, check=True):
        executed.append(cmd)

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    rc = setup.setup_cli(yes=True)
    assert rc == 0
    assert len(executed) == 2
    assert executed[0] == [claude_bin, "mcp", "add", "--scope", "user", "edge", "--", sys.executable, "-m", "agent_browser_bridge", "mcp"]
    assert executed[1] == [gemini_cmd, "mcp", "add", "--scope", "user", "edge", sys.executable, "-m", "agent_browser_bridge", "mcp"]

