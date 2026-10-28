"""Paths, port and token shared by daemon and clients.

Test-only knobs: EDGE_BRIDGE_PORT, EDGE_BRIDGE_HOME, EDGE_BRIDGE_HEARTBEAT (seconds),
EDGE_BRIDGE_PONG_TIMEOUT (seconds), EDGE_BRIDGE_LOCK_TIMEOUT (seconds), EDGE_BRIDGE_HTTP_IDLE (seconds),
EDGE_BRIDGE_SESSION_IDLE_TTL_S (seconds).
The extension itself always talks to 18999.
"""
import os
import secrets
import sys
from pathlib import Path

DEFAULT_PORT = 18999
TOKEN_HEADER = "X-Bridge-Token"
# The oldest extension this daemon drives in full. Store review lags a release by days, so version
# checks compare against this floor, not against the daemon's own version.
MIN_EXTENSION_VERSION = "2.6.0"
MAX_CALL_TIMEOUT_SEC = 120.0
DEFAULT_SESSION_IDLE_TTL_SEC = 24 * 60 * 60.0


def home() -> Path:
    env = os.environ.get("EDGE_BRIDGE_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "edge-agent-bridge"


def port() -> int:
    try:
        return int(os.environ.get("EDGE_BRIDGE_PORT", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def heartbeat_seconds() -> float:
    return _float_env("EDGE_BRIDGE_HEARTBEAT", 20.0)


def pong_timeout_seconds() -> float:
    return _float_env("EDGE_BRIDGE_PONG_TIMEOUT", 45.0)


def lock_timeout_seconds() -> float:
    return _float_env("EDGE_BRIDGE_LOCK_TIMEOUT", 10.0)


def session_idle_ttl_seconds() -> float:
    # A dead client leaks its session, so idle expiry is the reaper. 24 h is
    # safely above any real browsing session's idle gaps. An unreadable or
    # non-positive override is ignored rather than expiring everything at
    # once (or never), and the default applies instead.
    value = _float_env("EDGE_BRIDGE_SESSION_IDLE_TTL_S", DEFAULT_SESSION_IDLE_TTL_SEC)
    return value if value > 0 else DEFAULT_SESSION_IDLE_TTL_SEC


def http_idle_seconds() -> float:
    return _float_env("EDGE_BRIDGE_HTTP_IDLE", 30.0)


def call_timeout(value) -> float:
    """A call's timeout in seconds as the daemon reads it: 20 when unreadable, kept within 1..120."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 20.0
    return max(1.0, min(seconds, MAX_CALL_TIMEOUT_SEC))


def extension_dir() -> Path:
    return Path(__file__).resolve().parent / "extension"


def token_path() -> Path:
    return home() / "token"


def log_path() -> Path:
    return home() / "bridge.log"


def pid_path() -> Path:
    return home() / "bridge.pid"


def config_path() -> Path:
    return home() / "config"


def ensure_token() -> str:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        existing = p.read_text(encoding="utf-8").strip()
        if len(existing) == 64:
            return existing
    token = secrets.token_hex(32)
    # Created 0600 rather than written then chmodded: the write-first order leaves the token
    # world-readable for the window in between, which is the window pairing exists to close.
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def read_token():
    p = token_path()
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def read_config() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_config(values: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
