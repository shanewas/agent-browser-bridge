#!/usr/bin/env python3
"""
Agent Browser Bridge CLI.
Allows the agent or user to drive Microsoft Edge directly via command line.
"""
import argparse
import importlib.metadata
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

from . import __version__, config
from .client import Edge, EdgeClient, send_cmd, ensure_bridge_running
from .bridge import parse_version

EDGE_STORE_URL = "https://microsoftedge.microsoft.com/addons/detail/agent-browser-bridge/dfkieodkfepoidihjapiggpjmfapanpd"
# The oldest extension this daemon drives in full. Store review lags a release by days, so the
# version check compares against this floor, not against the daemon's own version.
MIN_EXTENSION_VERSION = "2.6.0"

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _probe_status(port: int) -> dict:
    """/status with a short timeout and no auto-start, so stop never blocks on a hung daemon."""
    with Edge(port=port, timeout=2, auto_start=False) as client:
        return client.status()


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) terminates the process on Windows.
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, errors="replace").stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _port_listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def _read_stdin_text() -> str:
    """Read all of stdin as UTF-8, whatever the console code page says, without a leading BOM."""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8-sig")
    return sys.stdin.read().removeprefix(chr(0xFEFF))


def _handle_daemon_cmd(args, port: int) -> int:
    sub = args.daemon_action
    pid_p = config.pid_path()

    if sub == "stop":
        if not pid_p.exists():
            # Without bridge.pid, the daemon answering on the port is the only source of its PID.
            pid = _probe_status(port).get("pid")
            if not pid:
                print("Daemon PID file not found")
                return 0
        else:
            try:
                pid = int(pid_p.read_text(encoding="utf-8").strip())
            except (OSError, ValueError) as e:
                print(f"Error stopping daemon: {e}")
                pid_p.unlink(missing_ok=True)
                return 0
        if pid_p.exists() and not getattr(args, "force", False):
            st = _probe_status(port)
            if st.get("bridge_running") and "pid" not in st:
                # An answer proves a bridge runs on the port, not that bridge.pid names it.
                print(f"The daemon on 127.0.0.1:{port} predates pid reporting, so PID {pid} cannot be confirmed. "
                      "Run: agent-bridge daemon stop --force", file=sys.stderr)
                return 1
            if not st.get("bridge_running") and _pid_alive(pid) and _port_listening(port):
                print(f"PID {pid} is alive and 127.0.0.1:{port} accepts connections but does not answer /status. "
                      "If that is a hung daemon, run: agent-bridge daemon stop --force", file=sys.stderr)
                return 1
            if st.get("pid") != pid:
                pid_p.unlink(missing_ok=True)
                print(f"Removed stale PID file: PID {pid} is not the daemon on 127.0.0.1:{port}")
                return 0
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"Stopped daemon (PID {pid})")
        except Exception as e:
            print(f"Error stopping daemon: {e}")
        finally:
            try:
                pid_p.unlink(missing_ok=True)
            except Exception:
                pass
        return 0

    elif sub == "start":
        if getattr(args, "require_pairing", False):
            cfg = config.read_config()
            cfg["pairing"] = "1"
            config.write_config(cfg)
        ok = ensure_bridge_running(port=port, wait_for_extension=False, timeout=5.0)
        if ok:
            print(f"Daemon running on port {port}")
            return 0
        else:
            print(f"Failed to start daemon on port {port}", file=sys.stderr)
            return 2

    elif sub == "restart":
        rc = _handle_daemon_cmd(argparse.Namespace(daemon_action="stop", force=getattr(args, "force", False)), port)
        if rc:
            return rc
        deadline = time.time() + 5
        while (st := _probe_status(port)).get("bridge_running") and time.time() < deadline:
            time.sleep(0.2)
        if st.get("bridge_running"):
            print(f"Daemon PID {st.get('pid', '?')} still answers on 127.0.0.1:{port}; not restarted", file=sys.stderr)
            return 1
        return _handle_daemon_cmd(argparse.Namespace(daemon_action="start", require_pairing=False), port)

    elif sub == "status":
        with Edge(port=port, auto_start=False) as client:
            st = client.status()
        if st.get("bridge_running"):
            print(f"Daemon: running on 127.0.0.1:{port} (PID {st.get('pid', '?')})")
            print(f"Extension: {st.get('extension_version', 'none')} ({'connected' if st.get('websocket_active') else 'disconnected'})")
            return 0
        else:
            print(f"Daemon not running on 127.0.0.1:{port}")
            return 2

    return 3


def _is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    override = os.environ.get("EDGE_BRIDGE_TEST_ELEVATED")
    if override is not None:
        return override == "1"
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


RENAME_NOTICE = ("edge-agent-bridge is now agent-browser-bridge. The command is agent-bridge "
                 "(edge-bridge still works); upgrade with: pip install -U agent-browser-bridge")


def _rename_notice() -> str | None:
    """The upgrade hint, only while an edge-agent-bridge distribution (shim or older) is installed."""
    try:
        dist = importlib.metadata.distribution("edge-agent-bridge")
    except importlib.metadata.PackageNotFoundError:
        return None
    old = getattr(dist, "version", "3.0.0")
    if parse_version(old) < parse_version("3.0.0"):
        # pip install agent-browser-bridge next to 2.x leaves both; edge-bridge then still runs the old code.
        return (f"edge-agent-bridge {old} is still installed next to agent-browser-bridge; "
                "run: pip install -U edge-agent-bridge (or pip uninstall edge-agent-bridge)")
    return RENAME_NOTICE


