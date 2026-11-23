"""Behavioural tests for the extension-side tab-group paths in actions.js:
group_list / group_move / group_ungroup and tab_new's join-by-name group,
driven under Node with chrome.tabs / chrome.tabGroups stubs that follow
Chrome semantics (a group lives in one window; grouping across windows
throws). The Python-side tests in test_groups.py only see param building
and daemon forwarding; these cover the worker code itself. Skipped when
Node is missing."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "agent_browser_bridge" / "extension"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")

HARNESS = r"""
import fs from "node:fs";

const storageLocal = new Map();
const storageSession = new Map();
const area = (map) => ({
  get: async (keys) => {
    if (keys == null) return Object.fromEntries(map);
    const arr = Array.isArray(keys) ? keys : [keys];
    const o = {};
    for (const k of arr) if (map.has(k)) o[k] = map.get(k);
    return o;
  },
  set: async (obj) => { for (const [k, v] of Object.entries(obj)) map.set(k, v); },
  remove: async (keys) => { for (const k of [].concat(keys)) map.delete(k); },
});
const listener = { addListener() {}, removeListener() {} };

let nextTab = 10, nextGroup = 500;
const tabs = new Map();
function mkTab(id, windowId, url) {
  const t = { id, windowId, url, title: "T" + id, active: false, groupId: -1, status: "complete", index: id };
  tabs.set(id, t); return t;
}
mkTab(1, 1, "https://a.example/"); mkTab(2, 1, "https://b.example/");
mkTab(3, 1, "https://c.example/"); mkTab(4, 2, "https://d.example/");
tabs.get(1).active = true;
const groups = new Map();

globalThis.chrome = {
  storage: { local: area(storageLocal), session: area(storageSession), onChanged: listener },
  tabs: {
    get: async (id) => { const t = tabs.get(id); if (!t) throw new Error("No tab with id: " + id); return { ...t }; },
    query: async (q = {}) => [...tabs.values()].filter(t =>
      (q.windowId === undefined || t.windowId === q.windowId) &&
      (q.active === undefined || t.active === q.active)).map(t => ({ ...t })),
    create: async ({ url, active, windowId }) => {
      const t = mkTab(nextTab++, windowId ?? 1, url || "about:blank");
      t.active = !!active; return { ...t };
    },
    update: async (id, props) => { Object.assign(tabs.get(id), props); return { ...tabs.get(id) }; },
    remove: async (ids) => { for (const id of [].concat(ids)) tabs.delete(id); },
    group: async ({ tabIds, groupId }) => {
      const ids = [].concat(tabIds);
      const wins = new Set(ids.map(id => tabs.get(id).windowId));
      if (wins.size > 1) throw new Error("Tabs can only be grouped within the same window.");
      let gid = groupId;
      if (gid !== undefined && gid !== null) {
        const g = groups.get(gid);
        if (!g) throw new Error("No group with id: " + gid);
        if (g.windowId !== [...wins][0]) throw new Error("Group is in a different window.");
      } else {
        gid = nextGroup++;
        groups.set(gid, { id: gid, title: "", color: "grey", collapsed: false, windowId: [...wins][0] });
      }
      for (const id of ids) tabs.get(id).groupId = gid;
      return gid;
    },
    ungroup: async (ids) => { for (const id of [].concat(ids)) tabs.get(id).groupId = -1; },
    sendMessage: async () => ({}),
    onRemoved: listener, onUpdated: listener, onCreated: listener,
  },
  tabGroups: {
    query: async (q = {}) => [...groups.values()].filter(g =>
      (q.windowId === undefined || g.windowId === q.windowId) &&
      (q.title === undefined || g.title === q.title) &&
      (q.color === undefined || g.color === q.color) &&
      (q.collapsed === undefined || g.collapsed === q.collapsed)).map(g => ({ ...g })),
    get: async (id) => { const g = groups.get(id); if (!g) throw new Error("No group with id: " + id); return { ...g }; },
    update: async (id, props) => { Object.assign(groups.get(id), props); return { ...groups.get(id) }; },
    onRemoved: listener, onUpdated: listener,
  },
  scripting: { executeScript: async () => [{ result: undefined }] },
  runtime: {
    getManifest: () => ({ version: "4.3.0" }), id: "testext",
    lastError: undefined, onMessage: listener, onConnect: listener,
    sendMessage: async () => ({}), getURL: (p) => p,
  },
  debugger: {
    attach: (t, v, cb) => { if (cb) cb(); },
    detach: (t, cb) => { if (cb) cb(); },
    sendCommand: (t, m, p, cb) => { if (cb) cb({}); },
    onEvent: listener, onDetach: listener, getTargets: async () => [],
  },
  webNavigation: { onCommitted: listener, onCompleted: listener, onHistoryStateUpdated: listener },
  webRequest: { onBeforeRequest: listener, onCompleted: listener, onErrorOccurred: listener },
  downloads: { download: async () => 1, onChanged: listener },
  alarms: { create() {}, onAlarm: listener },
  action: { setIcon: async () => {}, setBadgeText: async () => {}, setTitle: async () => {} },
  windows: { get: async () => ({ id: 1 }), create: async () => ({ id: 3, tabs: [mkTab(nextTab++, 3, "about:blank")] }), onFocusChanged: listener },
};

