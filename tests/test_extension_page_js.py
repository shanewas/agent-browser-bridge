"""Behavioural tests for page.js functions, run under Node with a stub DOM.

The extension's page functions are plain ES module exports with no
top-level DOM access, so Node can import a copy and call them against
just enough fake DOM to cover the branch under test. Skipped when
Node is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "agent_browser_bridge" / "extension" / "page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")

HARNESS = r"""
const page = await import(process.argv[2]);
const scenario = process.argv[3];
const out = {};

if (scenario === "resolve") {
  const mainEl = {
    tagName: "MAIN", id: "",
    getBoundingClientRect: () => ({ width: 100, height: 50, x: 0, y: 0 }),
    scrollIntoView() {},
    contains: (o) => o === mainEl,
    getRootNode: () => ({ host: null }),
  };
  const lib = {
    frameInfo: async () => ({ x: 0, y: 0 }),
    queryAllDeep: () => [],
    nameOf: () => "",
    roleOf: () => "",
    isVisible: () => true,
    rectOf: () => ({ width: 100, height: 50, x: 0, y: 0 }),
    center: () => ({ x: 50, y: 25 }),
    highlight: () => {},
    collapse: (s) => String(s || "").replace(/\s+/g, " ").trim(),
    INTERACTIVE_ROLES: new Set(["button", "link"]),
  };
  globalThis.window = {
    __eabLib: lib, innerHeight: 800, innerWidth: 1000,
    getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  };
  globalThis.document = {
    querySelector: (q) => (q === "main" ? mainEl : null),
    elementFromPoint: () => mainEl,
  };
  // "main" is a cached ref *name* on the ladder's exact rung, and also a
  // tag name. Exact mode must not resolve it to the first <main>.
  const exact = await page.pageResolve("main", { kind: "exact" });
  out.exact_found = exact.found === true;
  const normal = await page.pageResolve("main", { kind: "target" });
  out.target_found = normal.found === true;
}

if (scenario === "scroll") {
  const scroller = {
    tagName: "DIV", nodeType: 1,
    scrollHeight: 500, clientHeight: 100, scrollWidth: 100, clientWidth: 100,
    scrollTop: 0, scrollLeft: 0, parentElement: null,
    scrollBy(x, y) { this.scrollLeft += x; this.scrollTop += y; },
    getRootNode: () => ({ host: hostEl }),
  };
  const hostEl = {
    tagName: "X-SCROLLER", nodeType: 1, parentElement: null,
    shadowRoot: { elementFromPoint: () => scroller },
    getRootNode: () => ({}),
  };
  let windowScrolled = 0;
  globalThis.window = {
    getComputedStyle: (el) => el === scroller
      ? { overflowY: "auto", overflowX: "visible" }
      : { overflowY: "visible", overflowX: "visible" },
    scrollBy(x, y) { windowScrolled += y; },
    scrollX: 0, scrollY: 0,
  };
  globalThis.document = {
    elementFromPoint: () => hostEl,
    body: { tagName: "BODY" },
    documentElement: { tagName: "HTML" },
  };
  const r = page.pageScroll(0, 200, 10, 10);
  out.element = r.element || null;
  out.scroller_top = scroller.scrollTop;
  out.window_scrolled = windowScrolled;
}