def _handle_doctor(port: int, opt_json: bool) -> int:
    checks = []

    def add(name, ok, detail, fix=""):
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    with Edge(port=port, auto_start=False) as edge:
        try:
            st = edge.status()
        except Exception as e:
            st = {"success": False, "code": "daemon_unreachable", "error": str(e)}
    up = bool(st.get("bridge_running"))
    add("daemon", up,
        f"reachable on 127.0.0.1:{port}" if up else "not running",
        "" if up else "agent-bridge daemon start")

    tok_p = config.token_path()
    if tok_p.exists():
        if sys.platform == "win32":
            add("token", True, f"present at {tok_p}", "")
        else:
            mode = oct(os.stat(tok_p).st_mode & 0o777)
            ok = mode == "0o600"
            add("token", ok, f"{tok_p} mode {mode}",
                "" if ok else f"delete {tok_p} and run: agent-bridge daemon start")
    else:
        add("token", False, f"missing at {tok_p}",
            "agent-bridge daemon start (creates it on first start)")

    if up:
        connected = bool(st.get("websocket_active"))
        rej = st.get("rejected_instance")
        ext_detail = f"connected, version {st.get('extension_version')}" if connected else "not connected"
        if rej:
            ext_detail += f" (another browser rejected: instance {rej})"
        add("extension", connected, ext_detail,
            "" if connected else "open Edge and check the popup shows Connected")
        ext_v, dae_v = st.get("extension_version"), st.get("daemon_version")
        if dae_v is not None and dae_v != __version__:
            add("versions", False, f"CLI {__version__} differs from daemon {dae_v}",
                "agent-bridge daemon restart")
        elif ext_v is None:
            add("versions", False, "extension version unknown",
                "open Edge and check the popup shows Connected")
        elif parse_version(ext_v) < parse_version(MIN_EXTENSION_VERSION):
            add("versions", False, f"extension {ext_v} predates the oldest supported {MIN_EXTENSION_VERSION}",
                f"update from {EDGE_STORE_URL} or reload unpacked from {config.extension_dir()}")
        elif dae_v is not None and parse_version(ext_v) > parse_version(dae_v):
            add("versions", True, f"daemon {dae_v}, extension {ext_v} (extension newer than daemon)", "")
        else:
            add("versions", True, f"daemon {dae_v}, extension {ext_v}", "")
        with Edge(port=port, auto_start=False) as edge2:
            tabs_res = edge2.tabs()
            ping_res = edge2.ping() if connected else {}
        if tabs_res.get("success"):
            tabs = tabs_res.get("tabs", [])
            add("tabs", True, f"{len(tabs)} tab(s) reachable", "")
        else:
            add("tabs", False, f"{tabs_res.get('code')}: {tabs_res.get('error')}",
                "open Edge and check the popup shows Connected")
        if ping_res.get("success") and ping_res.get("fileAccess"):
            add("file-access", True, "allowed", "")
        elif ping_res.get("success") and "fileAccess" not in ping_res:
            add("file-access", True, "unknown: the extension predates this check", "")
        elif ping_res.get("success"):
            add("file-access", False,
                "turned off: file:// navigation and uploads will fail",
                "turn on 'Allow access to file URLs' in edge://extensions > Details")
        else:
            detail = f"{ping_res.get('code')}: {ping_res.get('error')}" if ping_res.get("code") else "extension not connected"
            add("file-access", False, detail, "open Edge and check the popup shows Connected")
        if st.get("pairing_required"):
            if connected:
                add("pairing", True, "required and paired", "")
            else:
                data_dir = st.get("data_dir") or str(config.home())
                add("pairing", False, "required but unpaired",
                    f"paste the token from {Path(data_dir) / 'token'} into the extension popup")
        else:
            add("pairing", True, "not required", "")
    else:
        for name in ("extension", "versions", "tabs", "file-access", "pairing"):
            add(name, False, "skipped: daemon not running", "agent-bridge daemon start")

    pid_p = config.pid_path()
    if pid_p.exists() and not up:
        add("pid", False, f"stale pid file at {pid_p}", "agent-bridge daemon restart")
    else:
        add("pid", True, "no stale pid file" if up else "no pid file", "")

    scripts_dir = sysconfig.get_path("scripts")
    launcher_name = "agent-bridge.exe" if sys.platform == "win32" else "agent-bridge"
    found = shutil.which(launcher_name)
    if found:
        add("launcher", True, f"resolved at {found}", "")
    elif (Path(scripts_dir) / launcher_name).exists():
        add("launcher", False, f"present at {scripts_dir} but that dir is not on PATH",
            "agent-bridge setup --fix-path")
    else:
        add("launcher", False, f"missing from {scripts_dir} (stale install?)",
            "py -m pip install --force-reinstall agent-browser-bridge")

    notice = _rename_notice()
    add("package", True, f"agent-browser-bridge {__version__}" + (f"; {notice}" if notice else ""), "")

    # normpath drops the trailing separator the python.org installer writes into PATH.
    path_entries = [os.path.normcase(os.path.normpath(e)) for e in os.environ.get("PATH", "").split(os.pathsep) if e]
    if os.path.normcase(os.path.normpath(scripts_dir)) in path_entries:
        add("path", True, f"{scripts_dir} on PATH", "")
    else:
        add("path", False, f"{scripts_dir} not on PATH", "agent-bridge setup --fix-path")

    if sys.platform != "win32":
        add("elevation", True, "not applicable", "")
    elif _is_elevated():
        add("elevation", False,
            "running elevated: the daemon pairs per OS user and Edge runs unelevated",
            "use a normal non-admin PowerShell")
    else:
        add("elevation", True, "running unelevated", "")

    from .setup import registration_status
    try:
        reg = registration_status()
        detail = ", ".join(f"{k}={v}" for k, v in sorted(reg.items()))
        has_stale = any(v == "stale" for v in reg.values())
        if has_stale:
            add("mcp", False, detail or "no clients found", "run agent-bridge setup to re-register")
        else:
            add("mcp", True, detail or "no clients found", "")
    except Exception as e:
        detail = f"check failed: {e}"
        add("mcp", True, detail, "")

    if opt_json:
        print(json.dumps({"success": all(c["ok"] for c in checks), "checks": checks}))
    else:
        for c in checks:
            mark = "ok  " if c["ok"] else "FAIL"
            line = f"[{mark}] {c['name']}: {c['detail']}"
            if not c["ok"] and c["fix"]:
                line += f"  (fix: {c['fix']})"
            print(line)
    return 0 if all(c["ok"] for c in checks) else 1


