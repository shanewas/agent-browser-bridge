import re

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _nav(client, pages):
    assert client("nav", {"url": pages + "/text.html"})["success"]


def test_text_container_returns_all_its_text(client):
    r = client("text", {"selector": "#m"})
    assert r["success"], r
    lines = [ln.strip() for ln in r["text"].splitlines() if ln.strip()]
    assert lines == ["first", "second", "third"], r


def test_text_input_returns_its_value(client):
    r = client("text", {"selector": "#inp"})
    assert r["success"] and r["text"] == "field value", r


def test_text_by_ref_returns_element_text(client):
    snap = client("snapshot")["text"]
    m = re.search(r'- button "Press me" \[((?:f\d+)?e\d+)\]', snap)
    assert m, snap
    r = client("text", {"ref": m.group(1)})
    assert r["success"] and r["text"] == "Press me", r


def test_text_display_none_returns_own_text(client):
    r = client("text", {"selector": "#hidden"})
    assert r["success"] and r["text"] == "secret text", r
