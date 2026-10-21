"""The edge-agent-bridge compatibility shim: owns only the old paths and re-exports the new package."""
import subprocess
import sys
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")
ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "shim"


def test_shim_metadata():
    p = tomllib.loads((SHIM / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert p["name"] == "edge-agent-bridge" and p["version"] == "3.0.0"
    assert p["dependencies"] == ["agent-browser-bridge>=3.0.0"]
    assert p["scripts"] == {"edge-bridge": "agent_browser_bridge.cli:main", "edge": "agent_browser_bridge.cli:main"}


def test_new_dist_owns_only_new_paths():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert list(p["project"]["scripts"]) == ["agent-bridge"]
    assert p["tool"]["setuptools"]["packages"]["find"]["include"] == ["agent_browser_bridge*"]
    assert not (ROOT / "edge_agent_bridge").exists()


def test_shim_reexports_public_api(tmp_path):
    code = ("import sys; sys.path[:0] = [%r, %r]; import edge_agent_bridge as e, agent_browser_bridge as a; "
            "assert e.Edge is a.Edge and e.__version__ == a.__version__ and e.__all__ == a.__all__; print('ok')" % (str(SHIM), str(ROOT)))
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, encoding="utf-8", errors="replace")
    assert out.stdout.strip() == "ok", out.stderr


def test_shim_main_runs_cli(tmp_path):
    code = ("import sys, runpy; sys.path[:0] = [%r, %r]; sys.argv = ['edge-bridge', '--help']; "
            "runpy.run_module('edge_agent_bridge', run_name='__main__')" % (str(SHIM), str(ROOT)))
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, encoding="utf-8", errors="replace")
    assert out.returncode == 0 and "usage: agent-bridge" in out.stdout