def _handle_logs(args, opt_json: bool) -> int:
    p = config.log_path()
    if not p.exists():
        err = {"success": False, "code": "no_log",
               "error": "No log file yet; the daemon has never started here. Run: agent-bridge daemon start"}
        if opt_json:
            print(json.dumps(err))
        else:
            print(f"Error ({err['code']}): {err['error']}", file=sys.stderr)
        return 1
    tail_n = max(0, args.tail)
    text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    lines = text[-tail_n:] if tail_n else []
    if opt_json:
        print(json.dumps({"success": True, "path": str(p), "lines": lines}))
    else:
        for ln in lines:
            print(ln)
    if args.follow and not opt_json:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)
                while True:
                    line = fh.readline()
                    if line:
                        print(line, end="" if line.endswith("\n") else "\n")
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return 0


def _handle_run(args, edge: Edge, opt_session, opt_json: bool, opt_tab=None) -> int:
    def usage(msg):
        err = {"success": False, "code": "bad_params", "error": msg}
        if opt_json:
            print(json.dumps(err))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 3

    src = args.steps_file
    try:
        raw = _read_stdin_text() if src == "-" else Path(src).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        return usage(f"Cannot read {'stdin' if src == '-' else src}: {e}")
    if len(raw.encode("utf-8")) > 1024 * 1024:
        return usage("steps file over 1 MB; batches are not bulk transport")
    try:
        steps = json.loads(raw)
    except Exception as e:
        return usage(f"Invalid JSON in {src}: {e}")
    if not isinstance(steps, list) or not steps:
        return usage("steps file must hold a non-empty array of {action, params}")
    worst = 0
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "action" not in step:
            print(json.dumps({"step": i, "action": None, "success": False,
                              "code": "bad_params", "error": "each step needs {action, params?}"}))
            worst = 1
            if args.stop_on_error:
                break
            continue
        params = dict(step.get("params") or {})
        if opt_tab is not None and "tabId" not in params:
            params["tabId"] = opt_tab
        res = edge.send(step["action"], params)
        out = {"step": i, "action": step["action"]}
        out.update(res if isinstance(res, dict) else {"success": False, "code": "bad_response"})
        print(json.dumps(out))
        if not out.get("success"):
            if out.get("code") in ("daemon_unreachable", "missing_token"):
                worst = 2
                break
            worst = 1
            if args.stop_on_error:
                break
    if opt_session and edge.session_token != opt_session:
        print(f"agent-bridge: session expired; re-minted sessionToken={edge.session_token} "
              f"(export EDGE_BRIDGE_SESSION={edge.session_token})", file=sys.stderr)
    return worst


def _handle_extension_cmd(args) -> int:
    ext_dir = config.extension_dir()
    if args.extension_action == "path":
        print(str(ext_dir))
        return 0
    elif args.extension_action == "open":
        if sys.platform == "win32":
            os.startfile(ext_dir)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(ext_dir)])
        else:
            subprocess.run(["xdg-open", str(ext_dir)])
        return 0
    return 3