const mode = process.argv[3];
if (mode === "noapi") {
  delete chrome.tabGroups;
  delete chrome.tabs.group;
  delete chrome.tabs.ungroup;
}

const actions = await import(process.argv[2]);
const D = (action, params) => actions.dispatch({ action, params });
const out = {};
if (mode === "noapi") {
  out.tab_new_group = await D("tab_new", { url: "about:blank", group: "Research", wait: "none" });
  out.move = await D("group_move", { tabIds: [1, 2], title: "Work" });
} else {
  out.list_empty = await D("group_list", {});
  out.move_new = await D("group_move", { tabIds: [1, 2], title: "Work", color: "blue" });
  out.list_after = await D("group_list", {});
  out.move_existing = await D("group_move", { tabIds: [3], groupId: out.move_new.groupId });
  out.tabs_after_join = await D("tabs", {});
  out.ungroup = await D("group_ungroup", { tabIds: [2] });
  out.tabs_after_ungroup = await D("tabs", {});
  out.cross_window = await D("group_move", { tabIds: [1, 4], title: "X" });
  out.move_no_tabs = await D("group_move", { tabIds: [] });
  out.tab_new_group = await D("tab_new", { url: "about:blank", group: "Research", wait: "none" });
  out.tab_new_group_join = await D("tab_new", { url: "about:blank", group: "Research", wait: "none" });
  out.list_final = await D("group_list", {});
}
console.log(JSON.stringify(out));
"""


def _run(tmp_path, mode):
    harness = tmp_path / f"groups_harness_{mode}.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), (EXT / "actions.js").as_uri(), mode],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("groups_full"), "full")


@pytest.fixture(scope="module")
def noapi(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("groups_noapi"), "noapi")


def test_group_list_starts_empty(full):
    assert full["list_empty"] == {"success": True, "count": 0, "groups": []}


def test_group_move_creates_group_with_title_and_color(full):
    res = full["move_new"]
    assert res["success"] is True
    assert res["group"]["title"] == "Work"
    assert res["group"]["color"] == "blue"
    assert full["list_after"]["count"] == 1


def test_group_move_joins_existing_group(full):
    gid = full["move_new"]["groupId"]
    assert full["move_existing"]["groupId"] == gid
    by_id = {t["id"]: t for t in full["tabs_after_join"]["tabs"]}
    assert by_id[1]["groupId"] == by_id[2]["groupId"] == by_id[3]["groupId"] == gid
    assert by_id[4]["groupId"] == -1  # other window untouched


def test_group_ungroup_removes_only_named_tabs(full):
    assert full["ungroup"] == {"success": True, "tabIds": [2]}
    by_id = {t["id"]: t for t in full["tabs_after_ungroup"]["tabs"]}
    assert by_id[2]["groupId"] == -1
    assert by_id[1]["groupId"] == full["move_new"]["groupId"]


def test_group_move_across_windows_fails_cleanly(full):
    res = full["cross_window"]
    assert res["success"] is False
    assert res["code"] == "group_failed"


def test_group_move_without_tabs_is_bad_params(full):
    res = full["move_no_tabs"]
    assert res["success"] is False
    assert res["code"] == "bad_params"


def test_tab_new_creates_then_joins_group_by_name(full):
    first, second = full["tab_new_group"], full["tab_new_group_join"]
    assert first["success"] is True and second["success"] is True
    assert first["group"]["title"] == "Research"
    assert first["groupId"] == second["groupId"] == first["group"]["id"]
    titles = {g["title"] for g in full["list_final"]["groups"]}
    assert titles == {"Work", "Research"}


def test_tab_new_group_failure_is_loud_not_silent(noapi):
    res = noapi["tab_new_group"]
    assert res["success"] is True  # the tab itself opened
    assert res["group"]["code"] == "group_unsupported"
    assert "warning" in res and "Research" in res["warning"]


def test_group_move_without_api_fails_group_failed(noapi):
    res = noapi["move"]
    assert res["success"] is False
    assert res["code"] == "group_failed"
