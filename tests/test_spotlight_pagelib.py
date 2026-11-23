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
    labels: opts.labels || undefined,
    contentDocument: opts.contentDocument || null,
    getRootNode() { return opts.rootNode || { getElementById: () => null, querySelector: () => null }; },
    closest() { return null; },
    getAttribute(name) { return name in el.__attrs ? el.__attrs[name] : null; },
    hasAttribute(name) { return name in el.__attrs; },
    querySelectorAll(sel) {
      if (sel === "[contenteditable]") return el.__light.filter((c) => c.isContentEditable);
      const want = sel.toUpperCase();
      return el.__light.filter((c) => c.tagName.toUpperCase() === want);
    },
  };
  return el;
}

function makeDoc(light = [], descendants = []) {
  return {
    __light: light,
    __descendants: descendants,
    querySelectorAll(sel) {
      if (sel === "[contenteditable]") return light.filter((c) => c.isContentEditable);
      const want = sel.toUpperCase();
      return light.filter((c) => (c.tagName || "").toUpperCase() === want);
    },
  };
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

// A container wrapping an editable card field: the container check must
// agree with the direct-target check, which flags the field by its hint.
const wrapEditable = makeEl("DIV", {}, { light: [makeEl("DIV", { "aria-label": "Card number" }, { editable: true })], descendants: [] });
out.contains_editable_card = L.containsSensitiveInput(wrapEditable);
const wrapEditablePlain = makeEl("DIV", {}, { light: [makeEl("DIV", { "aria-label": "Notes" }, { editable: true })], descendants: [] });
out.contains_editable_plain = L.containsSensitiveInput(wrapEditablePlain);

const stripeFrame = makeEl("IFRAME", { src: "https://js.stripe.com/v3/elements.html" });
out.stripe_frame = L.isSensitiveInput(stripeFrame);
const wrapFrame = makeEl("DIV", {}, { light: [stripeFrame], descendants: [] });
out.contains_stripe_frame = L.containsSensitiveInput(wrapFrame);
out.plain_frame = L.isSensitiveInput(makeEl("IFRAME", { src: "https://example.com/embed" }));

// F3: name shapes the token net missed - "pincode" is one token and
// camelCase is never split, so "pwdField" hid its "pwd" token.
out.by_pincode = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "pincode" }));
out.by_pwd_camel = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "pwdField" }));
out.by_card_camel = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "cardNumber" }));

// F3: semantics that live only in the associated <label> element or in the
// element an aria-labelledby points at.
out.by_label_element = L.isSensitiveInput(makeEl("INPUT",
  { type: "text", name: "field7", id: "field7" },
  { labels: [{ textContent: "Card number", innerText: "Card number" }] }));
out.by_labelledby = L.isSensitiveInput(makeEl("INPUT",
  { type: "text", name: "field8", "aria-labelledby": "lbl8" },
  { rootNode: { getElementById: (id) => (id === "lbl8" ? { textContent: "Card number", innerText: "Card number" } : null), querySelector: () => null } }));

// F3: a verification-code field is flagged only with a second signal
// (digit count / verification wording); a bare "code" name is not enough.
out.by_code_placeholder = L.isSensitiveInput(makeEl("INPUT",
  { type: "text", name: "code", placeholder: "6-digit code", inputmode: "numeric" }));
out.code_plain = L.isSensitiveInput(makeEl("INPUT", { type: "text", name: "code" }));
out.code_postal = L.isSensitiveInput(makeEl("INPUT",
  { type: "text", name: "code", placeholder: "Postal code" }));

// F3: a same-origin iframe's document is inspectable, so a login form
// inside one must be seen; a frame holding only plain fields must not.
const loginDoc = makeDoc([makeEl("INPUT", { type: "password" })]);
const loginFrame = makeEl("IFRAME",
  { src: "https://shop.example/login-frame", name: "auth", title: "Sign in" },
  { contentDocument: loginDoc });
const wrapLoginFrame = makeEl("DIV", {}, { light: [loginFrame], descendants: [loginFrame] });
out.contains_login_frame = L.containsSensitiveInput(wrapLoginFrame);
const plainDoc = makeDoc([makeEl("INPUT", { type: "text", name: "city" })]);
const plainDocFrame = makeEl("IFRAME",
  { src: "https://shop.example/embed", name: "embed", title: "" },
  { contentDocument: plainDoc });
const wrapPlainDocFrame = makeEl("DIV", {}, { light: [plainDocFrame], descendants: [plainDocFrame] });
out.contains_plain_doc_frame = L.containsSensitiveInput(wrapPlainDocFrame);

// F3: enumerateSensitiveFields lists the fields containsSensitiveInput
// would flag, descending into accessible same-origin frame documents.
out.has_enum = typeof L.enumerateSensitiveFields === "function";
const enumOf = (root) => (out.has_enum ? L.enumerateSensitiveFields(root) : []);
out.enum_mixed_count = enumOf(wrapEditable).length;
const enumFrameFields = enumOf(wrapLoginFrame);
out.enum_frame_count = enumFrameFields.length;
out.enum_frame_is_inner_pw = enumFrameFields.length === 1
  && enumFrameFields[0].getAttribute("type") === "password";
out.enum_plain_count = enumOf(wrapPlain).length;
out.enum_self_count = enumOf(pwType).length;

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def lib():
    harness = HARNESS.replace("__LIB__", (EXT / "page-lib.js").as_posix())
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
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
    assert lib["contains_editable_card"] is True
    assert lib["contains_editable_plain"] is False


def test_shadow_root_fields_are_seen(lib):
    assert lib["contains_shadow_pw"] is True
    assert lib["contains_plain"] is False


def test_payment_frames_are_sensitive(lib):
    assert lib["stripe_frame"] is True
    assert lib["contains_stripe_frame"] is True
    assert lib["plain_frame"] is False


def test_f3_name_shapes(lib):
    assert lib["by_pincode"] is True
    assert lib["by_pwd_camel"] is True
    assert lib["by_card_camel"] is True


def test_f3_label_element_semantics(lib):
    assert lib["by_label_element"] is True
    assert lib["by_labelledby"] is True


def test_f3_verification_code_needs_a_second_signal(lib):
    assert lib["by_code_placeholder"] is True
    assert lib["code_plain"] is False
    assert lib["code_postal"] is False


def test_f3_same_origin_frame_contents(lib):
    assert lib["contains_login_frame"] is True
    assert lib["contains_plain_doc_frame"] is False


def test_f3_enumerate_sensitive_fields(lib):
    assert lib["has_enum"] is True
    assert lib["enum_mixed_count"] == 1
    assert lib["enum_frame_count"] == 1
    assert lib["enum_frame_is_inner_pw"] is True
    assert lib["enum_plain_count"] == 0
    assert lib["enum_self_count"] == 1
