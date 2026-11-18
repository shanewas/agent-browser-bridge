"""Sensitive-field detection in page-lib.js (the Spotlight safety scan),
run under Node with a stub DOM. Skipped when Node is missing."""
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

globalThis.window = globalThis;
globalThis.NodeFilter = { SHOW_ELEMENT: 1 };
globalThis.location = { href: "https://shop.example/checkout" };
// queryAllDeep walks the root's descendants looking for shadow hosts; the
// stub walker replays each root's registered descendant list.
globalThis.document = {
  createTreeWalker: (root) => {
    const nodes = (root && root.__descendants) || [];
    let i = 0;
    return { nextNode: () => nodes[i++] || null };
  },
};

function makeEl(tag, attrs = {}, opts = {}) {
  const el = {
    tagName: tag,
    __attrs: attrs,
    __light: opts.light || [],
    __descendants: opts.descendants || [],
    shadowRoot: opts.shadowRoot || null,
    isContentEditable: Boolean(opts.editable),
    getAttribute(name) { return name in el.__attrs ? el.__attrs[name] : null; },
    hasAttribute(name) { return name in el.__attrs; },
    querySelectorAll(sel) {
      const want = sel.toUpperCase();
      return el.__light.filter((c) => c.tagName.toUpperCase() === want);
    },
  };
  return el;
}

eval(fs.readFileSync("__LIB__", "utf8"));
const L = globalThis.__eabLib;
const out = {};

const pwType = makeEl("INPUT", { type: "password" });
out.by_type = L.isSensitiveInput(pwType);
out.by_name = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "password" }));
out.by_aria = L.isSensitiveInput(makeEl("INPUT", { type: "text", "aria-label": "Card number" }));
out.by_pin_token = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "pin_code" }));
out.plain = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "username" }));
out.xml_lowercase = L.isSensitiveInput(makeEl("input", { type: "password" }));
out.editable_card = L.isSensitiveInput(makeEl("DIV", { "aria-label": "Card number" }, { editable: true }));
out.editable_plain = L.isSensitiveInput(makeEl("DIV", { "aria-label": "Notes" }, { editable: true }));

// A password field inside an open shadow root: the resolver can reach it, so
// the safety scan must see it too.
const shadowPw = makeEl("INPUT", { type: "password" });
const host = makeEl("LOGIN-FORM", {}, { shadowRoot: { querySelectorAll: (sel) => (sel === "input" ? [shadowPw] : []) } });
const wrapShadow = makeEl("DIV", {}, { light: [], descendants: [host] });
host.shadowRoot.__descendants = [];
out.contains_shadow_pw = L.containsSensitiveInput(wrapShadow);

const wrapPlain = makeEl("DIV", {}, { light: [makeEl("INPUT", { type: "text", name: "city" })], descendants: [] });
out.contains_plain = L.containsSensitiveInput(wrapPlain);

const stripeFrame = makeEl("IFRAME", { src: "https://js.stripe.com/v3/elements.html" });
out.stripe_frame = L.isSensitiveInput(stripeFrame);
const wrapFrame = makeEl("DIV", {}, { light: [stripeFrame], descendants: [] });
out.contains_stripe_frame = L.containsSensitiveInput(wrapFrame);
out.plain_frame = L.isSensitiveInput(makeEl("IFRAME", { src: "https://example.com/embed" }));

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def lib():
    harness = HARNESS.replace("__LIB__", str(EXT / "page-lib.js"))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_sensitive_input_signals(lib):
    assert lib["by_type"] is True
    assert lib["by_name"] is True
    assert lib["by_aria"] is True
    assert lib["by_pin_token"] is True
    assert lib["plain"] is False
    assert lib["xml_lowercase"] is True


def test_contenteditable_fields(lib):
    assert lib["editable_card"] is True
    assert lib["editable_plain"] is False


def test_shadow_root_fields_are_seen(lib):
    assert lib["contains_shadow_pw"] is True
    assert lib["contains_plain"] is False


def test_payment_frames_are_sensitive(lib):
    assert lib["stripe_frame"] is True
    assert lib["contains_stripe_frame"] is True
    assert lib["plain_frame"] is False
