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
        capture_output=True, text=True, timeout=30)
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
        capture_output=True, text=True, timeout=30)
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
