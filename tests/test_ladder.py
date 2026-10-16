from edge_agent_bridge.client import Edge, LADDER_ACTIONS, LADDER_RETRY_CODES, score_element_match


class ScriptedEdge(Edge):
    def __init__(self, script):
        super().__init__(auto_start=False)
        self.script = list(script)
        self.calls = []

    def _send_once(self, action, params=None, timeout=None):
        self.calls.append((action, dict(params or {})))
        assert self.script, f"unexpected call {action} {params}"
        expected_action, response = self.script.pop(0)
        assert action == expected_action, f"expected {expected_action}, got {action}"
        return dict(response)


def els(*items):
    return {"success": True, "elements": [
        {"ref": f"e{i}", "text": t, "x": 10 * i, "y": 20} for i, t in enumerate(items)]}


def test_rung1_success_no_ladder():
    e = ScriptedEdge([("click", {"success": True})])
    data = e.send("click", {"ref": "e1"})
    assert data == {"success": True}
    assert len(e.calls) == 1


def test_rung2_uses_call_target_after_stale_ref():
    e = ScriptedEdge([
        ("click", {"success": False, "code": "stale_ref", "error": "old"}),
        ("click", {"success": True}),
    ])
    data = e.send("click", {"ref": "e1", "target": "Save"})
    assert data["success"] is True and data["ladder"]["rung"] == "text"
    assert e.calls[1][1] == {"target": "Save"}
    assert data["ladder"]["tried"][0] == {"step": "ref", "outcome": "stale_ref"}


def test_rung2_uses_cached_ref_text():
    e = ScriptedEdge([
        ("elements", els("Cancel", "Save")),
        ("click", {"success": False, "code": "stale_ref", "error": "old"}),
        ("click", {"success": True}),
    ])
    e.send("elements")
    data = e.send("click", {"ref": "e1"})
    assert data["success"] is True
    assert e.calls[2][1] == {"target": "Save"}


def test_rung3_scan_match_and_rung4_coords():
    e = ScriptedEdge([
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("elements", els("Nope", "Save")),
        ("click", {"success": True}),
    ])
    data = e.send("click", {"target": "Save"})
    assert data["success"] is True and data["ladder"]["rung"] == "coords"
    assert e.calls[3][1] == {"x": 10, "y": 20}
    assert [t["step"] for t in data["ladder"]["tried"]] == ["target", "text", "scan", "coords"]


def test_exhausted_ref_only_without_cached_text():
    e = ScriptedEdge([
        ("click", {"success": False, "code": "stale_ref", "error": "old"}),
    ])
    data = e.send("click", {"ref": "e9"})
    assert data["success"] is False and data["code"] == "exhausted_fallback"
    assert [c[0] for c in e.calls] == ["click"]
    assert data["tried"] == [{"step": "ref", "outcome": "stale_ref"},
                             {"step": "text", "outcome": "no text target"}]


def test_two_scan_budget_then_exhausted():
    e = ScriptedEdge([
        ("click", {"success": False, "code": "stale_ref", "error": "old"}),
        ("click", {"success": False, "code": "stale_ref", "error": "old"}),
        ("elements", els()),
        ("elements", els()),
    ])
    data = e.send("click", {"target": "Save"})
    assert data["code"] == "exhausted_fallback"
    assert [c[0] for c in e.calls].count("elements") == 2
    assert data["tried"][-1] == {"step": "scan", "outcome": "0 matches"}


def test_drag_ladder_resolves_both_ends_via_scan():
    e = ScriptedEdge([
        ("drag", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("elements", els("FileA", "FolderB")),
        ("elements", els("FileA", "FolderB")),
        ("drag", {"success": True}),
    ])
    data = e.send("drag", {"from": "FileA", "to": "FolderB", "steps": 10})
    assert data["success"] is True and data["ladder"]["rung"] == "coords"
    assert e.calls[3][1] == {"steps": 10, "fromX": 0, "fromY": 20, "toX": 10, "toY": 20}
    assert [t["step"] for t in data["ladder"]["tried"]] == ["target", "text", "scan", "scan", "coords"]


def test_opt_out_and_coords_bypass():
    e = ScriptedEdge([("click", {"success": False, "code": "stale_ref", "error": "old"})])
    data = e.send("click", {"ref": "e1", "fallback": False})
    assert data["code"] == "stale_ref" and len(e.calls) == 1
    assert "fallback" not in e.calls[0][1]
    e2 = ScriptedEdge([("click", {"success": False, "code": "target_not_found", "error": "nf"})])
    data2 = e2.send("click", {"x": 1, "y": 2})
    assert data2["code"] == "target_not_found" and len(e2.calls) == 1


def test_score_priority_and_dom_tiebreak():
    entries = [{"ref": "e0", "text": "save all"}, {"ref": "e1", "text": "Save"}, {"ref": "e2", "text": "save"}]
    assert score_element_match(entries, "Save")["ref"] == "e1"
    assert score_element_match(entries, "save")["ref"] == "e2"
    assert score_element_match(entries, "sav") is None
    assert score_element_match(entries, "zzz") is None
    assert score_element_match([{"ref": "a", "text": "x"}, {"ref": "b", "text": "x"}], "x") is None


def test_scan_substring_match_never_clicks():
    # Issue #19 repro: "Delete" must not click "Delete account permanently".
    e = ScriptedEdge([
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("elements", els("Undo", "Delete account permanently")),
        ("elements", els("Undo", "Delete account permanently")),
    ])
    data = e.send("click", {"target": "Delete"})
    assert data["success"] is False and data["code"] == "exhausted_fallback"
    assert all("x" not in c[1] for c in e.calls), e.calls


def test_scan_single_case_insensitive_match_reports_matched():
    e = ScriptedEdge([
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("elements", els("Undo", "delete")),
        ("click", {"success": True}),
    ])
    data = e.send("click", {"target": "Delete"})
    assert data["success"] is True and data["ladder"]["rung"] == "coords"
    assert data["ladder"]["matched"] == {"name": "delete", "ref": "e1"}
    assert e.calls[3][1] == {"x": 10, "y": 20}


def test_scan_duplicate_names_exhaust():
    e = ScriptedEdge([
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("click", {"success": False, "code": "target_not_found", "error": "nf"}),
        ("elements", els("Save", "Save")),
        ("elements", els("Save", "Save")),
    ])
    data = e.send("click", {"target": "Save"})
    assert data["success"] is False and data["code"] == "exhausted_fallback"
    assert all("x" not in c[1] for c in e.calls), e.calls


def test_non_ladder_action_and_code_pass_through():
    e = ScriptedEdge([("eval", {"success": False, "code": "stale_ref", "error": "x"})])
    assert e.send("eval", {"code": "1"})["code"] == "stale_ref"
    e2 = ScriptedEdge([("click", {"success": False, "code": "tab_busy", "error": "b"})])
    assert e2.send("click", {"ref": "e1"})["code"] == "tab_busy"
    assert "eval" not in LADDER_ACTIONS and "click" in LADDER_ACTIONS
    assert set(LADDER_RETRY_CODES) == {"stale_ref", "stale_snapshot", "target_not_found"}


def test_fill_preserves_content_across_rungs():
    e = ScriptedEdge([
        ("fill", {"success": False, "code": "stale_ref", "error": "old"}),
        ("fill", {"success": True, "written": "hi", "readback": "hi", "match": True}),
    ])
    data = e.send("fill", {"ref": "e1", "target": "Name", "text": "hi", "clear": True})
    assert data["success"] is True
    assert e.calls[1][1] == {"target": "Name", "text": "hi", "clear": True}
