import pytest

pytestmark = pytest.mark.e2e


def _urls(client):
    r = client("tabs")
    assert r.get("success"), r
    return {t["id"]: t["url"] for t in r["tabs"]}


def _new_window(client, url):
    r = client("tab_new", {"url": url, "window": True})
    assert r.get("success"), r
    return r["tab"]["id"]


@pytest.fixture(params=["browser-page", "extension-page", "view-source"])
def two_windows(request, client, pages):
    """Window with an http page (tab a) plus a focused window whose tab can't be scripted (tab b)."""
    if request.param == "browser-page":
        b_url = "edge://version/"
    elif request.param == "extension-page":
        b_url = f"chrome-extension://{client('status')['id']}/popup.html"
    else:
        b_url = "view-source:" + pages + "/blank.html"
    opened = []
    try:
        opened.append(_new_window(client, pages + "/form.html"))
        opened.append(_new_window(client, b_url))
        yield opened[0], opened[1], b_url
    finally:
        for tid in opened:
            client("tab_close", {"tabId": tid})


def test_tabless_nav_stays_on_focused_unscriptable_tab(client, pages, two_windows):
    a, b, _ = two_windows
    r = client("tab")
    assert r.get("success") and r["tab"]["id"] == b, r
    before = _urls(client)
    r = client("nav", {"url": pages + "/blank.html"})
    assert r.get("success") and r["tab"]["id"] == b, r
    after = _urls(client)
    assert after.pop(b).endswith("/blank.html"), after
    before.pop(b)
    assert after == before


@pytest.mark.parametrize("action,params", [
    ("snapshot", {}),
    ("click", {"target": "Sign in"}),
    ("fill", {"target": "#username", "text": "intruder"}),
])
def test_tabless_scripted_verb_refuses_unscriptable_tab(client, two_windows, action, params):
    a, b, b_url = two_windows
    before = _urls(client)
    r = client(action, params)
    assert r.get("success") is False and r.get("code") == "tab_not_scriptable", r
    assert r["tab"]["id"] == b and r["tab"]["url"] == b_url, r
    assert _urls(client) == before
    state = client("eval", {"tabId": a, "code": "[document.getElementById('username').value, document.getElementById('signin').dataset.clicked || '']"})
    assert state.get("result") == ["", ""], state


@pytest.mark.parametrize("action,params", [("dialog", {"policy": "manual"}), ("console", {"clear": True})])
def test_tab_bookkeeping_works_on_unscriptable_tab(client, two_windows, action, params):
    _, b, _ = two_windows
    r = client(action, {"tabId": b, **params})
    assert r.get("success"), r


def test_wait_follows_tab_leaving_unscriptable_page(client, pages, two_windows):
    _, b, _ = two_windows
    r = client("nav", {"tabId": b, "url": pages + "/slow?ms=1500", "wait": "none"})
    assert r.get("success"), r
    r = client("wait", {"tabId": b, "url": "/slow", "timeout": 8000}, timeout=15)
    assert r.get("success"), r


def test_store_url_in_query_string_stays_scriptable(client, pages):
    r = client("tab_new", {"url": pages + "/blank.html?u=https://microsoftedge.microsoft.com/addons/detail/x"})
    assert r.get("success"), r
    tid = r["tab"]["id"]
    try:
        r = client("snapshot", {"tabId": tid})
        assert r.get("success"), r
    finally:
        client("tab_close", {"tabId": tid})
