"""Behavioural tests for the worker-side Spotlight path in actions.js:
execute() draws, error codes, downgrades, the audit log, and the exported
event/navigation/settings transitions. Run under Node with chrome.* and
page-world stubs. Skipped when Node is missing."""
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

// ---- chrome.* stubs ---------------------------------------------------------
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
let tabUrl = "https://example.com/form";
let drawResponder = () => ({ applied: true, covered: false, offscreen: false });
let rectCheckFails = false;
const drawTabs = [];
const listener = { addListener() {}, removeListener() {} };
globalThis.chrome = {
  storage: { local: area(storageLocal), session: area(storageSession), onChanged: listener },
  tabs: {
    get: async (id) => ({ id, url: tabUrl, active: true, windowId: 1, title: "T" }),
    query: async () => [{ id: 1, url: tabUrl, active: true, windowId: 1, title: "T" }],
    sendMessage: async (id, msg) => {
      if (msg && msg.type === "SPOTLIGHT" && msg.op === "draw") { drawTabs.push(id); return drawResponder(msg); }
      return {};
    },
    onRemoved: listener, onUpdated: listener,
    create: async () => ({ id: 99 }), update: async () => ({}),
  },
  scripting: { executeScript: async ({ func, args }) => {
    // RV-3: simulate the rect-check injection returning no result, so
    // execInTab answers its inject_failed failure shape.
    if (rectCheckFails && func && /pageSpotlightRectCheck/.test(func.name || "")) return [{}];
    return [{ result: await func(...(args || [])) }];
  } },
  runtime: {
    getManifest: () => ({ version: "4.4.0" }), id: "testext",
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
  windows: { get: async () => ({ id: 1 }), onFocusChanged: listener },
};

// ---- page world --------------------------------------------------------------
const rect = (x, y, w, h) => ({ x, y, width: w, height: h, left: x, top: y, right: x + w, bottom: y + h });
let pwRect = rect(10, 10, 100, 30);
const pw = { tagName: "INPUT", __sensitive: true, __children: [], getBoundingClientRect: () => pwRect };
// F1 PoC field below the fold: inside a tall rect's unprobed part only.
const belowPw = { tagName: "INPUT", __sensitive: true, __children: [], getBoundingClientRect: () => rect(200, 900, 400, 40) };
const btnSave = { tagName: "BUTTON", __children: [], getBoundingClientRect: () => rect(500, 100, 120, 40) };
const btnSave2 = { tagName: "BUTTON", __children: [], getBoundingClientRect: () => rect(500, 200, 120, 40) };
const cardWrap = { tagName: "DIV", __children: [pw], getBoundingClientRect: () => rect(700, 100, 300, 200) };
const bigEl = { tagName: "SECTION", __children: [], getBoundingClientRect: () => rect(0, 0, 1200, 760) };
const rootEl = { tagName: "HTML", __children: [pw, btnSave, cardWrap], getBoundingClientRect: () => rect(0, 0, 1280, 800) };
const inRect = (r, x, y) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
let saveCount = 1;
const lib = {
  queryAllDeep: (root, sel) => (sel === ".card" ? [cardWrap] : sel === ".big" ? [bigEl]
    : sel === "input" ? [pw] : (saveCount === 2 ? [btnSave, btnSave2] : [btnSave])),
  roleOf: (el) => (el.tagName === "BUTTON" ? "button" : ""),
  INTERACTIVE_ROLES: new Set(["button", "link"]),
  isVisible: () => true,
  nameOf: (el) => (el.tagName === "BUTTON" ? "Save" : ""),
  descriptorOf: (el) => ({ tag: el.tagName.toLowerCase(), role: lib.roleOf(el), name: lib.nameOf(el) }),
  isSensitiveInput: (el) => Boolean(el && el.__sensitive),
  containsSensitiveInput: (el) => Boolean(el) && (lib.isSensitiveInput(el)
    || (el.__children || []).some((c) => lib.containsSensitiveInput(c))),
  enumerateSensitiveFields: () => [pw, belowPw],
  labelText: () => "",
  frameInfo: async () => ({ x: 0, y: 0 }),
  rectOf: (el) => el.getBoundingClientRect(),
  center: () => ({ x: 560, y: 120 }),
  highlight: () => {},
  collapse: (s) => String(s || "").replace(/\s+/g, " ").trim(),
  deepFromPoint: (x, y) => (inRect(pw.getBoundingClientRect(), x, y) ? pw
    : inRect(btnSave.getBoundingClientRect(), x, y) ? btnSave : rootEl),
};
globalThis.window = globalThis;
globalThis.innerWidth = 1280;
globalThis.innerHeight = 800;
globalThis.scrollX = 0;
globalThis.scrollY = 0;
globalThis.__eabLib = lib;
globalThis.document = {
  elementsFromPoint: (x, y) => (inRect(pw.getBoundingClientRect(), x, y) ? [pw, rootEl]
    : inRect(btnSave.getBoundingClientRect(), x, y) ? [btnSave, rootEl] : [rootEl]),
  createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
  addEventListener() {},
  documentElement: rootEl,
  body: rootEl,
};
globalThis.addEventListener = () => {};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);

