import base64
import re
import time

import pytest

from edge_agent_bridge.client import Edge

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _nav(client, pages):
    assert client("nav", {"url": pages + "/form.html"})["success"]


def ref_of(text, role, name):
    m = re.search(rf'^\s*- {role} "{re.escape(name)}" \[((?:f\d+)?e\d+)\]', text, re.M)
    assert m, f"{role} {name!r} not in snapshot:\n{text}"
    return m.group(1)


def test_tab_new_group_close_and_history(client, pages):
    r = client("tab_new", {"url": pages + "/blank.html", "group": "Agent"})
    assert r["success"], r
    new_id = r["tab"]["id"]
    assert r["tab"]["url"].endswith("/blank.html")
    assert (r["group"] and r["group"].get("id") is not None and r["groupId"] == r["group"]["id"]) or r["group"]["code"] == "group_unsupported", r
    tabs = client("tabs")["tabs"]
    assert any(t["id"] == new_id and t["active"] for t in tabs)

    r = client("nav", {"url": pages + "/form.html", "tabId": new_id})
    assert r["success"]
    r = client("back", {"tabId": new_id})
    assert r["success"] and r["url"].endswith("/blank.html"), r
    r = client("forward", {"tabId": new_id})
    assert r["success"] and r["url"].endswith("/form.html"), r
    r = client("reload", {"tabId": new_id})
    assert r["success"] and r["url"].endswith("/form.html"), r

    r = client("tab_close", {"tabId": new_id})
    assert r["success"] and r["closedTabId"] == new_id
    r = client("tab", {"tabId": new_id})
    assert r["success"] is False and r["code"] == "tab_not_found"


def test_back_without_history_reports_code(client, pages):
    r = client("tab_new", {"url": pages + "/blank.html"})
    tid = r["tab"]["id"]
    try:
        r = client("back", {"tabId": tid})
        assert r["success"] is False and r["code"] == "no_history", r
    finally:
        client("tab_close", {"tabId": tid})


def test_select_by_label_value_and_error(client):
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "combobox", "Language")
    r = client("select", {"ref": ref, "label": "日本語"})
    assert r["success"] and r["value"] == "ja", r
    assert client("eval", {"code": "document.getElementById('language').dataset.changed"})["result"] == "ja"
    r = client("select", {"target": "Language", "value": "de"})
    assert r["success"] and r["label"] == "Deutsch", r
    r = client("select", {"ref": ref, "label": "Klingon"})
    assert r["success"] is False and r["code"] == "option_not_found" and r["options"] == ["English", "日本語", "Deutsch"], r
    r = client("select", {"target": "Username", "value": "x"})
    assert r["success"] is False and r["code"] == "not_select", r


def test_type_sends_key_events_and_keeps_unicode(client):
    payload = "日本語abc"
    r = client("type", {"target": "Notes", "text": payload, "delay": 5})
    assert r["success"] and r["chars"] == 6, r
    state = client("eval", {"code": "[document.getElementById('notes').value, document.getElementById('notes').dataset.keydowns]"})["result"]
    assert state[0] == payload, state
    assert int(state[1]) >= 6, state



def test_type_into_rich_editor_keeps_markup(e2e_daemon, client, monkeypatch):
    # Through the client: a type mismatch used to become a clearing fill that wiped the <b>.
    monkeypatch.setenv("EDGE_BRIDGE_HOME", str(e2e_daemon.home))
    with Edge(port=e2e_daemon.port, auto_start=False, pin=False) as edge:
        r = edge.send("type", {"target": "#editor", "text": "!", "delay": 5, "highlight": False})
    assert r["success"] and r["field"] == "contenteditable" and "retried" not in r, r
    assert r["match"] is True, r
    state = client("eval", {"code": "[document.querySelectorAll('#editor b').length, document.getElementById('editor').innerText]"})["result"]
    assert state[0] == 1 and "!" in state[1], state

def test_upload_by_ref_button_and_failure(client, tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hi", encoding="utf-8")
    snap = client("snapshot")["text"]
    file_ref = ref_of(snap, "file", "Choose file")
    r = client("upload", {"ref": file_ref, "files": [str(f)]})
    assert r["success"], r
    time.sleep(0.3)
    assert client("eval", {"code": "document.getElementById('upload-name').textContent"})["result"] == "hello.txt"
    assert client("eval", {"code": "document.querySelectorAll('[data-eab-upload]').length"})["result"] == 0
    assert client("eval", {"code": "document.getElementById('attachment').getAttribute('style') || 'none'"})["result"] == "none"

    g = tmp_path / "second.txt"
    g.write_text("2", encoding="utf-8")
    btn_ref = ref_of(snap, "button", "Upload")
    r = client("upload", {"ref": btn_ref, "files": [str(g)]})
    assert r["success"], r
    time.sleep(0.3)
    assert client("eval", {"code": "document.getElementById('upload-name').textContent"})["result"] == "second.txt"

    r = client("upload", {"target": "Verify", "files": [str(f)]})
    assert r["success"] is False and r["code"] == "no_file_input", r
    r = client("upload", {"ref": file_ref, "files": ["relative.txt"]})
    assert r["success"] is False and r["code"] == "bad_params", r


def _png_size(data_url):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def test_screenshot_formats_clip_and_of(client):
    r = client("screenshot")
    assert r["success"] and r["dataUrl"].startswith("data:image/jpeg;base64,"), r
    full = client("screenshot", {"format": "png"})
    assert full["dataUrl"].startswith("data:image/png;base64,")
    w, h = _png_size(full["dataUrl"])
    clipped = client("screenshot", {"format": "png", "clip": {"x": 0, "y": 0, "width": 200, "height": 100}})
    cw, ch = _png_size(clipped["dataUrl"])
    assert (cw, ch) == (200, 100) and cw < w and ch < h
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Verify")
    of = client("screenshot", {"format": "png", "of": ref})
    ow, oh = _png_size(of["dataUrl"])
    assert 0 < ow < 300 and 0 < oh < 100, (ow, oh)


def test_screenshot_of_background_tab_keeps_focus(client, pages):
    first = client("tab")["tab"]["id"]
    r = client("tab_new", {"url": pages + "/blank.html"})
    second = r["tab"]["id"]
    try:
        assert client("tab")["tab"]["id"] == second
        shot = client("screenshot", {"tabId": first, "format": "png"})
        assert shot["success"], shot
        assert client("tab")["tab"]["id"] == second
    finally:
        client("tab_close", {"tabId": second})


def test_scroll_ref_brings_element_into_view(client, pages):
    assert client("nav", {"url": pages + "/scroll.html"})["success"]
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Bottom button")
    before = client("eval", {"code": "document.getElementById('bottomBtn').getBoundingClientRect().top"})["result"]
    assert before > 900, before
    r = client("scroll", {"ref": ref})
    assert r["success"], r
    time.sleep(0.3)
    top = client("eval", {"code": "document.getElementById('bottomBtn').getBoundingClientRect().top"})["result"]
    height = client("eval", {"code": "window.innerHeight"})["result"]
    assert 0 <= top < height, (top, height)


def test_scroll_x_only_scrolls_horizontally(client, pages):
    assert client("nav", {"url": pages + "/scroll.html"})["success"]
    r = client("scroll", {"x": 200, "y": 0})
    assert r["success"], r
    time.sleep(0.3)
    pos = client("eval", {"code": "[window.scrollX, window.scrollY]"})["result"]
    assert pos[0] > 0 and pos[1] == 0, pos
