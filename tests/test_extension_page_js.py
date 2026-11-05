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
