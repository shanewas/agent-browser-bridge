import re
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def frames_page(client, pages, pages2):
    r = client("nav", {"url": f"{pages}/frames.html?cross={pages2}"})
    assert r["success"], r
    time.sleep(0.5)
    return r


def child_ref(text, title, role, name):
    block = re.search(rf'^- iframe "{title}" \[(f\d+)\]\n((?:  .*\n?)+)', text, re.M)
    assert block, f"iframe {title!r} missing or unreachable:\n{text}"
    m = re.search(rf'- {role} "{re.escape(name)}" \[({block.group(1)}e\d+)\]', block.group(2))
    assert m, f"{role} {name!r} not under iframe {title!r}:\n{text}"
    return m.group(1)


def test_snapshot_nests_reachable_frames(client, frames_page):
    t = client("snapshot")["text"]
    for title in ("same-origin", "cross-origin", "shadow", "sandboxed"):
        assert re.search(rf'^- iframe "{title}" \[f\d+\]', t, re.M), t
        child_ref(t, title, "button", "Ping")
        child_ref(t, title, "textbox", "Note")
    assert "[unreachable]" not in t


def test_click_ref_inside_each_frame(client, frames_page):
    t = client("snapshot")["text"]
    for title, tag in (("same-origin", "same"), ("cross-origin", "cross"), ("shadow", "shadow")):
        ref = child_ref(t, title, "button", "Ping")
        r = client("click", {"ref": ref})
        assert r["success"] and r["native"] is True, (title, r)
    time.sleep(0.4)
    hits = client("eval", {"code": "window.__hits"})["result"]
    assert [h[0] for h in hits] == ["same", "cross", "shadow"], hits
    assert all(h[1] is True for h in hits), hits


def test_check_and_select_take_a_framed_ref_as_target(client, frames_page):
    t = client("snapshot")["text"]
    r = client("check_radio", {"target": child_ref(t, "cross-origin", "checkbox", "Agree")})
    assert r["success"] and r["checked"] is True, r
    r = client("select", {"target": child_ref(t, "cross-origin", "combobox", "Pick"), "value": "b"})
    assert r["success"] and r["value"] == "b", r
    time.sleep(0.3)
    notes = client("eval", {"code": "window.__notes"})["result"]
    assert "cross:agree=true" in notes and "cross:pick=b" in notes, notes


def test_fill_ref_in_cross_origin_frame(client, frames_page):
    t = client("snapshot")["text"]
    ref = child_ref(t, "cross-origin", "textbox", "Note")
    r = client("fill", {"ref": ref, "text": "hello frame"})
    assert r["success"], r
    time.sleep(0.3)
    notes = client("eval", {"code": "window.__notes"})["result"]
    assert notes and notes[-1] == "cross:hello frame", notes


def test_fill_ref_in_same_origin_frame(client, frames_page):
    t = client("snapshot")["text"]
    ref = child_ref(t, "same-origin", "textbox", "Note")
    r = client("fill", {"ref": ref, "text": "hello same"})
    assert r["success"] and r["match"] is True, r
    time.sleep(0.3)
    notes = client("eval", {"code": "window.__notes"})["result"]
    assert notes and notes[-1] == "same:hello same", notes


def test_type_ref_in_cross_origin_frame(client, frames_page):
    t = client("snapshot")["text"]
    ref = child_ref(t, "cross-origin", "textbox", "Note")
    r = client("type", {"ref": ref, "text": "typed", "delay": 5})
    assert r["success"] and r["match"] is True, r
    time.sleep(0.3)
    notes = client("eval", {"code": "window.__notes"})["result"]
    assert notes and notes[-1] == "cross:typed", notes


def test_frames_false_skips_subframes(client, frames_page):
    t = client("snapshot", {"frames": False})["text"]
    assert re.search(r'^- iframe "same-origin"$', t, re.M), t
    assert "Ping" not in t
