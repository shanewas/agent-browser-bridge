"""Benchmark gates BM-01..BM-09 against the headless E2E stack. Run with -s to see the scorecard."""
import base64
import http.client
import json
import os
import time

import pytest

from agent_browser_bridge import config

pytestmark = pytest.mark.e2e

SCORE = {}


def record(key, name, passed, **metrics):
    SCORE[key] = {"name": name, "pass": passed, **metrics}
    detail = " | ".join(f"{k}={v}" for k, v in metrics.items())
    print(f"\n  {key} ({name}): {'PASS' if passed else 'FAIL'} => {detail}")
    assert passed, f"{key} {name}: {detail}"


@pytest.fixture(scope="module")
def arena(e2e_daemon, edge, pages):
    code, body = e2e_daemon.exec("nav", {"url": pages + "/arena.html", "highlight": False}, timeout=20)
    assert body.get("success"), body
    for _ in range(40):
        code, r = e2e_daemon.exec("eval", {"code": "window.__domReady === true", "highlight": False})
        if r.get("result") is True:
            return body["tab"]["id"]
        time.sleep(0.2)
    pytest.fail("arena did not finish DOM setup")


@pytest.fixture
def call(e2e_daemon, arena):
    def f(action, params=None, timeout=15):
        p = dict(params or {})
        p.setdefault("highlight", False)
        p.setdefault("tabId", arena)
        return e2e_daemon.exec(action, p, timeout=timeout)[1]
    return f


def coords(call, element_id):
    r = call("eval", {"code": f"(() => {{ const r = document.getElementById('{element_id}').getBoundingClientRect(); return {{x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)}}; }})()"})
    return r["result"]["x"], r["result"]["y"]


def test_bm01_roundtrip_latency(e2e_daemon, arena):
    conn = http.client.HTTPConnection("127.0.0.1", e2e_daemon.port, timeout=10)
    headers = {"Content-Type": "application/json", config.TOKEN_HEADER: e2e_daemon.token}
    body = json.dumps({"action": "ping", "params": {}, "timeout": 5}).encode()
    lat = []
    for _ in range(200):
        t0 = time.perf_counter()
        conn.request("POST", "/exec", body=body, headers=headers)
        r = conn.getresponse()
        ok = json.loads(r.read()).get("success")
        if ok:
            lat.append((time.perf_counter() - t0) * 1000)
    conn.close()
    lat.sort()
    p50 = lat[len(lat) // 2]
    record("BM-01", "WebSocket RTT (200 pings)", len(lat) == 200, timing_ok=p50 < 20.0,
           min=f"{lat[0]:.2f}ms", p50=f"{p50:.2f}ms", p95=f"{lat[int(len(lat) * 0.95)]:.2f}ms", p99=f"{lat[int(len(lat) * 0.99)]:.2f}ms")


def test_bm02_batch_speedup(call):
    t0 = time.perf_counter()
    for i in range(50):
        call("eval", {"code": f"window.__seq = {i}"})
    seq = (time.perf_counter() - t0) * 1000
    steps = [{"action": "eval", "code": f"window.__batch = {i}"} for i in range(50)]
    t0 = time.perf_counter()
    r = call("batch", {"steps": steps})
    batched = (time.perf_counter() - t0) * 1000
    speedup = seq / max(batched, 0.01)
    record("BM-02", "Batching speedup (50 evals)", r.get("success") is True, timing_ok=speedup >= 3.0,
           sequential=f"{seq:.1f}ms", batched=f"{batched:.1f}ms", speedup=f"{speedup:.1f}x")


def test_bm03_click_burst_trusted(call):
    call("eval", {"code": "clickCnt = 0; trustedCnt = 0; document.getElementById('click-count').textContent = '0'; document.getElementById('trusted-count').textContent = '0';"})
    x, y = coords(call, "bm-click-btn")
    t0 = time.perf_counter()
    for _ in range(100):
        call("click", {"x": x, "y": y})
    burst = (time.perf_counter() - t0) * 1000
    time.sleep(0.3)
    res = call("eval", {"code": "({clicks: clickCnt, trusted: trustedCnt})"})["result"]
    record("BM-03", "Click burst (100 clicks)", res["clicks"] == 100 and res["trusted"] == 100,
           clicks=res["clicks"], trusted=res["trusted"], rate=f"{100 / (burst / 1000):.1f}/s")


def test_bm04_typing_integrity(call):
    payload = "EdgeAgent 日本語入力テスト 12345 !@#$%^&*()_+-=[]{}|;':,./<>? 🚀"
    t0 = time.perf_counter()
    r = call("fill", {"target": "#bm-type-input", "text": payload})
    ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.2)
    got = call("eval", {"code": "document.getElementById('bm-type-input').value"})["result"]
    record("BM-04", "Typing integrity (unicode)", r.get("success") is True and got == payload,
           expected=len(payload), actual=len(got or ""), time=f"{ms:.1f}ms")