if (scenario === "spotlight") {
  const rect = (x, y, w, h) => ({ x, y, width: w, height: h, left: x, top: y, right: x + w, bottom: y + h });
  const pw = { tagName: "INPUT", __sensitive: true, __children: [], getBoundingClientRect: () => rect(10, 10, 100, 30) };
  const formEl = { tagName: "FORM", __children: [pw], getBoundingClientRect: () => rect(0, 0, 400, 300) };
  const btnA = { tagName: "BUTTON", __children: [], getBoundingClientRect: () => rect(500, 100, 120, 40) };
  const btnB = { tagName: "BUTTON", __children: [], getBoundingClientRect: () => rect(500, 200, 120, 40) };
  const cardPw = { tagName: "INPUT", __sensitive: true, __children: [], getBoundingClientRect: () => rect(720, 120, 200, 30) };
  const cardWrap = { tagName: "DIV", __children: [cardPw], getBoundingClientRect: () => rect(700, 100, 300, 200) };
  // F1 PoC fields: one sitting between any grid-probe points of a full
  // viewport rect, one below the fold in the unprobed part of a tall rect.
  // Neither is reachable by the point hit test in this stub.
  const midPw = { tagName: "INPUT", __sensitive: true, __children: [], getBoundingClientRect: () => rect(200, 180, 400, 40) };
  const belowPw = { tagName: "INPUT", __sensitive: true, __children: [], getBoundingClientRect: () => rect(200, 900, 400, 40) };
  const rootEl = { tagName: "HTML", __children: [formEl, btnA, btnB, cardWrap], getBoundingClientRect: () => rect(0, 0, 1280, 800) };
  const inRect = (r, x, y) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  const lib = {
    queryAllDeep: (root, sel) => (sel === ".card" ? [cardWrap] : sel === "input" ? [pw, cardPw] : [btnA, btnB, cardWrap]),
    roleOf: (el) => (el.tagName === "BUTTON" ? "button" : ""),
    INTERACTIVE_ROLES: new Set(["button", "link"]),
    isVisible: () => true,
    nameOf: (el) => (el.tagName === "BUTTON" ? "Save" : ""),
    descriptorOf: (el) => ({ tag: el.tagName.toLowerCase(), role: lib.roleOf(el), name: lib.nameOf(el) }),
    isSensitiveInput: (el) => Boolean(el && el.__sensitive),
    containsSensitiveInput: (el) => {
      if (!el) return false;
      if (lib.isSensitiveInput(el)) return true;
      return (el.__children || []).some((c) => lib.containsSensitiveInput(c));
    },
    enumerateSensitiveFields: () => [pw, cardPw, midPw, belowPw],
    labelText: () => "",
    deepFromPoint: (x, y) => (inRect(pw.getBoundingClientRect(), x, y) ? pw
      : inRect(btnA.getBoundingClientRect(), x, y) ? btnA : rootEl),
  };
  globalThis.window = { __eabLib: lib, innerWidth: 1280, innerHeight: 800 };
  globalThis.document = {
    elementsFromPoint: (x, y) => (inRect(pw.getBoundingClientRect(), x, y) ? [pw, formEl, rootEl]
      : inRect(btnA.getBoundingClientRect(), x, y) ? [btnA, rootEl] : [rootEl]),
  };
  const amb = page.pageSpotlightResolve("Save", { kind: "target" });
  out.ambiguous_code = amb.code;
  out.ambiguous_count = amb.count;
  out.ambiguous_candidates = (amb.candidates || []).length;
  const picked = page.pageSpotlightResolve("Save", { kind: "target", index: 1 });
  out.picked_found = picked.found === true && picked.index === 1 && String(picked.targetKey).startsWith("spot-");
  const outOfRange = page.pageSpotlightResolve("Save", { kind: "target", index: 5 });
  out.out_of_range = outOfRange.found === false && outOfRange.count === 2;
  const card = page.pageSpotlightResolve(".card", { kind: "selector" });
  out.card_found = card.found === true;
  out.card_sensitive = card.sensitive === true;
  out.hit_on_field = page.pageSpotlightHitTest(20, 20).sensitive;
  out.hit_elsewhere = page.pageSpotlightHitTest(900, 600).sensitive;
  out.hit_on_button = page.pageSpotlightHitTest(560, 120).sensitive;
  // F1: the exact rect check sees fields the grid probes miss.
  out.rect_check_fn = typeof page.pageSpotlightRectCheck;
  if (out.rect_check_fn === "function") {
    out.rect_between_probes = page.pageSpotlightRectCheck({ x: 0, y: 0, width: 1280, height: 800 }).sensitive;
    out.rect_below_fold = page.pageSpotlightRectCheck({ x: 0, y: 0, width: 1280, height: 1600 }).sensitive;
    out.rect_clear = page.pageSpotlightRectCheck({ x: 900, y: 600, width: 100, height: 50 }).sensitive;
  }
}

