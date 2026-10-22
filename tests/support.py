"""Shared helpers for hermetic and E2E tests: daemon handle, free port, CLI runner, recording client."""
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from agent_browser_bridge import config  # noqa: E402
from agent_browser_bridge.client import Edge  # noqa: E402


def edge_binary():
    """Path to Microsoft Edge on this machine, or None. EDGE_BRIDGE_E2E_EDGE overrides."""
    import shutil
    env = os.environ.get("EDGE_BRIDGE_E2E_EDGE")
    if env and Path(env).exists():
        return env
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if base:
                p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                if p.exists():
                    return str(p)
        return shutil.which("msedge")
    if sys.platform == "darwin":
        p = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
        return str(p) if p.exists() else None
    for name in ("microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta"):
        found = shutil.which(name)
        if found:
            return found
    return None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class DaemonHandle:
    def __init__(self, port, home):
        self.port, self.home = port, home
        self.proc = None

    @property
    def token(self):
        p = self.home / "token"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def _request(self, method, path, body=None, headers=None, timeout=10):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        try:
            conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
            r = conn.getresponse()
            raw = r.read().decode("utf-8")
        finally:
            conn.close()
        try:
            return r.status, json.loads(raw) if raw else {}
        except ValueError:
            return r.status, {"raw": raw}

    def status(self):
        return self._request("GET", "/status")[1]

    def exec(self, action, params=None, timeout=5, token="auto", headers=None, session_token=None):
        h = dict(headers or {})
        if token == "auto":
            h[config.TOKEN_HEADER] = self.token
        elif token is not None:
            h[config.TOKEN_HEADER] = token
        body = {"action": action, "params": params or {}, "timeout": timeout}
        if session_token is not None:
            body["sessionToken"] = session_token
        return self._request("POST", "/exec", body, headers=h, timeout=timeout + 5)

    def start(self, extra_env=None):
        env = dict(os.environ, EDGE_BRIDGE_HOME=str(self.home), EDGE_BRIDGE_PORT=str(self.port),
                   EDGE_BRIDGE_NO_OVERLAY="1",
                   EDGE_BRIDGE_HEARTBEAT="1", EDGE_BRIDGE_PONG_TIMEOUT="3", PYTHONUNBUFFERED="1")
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "agent_browser_bridge.bridge", "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if self.status().get("bridge_running"):
                    return self
            except (OSError, http.client.HTTPException, ValueError):
                # A socket that is listening but not yet serving answers with a truncated response,
                # which surfaces as BadStatusLine rather than OSError. Catching only OSError let that
                # escape the retry loop, so the fixture failed whenever the machine was loaded.
                pass
            time.sleep(0.1)
        out = self.proc.stdout.read().decode(errors="replace")[-2000:] if self.proc.poll() is not None else ""
        self.stop()
        raise RuntimeError("daemon did not start: " + out)

    def stop(self):
        if not self.proc:
            return
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)
        if self.proc.stdout:
            self.proc.stdout.close()

    def restart(self):
        self.stop()
        time.sleep(0.2)
        return self.start()

def run_cli(args, env=None, input_text=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    cmd = [sys.executable, "-u", "-m", "agent_browser_bridge.cli"] + args
    res = subprocess.run(cmd, cwd=ROOT, env=e, capture_output=True, text=True,
                         input=input_text)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def recording_edge():
    seen = {}
    edge = Edge(pin=False, auto_start=False)

    def fake_send(action, params=None, timeout=None):
        seen["action"] = action
        seen["params"] = params
        return {"success": True}

    edge.send = fake_send
    return edge, seen
