"""Behavioural tests for spotlight.js (the agent-pointing overlay), run under
Node with a stub DOM. Skipped when Node is missing."""
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

const sent = [];
let msgListener = null;
const windowListeners = {};

function makeEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    id: "",
    children: [],
    style: {},
    _text: "",
    _listeners: {},
    _classes: new Set(),
    isConnected: false,
    offsetWidth: 180,
    offsetHeight: 48,
    classList: {
      add: (c) => el._classes.add(c),
      remove: (c) => el._classes.delete(c),
      toggle: (c, force) => { if (force) el._classes.add(c); else el._classes.delete(c); },
      contains: (c) => el._classes.has(c),
    },
    appendChild(child) { el.children.push(child); child.isConnected = true; return child; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    setAttribute() {},
    getAttribute() { return null; },
    attachShadow() { el._shadow = makeEl("shadow"); return el._shadow; },
    getBoundingClientRect: () => ({ left: 100, top: 100, right: 220, bottom: 140, x: 100, y: 100, width: 120, height: 40 }),
  };
  Object.defineProperty(el, "className", {
    get: () => [...el._classes].join(" "),
    set: (v) => { el._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(el, "textContent", {
    get: () => el._text + el.children.map((c) => c.textContent).join(""),
    set: (v) => { el._text = String(v); el.children = []; },
  });
  return el;
}

const documentElement = makeEl("html");
const head = makeEl("head");
globalThis.document = {
  documentElement,
  head,
  createElement: (tag) => makeEl(tag),
  createElementNS: (ns, tag) => makeEl(tag),
  addEventListener() {},
  visibilityState: "visible",
};
globalThis.window = globalThis;
globalThis.innerWidth = 1280;
globalThis.innerHeight = 800;
globalThis.scrollX = 0;
globalThis.scrollY = 0;
globalThis.addEventListener = (type, fn) => { (windowListeners[type] = windowListeners[type] || []).push(fn); };
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
const moInstances = [];
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; moInstances.push(this); }
  observe() {}
  disconnect() {}
};
globalThis.chrome = {
  runtime: {
    sendMessage: (msg) => { sent.push(msg); return Promise.resolve({}); },
    onMessage: { addListener: (fn) => { msgListener = fn; } },
    connect: () => ({ onDisconnect: { addListener: () => {} } }),
  },
};

const targetEl = makeEl("button");
targetEl.isConnected = true;
targetEl.parentElement = makeEl("div");
globalThis.__eabSpotlightTargets = new Map([["k1", targetEl]]);

eval(fs.readFileSync("__SPOT__", "utf8"));

const out = {};
const api = globalThis.__eabSpotlight;
out.has_api = Boolean(api);
out.initial = api._state();

function findByClass(root, cls) {
  if (root._classes && root._classes.has(cls)) return root;
  for (const c of root.children || []) {
    const hit = findByClass(c, cls);
    if (hit) return hit;
  }
  return null;
}
const hosts = documentElement.children;
const spotHost = hosts.find((h) => h.id === "__eab_spotlight_host");
const stage = spotHost._shadow.children[1];
const chip = findByClass(stage, "chip");

let drawResp = null;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "About to click Save", agent: "Sigil",
  highlightUntil: Date.now() + 4000, generation: 1, session: 1,
  descriptor: { tag: "button", role: "button", name: "Save" } },
  {}, (r) => { drawResp = r; });
out.after_draw = api._state();
out.draw_resp = drawResp;
out.chip_text = chip.textContent;
out.stage_on = stage._classes.has("on");

// Esc clears locally and reports.
for (const fn of windowListeners.keydown || []) fn({ key: "Escape" });
out.after_esc = api._state();
out.esc_report = sent.filter((m) => m.type === "SPOTLIGHT_CLEARED").map((m) => m.reason);