if (scenario === "spotlight_frame") {
  const rect = (x, y, w, h) => ({ x, y, width: w, height: h, left: x, top: y, right: x + w, bottom: y + h });
  // A password field inside a same-origin frame: its own rect is
  // frame-relative (10,10,100,30), while the frame element sits at
  // (300,200) in the main page, so on the main page the field occupies
  // (310,210)-(410,240).
  const frameDoc = {};
  const mainDoc = {};
  const frameView = { parent: { document: mainDoc } };
  frameDoc.defaultView = frameView;
  const framePw = { tagName: "INPUT", __sensitive: true, ownerDocument: frameDoc,
    getBoundingClientRect: () => rect(10, 10, 100, 30) };
  const frameEl = { tagName: "IFRAME", ownerDocument: mainDoc, contentDocument: frameDoc,
    getBoundingClientRect: () => rect(300, 200, 400, 300) };
  const lib = {
    queryAllDeep: (root, sel) => (sel === "iframe" && root === mainDoc ? [frameEl] : []),
    descriptorOf: (el) => ({ tag: el.tagName.toLowerCase(), role: "", name: "" }),
    enumerateSensitiveFields: () => [framePw],
  };
  globalThis.window = { __eabLib: lib, innerWidth: 1280, innerHeight: 800 };
  globalThis.document = mainDoc;
  out.frame_check_fn = typeof page.pageSpotlightRectCheck;
  if (out.frame_check_fn === "function") {
    // A draw rect covering the field's true on-page position flags.
    out.frame_true_position = page.pageSpotlightRectCheck({ x: 300, y: 200, width: 100, height: 30 }).sensitive;
    // A draw rect at the frame-relative position alone does not flag.
    out.frame_relative_position = page.pageSpotlightRectCheck({ x: 10, y: 10, width: 100, height: 30 }).sensitive;
  }
}

console.log(JSON.stringify(out));
"""


def run_scenario(tmp_path, scenario):
    page_copy = tmp_path / "page.mjs"
    page_copy.write_text(PAGE.read_text(encoding="utf-8"), encoding="utf-8")
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), page_copy.as_uri(), scenario],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_exact_rung_does_not_fall_back_to_a_bare_tag(tmp_path):
    out = run_scenario(tmp_path, "resolve")
    assert out["exact_found"] is False, out
    # The same word still resolves as a tag on the normal target path.
    assert out["target_found"] is True, out


def test_scroll_without_debugger_reaches_a_shadow_scroller(tmp_path):
    out = run_scenario(tmp_path, "scroll")
    assert out["element"] == "div", out
    assert out["scroller_top"] == 200, out
    assert out["window_scrolled"] == 0, out


def test_spotlight_resolver_enumerates_and_flags_sensitive(tmp_path):
    out = run_scenario(tmp_path, "spotlight")
    assert out["ambiguous_code"] == "target_ambiguous", out
    assert out["ambiguous_count"] == 2, out
    assert out["ambiguous_candidates"] == 2, out
    assert out["picked_found"] is True, out
    assert out["out_of_range"] is True, out
    assert out["card_found"] is True and out["card_sensitive"] is True, out


def test_spotlight_rect_check_is_exact_not_probed(tmp_path):
    out = run_scenario(tmp_path, "spotlight")
    assert out["rect_check_fn"] == "function", out
    # A field between grid probes, and one in the below-fold part of a
    # tall rect, both intersect the full requested rect.
    assert out["rect_between_probes"] is True, out
    assert out["rect_below_fold"] is True, out
    assert out["rect_clear"] is False, out


def test_spotlight_rect_check_offsets_frame_fields(tmp_path):
    out = run_scenario(tmp_path, "spotlight_frame")
    assert out["frame_check_fn"] == "function", out
    # The field's true main-page position is its frame-relative rect
    # shifted by the frame element's position (300,200).
    assert out["frame_true_position"] is True, out
    assert out["frame_relative_position"] is False, out


def test_spotlight_hit_test_is_point_precise(tmp_path):
    out = run_scenario(tmp_path, "spotlight")
    # On the field: sensitive. Elsewhere on a page that merely contains a
    # password field: not sensitive (the old stack-wide subtree scan refused
    # every point on such pages).
    assert out["hit_on_field"] is True, out
    assert out["hit_elsewhere"] is False, out
    assert out["hit_on_button"] is False, out
