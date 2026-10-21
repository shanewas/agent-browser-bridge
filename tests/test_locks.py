import threading
import time
from agent_browser_bridge import bridge
from tests.fake_extension import FakeExtension
from tests.support import DaemonHandle, free_port


def _state():
    return bridge.BridgeState(port=1, home=None, token="t")


def test_same_tab_same_lock_cross_tab_distinct():
    st = _state()
    with st.lock:
        a = st.tab_lock_locked(5)
        b = st.tab_lock_locked(5)
        c = st.tab_lock_locked(6)
    assert a is b and a is not c


def test_lock_evict_and_idle_expiry():
    st = _state()
    with st.lock:
        a = st.tab_lock_locked(5)
        st.tab_lock_evict_locked(5)
        b = st.tab_lock_locked(5)
    assert a is not b
    with st.lock:
        st.tab_locks[5][1] = time.monotonic() - 3700.0
        st.tab_lock_locked(6)
        assert 5 not in st.tab_locks


def test_same_tab_serial_via_daemon(daemon):
    started = threading.Event()
    release = threading.Event()
    received_n = []

    def handler(action, params):
        received_n.append(params.get("tabId"))
        if len(received_n) == 1:
            started.set()
            assert release.wait(timeout=10)
        return {"success": True}

    ext = FakeExtension(daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    out = []

    def call(tid):
        out.append(daemon.exec("click", {"target": "b", "tabId": tid}, timeout=15))

    t1 = threading.Thread(target=call, args=(5,))
    t1.start()
    assert started.wait(timeout=10)
    t2 = threading.Thread(target=call, args=(5,))
    t2.start()
    time.sleep(0.5)
    assert len(received_n) == 1  # second same-tab call still queued on the lock
    release.set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert all(code == 200 for code, _ in out)
    ext.close()


def test_tab_busy_when_lock_held(tmp_path):
    port = free_port()
    handle = DaemonHandle(port, tmp_path)
    handle.start(extra_env={"EDGE_BRIDGE_LOCK_TIMEOUT": "1"})
    try:
        release = threading.Event()

        def handler(action, params):
            if params.get("target") == "block":
                assert release.wait(timeout=15)
            return {"success": True}

        ext = FakeExtension(port, handler=handler).connect().run()
        time.sleep(0.3)
        t = threading.Thread(target=lambda: handle.exec("click", {"target": "block", "tabId": 5}, timeout=15))
        t.start()
        time.sleep(0.5)
        start = time.monotonic()
        code, body = handle.exec("click", {"target": "b", "tabId": 5}, timeout=10)
        elapsed = time.monotonic() - start
        assert code == 200 and body["code"] == "tab_busy"
        assert elapsed < 5
        release.set()
        t.join(timeout=15)
        ext.close()
    finally:
        handle.stop()


def test_lock_wait_counts_against_the_call_timeout(patient_daemon):
    # The call's timeout started only once the tab lock was free, so a call queued behind another
    # could run long after its caller had stopped waiting.
    release = threading.Event()

    def handler(action, params):
        if params.get("target") == "block":
            assert release.wait(timeout=15)
        return {"success": True}

    ext = FakeExtension(patient_daemon.port, handler=handler).connect().run()
    time.sleep(0.3)
    t = threading.Thread(target=lambda: patient_daemon.exec("click", {"target": "block", "tabId": 5}, timeout=15))
    t.start()
    time.sleep(0.5)
    start = time.monotonic()
    try:
        code, body = patient_daemon.exec("click", {"target": "b", "tabId": 5}, timeout=1)
    finally:
        elapsed = time.monotonic() - start
        release.set()
        t.join(timeout=15)
        ext.close()
    assert code == 200 and body["code"] == "tab_busy", body
    assert elapsed < 3, elapsed
