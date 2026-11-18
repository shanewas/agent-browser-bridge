"""Behavioural tests for banner.js (the presence pill) and the page.js cover
exceptions, run under Node with a stub DOM. Skipped when Node is missing."""
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
    classList: {
      add: (c) => el._classes.add(c),
      remove: (c) => el._classes.delete(c),
      toggle: (c, force) => { if (force) el._classes.add(c); else el._classes.delete(c); },
      contains: (c) => el._classes.has(c),
    },
    appendChild(child) { el.children.push(child); child.isConnected = true; return child; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    setAttribute() {},
    attachShadow() { el._shadow = makeEl("shadow"); return el._shadow; },
    getBoundingClientRect: () => ({ left: 400, right: 600, top: 12, bottom: 50 }),
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
  fullscreenElement: null,
  createElement: (t) => makeEl(t),
  addEventListener() {},
  getElementById: () => null,
};
globalThis.window = {
  addEventListener() {},
  __eabBanner: undefined,
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.chrome = {
  runtime: {
    onMessage: { addListener: (fn) => { msgListener = fn; } },
    sendMessage: (m) => { sent.push(m); return undefined; },
  },
};

const src = fs.readFileSync(process.argv[2], "utf8");
eval(src);

const out = {};
const banner = globalThis.window.__eabBanner;
out.api = Boolean(banner);
const host = documentElement.children.find((c) => c.id === "__eab_banner_host");
out.host_mounted = Boolean(host);
out.query_sent = sent.some((m) => m.type === "BANNER_QUERY");

msgListener({ type: "BANNER", state: "working", agent: "Sigil", verb: "Clicking", mutating: true, workingUntil: Date.now() + 60000 });
out.state_working = banner._state();
const wrap = host._shadow.children.find((c) => c._classes.has("wrap"));
const pill = wrap.children[0];
out.label = pill.children[1].textContent;
out.verb = pill.children[2].textContent;

// a genuine click on the pill body asks for takeover
const click = (pill._listeners.click || [])[0];
click({ isTrusted: true, target: pill.children[0], stopPropagation() {} });
out.takeover_sent = sent.some((m) => m.type === "TAKEOVER");
// an untrusted (synthetic) click must not
sent.length = 0;
click({ isTrusted: false, target: pill.children[0], stopPropagation() {} });
out.untrusted_ignored = !sent.some((m) => m.type === "TAKEOVER");

msgListener({ type: "BANNER", state: "takenover", agent: "Sigil" });
out.state_paused = banner._state();
out.paused_label = pill.children[1].textContent;
out.paused_class = pill._classes.has("paused");

banner.fadeForPoint(500, 20);
out.faded = host.style.opacity === "0.25";
banner.fadeForPoint(10, 400);
out.still_faded_only_for_pill = host.style.opacity === "0.25";

console.log(JSON.stringify(out));
"""


COVER_HARNESS = r"""
import { pathToFileURL } from "node:url";
// process.argv[2] is a plain filesystem path; on Windows a drive-letter path
// parses as a "c:" URL scheme and import() rejects it, so convert first.
const page = await import(pathToFileURL(process.argv[2]).href);
const host = { id: "__eab_banner_host", style: { display: "" }, tagName: "DIV" };
const iframe = { id: "", tagName: "IFRAME", style: {} };
const div = { id: "", tagName: "DIV", style: {} };
let seq = [];
globalThis.document = {
  elementFromPoint: () => seq.shift(),
  getElementById: (id) => (id === "__eab_banner_host" ? host : null),
};
const out = {};
seq = [host, iframe];
out.under_pill_iframe = page.pageCoverCheck(10, 10);
out.host_restored = host.style.display === "";
seq = [host, div];
out.under_pill_div = page.pageCoverCheck(10, 10);
seq = [div];
out.plain_div = page.pageCoverCheck(10, 10);
console.log(JSON.stringify(out));
"""


def test_cover_check_sees_through_the_pill(tmp_path):
    harness = tmp_path / "cover_harness.mjs"
    harness.write_text(COVER_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(EXT / "page.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    # The subframe case: the iframe under the pill is the frame itself, not a cover.
    assert out["under_pill_iframe"] == {"covered": False}
    assert out["host_restored"]
    # A real element under the pill still counts as covering.
    assert out["under_pill_div"]["covered"] is True
    assert out["plain_div"]["covered"] is True


def test_banner_behaviour(tmp_path):
    harness = tmp_path / "banner_harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(EXT / "banner.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["api"] and out["host_mounted"] and out["query_sent"]
    assert out["state_working"] == "working"
    assert out["label"].startswith("Sigil Working")
    assert "Clicking" in out["verb"]
    assert out["takeover_sent"] and out["untrusted_ignored"]
    assert out["state_paused"] == "paused"
    assert out["paused_label"].startswith("You")
    assert out["paused_class"]
    assert out["faded"] and out["still_faded_only_for_pill"]


PRESENCE_HARNESS = r"""
import fs from "node:fs";

const sent = [];
let msgListener = null;

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
    classList: {
      add: (c) => el._classes.add(c),
      remove: (c) => el._classes.delete(c),
      toggle: (c, force) => { if (force) el._classes.add(c); else el._classes.delete(c); },
      contains: (c) => el._classes.has(c),
    },
    appendChild(child) { el.children.push(child); child.isConnected = true; return child; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    setAttribute() {},
    attachShadow() { el._shadow = makeEl("shadow"); return el._shadow; },
    getBoundingClientRect: () => ({ left: 400, right: 600, top: 12, bottom: 50 }),
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
  fullscreenElement: null,
  createElement: (t) => makeEl(t),
  addEventListener() {},
  getElementById: () => null,
};
globalThis.window = {
  addEventListener() {},
  __eabBanner: undefined,
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.chrome = {
  runtime: {
    onMessage: { addListener: (fn) => { msgListener = fn; } },
    sendMessage: (m) => { sent.push(m); return undefined; },
  },
};

const src = fs.readFileSync(process.argv[2], "utf8");
eval(src);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = {};
const banner = globalThis.window.__eabBanner;
const host = documentElement.children.find((c) => c.id === "__eab_banner_host");
const frameHost = documentElement.children.find((c) => c.id === "__eab_frame_host");
out.frame_host_mounted = Boolean(frameHost);
out.frame_host_passive = String(frameHost.style.cssText || "").includes("pointer-events:none");
out.frame_below_pill = String(frameHost.style.cssText || "").includes("2147483646")
  && String(host.style.cssText || "").includes("2147483647");
const frame = frameHost._shadow.children.find((c) => c._classes.has("frame"));
out.frame_mounted = Boolean(frame);
const frameCss = frameHost._shadow.children.map((c) => c._text || "").join("\n");
out.frame_gradient = frameCss.includes("linear-gradient") && frameCss.includes("eabframeflow");
out.frame_reduced_motion = frameCss.includes("prefers-reduced-motion");
out.frame_off_initially = !frame._classes.has("on");
const wrap = host._shadow.children.find((c) => c._classes.has("wrap"));

// A mutating action shows the pill + frame. workingUntil is a relic the worker
// no longer sends; a stale one in the past must not hide a working pill.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", mutating: true, workingUntil: Date.now() + 100 });
out.working_state = banner._state();
out.frame_on_working = frame._classes.has("on");

// The worker's hide lands inside the 800 ms minimum display for a mutating
// action: the pill stays until the minimum passes, then hides.
msgListener({ type: "BANNER", state: "hidden" });
await sleep(250);
out.min_display_holds = banner._state() === "working" && frame._classes.has("on");
await sleep(1100);
out.min_display_then_hides = banner._state() === "hidden" && !frame._classes.has("on") && !wrap._classes.has("on");

// Working again: no local timer may hide it, however long the stretch runs.
// The old banner hid at max(workingUntil, shownAt + 800) - long past by now.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", mutating: true, workingUntil: Date.now() + 100 });
await sleep(1500);
out.working_sticky = banner._state() === "working" && wrap._classes.has("on");
out.frame_sticky = frame._classes.has("on");

// Paused: pill stays (user is in control), frame goes away.
msgListener({ type: "BANNER", state: "paused", agent: "Sigil" });
out.paused_state = banner._state();
out.frame_off_paused = !frame._classes.has("on") && wrap._classes.has("on");

// Working again: frame returns, and capture hiding covers the frame host too.
msgListener({ type: "BANNER", state: "working", agent: "Sigil" });
out.frame_back_on_resume = frame._classes.has("on");
banner.hideForCapture();
out.capture_hides_both = host.style.visibility === "hidden" && frameHost.style.visibility === "hidden";
banner.restoreAfterCapture();
out.capture_restores_both = host.style.visibility === "" && frameHost.style.visibility === "";

console.log(JSON.stringify(out));
"""


YIELD_HARNESS = r"""
import fs from "node:fs";

const sent = [];
let msgListener = null;
const winListeners = {};

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
    classList: {
      add: (c) => el._classes.add(c),
      remove: (c) => el._classes.delete(c),
      toggle: (c, force) => { if (force) el._classes.add(c); else el._classes.delete(c); },
      contains: (c) => el._classes.has(c),
    },
    appendChild(child) { el.children.push(child); child.isConnected = true; return child; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    setAttribute() {},
    attachShadow() { el._shadow = makeEl("shadow"); return el._shadow; },
    getBoundingClientRect: () => ({ left: 400, right: 600, top: 12, bottom: 50 }),
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
  fullscreenElement: null,
  createElement: (t) => makeEl(t),
  addEventListener() {},
  getElementById: () => null,
};
globalThis.window = {
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
  __eabBanner: undefined,
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.chrome = {
  runtime: {
    onMessage: { addListener: (fn) => { msgListener = fn; } },
    sendMessage: (m) => { sent.push(m); return undefined; },
  },
};

const src = fs.readFileSync(process.argv[2], "utf8");
eval(src);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fire = (type, ev) => { for (const fn of winListeners[type] || []) fn(ev); };
const inputs = () => sent.filter((m) => m.type === "USER_INPUT").length;
const out = {};
const banner = globalThis.window.__eabBanner;
const host = documentElement.children.find((c) => c.id === "__eab_banner_host");
const wrap = host._shadow.children.find((c) => c._classes.has("wrap"));
const pill = wrap.children[0];
const label = pill.children[1];
const doneBtn = pill.children[3];

out.press_listeners = ["pointerdown", "mousedown", "keydown"]
  .every((t) => (winListeners[t] || []).length > 0);
out.no_watch_listeners = !winListeners.mousemove && !winListeners.pointermove && !winListeners.wheel;

msgListener({ type: "BANNER", state: "working", agent: "Sigil", mutating: true });
out.working = banner._state();

// Watching is not yielding: mouse movement and scroll have no listener, and a
// synthetic (untrusted) press is ignored.
fire("mousemove", { isTrusted: true });
fire("wheel", { isTrusted: true });
out.watch_no_yield = inputs() === 0;
fire("mousedown", { isTrusted: false });
out.untrusted_no_yield = inputs() === 0;

// A genuine mouse press yields (one USER_INPUT signal).
fire("mousedown", { isTrusted: true });
out.mousedown_yields = inputs() === 1;

// The 1 s signal throttle folds an immediate second signal; after it lapses a
// keydown yields too.
fire("keydown", { isTrusted: true });
out.throttled = inputs() === 1;
await sleep(1100);
fire("keydown", { isTrusted: true });
out.keydown_yields = inputs() === 2;

// The worker confirms the takeover: the pill flips to the yielded state - the
// user is in control, and the one action offered is Done.
msgListener({ type: "BANNER", state: "takenover", agent: "Sigil" });
out.yielded_state = banner._state();
out.yielded_label = label.textContent;
out.done_label = doneBtn.textContent;
out.done_visible = doneBtn.style.display !== "none";

// Done hands the tab back through the existing RESUME message.
sent.length = 0;
const doneClick = (doneBtn._listeners.click || [])[0];
doneClick({ isTrusted: true, target: doneBtn, stopPropagation() {} });
out.done_sends_resume = sent.some((m) => m.type === "RESUME");

console.log(JSON.stringify(out));
"""


def test_banner_yield_on_click_and_done_resume(tmp_path):
    harness = tmp_path / "yield_harness.mjs"
    harness.write_text(YIELD_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(EXT / "banner.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["press_listeners"] and out["no_watch_listeners"]
    assert out["working"] == "working"
    assert out["watch_no_yield"] and out["untrusted_no_yield"]
    assert out["mousedown_yields"] and out["throttled"] and out["keydown_yields"]
    assert out["yielded_state"] == "paused"
    assert out["yielded_label"] == "You\u2019re in control"
    assert out["done_label"] == "Done" and out["done_visible"]
    assert out["done_sends_resume"]


LASTPOINT_HARNESS = r"""
import fs from "node:fs";

const sent = [];
let msgListener = null;
const winListeners = {};
let focused = false;

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
    classList: {
      add: (c) => el._classes.add(c),
      remove: (c) => el._classes.delete(c),
      toggle: (c, force) => { if (force) el._classes.add(c); else el._classes.delete(c); },
      contains: (c) => el._classes.has(c),
    },
    appendChild(child) { el.children.push(child); child.isConnected = true; return child; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    setAttribute() {},
    attachShadow() { el._shadow = makeEl("shadow"); return el._shadow; },
    getBoundingClientRect: () => ({ left: 400, right: 600, top: 12, bottom: 50 }),
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
  fullscreenElement: null,
  createElement: (t) => makeEl(t),
  addEventListener() {},
  getElementById: () => null,
};
globalThis.window = {
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
  __eabBanner: undefined,
  __eabSpotlight: {
    interactionInside: (x, y) => x < 300,
    targetFocused: () => focused,
  },
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.chrome = {
  runtime: {
    onMessage: { addListener: (fn) => { msgListener = fn; } },
    sendMessage: (m) => { sent.push(m); return undefined; },
  },
};

const src = fs.readFileSync(process.argv[2], "utf8");
eval(src);

const fire = (type, ev) => { for (const fn of winListeners[type] || []) fn(ev); };
const inputs = () => sent.filter((m) => m.type === "USER_INPUT").length;
const out = {};
const host = documentElement.children.find((c) => c.id === "__eab_banner_host");
const wrap = host._shadow.children.find((c) => c._classes.has("wrap"));
const lastpoint = wrap.children.find((c) => c._classes.has("lastpoint"));
const frameHost = documentElement.children.find((c) => c.id === "__eab_frame_host");
const frame = frameHost._shadow.children.find((c) => c._classes.has("frame"));

msgListener({ type: "BANNER", state: "working", agent: "Sigil", mutating: true, workingUntil: Date.now() + 60000 });
out.frame_on_working = frame._classes.has("on");

// Spotlight mode dims the presence frame to 40%.
msgListener({ type: "BANNER", state: "frame_dim", dim: true });
out.frame_dimmed = frame.style.opacity;
msgListener({ type: "BANNER", state: "frame_dim", dim: false });
out.frame_undimmed = frame.style.opacity;

// Input inside the highlighted rect is not a takeover signal; typing into
// the highlighted element is not either. Input outside still is.
focused = true;
fire("keydown", { isTrusted: true, type: "keydown" });
out.keydown_focused_suppressed = inputs() === 0;
focused = false;
fire("mousedown", { isTrusted: true, type: "mousedown", clientX: 900, clientY: 100 });
out.outside_input_signals = inputs() === 1;
fire("mousedown", { isTrusted: true, type: "mousedown", clientX: 100, clientY: 100 });
out.inside_input_suppressed = inputs() === 1;

// The retained label shows after a takeover and re-shows on click.
msgListener({ type: "BANNER", state: "takenover", agent: "Sigil" });
msgListener({ type: "BANNER", state: "lastpoint", label: "Save button" });
out.lastpoint_text = lastpoint.textContent;
out.lastpoint_on = lastpoint._classes.has("on");
sent.length = 0;
const lpClick = (lastpoint._listeners.click || [])[0];
lpClick({ isTrusted: true, stopPropagation() {} });
out.click_reshows = sent.some((m) => m.type === "SPOTLIGHT_RESHOW");
msgListener({ type: "BANNER", state: "lastpoint_clear" });
out.lastpoint_cleared = !lastpoint._classes.has("on");

console.log(JSON.stringify(out));
"""


def test_banner_lastpoint_frame_dim_and_input_exemption(tmp_path):
    harness = tmp_path / "lastpoint_harness.mjs"
    harness.write_text(LASTPOINT_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(EXT / "banner.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["frame_on_working"]
    assert out["frame_dimmed"] == "0.4" and out["frame_undimmed"] == ""
    assert out["keydown_focused_suppressed"] and out["inside_input_suppressed"]
    assert out["outside_input_signals"]
    assert out["lastpoint_text"] == "Last point: Save button" and out["lastpoint_on"]
    assert out["click_reshows"] and out["lastpoint_cleared"]


def test_banner_sticky_working_and_frame(tmp_path):
    harness = tmp_path / "presence_harness.mjs"
    harness.write_text(PRESENCE_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(EXT / "banner.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["frame_host_mounted"] and out["frame_mounted"]
    assert out["frame_host_passive"] and out["frame_below_pill"]
    assert out["frame_gradient"] and out["frame_reduced_motion"]
    assert out["frame_off_initially"]
    assert out["working_state"] == "working" and out["frame_on_working"]
    assert out["min_display_holds"] and out["min_display_then_hides"]
    assert out["working_sticky"] and out["frame_sticky"]
    assert out["paused_state"] == "paused" and out["frame_off_paused"]
    assert out["frame_back_on_resume"]
    assert out["capture_hides_both"] and out["capture_restores_both"]