const actions = await import("__ACTIONS__");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = {};
const log = () => storageLocal.get("spotlightLog") || [];
const draw = (tabId, params) => actions.execute({ action: "spotlight", params: { tabId, ...params }, agent: "Sigil" });
const status = (tabId) => actions.execute({ action: "spotlight_status", params: { tabId } });

// 1. Plain element draw + audit entry.
out.draw1 = await draw(1, { target: "Save", label: "About to click Save" });
out.status1 = await status(1);

// 2. A second labelled draw replaces the first; the first entry closes as
// replaced. (Past the 2s labelled-draw window, as a real agent's would be.)
await sleep(2100);
out.draw2 = await draw(1, { target: "Save", label: "Second point" });
await sleep(50);
out.log_after_replace = log().map((e) => ({ label: e.label, clearedReason: e.clearedReason }));

// 3. Rate limit: a third labelled draw inside the window fails, even after a clear.
out.rate_limited = await draw(1, { target: "Save", label: "Third point" });
out.clear1 = await actions.execute({ action: "spotlight_clear", params: { tabId: 1 } });
out.rate_limited_after_clear = await draw(1, { target: "Save", label: "Third point" });
out.status_after_clear = await status(1);

// 4. Ambiguity surfaces candidates.
saveCount = 2;
out.ambiguous = await draw(2, { target: "Save", label: "Pick one" });
saveCount = 1;

// 5. Sensitive target refused; generic draw allowed with the fixed chip.
out.sensitive = await draw(3, { selector: ".card", label: "The card field" });
out.generic = await draw(3, { selector: ".card", generic: true });
await sleep(50);
out.generic_log_label = (log().find((e) => e.label && e.label.includes("generic")) || {}).label || null;

// 6. Downgrades: never-dim and viewport fill.
storageLocal.set("spotlightNeverDim", true);
out.never_dim = await draw(4, { target: "Save", mode: "spotlight", label: "Section" });
storageLocal.delete("spotlightNeverDim");
out.viewport_fill = await draw(5, { selector: ".big", mode: "spotlight", label: "Whole section" });

// 7. Paused tabs: draws blocked with tab_paused, status still answers.
actions.setPauseGate((tabId) => tabId === 6);
out.paused_draw = await draw(6, { target: "Save", label: "Nope" });
out.paused_status = await status(6);
actions.setPauseGate(null);

// 8. Geometry validation.
out.rect_off_viewport = await draw(7, { rect: { x: 5000, y: 10, width: 100, height: 50 }, mode: "ring", label: "Off" });
out.rect_point_mode = await draw(7, { rect: { x: 10, y: 10, width: 100, height: 50 }, mode: "point", label: "No" });
out.rect_over_field = await draw(7, { rect: { x: 0, y: 0, width: 300, height: 120 }, mode: "ring", label: "Form" });
out.point_ok = await draw(7, { x: 560, y: 120, label: "The button", scale_factor: 2 });
out.point_on_field = await draw(15, { x: 20, y: 20, label: "The field" });
// F1: pw sits at (10,10) sized 100x30 - no grid probe of a full-viewport
// rect lands inside it, yet the exact rect check must refuse the draw.
// The tall rect's field sits below the fold, in the part probes clamp away.
out.rect_between_probes = await draw(16, { rect: { x: 0, y: 0, width: 1280, height: 800 }, mode: "ring", label: "Whole form" });
out.rect_below_fold = await draw(17, { rect: { x: 0, y: 0, width: 1280, height: 1600 }, mode: "ring", label: "Tall section" });

