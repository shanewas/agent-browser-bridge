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
globalThis.MutationObserver = class { observe() {} disconnect() {} };
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

// Expiry fires on the deadline.
sent.length = 0;
msgListener({ type: "SPOTLIGHT", op: "draw", mode: "ring", targetKey: "k1",
  label: "Short", agent: "Sigil", highlightUntil: Date.now() + 60,
  generation: 5, session: 2, descriptor: { tag: "button", role: "button", name: "Save" } }, {}, () => {});
await new Promise((r) => setTimeout(r, 140));
out.after_expiry = api._state();
out.expiry_report = sent.filter((m) => m.type === "SPOTLIGHT_CLEARED").map((m) => m.reason);

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
    assert spot["draw_resp"] is not None


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
