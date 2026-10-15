import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
from tests.fake_extension import FakeExtension  # noqa: E402
from tests.support import DaemonHandle, free_port  # noqa: E402,F401


@pytest.fixture
def daemon(tmp_path):
    handle = DaemonHandle(free_port(), tmp_path).start()
    yield handle
    handle.stop()


@pytest.fixture
def fake_ext(daemon):
    ext = FakeExtension(daemon.port).connect().run()
    deadline = time.time() + 5
    while time.time() < deadline and not daemon.status().get("websocket_active"):
        time.sleep(0.05)
    if not daemon.status().get("websocket_active"):
        ext.close()
        raise RuntimeError("fake extension never registered with the daemon")
    yield ext
    ext.close()


@pytest.fixture
def patient_daemon(tmp_path):
    """Daemon whose pong timeout outlasts a slow fake-extension handler: the fake answers pings
    on the thread that runs the handler, so the 3 s default would drop it mid-command."""
    handle = DaemonHandle(free_port(), tmp_path).start(extra_env={"EDGE_BRIDGE_PONG_TIMEOUT": "120"})
    yield handle
    handle.stop()