// Worker clear does not echo back.
sent.length = 0;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Second", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 2, session: 1, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
msgListener({ type: "SPOTLIGHT", op: "clear", reason: "cleared" }, {}, () => {});
out.after_worker_clear = api._state();
out.worker_clear_echoes = sent.filter((m) => m.type === "SPOTLIGHT_CLEARED").length;

// Capture hide/restore is a counted pair.
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "point", point: { x: 50, y: 60 },
  label: "Here", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 3, session: 1, descriptor: null }, {}, () => {});
api.hideForCapture();
api.hideForCapture();
api.restoreAfterCapture();
out.still_hidden_after_one_restore = spotHost.style.visibility;
api.restoreAfterCapture();
out.visible_after_second_restore = spotHost.style.visibility;

// A new session replaces the old highlight.
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "New session", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 4, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
out.new_session = api._state();

// A sibling mutation under the target's parent is not target-gone; the
// target's own removal is.
sent.length = 0;
const targetMO = moInstances[moInstances.length - 1];
targetMO.cb([{ type: "childList", removedNodes: [] }]);
out.sibling_mutation_gone_reports = sent.filter((m) => m.type === "SPOTLIGHT_TARGET_GONE").length;
targetEl.isConnected = false;
targetMO.cb([{ type: "childList", removedNodes: [targetEl] }]);
out.removal_gone_reports = sent.filter((m) => m.type === "SPOTLIGHT_TARGET_GONE").length;
targetEl.isConnected = true;

// A draw inside the fade window of a local clear survives the fade timer.
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Fade one", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 5, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
for (const fn of windowListeners.keydown || []) fn({ key: "Escape" });
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Fade two", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 6, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
await new Promise((r) => setTimeout(r, 220));
out.fade_race_showing = api._state().showing;
out.fade_race_stage_on = stage._classes.has("on");

// An off-screen point still signals, via the edge indicator.
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "point", point: { x: 5000, y: 60 },
  label: "Far point", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 7, session: 2, descriptor: null }, {}, () => {});
const edgeEl = findByClass(stage, "edge");
out.offscreen_point_edge_display = edgeEl.style.display;
out.offscreen_point_edge_text = edgeEl.textContent;

// Expiry fires on the deadline.
sent.length = 0;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Short", agent: "Sigil", highlightUntil: Date.now() + 60,
  generation: 8, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
await new Promise((r) => setTimeout(r, 140));
out.after_expiry = api._state();
out.expiry_report = sent.filter((m) => m.type === "SPOTLIGHT_CLEARED").map((m) => m.reason);

// A draw the content script drops (stale generation) must say so: the worker
// used to mark success for a highlight that never landed.
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Fresh", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 9, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
let staleResp = null;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Stale", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 6, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } },
  {}, (r) => { staleResp = r; });
out.stale_draw_resp = staleResp;
out.stale_draw_kept_current = api._state();

// An evicted target key with no geometry fallback is a dropped draw too.
let missingResp = null;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "ghost",
  label: "Ghost", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 10, session: 2, descriptor: null }, {}, (r) => { missingResp = r; });
out.missing_target_resp = missingResp;
out.missing_target_kept_current = api._state().generation;

// Target-gone is latched per generation: repeated removal batches while the
// target stays disconnected send one report, not one per batch.
sent.length = 0;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Latch", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 11, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
targetEl.isConnected = false;
const latchMO = moInstances[moInstances.length - 1];
latchMO.cb([{ type: "childList", removedNodes: [targetEl] }]);
latchMO.cb([{ type: "childList", removedNodes: [targetEl] }]);
out.latch_gone_reports = sent.filter((m) => m.type === "SPOTLIGHT_TARGET_GONE").length;
targetEl.isConnected = true;

// A target collapsed to zero area reports gone instead of painting a ring.
sent.length = 0;
const realRect = targetEl.getBoundingClientRect;
targetEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: 0, bottom: 0, x: 0, y: 0, width: 0, height: 0 });
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Collapsed", agent: "Sigil", highlightUntil: Date.now() + 4000,
  generation: 12, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