def _handle_session_cmd(args, edge: Edge, opt_json: bool) -> int:
    sub = args.session_action
    if sub == "start" and not getattr(args, "new", False) and edge.session_token:
        st = edge._send_once("session_status", {})
        if st.get("success"):
            if opt_json:
                print(json.dumps({"success": True, "sessionToken": edge.session_token, **st}))
            else:
                print(f"sessionToken={edge.session_token} (existing, tab {st.get('tabId')})")
            return 0
    if sub == "start":
        name = getattr(args, "name", None)
        res = edge._send_once("session_start", {"name": name} if name else {})
        if not res.get("success"):
            if opt_json:
                print(json.dumps(res))
            else:
                print(f"Error ({res.get('code')}): {res.get('error')}", file=sys.stderr)
            return 1
        if opt_json:
            print(json.dumps(res))
        else:
            print(f"sessionToken={res['sessionToken']}")
            print(f"name: {res.get('name', '?')}")
            print(f"export EDGE_BRIDGE_SESSION={res['sessionToken']}")
        return 0
    if sub == "list":
        res = edge._send_once("session_list", {})
        if opt_json:
            print(json.dumps(res))
        else:
            if res.get("success"):
                items = res.get("sessions", [])
                if not items:
                    print("No sessions")
                for s in items:
                    if s.get("tabClosed"):
                        state = "closed"
                    elif s.get("tabId") is None:
                        state = "unpinned"
                    else:
                        state = f"tab {s.get('tabId')}"
                    print(f"  {s.get('name')}  {state}  age {s.get('age_s')}s")
            else:
                print(f"Error ({res.get('code')}): {res.get('error')}", file=sys.stderr)
        return 0 if res.get("success") else 1
    if sub == "prune":
        res = edge._send_once("session_prune", {})
        if opt_json:
            print(json.dumps(res))
        else:
            if res.get("success"):
                print(f"Pruned {res.get('dropped', 0)} closed session(s)")
            else:
                print(f"Error ({res.get('code')}): {res.get('error')}", file=sys.stderr)
        return 0 if res.get("success") else 1
    if not edge.session_token:
        msg = "no session: pass --session TOKEN or set EDGE_BRIDGE_SESSION"
        if opt_json:
            print(json.dumps({"success": False, "code": "no_session", "error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 3
    res = edge._send_once("session_status" if sub == "status" else "session_stop", {})
    if opt_json:
        print(json.dumps(res))
    else:
        if res.get("success"):
            if sub == "status":
                print(f"Session {res.get('name', '?')}: tab {res.get('tabId')} "
                      f"(closed: {res.get('tabClosed')}, age {res.get('age_s')}s)")
            else:
                print("Session stopped")
        else:
            print(f"Error ({res.get('code')}): {res.get('error')}", file=sys.stderr)
    return 0 if res.get("success") else 1


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output raw result as single JSON line")
    common.add_argument("--no-highlight", action="store_true", default=argparse.SUPPRESS, help="Disable visual highlight ring and cursor")
    common.add_argument("--tab", type=int, default=argparse.SUPPRESS, help="Target specific tab ID")
    common.add_argument("--port", type=int, default=argparse.SUPPRESS, help="Bridge port override")
    common.add_argument("--session", default=argparse.SUPPRESS, help="Daemon session token (or EDGE_BRIDGE_SESSION)")
    common.add_argument("--no-fallback", action="store_true", default=argparse.SUPPRESS, help="Disable ref→text→scan→coords fallback ladder")

    parser = argparse.ArgumentParser(
        prog="agent-bridge",
        parents=[common],
        description="Agent Browser Bridge CLI for driving the browser you already have open.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="cmd", help="Command to run")

    def add_cmd(name, **kwargs):
        kwargs.setdefault("parents", [common])
        return subparsers.add_parser(name, **kwargs)

    # ping
    add_cmd("ping", help="Ping daemon/extension")

    # status
    add_cmd("status", help="Get daemon and extension status")

    # doctor
    add_cmd("doctor", help="Diagnose daemon, extension, token, sessions and MCP registration")

    # logs
    p_logs = add_cmd("logs", help="Show daemon log")
    p_logs.add_argument("--tail", type=int, default=50)
    p_logs.add_argument("--follow", action="store_true")

    # run
    p_run = add_cmd("run", help="Execute a JSON file of {action, params} steps over one connection")
    p_run.add_argument("steps_file")
    p_run.add_argument("--stop-on-error", action="store_true")

    # daemon
    p_daemon = add_cmd("daemon", help="Manage daemon process")
    p_daemon.add_argument("daemon_action", choices=["start", "stop", "restart", "status"])
    p_daemon.add_argument("--require-pairing", action="store_true", help="Require pairing token")
    p_daemon.add_argument("--force", action="store_true",
                          help="stop/restart: kill the PID in bridge.pid even if /status does not confirm it")


    # tabs
    add_cmd("tabs", help="List open tabs")

    # tab
    add_cmd("tab", help="Get active or pinned tab")

    # switch
    p_switch = add_cmd("switch", help="Switch to tab ID")
    p_switch.add_argument("tab_id", type=int)

    # new
    p_new = add_cmd("new", help="Open new tab")
    p_new.add_argument("url", nargs="?", default="about:blank")
    p_new.add_argument("--group", default=None)
    p_new.add_argument("--window", action="store_true")
    p_new.add_argument("--background", action="store_true")

    # close
    add_cmd("close", help="Close tab (requires --tab ID)")

    # session
    p_session = add_cmd("session", help="Manage daemon tab sessions")
    p_session.add_argument("session_action", choices=["start", "status", "stop", "list", "prune"])
    p_session.add_argument("--new", action="store_true", help="session start: always mint a fresh token")
    p_session.add_argument("--name", default=None, help="session start: pin a name to the session")

    # history
    p_history = add_cmd("history", help="Search or delete browsing history")
    p_history.add_argument("history_action", choices=["search", "delete"])
    p_history.add_argument("query", nargs="?", default="", help="Search text or URL to delete")
    p_history.add_argument("--max-results", type=int, default=20, help="history search: max results (default 20)")
    p_history.add_argument("--start-time", type=float, default=None, help="history search: only items after this time (ms since epoch)")
    p_history.add_argument("--end-time", type=float, default=None, help="history search: only items before this time (ms since epoch)")

    # group
    p_group = add_cmd("group", help="List or manage tab groups")
    p_group.add_argument("group_action", choices=["list", "move", "ungroup"])
    p_group.add_argument("tabs", nargs="*", default=[], help="Tab IDs to move or ungroup")
    p_group.add_argument("--group-id", type=int, default=None, help="group move: existing group ID (omit for a new group)")
    p_group.add_argument("--title", default=None, help="group move: title for the group")
    p_group.add_argument("--color", default=None, help="group move: color (grey, blue, red, yellow, green, pink, purple, cyan, orange)")
    p_group.add_argument("--window-id", type=int, default=None, help="group list: only this window")

    # nav
    p_nav = add_cmd("nav", help="Navigate to URL")
    p_nav.add_argument("url")
    p_nav.add_argument("--no-wait", action="store_true")

    # back / forward / reload
    p_back = add_cmd("back", help="History back")
    p_back.add_argument("--no-wait", action="store_true")
    p_fwd = add_cmd("forward", help="History forward")
    p_fwd.add_argument("--no-wait", action="store_true")
    p_rel = add_cmd("reload", help="Reload page")
    p_rel.add_argument("--no-wait", action="store_true")

    # snapshot
    p_snap = add_cmd("snapshot", help="Take semantic accessibility snapshot")
    p_snap.add_argument("--full", action="store_true", help="Include static text")
    p_snap.add_argument("--compact", action="store_true", help="One terse line per ref, no values")
    p_snap.add_argument("--no-frames", action="store_true", help="Skip subframes")
    p_snap.add_argument("--max-nodes", type=int, default=400, help="Truncate after N nodes (0 = unlimited)")

    # elements
    p_el = add_cmd("elements", help="List interactive elements with coordinates and refs")
    p_el.add_argument("filter", nargs="?", default=None)

    # click / dblclick / rightclick / hover
    for act in ("click", "dblclick", "rightclick", "hover"):
        p_act = add_cmd(act, help=f"{act.capitalize()} on element")
        p_act.add_argument("target", nargs="?", default=None)
        p_act.add_argument("--x", type=float, default=None)
        p_act.add_argument("--y", type=float, default=None)
        p_act.add_argument("--button", default="left")
        p_act.add_argument("--ref", default=None)

    # drag
    p_drag = add_cmd("drag", help="Drag from one target to another")
    p_drag.add_argument("from_target")
    p_drag.add_argument("to_target")
    p_drag.add_argument("--steps", type=int, default=10)

    # fill
    p_fill = add_cmd("fill", help="Fill input or textarea")
    p_fill.add_argument("target")
    p_fill.add_argument("text")
    p_fill.add_argument("--append", action="store_true")
    p_fill.add_argument("--no-clear", action="store_true")

    # type
    p_type = add_cmd("type", help="Type text character by character")
    p_type.add_argument("text")
    p_type.add_argument("--delay", type=int, default=20)
    p_type.add_argument("--ref", default=None)
    p_type.add_argument("--target", default=None)

    # key
    p_key = add_cmd("key", help="Send keyboard key (e.g. Enter, Tab, Ctrl+A)")
    p_key.add_argument("key")

    # check-radio
    p_chk = add_cmd("check-radio", help="Check a radio button or checkbox")
    p_chk.add_argument("target", nargs="?", default=None, help="Label text, selector, or value")
    p_chk.add_argument("--ref", default=None)
    p_chk.add_argument("--selector", "-s", default=None)
    p_chk.add_argument("--text", default=None)
    p_chk.add_argument("--value", "-v", default=None)

    # select
    p_sel = add_cmd("select", help="Select option in dropdown")
    p_sel.add_argument("target")
    p_sel.add_argument("--value", default=None)
    p_sel.add_argument("--label", default=None)

    # upload
    p_up = add_cmd("upload", help="Upload files to file input")
    p_up.add_argument("target")
    p_up.add_argument("files", nargs="+")

    # scroll
    p_scroll = add_cmd("scroll", help="Scroll viewport or element")
    p_scroll.add_argument("--x", type=float, default=None)
    p_scroll.add_argument("--y", type=float, default=None)
    p_scroll.add_argument("--ref", default=None)
    p_scroll.add_argument("--target", default=None)

    # wait
    p_wait = add_cmd("wait", help="Wait for condition")
    p_wait.add_argument("--text", default=None)
    p_wait.add_argument("--selector", default=None)
    p_wait.add_argument("--ref", default=None)
    p_wait.add_argument("--url", default=None)
    p_wait.add_argument("--load", action="store_true")
    p_wait.add_argument("--idle", action="store_true")
    p_wait.add_argument("--timeout", type=int, default=5000)

    # text
    p_txt = add_cmd("text", help="Get text content of target")
    p_txt.add_argument("target")

    # eval
    p_eval = add_cmd("eval", help="Evaluate JavaScript expression in tab")
    p_eval.add_argument("code", nargs="?", default=None)
    p_eval.add_argument("--file", default=None, help="Read JS from file ('-' for stdin)")

    # screenshot
    p_ss = add_cmd("screenshot", help="Capture screenshot")
    p_ss.add_argument("path", nargs="?", default=None)
    p_ss.add_argument("--png", action="store_true")
    p_ss.add_argument("--quality", type=int, default=80)
    p_ss.add_argument("--clip", default=None)
    p_ss.add_argument("--of", default=None)

    # console
    p_con = add_cmd("console", help="Read console logs")
    p_con.add_argument("--clear", action="store_true")

    # dialog
    p_diag = add_cmd("dialog", help="Set dialog policy")
    p_diag.add_argument("policy", choices=["accept", "dismiss", "manual"])
    p_diag.add_argument("--prompt", default=None)

    # batch
    p_batch = add_cmd("batch", help="Execute batch steps")
    p_batch.add_argument("steps")

    # extension
    p_ext = add_cmd("extension", help="Extension paths and options")
    p_ext.add_argument("extension_action", choices=["path", "open"])

    # mcp
    add_cmd("mcp", help="Run MCP stdio server")

    # mcp-config
    p_mcp_cfg = add_cmd("mcp-config", help="Print MCP client configuration")
    p_mcp_cfg.add_argument("--client", required=True, choices=["claude", "cursor", "windsurf", "gemini", "codex", "vscode"])

    # setup
    p_setup = add_cmd("setup", help="Auto-configure detected MCP clients")
    p_setup.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    p_setup.add_argument("--fix-path", action="store_true", help="Append the scripts dir to the user PATH (Windows)")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 3

    opt_json = getattr(args, "json", False)
    opt_tab = getattr(args, "tab", None)
    opt_no_highlight = getattr(args, "no_highlight", False)
    opt_port = getattr(args, "port", None)
    opt_session = getattr(args, "session", None) or os.environ.get("EDGE_BRIDGE_SESSION")

    # Special handling: history delete without a URL
    if args.cmd == "history" and args.history_action == "delete" and not args.query:
        if opt_json:
            print(json.dumps({"success": False, "code": "bad_params", "error": "history delete requires a URL"}))
        else:
            print("Error: 'history delete' requires a URL", file=sys.stderr)
        return 3

    # Special handling: group move/ungroup requires integer tab IDs
    if args.cmd == "group" and args.group_action in ("move", "ungroup"):
        if not args.tabs:
            if opt_json:
                print(json.dumps({"success": False, "code": "bad_params", "error": f"group {args.group_action} requires tab IDs"}))
            else:
                print(f"Error: 'group {args.group_action}' requires tab IDs", file=sys.stderr)
            return 3
        try:
            for t in args.tabs:
                int(t)
        except ValueError:
            if opt_json:
                print(json.dumps({"success": False, "code": "bad_params", "error": f"group {args.group_action} tab IDs must be integers"}))
            else:
                print(f"Error: 'group {args.group_action}' tab IDs must be integers", file=sys.stderr)
            return 3

    # Special handling: close without --tab
    if args.cmd == "close" and opt_tab is None:
        if opt_json:
            print(json.dumps({"success": False, "code": "tab_required", "error": "close requires --tab ID"}))
        else:
            print("Error: 'close' requires --tab ID", file=sys.stderr)
        return 3

    port = opt_port or config.port()

    # Route special commands that don't need Edge client
    if args.cmd == "daemon":
        return _handle_daemon_cmd(args, port)
    if args.cmd == "extension":
        return _handle_extension_cmd(args)
    if args.cmd == "mcp":
        from .mcp import main as mcp_main
        return mcp_main()
    if args.cmd == "mcp-config":
        from .setup import mcp_config_cli
        return mcp_config_cli(args.client)
    if args.cmd == "setup":
        from .setup import fix_path, setup_cli
        if getattr(args, "fix_path", False):
            return fix_path(yes=getattr(args, "yes", False))
        return setup_cli(yes=getattr(args, "yes", False))
    if args.cmd == "doctor":
        return _handle_doctor(port, opt_json)
    if args.cmd == "logs":
        return _handle_logs(args, opt_json)

    edge = Edge(
        tab_id=opt_tab,
        port=port,
        highlight=not opt_no_highlight,
        pin=False,
        # A freshly started daemon has no sessions, so only session start is worth starting one for.
        auto_start=args.cmd != "status" and (args.cmd != "session" or args.session_action == "start"),
        session_token=opt_session,
    )

    if args.cmd == "session":
        return _handle_session_cmd(args, edge, opt_json)


    if args.cmd == "run":
        return _handle_run(args, edge, opt_session, opt_json, opt_tab)

    # Route status
    if args.cmd == "status":
        res = edge.status()
        sst = None
        if res.get("success") and opt_session:
            sst = edge._send_once("session_status", {})
            res["session"] = sst
        if opt_json:
            print(json.dumps(res))
        else:
            if res.get("success"):
                dae_v = res.get("daemon_version") or __version__
                ext_v = res.get("extension_version") or "none"
                print(f"CLI {__version__}, daemon {dae_v}, extension {ext_v}")
                if dae_v != __version__:
                    print("Fix: agent-bridge daemon restart")
                elif ext_v != "none" and parse_version(ext_v) < parse_version(MIN_EXTENSION_VERSION):
                    print(f"Fix: update extension from {EDGE_STORE_URL} or reload unpacked from {config.extension_dir()}")
                print(f"Daemon: running on 127.0.0.1:{port} (PID {res.get('pid', '?')})")
                print(f"Extension: {res.get('extension_version', 'none')} ({'connected' if res.get('websocket_active') else 'disconnected'})")
                if res.get("pin") is not None:
                    print(f"Pinned tab: {res['pin']}")
                if notice := _rename_notice():
                    print(notice)
                if sst is not None:
                    if sst.get("success"):
                        if sst.get("tabClosed"):
                            print(f"Session {sst.get('name', '?')}: tab {sst.get('tabId')} closed")
                        elif sst.get("tabId") is None:
                            print(f"Session {sst.get('name', '?')}: unpinned")
                        else:
                            print(f"Session {sst.get('name', '?')}: pinned to tab {sst.get('tabId')}")
                    else:
                        print("Session: expired; mint a new one with: agent-bridge session start")
            else:
                err_code = res.get("code", "error")
                err_msg = res.get("error", "Action failed")
                print(f"Error ({err_code}): {err_msg}", file=sys.stderr)
        if res.get("success"):
            return 0
        return 2 if res.get("code") in ("daemon_unreachable", "missing_token") else 1

    # Map command to action and params
    action = args.cmd
    params = {}
    if opt_tab is not None:
        params["tabId"] = opt_tab
    if getattr(args, "no_fallback", False):
        params["fallback"] = False

    if action == "ping":
        pass
    elif action == "tabs":
        pass
    elif action == "tab":
        pass
    elif action == "switch":
        action = "tab_switch"
        params["tabId"] = args.tab_id
    elif action == "new":
        action = "tab_new"
        params["url"] = args.url
        params["active"] = not args.background
        params["window"] = args.window
        if args.group:
            params["group"] = args.group
    elif action == "close":
        action = "tab_close"
        params["tabId"] = opt_tab
    elif action == "history":
        if args.history_action == "search":
            action = "history_search"
            params["text"] = args.query or ""
            params["maxResults"] = args.max_results
            if args.start_time is not None:
                params["startTime"] = args.start_time
            if args.end_time is not None:
                params["endTime"] = args.end_time
        else:
            action = "history_delete"
            params["url"] = args.query
    elif action == "group":
        if args.group_action == "list":
            action = "group_list"
            if args.window_id is not None:
                params["windowId"] = args.window_id
        elif args.group_action == "move":
            action = "group_move"
            params["tabIds"] = [int(t) for t in args.tabs]
            if args.group_id is not None:
                params["groupId"] = args.group_id
            if args.title is not None:
                params["title"] = args.title
            if args.color is not None:
                params["color"] = args.color
        else:
            action = "group_ungroup"
            params["tabIds"] = [int(t) for t in args.tabs]
    elif action == "nav":
        params["url"] = args.url
        params["wait"] = "none" if args.no_wait else "load"
    elif action in ("back", "forward", "reload"):
        params["wait"] = "none" if args.no_wait else "load"
    elif action == "snapshot":
        if args.compact:
            params["mode"] = "compact"
        else:
            params["mode"] = "full" if args.full else "interactive"
        params["frames"] = not args.no_frames
        params["maxNodes"] = args.max_nodes
    elif action == "elements":
        if args.filter:
            params["filter"] = args.filter
    elif action in ("click", "dblclick", "rightclick", "hover"):
        if args.ref:
            params["ref"] = args.ref
        elif args.target:
            params["target"] = args.target
        elif args.x is not None and args.y is not None:
            params["x"] = args.x
            params["y"] = args.y
        if hasattr(args, "button"):
            params["button"] = args.button
    elif action == "drag":
        params["from"] = args.from_target
        params["to"] = args.to_target
        params["steps"] = args.steps
    elif action == "fill":
        params["target"] = args.target
        params["text"] = args.text
        params["append"] = args.append
        params["clear"] = not args.no_clear
    elif action == "type":
        params["text"] = args.text
        params["delay"] = args.delay
        if args.ref:
            params["ref"] = args.ref
        elif args.target:
            params["target"] = args.target
    elif action == "key":
        params["key"] = args.key
    elif action == "check-radio":
        action = "check_radio"
        if args.ref:
            params["ref"] = args.ref
        elif args.selector:
            params["selector"] = args.selector
        elif args.target:
            params["target"] = args.target
        if args.text:
            params["text"] = args.text
        if args.value is not None:
            params["value"] = args.value
    elif action == "select":
        params["target"] = args.target
        if args.value:
            params["value"] = args.value
        if args.label:
            params["label"] = args.label
    elif action == "scroll":
        if args.x is not None:
            params["x"] = args.x
        if args.y is not None:
            params["y"] = args.y
        if args.ref:
            params["ref"] = args.ref
        elif args.target:
            params["target"] = args.target
    elif action == "wait":
        params["timeout"] = args.timeout
        if args.text:
            params["text"] = args.text
        elif args.selector:
            params["selector"] = args.selector
        elif args.ref:
            params["ref"] = args.ref
        elif args.url:
            params["url"] = args.url
        elif args.load:
            params["load"] = True
        elif args.idle:
            params["idle"] = True
    elif action == "text":
        params["target"] = args.target
    elif action == "eval":
        if args.file:
            if args.file == "-":
                try:
                    params["code"] = _read_stdin_text()
                except (OSError, UnicodeDecodeError) as e:
                    if opt_json:
                        print(json.dumps({"success": False, "code": "bad_params", "error": f"cannot read stdin: {e}"}))
                    else:
                        print(f"Error: cannot read stdin: {e}", file=sys.stderr)
                    return 3
            else:
                try:
                    params["code"] = Path(args.file).read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError) as e:
                    code = "bad_params" if isinstance(e, UnicodeDecodeError) else "file_not_found"
                    if opt_json:
                        print(json.dumps({"success": False, "code": code, "error": str(e)}))
                    else:
                        print(f"Error: cannot read --file: {e}", file=sys.stderr)
                    return 3
        elif args.code is not None:
            params["code"] = args.code
        else:
            if opt_json:
                print(json.dumps({"success": False, "code": "bad_params", "error": "eval needs code or --file"}))
            else:
                print("Error: 'eval' needs code or --file", file=sys.stderr)
            return 3
    elif action == "screenshot":
        params["format"] = "png" if args.png else "jpeg"
        params["quality"] = args.quality
        if args.clip:
            try:
                parts = [float(x) for x in args.clip.split(",")]
                params["clip"] = {"x": parts[0], "y": parts[1], "width": parts[2], "height": parts[3]}
            except Exception:
                pass
        if args.of:
            params["of"] = args.of
    elif action == "console":
        params["clear"] = args.clear
    elif action == "dialog":
        params["policy"] = args.policy
        if args.prompt:
            params["promptText"] = args.prompt
    elif action == "batch":
        try:
            params["steps"] = json.loads(args.steps)
        except Exception as e:
            if opt_json:
                print(json.dumps({"success": False, "code": "bad_params", "error": f"Invalid JSON in batch: {e}"}))
            else:
                print(f"Error: Invalid JSON for batch: {e}", file=sys.stderr)
            return 3

    if action == "upload":
        res = edge.upload(target=args.target, files=args.files, tab_id=opt_tab,
                          fallback=not getattr(args, "no_fallback", False))
    else:
        res = edge.send(action, params)
    if opt_session and edge.session_token != opt_session:
        print(f"agent-bridge: session expired; re-minted sessionToken={edge.session_token} "
              f"(export EDGE_BRIDGE_SESSION={edge.session_token})", file=sys.stderr)

    # If screenshot with path, save file
    if action == "screenshot" and getattr(args, "path", None) and res.get("success") and (res.get("dataUrl") or res.get("data")):
        data_str = res.get("dataUrl") or res["data"]
        if "," in data_str:
            data_str = data_str.split(",", 1)[1]
        import base64
        b = base64.b64decode(data_str)
        out_p = Path(args.path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(b)
        res["path"] = str(out_p.resolve())

    if opt_json:
        print(json.dumps(res))
    else:
        # Human-friendly output
        if res.get("success"):
            if action == "snapshot":
                print(res.get("text", ""))
            elif action == "elements":
                els = res.get("elements", [])
                print(f"Found {len(els)} interactive element(s):")
                for e in els:
                    ref = f"[{e.get('ref')}]" if e.get("ref") else ""
                    print(f"  {ref} {e.get('tag', '')} {e.get('text', '')[:40]} ({e.get('x')},{e.get('y')})")
            elif action == "tabs":
                tabs = res.get("tabs", [])
                print(f"{len(tabs)} tab(s):")
                for t in tabs:
                    act = "*" if t.get("active") else " "
                    print(f"  {act} [{t.get('id')}] {t.get('title', '')[:50]} - {t.get('url')}")
            elif action == "eval":
                if "result" in res:
                    v = res["result"]
                    print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            elif action == "text":
                print(res.get("text", ""))
            elif action == "screenshot":
                if res.get("path"):
                    print(f"Saved screenshot: {res['path']}")
                else:
                    print(f"Screenshot taken ({len(res.get('dataUrl', ''))} bytes base64)")
            elif action == "history_search":
                items = res.get("items", [])
                print(f"Found {len(items)} history item(s):")
                for it in items:
                    print(f"  {it.get('title', '')[:50]} - {it.get('url')}")
            elif action == "group_list":
                groups = res.get("groups", [])
                print(f"{len(groups)} group(s):")
                for g in groups:
                    print(f"  [{g.get('id')}] {g.get('title', '')} ({g.get('color', '')})")
            elif action == "console":
                entries = res.get("entries", [])
                print(f"{len(entries)} console entry(ies):")
                for item in entries:
                    print(f"  [{item.get('level')}] {item.get('text')}")
            else:
                tab_info = res.get("tab")
                tab_str = f" (tab {tab_info['id']})" if isinstance(tab_info, dict) and "id" in tab_info else ""
                ladder = res.get("ladder")
                matched = ladder.get("matched") if isinstance(ladder, dict) else None
                if isinstance(matched, dict):
                    print(f'OK{tab_str} via fallback: {action} "{matched.get("name", "")}"')
                elif isinstance(ladder, dict):
                    print(f"OK{tab_str} via fallback ({ladder.get('rung')})")
                else:
                    print(f"OK{tab_str}")
                if action in ("type", "fill") and res.get("match") is False:
                    print(f"Warning (write_mismatch): wrote {res.get('written')!r}, read back {res.get('readback')!r}",
                          file=sys.stderr)
                elif action in ("type", "fill") and res.get("match") == "unknown":
                    print("Warning: write not verified", file=sys.stderr)
        else:
            err_code = res.get("code", "error")
            err_msg = res.get("error", "Action failed")
            print(f"Error ({err_code}): {err_msg}", file=sys.stderr)

    # Determine exit code
    if res.get("success"):
        # A landed-but-wrong write must stop `&&` chains.
        return 1 if action in ("type", "fill") and res.get("match") is False else 0
    code = res.get("code")
    if code in ("daemon_unreachable", "missing_token"):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
