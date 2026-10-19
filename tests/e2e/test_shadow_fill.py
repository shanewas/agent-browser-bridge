import re
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _nav(client, pages):
    assert client("nav", {"url": pages + "/shadow-fill.html"})["success"]


def _ref(text, name):
    m = re.search(rf'- textbox "{re.escape(name)}" \[((?:f\d+)?e\d+)\]', text)
    assert m, f"textbox {name!r} not in snapshot:\n{text}"
    return m.group(1)


def _value(client, expr):
    return client("eval", {"code": expr})["result"]


def test_fill_by_ref_in_open_shadow_root(client):
    snap = client("snapshot")["text"]
    ref = _ref(snap, "name")
    r = client("fill", {"ref": ref, "text": "alice"})
    assert r["success"] and r["match"] is True, r
    got = _value(client, "document.getElementById('one').shadowRoot.querySelector('input').value")
    assert got == "alice", got


def test_fill_by_label_in_open_shadow_root(client):
    r = client("fill", {"target": "name", "text": "bob"})
    assert r["success"] and r["match"] is True, r
    got = _value(client, "document.getElementById('one').shadowRoot.querySelector('input').value")
    assert got == "bob", got


def test_fill_nested_two_shadow_roots_deep(client):
    snap = client("snapshot")["text"]
    ref = _ref(snap, "deep name")
    r = client("fill", {"ref": ref, "text": "carol"})
    assert r["success"] and r["match"] is True, r
    got = _value(client, "document.getElementById('two').shadowRoot.querySelector('x-inner').shadowRoot.querySelector('input').value")
    assert got == "carol", got


def test_type_into_shadow_input(client):
    snap = client("snapshot")["text"]
    ref = _ref(snap, "name")
    r = client("type", {"ref": ref, "text": "dave", "delay": 5})
    assert r["success"] and r["match"] is True, r
    time.sleep(0.2)
    got = _value(client, "document.getElementById('one').shadowRoot.querySelector('input').value")
    assert got == "dave", got


def test_type_reports_focus_stolen(client):
    snap = client("snapshot")["text"]
    ref = _ref(snap, "steal source")
    r = client("type", {"ref": ref, "text": "hello world", "delay": 60})
    assert r["success"] is False and r["code"] == "focus_stolen", r
