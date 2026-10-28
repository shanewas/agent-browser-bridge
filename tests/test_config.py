import os
import stat
import sys
from pathlib import Path

from agent_browser_bridge import config


def test_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path / "h"))
    assert config.home() == tmp_path / "h"


def test_home_per_platform(monkeypatch, tmp_path):
    monkeypatch.delenv("EDGE_BRIDGE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    h = config.home()
    if sys.platform == "win32":
        assert h == tmp_path / "la" / "edge-agent-bridge"
    elif sys.platform == "darwin":
        assert h == tmp_path / "Library" / "Application Support" / "edge-agent-bridge"
    else:
        assert h == tmp_path / "xdg" / "edge-agent-bridge"


def test_port_default_and_env(monkeypatch):
    monkeypatch.delenv("EDGE_BRIDGE_PORT", raising=False)
    assert config.port() == 18999
    monkeypatch.setenv("EDGE_BRIDGE_PORT", "19001")
    assert config.port() == 19001
    monkeypatch.setenv("EDGE_BRIDGE_PORT", "junk")
    assert config.port() == 18999


def test_heartbeat_knobs(monkeypatch):
    monkeypatch.delenv("EDGE_BRIDGE_HEARTBEAT", raising=False)
    monkeypatch.delenv("EDGE_BRIDGE_PONG_TIMEOUT", raising=False)
    assert config.heartbeat_seconds() == 20.0
    assert config.pong_timeout_seconds() == 45.0
    monkeypatch.setenv("EDGE_BRIDGE_HEARTBEAT", "1")
    monkeypatch.setenv("EDGE_BRIDGE_PONG_TIMEOUT", "x")
    assert config.heartbeat_seconds() == 1.0
    assert config.pong_timeout_seconds() == 45.0


def test_extension_dir_has_manifest():
    assert (config.extension_dir() / "manifest.json").exists()


def test_ensure_token_creates_once(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    t1 = config.ensure_token()
    t2 = config.ensure_token()
    assert t1 == t2 and len(t1) == 64 and int(t1, 16)
    assert config.read_token() == t1
    if os.name == "posix":
        assert stat.S_IMODE(config.token_path().stat().st_mode) == 0o600


def test_ensure_token_restricts_an_existing_0644_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    p = config.token_path()
    p.write_text("a" * 64, encoding="utf-8")
    if os.name == "posix":
        os.chmod(p, 0o644)
    assert config.ensure_token() == "a" * 64
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_ensure_token_rotated_token_file_is_0600(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    p = config.token_path()
    p.write_text("not-a-token", encoding="utf-8")
    if os.name == "posix":
        os.chmod(p, 0o644)
    token = config.ensure_token()
    assert len(token) == 64 and token != "not-a-token"
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_read_token_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    assert config.read_token() is None


def test_config_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(tmp_path))
    assert config.read_config() == {}
    config.write_config({"pairing": "1"})
    assert config.read_config() == {"pairing": "1"}
