import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _nav(client, pages):
    assert client("nav", {"url": pages + "/selectors.html"})["success"]


def _clicked(client, el_id):
    return client("eval", {"code": f"document.getElementById('{el_id}').dataset.clicked"}).get("result")


def test_explicit_selector_main_hits_main_not_button(client):
    r = client("click", {"selector": "main"})
    assert r["success"], r
    assert _clicked(client, "m") == "true"
    assert _clicked(client, "btnMain") is None


def test_target_compound_selectors_resolve(client):
    r = client("click", {"target": "h2.title"})
    assert r["success"], r
    assert _clicked(client, "h") == "true"
    r = client("click", {"target": "input#email"})
    assert r["success"], r
    assert _clicked(client, "email") == "true"


def test_target_bare_tag_after_name_matching(client):
    # A button named "Main" beats the <main> element for target "main"...
    r = client("click", {"target": "main"})
    assert r["success"], r
    assert _clicked(client, "btnMain") == "true"
    assert _clicked(client, "m") is None


def test_target_name_still_resolves(client):
    r = client("click", {"target": "Save"})
    assert r["success"], r
    assert _clicked(client, "btnSave") == "true"
    # ...and a name that looks like a compound selector falls back to name matching.
    r = client("click", {"target": "spec.pdf"})
    assert r["success"], r
    assert _clicked(client, "pdf") == "true"


def test_explicit_selector_main_for_text_and_wait(client):
    r = client("text", {"selector": "main"})
    assert r["success"], r
    assert "main content" in str(r.get("text")), r
    r = client("wait", {"selector": "main", "timeout": 2000})
    assert r["success"], r
    assert r.get("tag") == "main", r


def test_bad_selector_fails_fast(client):
    import time
    t0 = time.monotonic()
    r = client("click", {"selector": "div[[", "timeout": 5000})
    assert r.get("code") == "bad_selector", r
    assert time.monotonic() - t0 < 3