out.zero_area_gone_reports = sent.filter((m) => m.type === "SPOTLIGHT_TARGET_GONE").length;
out.zero_area_stage_on = stage._classes.has("on");
targetEl.getBoundingClientRect = realRect;

// A capture hide whose restore never arrives is undone by the watchdog.
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Capture", agent: "Sigil", highlightUntil: Date.now() + 30000,
  generation: 13, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
api.hideForCapture();
out.capture_hidden = spotHost.style.visibility;
await new Promise((r) => setTimeout(r, 2200));
out.capture_watchdog_restored = spotHost.style.visibility;

// An ancestor swap (no observer fires) is caught by the liveness watchdog.
sent.length = 0;
targetEl.isConnected = false;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Orphaned", agent: "Sigil", highlightUntil: Date.now() + 30000,
  generation: 14, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
await new Promise((r) => setTimeout(r, 1300));
out.watchdog_gone_reports = sent.filter((m) => m.type === "SPOTLIGHT_TARGET_GONE").length;
targetEl.isConnected = true;

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def spot():
    harness = HARNESS.replace("__SPOT__", str(EXT / "spotlight.js"))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_api_and_initial_state(spot):
    assert spot["has_api"] is True
    assert spot["initial"] == {"showing": False}


def test_draw_shows_chip_with_identity_line(spot):
    assert spot["after_draw"]["showing"] is True
    assert spot["stage_on"] is True
    # Identity line names the agent; the label follows it.
    assert "SIGIL" in spot["chip_text"].upper()
    assert "About to click Save" in spot["chip_text"]
    # The synchronous placement feedback the worker acts on.
    assert spot["draw_resp"]["applied"] is True
    assert spot["draw_resp"]["covered"] is False
    assert spot["draw_resp"]["offscreen"] is False


def test_esc_clears_and_reports(spot):
    assert spot["after_esc"]["showing"] is False
    assert spot["esc_report"] == ["esc"]


def test_worker_clear_does_not_echo(spot):
    assert spot["after_worker_clear"]["showing"] is False
    assert spot["worker_clear_echoes"] == 0


def test_capture_hide_restore_is_counted(spot):
    assert spot["still_hidden_after_one_restore"] == "hidden"
    assert spot["visible_after_second_restore"] == ""


def test_new_session_replaces(spot):
    assert spot["new_session"]["showing"] is True
    assert spot["new_session"]["generation"] == 4


def test_expiry_clears_and_reports(spot):
    assert spot["after_expiry"]["showing"] is False
    assert spot["expiry_report"] == ["expired"]


def test_sibling_mutation_is_not_target_gone(spot):
    assert spot["sibling_mutation_gone_reports"] == 0
    assert spot["removal_gone_reports"] == 1


def test_draw_inside_fade_window_survives(spot):
    assert spot["fade_race_showing"] is True
    assert spot["fade_race_stage_on"] is True


def test_offscreen_point_shows_edge_indicator(spot):
    assert spot["offscreen_point_edge_display"] == ""
    assert spot["offscreen_point_edge_text"]


def test_dropped_draws_report_applied_false(spot):
    assert spot["stale_draw_resp"]["applied"] is False
    assert spot["stale_draw_resp"]["reason"] == "stale_generation"
    assert spot["stale_draw_kept_current"]["generation"] == 9
    assert spot["missing_target_resp"]["applied"] is False
    assert spot["missing_target_resp"]["reason"] == "target_missing"
    assert spot["missing_target_kept_current"] == 9


def test_target_gone_is_latched(spot):
    assert spot["latch_gone_reports"] == 1


def test_zero_area_target_reports_gone(spot):
    assert spot["zero_area_gone_reports"] == 1
    assert spot["zero_area_stage_on"] is False


def test_capture_hide_watchdog_restores(spot):
    assert spot["capture_hidden"] == "hidden"
    assert spot["capture_watchdog_restored"] == ""


def test_ancestor_removal_caught_by_watchdog(spot):
    assert spot["watchdog_gone_reports"] == 1
