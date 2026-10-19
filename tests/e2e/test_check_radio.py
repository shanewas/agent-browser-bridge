import re
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _nav(client, pages):
    assert client("nav", {"url": pages + "/check-radio.html"})["success"]


def _checked(client, el_id):
    code = f"(document.getElementById('{el_id}').checked === true) || (document.getElementById('{el_id}').getAttribute('aria-checked') === 'true')"
    return client("eval", {"code": code}).get("result")


def test_check_radio_by_label_text(client):
    r = client("check_radio", {"target": "Completed"})
    assert r["success"], r
    assert _checked(client, "opt-completed") is True


def test_check_radio_on_role_radio_group(client):
    r = client("check_radio", {"selector": "#role-completed"})
    assert r["success"], r
    assert r["checked"] is True, r
    assert _checked(client, "role-completed") is True
    assert _checked(client, "role-in-progress") is False


def test_check_radio_reports_a_role_radio_that_ignores_the_click(client):
    r = client("check_radio", {"selector": "#role-dead"})
    assert r.get("checked") is False, r
    assert _checked(client, "role-dead") is False


def test_check_radio_by_ref(client):
    snap = client("snapshot")["text"]
    m = re.search(r'- radio "Pending" \[((?:f\d+)?e\d+)\]', snap)
    assert m, snap
    ref = m.group(1)
    r = client("check_radio", {"ref": ref})
    assert r["success"], r
    assert _checked(client, "opt-pending") is True


def test_check_radio_with_slash_and_non_ascii_label(client):
    r = client("check_radio", {"target": "1.完了 / Completed"})
    assert r["success"], r
    assert _checked(client, "opt-jp") is True


def test_check_radio_by_value_with_quotes_and_brackets(client):
    r = client("check_radio", {"value": 'val"foo]'})
    assert r["success"], r
    assert _checked(client, "opt-special") is True


def test_check_radio_bad_selector_error(client):
    r = client("check_radio", {"selector": "div:invalid[[["})
    assert r["success"] is False and r["code"] == "bad_selector", r


def test_check_radio_not_found_code(client):
    r = client("check_radio", {"target": "NonExistent"})
    assert r["success"] is False and r["code"] == "target_not_found", r
