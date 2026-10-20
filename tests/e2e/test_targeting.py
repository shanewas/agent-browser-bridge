import re
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _nav(client, pages):
    assert client("nav", {"url": pages + "/form.html"})["success"]


def ref_of(text, role, name):
    m = re.search(rf'^\s*- {role} "{re.escape(name)}" \[((?:f\d+)?e\d+)\]', text, re.M)
    assert m, f"{role} {name!r} not in snapshot:\n{text}"
    return m.group(1)


def test_click_by_ref_is_trusted(client):
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Verify")
    r = client("click", {"ref": ref})
    assert r["success"] and r["native"] is True and r["ref"] == ref, r
    time.sleep(0.2)
    state = client("eval", {"code": "[document.getElementById('verify').dataset.clicked, document.getElementById('verify').dataset.trusted]"})["result"]
    assert state == ["true", "true"]


def test_text_resolves_button_not_wrapper(client):
    r = client("click", {"target": "Sign in"})
    assert r["success"], r
    assert r["tag"] == "button" and r["id"] == "signin"
    assert client("eval", {"code": "document.getElementById('signin').dataset.clicked"})["result"] == "true"


def test_selector_and_label_targets(client):
    r = client("fill", {"target": "#username", "text": "shanewas"})
    assert r["success"], r
    assert client("eval", {"code": "document.getElementById('username').value"})["result"] == "shanewas"
    r = client("fill", {"target": "Notes", "text": "hello"})
    assert r["success"], r
    assert client("eval", {"code": "document.getElementById('notes').value"})["result"] == "hello"


def test_stale_ref_after_removal(client):
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Verify")
    client("eval", {"code": "document.getElementById('verify').remove()"})
    r = client("click", {"ref": ref, "timeout": 500})
    assert r["success"] is False and r["code"] == "stale_ref", r


def test_missing_target_times_out(client):
    t0 = time.time()
    r = client("click", {"target": "No such control anywhere", "timeout": 500})
    assert r["success"] is False and r["code"] == "target_not_found", r
    assert time.time() - t0 < 5


def test_highlight_ring_appears_and_fades(client):
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Verify")
    r = client("hover", {"ref": ref, "highlight": True})
    assert r["success"], r
    assert client("eval", {"code": "!!document.getElementById('__eab_ring')"})["result"] is True
    # The ring is removed by a 350 ms timer; a loaded runner can fire it late, so poll.
    deadline = time.time() + 3
    while client("eval", {"code": "!!document.getElementById('__eab_ring')"})["result"] is not False:
        assert time.time() < deadline, "highlight ring still present after 3 s"
        time.sleep(0.1)


def test_below_fold_target_reached(client, pages):
    assert client("nav", {"url": pages + "/deep.html"})["success"]
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Deep target")
    r = client("click", {"ref": ref})
    assert r["success"], r
    assert client("eval", {"code": "document.getElementById('deep-btn').dataset.clicked"})["result"] == "true"


def test_covered_target_reports_click_covered(client, pages):
    assert client("nav", {"url": pages + "/sticky.html"})["success"]
    snap = client("snapshot")["text"]
    ref = ref_of(snap, "button", "Covered target")
    r = client("click", {"ref": ref, "timeout": 2000})
    assert r["success"] is False and r["code"] == "click_covered", r
    assert client("eval", {"code": "document.getElementById('covered-btn').dataset.clicked || 'unset'"})["result"] == "unset"
    client("eval", {"code": "document.getElementById('banner').remove()"})
    r = client("click", {"ref": ref})
    assert r["success"], r
    assert client("eval", {"code": "document.getElementById('covered-btn').dataset.clicked"})["result"] == "true"