// 9. A dropped draw (content script refused it) is not recorded as showing.
drawResponder = () => ({ applied: false, reason: "target_missing" });
out.dropped = await draw(8, { target: "Save", label: "Ghost" });
out.dropped_status = await status(8);
drawResponder = () => ({ applied: true, covered: false, offscreen: false });

// 10. Stale-generation events do not touch the current highlight.
out.draw10 = await draw(9, { target: "Save", label: "Current" });
const genNow = out.draw10.generation;
await actions.spotlightEvent(9, { type: "cleared", reason: "expired", generation: genNow - 1 });
out.stale_event_status = await status(9);
await actions.spotlightEvent(9, { type: "cleared", reason: "expired", generation: genNow });
out.fresh_event_status = await status(9);

// 11. Navigation clears state and drops the stored draw.
out.draw11 = await draw(10, { target: "Save", label: "Before nav" });
actions.spotlightNavigation(10);
out.nav_status = await status(10);
await actions.spotlightReshow(10);
out.nav_reshow_status = await status(10);

// 12. Session end closes the audit entry.
out.draw12 = await draw(11, { target: "Save", label: "Session point" });
await sleep(50);
const beforeEnd = log().filter((e) => e.label === "Session point");
actions.spotlightSessionEnd();
await sleep(50);
out.session_log_reason = (log().find((e) => e.id === beforeEnd[0].id) || {}).clearedReason || null;
out.session_status = await status(11);

// 13. Allowlist revocation clears a showing highlight with reason settings.
tabUrl = "https://www.paypal.com/signin";
storageLocal.set("spotlightAllowlist", ["paypal.com"]);
out.draw13 = await draw(12, { target: "Save", label: "On the bank" });
storageLocal.set("spotlightAllowlist", []);
await actions.spotlightSettingsChanged();
out.allowlist_status = await status(12);
tabUrl = "https://example.com/form";

// 14. Acting on the highlighted element clears it shortly after.
out.draw14 = await draw(13, { target: "Save", label: "Click this" });
out.click14 = await actions.dispatch({ action: "click", params: { tabId: 13, target: "Save" }, agent: "Sigil" });
await sleep(1000);
out.action_clear_status = await status(13);

// 15. F2: a minimum draw interval applies to ALL draws, labelled or not,
// and log eviction drops unlabelled entries before labelled ones.
const safeRect = { rect: { x: 900, y: 600, width: 50, height: 50 }, mode: "ring" };
out.flood_evidence = await draw(18, { ...safeRect, label: "EVIDENCE entry" });
out.flood_immediate_unlabelled = await draw(18, { ...safeRect });
out.flood_other_tab = await draw(19, { ...safeRect });
await sleep(300);
out.flood_after_interval = await draw(18, { ...safeRect });
await sleep(50); // let that draw's log append settle before seeding
// Eviction exercised through the real append path: seed the log so the
// labelled evidence entry is the OLDEST of 200 entries, then push five
// more unlabelled draws (spaced past the draw interval) through it.
const evEntry = log().find((e) => e.label === "EVIDENCE entry");
storageLocal.set("spotlightLog", [evEntry,
  ...Array.from({ length: 199 }, (_, i) => ({ id: `filler-${i}`, label: null, clearedReason: null, capture: null }))]);
for (let i = 0; i < 5; i++) {
  await sleep(300);
  await draw(18, { ...safeRect });
}
await sleep(50);
out.flood_log_size = log().length;
out.flood_evidence_survives = log().some((e) => e.label === "EVIDENCE entry");
out.flood_fillers_remaining = log().filter((e) => String(e.id).startsWith("filler-")).length;

// 16. F4: geometry reshows re-run the sensitive check. A point draw lands
// on the button; the page then swaps a password field under the stored
// point (the PoC scenario); a reshow must refuse and clear, not replay.
out.reshow_point_draw = await draw(20, { x: 560, y: 120, label: "The button" });
pwRect = rect(500, 100, 200, 60);
out.reshow_fresh_control = await draw(21, { x: 560, y: 120, label: "Fresh check" });
out.reshow_point_result = await actions.spotlightReshow(20);
out.reshow_point_status = await status(20);
await actions.spotlightReshow(20);
out.reshow_point_status_again = await status(20);
// Same for a stored rect: drawn over a safe area, then a field moves in.
out.reshow_rect_draw = await draw(22, { rect: { x: 900, y: 600, width: 50, height: 50 }, mode: "ring", label: "Safe area" });
pwRect = rect(880, 580, 100, 60);
out.reshow_rect_result = await actions.spotlightReshow(22);
out.reshow_rect_status = await status(22);
pwRect = rect(10, 10, 100, 30);
// A generic draw is allowed over a sensitive field, fresh or reshown.
out.reshow_generic_draw = await draw(23, { x: 20, y: 20, generic: true });
out.reshow_generic_result = await actions.spotlightReshow(23);
out.reshow_generic_status = await status(23);

