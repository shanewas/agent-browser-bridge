import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "agent_browser_bridge" / "extension"


@pytest.fixture
def ext_copy(tmp_path):
    dst = tmp_path / "ext"
    shutil.copytree(EXT, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def run_build(*args):
    return subprocess.run([sys.executable, "scripts/build_extension.py", *args], cwd=ROOT, capture_output=True, text=True)


def test_build_syncs_manifest_and_zips(tmp_path, ext_copy):
    manifest = ext_copy / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = "0.0.0"
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    out = run_build("--ext", str(ext_copy), "--dist", str(tmp_path / "dist"))
    assert out.returncode == 0, out.stderr
    from agent_browser_bridge import __version__
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == __version__
    z = tmp_path / "dist" / f"edge-agent-bridge-extension-{__version__}.zip"
    assert z.exists()
    names = zipfile.ZipFile(z).namelist()
    assert "manifest.json" in names and "background.js" in names
    assert not any(n.endswith(".pyc") for n in names)


def test_check_mode_detects_drift(ext_copy):
    manifest = ext_copy / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = "0.0.0"
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out = run_build("--check", "--ext", str(ext_copy))
    assert out.returncode == 1
    assert "0.0.0" in out.stderr


def test_repo_manifest_in_sync():
    out = run_build("--check")
    assert out.returncode == 0, out.stderr


def test_store_flag_sets_name_and_keeps_fields_within_limits(tmp_path, ext_copy):
    from agent_browser_bridge import __version__
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    import build_extension as be

    for store, expected in be.STORE_NAME.items():
        out = run_build("--store", store, "--ext", str(ext_copy), "--dist", str(tmp_path / "dist"))
        assert out.returncode == 0, out.stderr
        data = json.loads((ext_copy / "manifest.json").read_text(encoding="utf-8"))
        assert data["name"] == expected
        assert data["action"]["default_title"] == expected
        # Chrome rejects a description over 132 characters and a name over 45.
        assert len(data["description"]) <= be.MAX_DESCRIPTION
        assert len(data["name"]) <= be.MAX_NAME
        suffix = "" if store == "edge" else f"-{store}"
        assert (tmp_path / "dist" / f"edge-agent-bridge-extension{suffix}-{__version__}.zip").exists()


def test_check_rejects_an_overlong_description(ext_copy):
    manifest = ext_copy / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["description"] = "x" * 200
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out = run_build("--check", "--ext", str(ext_copy))
    assert out.returncode == 1
    assert "132" in out.stderr


def test_popup_error_box_and_actions_wired():
    html = (EXT / "popup.html").read_text(encoding="utf-8")
    assert "edge-bridge status" not in html
    assert "microsoftedge.microsoft.com/addons/detail/agent-browser-bridge" in html
    for el in ("errSection", "errBox", "reconnectBtn", "diagBtn", "actionMsg"):
        assert f'id="{el}"' in html
    js = (EXT / "popup.js").read_text(encoding="utf-8")
    assert "lastError" in js and "FIXES" in js
    diag = js.split('getElementById("diagBtn")')[1].split("updateUI();")[0]
    for key in ("daemon_version", "extension_version", "extension_id",
                "websocket_active", "pairing_required", "data_dir"):
        assert key in diag
    assert '"token"' not in diag and '"url"' not in diag and "tabInfo" not in diag
    bg = (EXT / "background.js").read_text(encoding="utf-8")
    assert "lastError" in bg


def test_onboarding_page_and_install_hook_wired():
    html_path = EXT / "onboarding.html"
    js_path = EXT / "onboarding.js"
    assert html_path.exists()
    assert js_path.exists()

    html = html_path.read_text(encoding="utf-8")
    assert "onboarding.js" in html
    assert "py -m agent_browser_bridge" in html

    js = js_path.read_text(encoding="utf-8")
    assert "18999/status" in js
    assert "extension_connected" in js
    assert "extension_outdated" in js

    bg = (EXT / "background.js").read_text(encoding="utf-8")
    assert "onInstalled" in bg
    assert "onboarding.html" in bg
