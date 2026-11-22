"""Worker-side tab title path: a daemon frame's doing label lands in the
BANNER presence payload, set_doing updates it live and an explicit null
clears it. Drives the real background.js + actions.js under Node with
chrome.* stubs and a fake WebSocket. Skipped when Node is missing."""
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
const listener = { addListener() {}, removeListener() {} };
const storageChangedListeners = [];
const runtimeListeners = [];
const bannerSent = [];
globalThis.chrome = {
  storage: {
    local: area(storageLocal), session: area(storageSession),
    onChanged: { addListener: (fn) => storageChangedListeners.push(fn) },
  },
  tabs: {
    get: async (id) => ({ id, url: "https://example.com/", active: true, windowId: 1, title: "T" }),
    query: async () => [{ id: 7, url: "https://example.com/", active: true, windowId: 1, title: "T" }],
    sendMessage: async (id, msg) => { bannerSent.push({ tabId: id, msg }); return {}; },
    onRemoved: listener, onUpdated: listener,
    create: async () => ({ id: 99 }), update: async () => ({}),
  },
  scripting: { executeScript: async () => [{ result: undefined }] },
  runtime: {
    getManifest: () => ({ version: "4.4.0" }), id: "testext",
    lastError: undefined, onMessage: { addListener: (fn) => runtimeListeners.push(fn) }, onConnect: listener, onInstalled: listener,
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
  commands: { onCommand: listener },
};

// ---- fake WebSocket -----------------------------------------------------------
const wsInstances = [];
globalThis.WebSocket = class {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; wsInstances.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.document = {
  createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
  addEventListener() {},
  documentElement: { tagName: "HTML" },
  body: { tagName: "BODY" },
};

await import("__BACKGROUND__");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(150);
const ws = wsInstances[0];
const out = { ws_connected: Boolean(ws) };

async function drive(frame) {
  bannerSent.length = 0;
  const p = ws.onmessage({ data: JSON.stringify(frame) });
  if (p && p.catch) p.catch(() => {});
  await sleep(250);
  return bannerSent.filter((m) => m.msg && m.msg.type === "BANNER");
}

// A mutating command carrying the label: presence goes up with it.
let msgs = await drive({ id: "1", action: "click", params: { tabId: 7 }, agent: "Sigil", doing: "Deploying the cache rule" });
let working = msgs.filter((m) => m.msg.state === "working");
out.click_banner_doing = working.length ? working[working.length - 1].msg.doing : null;
out.click_banner_tab_title = working.length ? working[working.length - 1].msg.tabTitle : null;

// set_doing mid-flight: the showing tab gets the new text at once, and
// the command itself answers success.
msgs = await drive({ id: "2", action: "set_doing", params: { tabId: 7 }, doing: "Restarting the worker" });
working = msgs.filter((m) => m.msg.state === "working");
out.set_doing_banner_doing = working.length ? working[working.length - 1].msg.doing : null;
const res2 = ws.sent.filter((m) => m.id === "2");
out.set_doing_result = res2.length ? res2[res2.length - 1].result : null;

// An explicit null clears: the next presence push has no label, so the
// title falls back to the verb.
msgs = await drive({ id: "3", action: "set_doing", params: { tabId: 7 }, doing: null });
working = msgs.filter((m) => m.msg.state === "working");
out.cleared_banner_doing = working.length ? (working[working.length - 1].msg.doing ?? null) : "no-banner";

// The popup toggle off: later pushes carry tabTitle false.
for (const fn of storageChangedListeners) fn({ tabTitleEnabled: { newValue: false } }, "local");
msgs = await drive({ id: "4", action: "set_doing", params: { tabId: 7 }, doing: "One more thing" });
working = msgs.filter((m) => m.msg.state === "working");
out.toggle_off_tab_title = working.length ? working[working.length - 1].msg.tabTitle : null;

// set_doing on a tab with no live presence: no banner, no entry left
// behind - a later BANNER_QUERY from that tab must not invent a pill.
msgs = await drive({ id: "5", action: "set_doing", params: { tabId: 9 }, doing: "Ghost work" });
out.idle_set_doing_banner_count = msgs.length;
bannerSent.length = 0;
for (const fn of runtimeListeners) {
  try { fn({ type: "BANNER_QUERY" }, { tab: { id: 9 } }, () => {}); } catch (e) {}
}
await sleep(150);
out.idle_query_banner_count = bannerSent.filter((m) => m.tabId === 9).length;

console.log(JSON.stringify(out));
process.exit(0);
"""


@pytest.fixture(scope="module")
def worker():
    harness = HARNESS.replace("__BACKGROUND__", (EXT / "background.js").as_uri())
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_worker_connects(worker):
    assert worker["ws_connected"] is True


def test_click_frame_label_reaches_presence(worker):
    assert worker["click_banner_doing"] == "Deploying the cache rule"
    assert worker["click_banner_tab_title"] is True


def test_set_doing_updates_showing_tab(worker):
    assert worker["set_doing_banner_doing"] == "Restarting the worker"
    assert worker["set_doing_result"]["success"] is True


def test_set_doing_null_clears(worker):
    assert worker["cleared_banner_doing"] is None


def test_title_toggle_off_rides_presence(worker):
    assert worker["toggle_off_tab_title"] is False


def test_set_doing_on_idle_tab_leaves_no_phantom(worker):
    assert worker["idle_set_doing_banner_count"] == 0
    assert worker["idle_query_banner_count"] == 0
