"""Setup and configuration helpers for registering Agent Browser Bridge with agent clients.

Detects local agent clients (Claude Code, Gemini, Codex, Cursor, Windsurf, VS Code),
manages mcpServers JSON configuration with automatic backups, and runs CLI registration.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

# Registrations written by 2.x name the old module and command; both still run the same server.
MODULE_NAMES = ("agent_browser_bridge", "edge_agent_bridge")
COMMAND_NAMES = ("agent-bridge", "edge-bridge")


def _server_entry() -> dict:
    return {"command": sys.executable, "args": ["-m", "agent_browser_bridge", "mcp"]}


SERVER_ENTRY = _server_entry()

# VS Code keys its mcp.json on "servers"; Cursor and Windsurf use "mcpServers".
SERVER_KEY = {"cursor": "mcpServers", "windsurf": "mcpServers", "vscode": "servers"}


def get_cli_command(client: str, binary_path: str | None = None) -> list[str]:
    base = binary_path or client
    if client == "claude":
        return [base, "mcp", "add", "--scope", "user", "edge", "--", sys.executable, "-m", "agent_browser_bridge", "mcp"]
    if client == "gemini":
        return [base, "mcp", "add", "--scope", "user", "edge", sys.executable, "-m", "agent_browser_bridge", "mcp"]
    if client == "codex":
        return [base, "mcp", "add", "edge", "--", sys.executable, "-m", "agent_browser_bridge", "mcp"]
    return [base]


# argv rather than a shell string: nothing here reaches a shell interpreter.
CLI_COMMAND = {
    "claude": ["claude", "mcp", "add", "--scope", "user", "edge", "--", sys.executable, "-m", "agent_browser_bridge", "mcp"],
    "gemini": ["gemini", "mcp", "add", "--scope", "user", "edge", sys.executable, "-m", "agent_browser_bridge", "mcp"],
    "codex": ["codex", "mcp", "add", "edge", "--", sys.executable, "-m", "agent_browser_bridge", "mcp"],
}


def _template(client: str) -> dict:
    return {SERVER_KEY.get(client, "mcpServers"): {"edge": _server_entry()}}


def get_mcp_config(client: str) -> str:
    """Return the exact command line or JSON configuration snippet for a client."""
    client = client.lower().strip()
    if client in CLI_COMMAND:
        return " ".join(get_cli_command(client))
    if client in SERVER_KEY:
        return json.dumps(_template(client), indent=2)
    return f"# Unknown client '{client}'. Use one of: claude, gemini, codex, cursor, windsurf, vscode"


def merge_json_config(path: Path, client: str = "cursor") -> tuple[bool, str]:
    """Safely merge edge MCP server into a client's JSON configuration file.

    Creates a .bak.<timestamp> backup. If the file is unparsable, it is left untouched.
    """
    p = Path(path)
    if p.exists():
        raw = p.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except Exception as e:
            return False, f"Failed to parse {p}: invalid JSON ({e})"

        # Create backup
        ts = int(time.time())
        bak_path = p.with_name(f"{p.name}.bak.{ts}")
        bak_path.write_text(raw, encoding="utf-8")

        key = SERVER_KEY.get(client, "mcpServers")
        if not isinstance(data, dict):
            data = {}
        if key not in data or not isinstance(data[key], dict):
            data[key] = {}

        data[key]["edge"] = _server_entry()
        _write_atomic(p, json.dumps(data, indent=2) + "\n")
        return True, f"Updated {p} (backup saved to {bak_path.name})"
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(p, json.dumps(_template(client), indent=2) + "\n")
        return True, f"Created {p}"


def _write_atomic(path: Path, text: str) -> None:
    # A half-written editor config is worse than no write at all.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _get_vscode_mcp_path(env: dict) -> Path:
    if sys.platform == "win32":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Code" / "User" / "mcp.json"


def detect_clients(env: dict | None = None, path_which: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """Detect available agent clients on the system."""
    e = env if env is not None else os.environ
    which = path_which or shutil.which

    cli_clients = {
        "claude": which("claude"),
        "gemini": which("gemini"),
        "codex": which("codex"),
    }

    home = Path.home()
    cursor_path = home / ".cursor" / "mcp.json"
    windsurf_path = home / ".codeium" / "windsurf" / "mcp_config.json"
    vscode_path = _get_vscode_mcp_path(e)

    json_clients = {
        "cursor": {
            "path": cursor_path,
            "found": cursor_path.exists() or cursor_path.parent.exists(),
        },
        "windsurf": {
            "path": windsurf_path,
            "found": windsurf_path.exists() or windsurf_path.parent.exists(),
        },
        "vscode": {
            "path": vscode_path,
            "found": vscode_path.exists() or vscode_path.parent.exists(),
        },
    }

    return {"cli": cli_clients, "json": json_clients}


def registration_status(detected: dict[str, Any] | None = None) -> dict[str, str]:
    """Read-only check of which clients have the edge server registered.

    Returns per-client status: "registered" (JSON config contains the edge
    entry), "stale" (edge entry points to a missing interpreter),
    "detected" (client present, registration unknown), or "absent".
    CLI clients can't be queried without their own tooling, so they never
    report "registered" here.
    """
    found = detected or detect_clients()
    out: dict[str, str] = {}
    for name, path in (found.get("cli") or {}).items():
        out[name] = "detected" if path else "absent"
    for name, info in (found.get("json") or {}).items():
        p = Path(info["path"])
        entry = None
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            if isinstance(data, dict):
                key = SERVER_KEY.get(name, "mcpServers")
                sub = data.get(key)
                entry = sub.get("edge") if isinstance(sub, dict) else None
        except Exception:
            entry = None

        is_edge = False
        is_stale = False
        if isinstance(entry, dict):
            cmd = entry.get("command")
            args = entry.get("args") or []
            if cmd in COMMAND_NAMES:
                is_edge = True
            elif isinstance(args, list) and len(args) >= 3 and args[0] == "-m" and args[1] in MODULE_NAMES and args[2] == "mcp":
                is_edge = True
                if cmd:
                    exists = Path(cmd).exists() if Path(cmd).is_absolute() else bool(shutil.which(cmd))
                    if not exists:
                        is_stale = True
            elif isinstance(cmd, str) and any(cmd.endswith(n) or cmd.endswith(n + ".exe") for n in COMMAND_NAMES):
                is_edge = True
                exists = Path(cmd).exists() if Path(cmd).is_absolute() else bool(shutil.which(cmd))
                if not exists:
                    is_stale = True

        if is_edge:
            out[name] = "stale" if is_stale else "registered"
        elif info.get("found"):
            out[name] = "detected"
        else:
            out[name] = "absent"
    return out


def mcp_config_cli(client: str) -> int:
    """CLI handler for `agent-bridge mcp-config --client <name>`."""
    print(get_mcp_config(client))
    return 0


# New shells ignore a user PATH past this length, so refuse rather than write one.
HKCU_PATH_LIMIT = 2047


def fix_path(yes: bool = False) -> int:
    """CLI handler for `agent-bridge setup --fix-path`."""
    if sys.platform != "win32":
        print("setup --fix-path is only supported on Windows")
        return 3
    import sysconfig
    import winreg
    scripts_dir = sysconfig.get_path("scripts")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            try:
                current, reg_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, reg_type = "", winreg.REG_EXPAND_SZ
    except OSError as e:
        print(f"Cannot read the user PATH: {e}")
        return 1

    def norm(p):
        return p.replace("/", "\\").rstrip("\\").casefold()

    want = norm(scripts_dir)
    if any(norm(e) == want for e in current.split(";") if e):
        print(f"{scripts_dir} is already on the user PATH; nothing to do.")
        return 0
    new_path = f"{current};{scripts_dir}" if current else scripts_dir
    if len(new_path) > HKCU_PATH_LIMIT:
        print(f"Refusing: the resulting PATH would be {len(new_path)} chars, "
              f"over the {HKCU_PATH_LIMIT}-char HKCU limit. Shorten the user PATH first.")
        return 1
    print(f"Will append to the user PATH (HKCU\\Environment):\n  {scripts_dir}")
    if not yes:
        try:
            resp = input("Update the user PATH now? [y/N]: ").strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Path", 0, reg_type, new_path)
    except OSError as e:
        print(f"Cannot update the user PATH: {e}")
        return 1
    try:
        import ctypes
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
        )
    except Exception as e:
        print(f"Warning: PATH written but the broadcast failed ({e}); new shells may miss it.")
    print("User PATH updated. Reopen your terminal for the new PATH to take effect.")
    return 0


def setup_cli(yes: bool = False) -> int:
    """CLI handler for `agent-bridge setup`."""
    print("Agent Browser Bridge Setup — Client Auto-Detection")
    detected = detect_clients()

    targets_cli = []
    for name, path in detected["cli"].items():
        if path:
            targets_cli.append((name, path))

    targets_json = []
    for name, info in detected["json"].items():
        if info["found"]:
            targets_json.append((name, info["path"]))

    if not targets_cli and not targets_json:
        print("\nNo supported agent clients detected automatically.")
        print("To configure manually, run:")
        for client in ("claude", "cursor", "windsurf", "gemini", "codex", "vscode"):
            print(f"\n  agent-bridge mcp-config --client {client}")
        return 0

    print("\nDetected clients:")
    for c, path in targets_cli:
        print(f"  - CLI: {c} ({path})")
    for name, path in targets_json:
        print(f"  - App: {name} ({path})")

    if not yes:
        try:
            resp = input("\nConfigure detected clients with Agent Browser Bridge MCP server? [y/N]: ").strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 0

    # Configure CLI clients
    for c, path in targets_cli:
        print(f"\nConfiguring {c}...")
        try:
            cmd = get_cli_command(c, binary_path=path)
            subprocess.run(cmd, check=True)
            print(f"  Successfully added edge MCP to {c}")
        except Exception as err:
            print(f"  Failed to configure {c}: {err}")

    # Configure JSON clients
    for name, path in targets_json:
        print(f"\nConfiguring {name} ({path})...")
        ok, msg = merge_json_config(path, name)
        if ok:
            print(f"  {msg}")
        else:
            print(f"  Error: {msg}")

    print("\nSetup complete! You can verify daemon status with: agent-bridge status")
    return 0
