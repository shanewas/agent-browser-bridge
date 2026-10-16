"""CI helper: print the Edge binary path, installing Edge first on Linux/macOS runners that lack it."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import edge_binary  # noqa: E402


def install():
    # shell=True on purpose: these are constant pipelines with no external input.
    if sys.platform.startswith("linux"):
        cmds = [
            "curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-edge.gpg",
            "echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-edge.gpg] https://packages.microsoft.com/repos/edge stable main' | sudo tee /etc/apt/sources.list.d/microsoft-edge.list",
            "sudo apt-get update -qq",
            "sudo apt-get install -y -qq microsoft-edge-stable",
            "sudo chmod 4755 /opt/microsoft/msedge/msedge-sandbox 2>/dev/null || true",
        ]
        for c in cmds:
            subprocess.run(c, shell=True, check=True)
    elif sys.platform == "darwin":
        subprocess.run(["brew", "install", "--cask", "microsoft-edge"], check=True)
    else:
        raise SystemExit("Edge is part of the Windows runner image; nothing to install")


def main():
    path = edge_binary()
    if not path:
        install()
        path = edge_binary()
    if not path:
        raise SystemExit("Microsoft Edge not found after install attempt")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