def test_bm05_css_hover_cascade(call):
    pre = call("eval", {"code": "getComputedStyle(document.getElementById('menu-sub1')).display"})["result"]
    call("hover", {"target": "#hover-root-btn", "duration": 200})
    s1 = call("eval", {"code": "getComputedStyle(document.getElementById('menu-sub1')).display"})["result"]
    call("hover", {"target": "#hover-nested-item", "duration": 200})
    s2 = call("eval", {"code": "getComputedStyle(document.getElementById('menu-sub2')).display"})["result"]
    call("click", {"target": "#hover-target-final"})
    time.sleep(0.2)
    clicked = call("eval", {"code": "document.getElementById('hover-click-result').textContent"})["result"]
    record("BM-05", "CSS :hover cascade", pre == "none" and s1 == "block" and s2 == "block" and clicked == "YES",
           initial=pre, sub1=s1, sub2=s2, clicked=clicked)


def test_bm06_native_drag(call):
    sx, sy = coords(call, "drag-src")
    tx, ty = coords(call, "drag-tgt")
    t0 = time.perf_counter()
    r = call("drag", {"fromX": sx, "fromY": sy, "toX": tx, "toY": ty, "steps": 15})
    ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.2)
    dropped = call("eval", {"code": "document.getElementById('drag-tgt').getAttribute('data-dropped')"})["result"]
    record("BM-06", "CDP drag and drop", r.get("success") is True and dropped == "true", dropped=dropped, time=f"{ms:.1f}ms")


def test_bm07_dom_scan(call):
    t0 = time.perf_counter()
    r = call("elements")
    ms = (time.perf_counter() - t0) * 1000
    n = len(r.get("elements", []))
    record("BM-07", "Element scan (3k nodes)", r.get("success") is True and n >= 1500, timing_ok=ms < 600.0,
           elements=n, time=f"{ms:.1f}ms", gate="< 600ms")


def test_bm08_screenshot_throughput(call):
    times, sizes = [], []
    for _ in range(5):
        t0 = time.perf_counter()
        r = call("screenshot", {"format": "jpeg", "quality": 80})
        dt = (time.perf_counter() - t0) * 1000
        if r.get("success"):
            times.append(dt)
            sizes.append(len(base64.b64decode(r["dataUrl"].split(",", 1)[1])))
    avg = sum(times) / len(times) if times else 999.0
    record("BM-08", "Screenshot throughput (5 jpeg)", len(times) == 5 and min(sizes) > 5000, timing_ok=avg < 300.0,
           avg=f"{avg:.1f}ms", avg_kb=f"{sum(sizes) / len(sizes) / 1024:.1f}")


def test_bm09_tabs(call):
    t0 = time.perf_counter()
    r = call("tabs")
    ms = (time.perf_counter() - t0) * 1000
    active = [t for t in r.get("tabs", []) if t.get("active")]
    record("BM-09", "Tab query", r.get("success") is True and len(active) >= 1, tabs=len(r.get("tabs", [])), time=f"{ms:.1f}ms")


def test_scorecard():
    print("\n  SCORECARD: " + ", ".join(f"{k}={'PASS' if v['pass'] else 'FAIL'}" for k, v in SCORE.items()))
    assert all(v["pass"] for v in SCORE.values())