// 17. RV-3: a rect draw whose sensitive check call itself fails must
// fail closed: no draw is sent and the result is the check's failure.
rectCheckFails = true;
out.rect_check_fail = await draw(24, { rect: { x: 900, y: 600, width: 50, height: 50 }, mode: "ring", label: "Unchecked area" });
rectCheckFails = false;
out.rect_check_fail_status = await status(24);
out.rect_check_fail_draw_count = drawTabs.filter((t) => t === 24).length;

// 18. RV-4: an element reshow re-resolves the target. A stored GENERIC
// element draw must still reshow when the target is (or has become)
// sensitive - a fresh generic draw there succeeds with the fixed chip -
// while a stored non-generic draw keeps the current drop behaviour.
out.reshow_element_generic_draw = await draw(25, { selector: ".card", generic: true });
const genericDrawsBefore = drawTabs.filter((t) => t === 25).length;
out.reshow_element_generic_result = (await actions.spotlightReshow(25)) ?? null;
out.reshow_element_generic_draw_count = drawTabs.filter((t) => t === 25).length - genericDrawsBefore;
out.reshow_element_generic_status = await status(25);
out.reshow_element_nongeneric_draw = await draw(26, { target: "Save", label: "Button first" });
btnSave.__children = [pw];
const nongenericDrawsBefore = drawTabs.filter((t) => t === 26).length;
out.reshow_element_nongeneric_result = (await actions.spotlightReshow(26)) ?? null;
out.reshow_element_nongeneric_draw_count = drawTabs.filter((t) => t === 26).length - nongenericDrawsBefore;
btnSave.__children = [];

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def worker():
    harness = HARNESS.replace("__ACTIONS__", (EXT / "actions.js").as_uri())
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_element_draw_and_status(worker):
    d = worker["draw1"]
    assert d["success"] is True
    assert d["mode_effective"] == "ring"
    assert d["ttl_ms"] >= 4000
    assert d["covered"] is False and d["in_view"] is True
    assert worker["status1"]["showing"] is True


def test_replace_closes_previous_log_entry(worker):
    entries = worker["log_after_replace"]
    first = [e for e in entries if e["label"] == "About to click Save"]
    assert first and first[0]["clearedReason"] == "replaced"


def test_rate_limit_survives_clear(worker):
    assert worker["rate_limited"]["code"] == "rate_limited"
    assert worker["clear1"]["was_showing"] is True
    # Clearing does not lift the 2s labelled-draw window.
    assert worker["rate_limited_after_clear"]["code"] == "rate_limited"
    assert worker["status_after_clear"]["cleared_reason"] == "cleared"


def test_ambiguous_target(worker):
    assert worker["ambiguous"]["code"] == "target_ambiguous"
    assert worker["ambiguous"]["count"] == 2
    assert len(worker["ambiguous"]["candidates"]) == 2


def test_sensitive_target_and_generic(worker):
    assert worker["sensitive"]["code"] == "sensitive_target"
    assert worker["generic"]["success"] is True
    assert worker["generic_log_label"] == "(generic sensitive-field chip)"


def test_downgrades(worker):
    assert worker["never_dim"]["mode_effective"] == "ring"
    assert worker["never_dim"]["downgraded_reason"] == "never_dim"
    assert worker["viewport_fill"]["mode_effective"] == "ring"
    assert worker["viewport_fill"]["downgraded_reason"] == "viewport_fill"


def test_paused_tab(worker):
    assert worker["paused_draw"]["code"] == "tab_paused"
    assert worker["paused_status"]["success"] is True


