"""Behavioural tests for the banner.js tab-title decoration: while the
pill shows, document.title says what the agent is doing; the page's own
title is tracked underneath and restored exactly. Run under Node with a
fake DOM. Skipped when Node is missing."""
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
const moInstances = [];

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
  title: "Original Page",
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
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; this.target = null; moInstances.push(this); }
  observe(target) { this.target = target; }
  disconnect() { this.target = null; }
};
globalThis.chrome = {
  runtime: {
    onMessage: { addListener: (fn) => { msgListener = fn; } },
    sendMessage: (m) => { sent.push(m); return undefined; },
  },
};

const src = fs.readFileSync(process.argv[2], "utf8");
eval(src);

const out = {};
const DASH = "\u2014";
function titleObserver() {
  return moInstances.filter((m) => m.target === head).pop();
}

// 1. Working with a declared label: the title says what the agent does.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", doing: "Deploying the cache rule", tabTitle: true });
out.working_title = document.title;

// 2. The page retitles itself under the decoration: ours re-asserts, and
// the page's new title becomes the restore target.
document.title = "Inbox (2)";
const obs = titleObserver();
out.observer_armed = Boolean(obs);
if (obs) obs.cb();
out.reasserted = document.title;

// 3. Work ends: the page's latest title comes back, not the stale one.
msgListener({ type: "BANNER", state: "hidden" });
out.restored = document.title;

// 4. No label: the per-action verb carries the title.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", verb: "Clicking", tabTitle: true });
out.verb_title = document.title;

// 5. Takeover: the title says Paused, and restores after.
msgListener({ type: "BANNER", state: "takenover", agent: "Sigil", tabTitle: true });
out.paused_title = document.title;
msgListener({ type: "BANNER", state: "hidden" });
out.restored_after_pause = document.title;

// 6. Toggle off: a working message with tabTitle false never decorates.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", doing: "Sneaky", tabTitle: false });
out.toggle_off_title = document.title;
msgListener({ type: "BANNER", state: "hidden" });

// 7. Neither label nor verb yet: plain Working.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", tabTitle: true });
out.plain_title = document.title;
msgListener({ type: "BANNER", state: "hidden" });
out.final_title = document.title;

// 8. Restore race: the page retitles and the hide lands before the
// observer callback runs - the page's newer title must stand.
msgListener({ type: "BANNER", state: "working", agent: "Sigil", doing: "Race test", tabTitle: true });
out.race_decorated = document.title;
document.title = "Fresh Title";
msgListener({ type: "BANNER", state: "hidden" });
out.race_after_hide = document.title;
const stale = titleObserver();
if (stale) stale.cb();
out.race_after_stale_cb = document.title;

console.log(JSON.stringify(out));
process.exit(0);
"""


@pytest.fixture(scope="module")
def banner():
    harness_file = Path(__file__).parent / "_banner_title_harness.mjs"
    harness_file.write_text(HARNESS, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", str(harness_file), str(EXT / "banner.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    finally:
        harness_file.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_working_title_shows_the_doing_label(banner):
    assert banner["working_title"] == "Sigil — Deploying the cache rule"


def test_page_retitle_is_tracked_and_reasserted(banner):
    assert banner["observer_armed"] is True
    assert banner["reasserted"] == "Sigil — Deploying the cache rule"
    assert banner["restored"] == "Inbox (2)"


def test_verb_fallback(banner):
    assert banner["verb_title"] == "Sigil — Clicking"


def test_paused_title_and_restore(banner):
    assert banner["paused_title"] == "Sigil — Paused"
    assert banner["restored_after_pause"] == "Inbox (2)"


def test_toggle_off_never_decorates(banner):
    assert banner["toggle_off_title"] == "Inbox (2)"


def test_plain_working_fallback(banner):
    assert banner["plain_title"] == "Sigil — Working"
    assert banner["final_title"] == "Inbox (2)"


def test_restore_race_keeps_the_pages_newer_title(banner):
    assert banner["race_decorated"] == "Sigil — Race test"
    assert banner["race_after_hide"] == "Fresh Title"
    assert banner["race_after_stale_cb"] == "Fresh Title"