def test_geometry_validation(worker):
    assert worker["rect_off_viewport"]["code"] == "bad_params"
    assert worker["rect_point_mode"]["code"] == "bad_params"
    assert worker["rect_over_field"]["code"] == "sensitive_target"
    p = worker["point_ok"]
    assert p["success"] is True and p["scale_factor"] == 2 and p["in_view"] is True
    assert worker["point_on_field"]["code"] == "sensitive_target"
    assert worker["rect_between_probes"]["code"] == "sensitive_target"
    assert worker["rect_below_fold"]["code"] == "sensitive_target"


def test_dropped_draw_not_showing(worker):
    assert worker["dropped"]["code"] == "target_not_found"
    assert worker["dropped_status"]["showing"] is False


def test_stale_generation_event_ignored(worker):
    assert worker["stale_event_status"]["showing"] is True
    assert worker["fresh_event_status"]["showing"] is False
    assert worker["fresh_event_status"]["cleared_reason"] == "expired"


def test_navigation_clears_and_blocks_reshow(worker):
    assert worker["nav_status"]["cleared_reason"] == "navigation"
    assert worker["nav_reshow_status"]["showing"] is False


def test_session_end_closes_log_entry(worker):
    assert worker["session_log_reason"] == "session_end"
    assert worker["session_status"]["cleared_reason"] == "session_end"


def test_allowlist_revocation_clears(worker):
    assert worker["draw13"]["success"] is True
    assert worker["allowlist_status"]["cleared_reason"] == "settings"


def test_min_draw_interval_covers_unlabelled_draws(worker):
    assert worker["flood_evidence"]["success"] is True
    # No label on the second draw: the old code rate-limited labelled
    # draws only, so this went through and appended a log entry.
    assert worker["flood_immediate_unlabelled"]["code"] == "rate_limited"
    # The interval is per tab, and a draw past it succeeds again.
    assert worker["flood_other_tab"]["success"] is True
    assert worker["flood_after_interval"]["success"] is True


def test_log_eviction_drops_unlabelled_before_labelled(worker):
    assert worker["flood_log_size"] == 200
    # The labelled evidence entry was the oldest entry in a full log;
    # five unlabelled appends must evict fillers, never it.
    assert worker["flood_evidence_survives"] is True
    assert worker["flood_fillers_remaining"] == 194


def test_geometry_reshow_rechecks_sensitive(worker):
    assert worker["reshow_point_draw"]["success"] is True
    # Control: a fresh draw at the stored point is now refused.
    assert worker["reshow_fresh_control"]["code"] == "sensitive_target"
    # The reshow must refuse the same way, and clear the stored draw.
    r = worker["reshow_point_result"]
    assert r["success"] is False and r["code"] == "sensitive_target"
    assert worker["reshow_point_status"]["showing"] is False
    assert worker["reshow_point_status"]["cleared_reason"] == "sensitive_target"
    assert worker["reshow_point_status_again"]["showing"] is False
    assert worker["reshow_rect_draw"]["success"] is True
    r2 = worker["reshow_rect_result"]
    assert r2["success"] is False and r2["code"] == "sensitive_target"
    assert worker["reshow_rect_status"]["showing"] is False


def test_generic_reshow_still_allowed_over_field(worker):
    assert worker["reshow_generic_draw"]["success"] is True
    assert worker["reshow_generic_result"]["success"] is True
    assert worker["reshow_generic_status"]["showing"] is True


def test_rect_draw_fails_closed_when_check_fails(worker):
    assert worker["rect_check_fail"]["success"] is False, worker["rect_check_fail"]
    assert worker["rect_check_fail"]["code"] == "inject_failed", worker["rect_check_fail"]
    assert worker["rect_check_fail_status"]["showing"] is False
    assert worker["rect_check_fail_draw_count"] == 0


def test_generic_element_reshow_not_dropped_when_sensitive(worker):
    assert worker["reshow_element_generic_draw"]["success"] is True
    r = worker["reshow_element_generic_result"]
    assert r["success"] is True, r
    assert worker["reshow_element_generic_draw_count"] == 1
    assert worker["reshow_element_generic_status"]["showing"] is True
    # The non-generic counterpart keeps the drop behaviour: no result
    # and no new draw sent.
    assert worker["reshow_element_nongeneric_draw"]["success"] is True
    assert worker["reshow_element_nongeneric_result"] is None
    assert worker["reshow_element_nongeneric_draw_count"] == 0


def test_action_on_highlight_clears_it(worker):
    assert worker["click14"]["success"] is True
    assert worker["action_clear_status"]["showing"] is False
    assert worker["action_clear_status"]["cleared_reason"] == "action"
