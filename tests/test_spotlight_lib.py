"""Unit tests for spotlight_lib.js (label rules, TTL, sensitive origins),
run under Node as an ES module import. Skipped when Node is missing."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "agent_browser_bridge" / "extension" / "spotlight_lib.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")

HARNESS = r"""
import { validateLabel, computeTtlMs, isSensitiveOrigin } from "__LIB__";

const out = {};
const v = (s) => validateLabel(s);

out.plain = v("About to click Save");
out.empty = v("");
out.none = v(null);
out.url = v("See https://example.com for details");
out.domain = v("Go to example.com now");
out.domain_other_tld = v("Mirror at evil.xyz here");
out.trust = v("Verified safe - click here");
out.trust_split = v("This is a verified  safe button");
out.zero_width = v("veri\u200bfied safe");
out.honest_official = v("The official documentation section");
out.urgency = v("Your session expired, act now");
out.password = v("Please re-enter your password here");
out.japanese = v("パスワードを入力してください");
out.too_long = v("x".repeat(121));
out.exactly_120 = v("x".repeat(120));

out.ttl_unlabelled = computeTtlMs({ label: null });
out.ttl_short_label = computeTtlMs({ label: "Save button" });
out.ttl_long_label = computeTtlMs({ label: Array(40).fill("word").join(" ") });
out.ttl_huge_label = computeTtlMs({ label: Array(400).fill("word").join(" ") });
out.ttl_explicit = computeTtlMs({ label: "x", ttlMs: 9000 });
out.ttl_explicit_low = computeTtlMs({ label: "x", ttlMs: 10 });
out.ttl_explicit_high = computeTtlMs({ label: "x", ttlMs: 999999 });
out.ttl_persist = computeTtlMs({ label: "x", persist: true });
out.ttl_multiplier = computeTtlMs({ label: Array(20).fill("word").join(" "), multiplier: 2 });

out.origin_bank = isSensitiveOrigin("https://www.paypal.com/signin", []);
out.origin_bank_sub = isSensitiveOrigin("https://secure.smbc.co.jp/login", []);
out.origin_webmail = isSensitiveOrigin("https://mail.google.com/mail/u/0/", []);
out.origin_normal = isSensitiveOrigin("https://example.com/shop", []);
out.origin_allowlisted = isSensitiveOrigin("https://www.paypal.com/signin", ["paypal.com"]);
out.origin_lookalike = isSensitiveOrigin("https://notpaypal.com.evil.example/", []);
out.origin_trailing_dot = isSensitiveOrigin("https://www.paypal.com./signin", []);
out.origin_blob_bank = isSensitiveOrigin("blob:https://www.paypal.com/9f1c2d", []);
out.origin_blob_normal = isSensitiveOrigin("blob:https://example.com/9f1c2d", []);
out.origin_idp = isSensitiveOrigin("https://accounts.google.com/signin", []);

out.homoglyph = v("verifi\u0435d safe - click here");
out.soft_hyphen = v("verifi\u00aded safe");
out.fullwidth_domain = v("\uff47\uff4f\uff4f\uff47\uff4c\uff45\uff0e\uff43\uff4f\uff4d");
out.ipv4 = v("Go to 192.168.0.1 now");
out.spelled_domain = v("Go to example dot com");
out.japanese_punct = v("\u30d1\u30b9\u30ef\u30fc\u30c9\u3092\u3001\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044");
out.bidi_label = v("Save\u202e button");

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def lib():
    harness = HARNESS.replace("__LIB__", LIB.as_uri())
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_label_plain_text_passes(lib):
    assert lib["plain"] == {"ok": True, "label": "About to click Save"}
    assert lib["empty"]["ok"] is True and lib["empty"]["label"] is None
    assert lib["none"]["ok"] is True


def test_label_urls_rejected(lib):
    assert lib["url"]["ok"] is False and lib["url"]["code"] == "label_rejected"
    assert lib["domain"]["ok"] is False
    assert lib["domain_other_tld"]["ok"] is False


def test_label_trust_and_urgency_claims_rejected(lib):
    for key in ("trust", "trust_split", "zero_width", "urgency", "password", "japanese"):
        assert lib[key]["ok"] is False, key
        assert lib[key]["code"] == "label_rejected", key


def test_label_honest_descriptive_text_passes(lib):
    # "official" inside an honest description is not a trust claim.
    assert lib["honest_official"]["ok"] is True


def test_label_length_cap(lib):
    assert lib["too_long"]["ok"] is False
    assert lib["exactly_120"]["ok"] is True


def test_ttl_rules(lib):
    assert lib["ttl_unlabelled"] == 4000
    assert lib["ttl_short_label"] == 4000  # floor
    assert lib["ttl_long_label"] == 14000  # 40 words * 350ms
    assert lib["ttl_huge_label"] == 30000  # cap
    assert lib["ttl_explicit"] == 9000
    assert lib["ttl_explicit_low"] == 1000  # floor
    assert lib["ttl_explicit_high"] == 30000  # cap
    assert lib["ttl_persist"] == 300000
    assert lib["ttl_multiplier"] == 14000  # 20 words * 350ms * 2


def test_sensitive_origins(lib):
    assert lib["origin_bank"] is True
    assert lib["origin_bank_sub"] is True
    assert lib["origin_webmail"] is True
    assert lib["origin_normal"] is False
    assert lib["origin_allowlisted"] is False
    assert lib["origin_lookalike"] is False


def test_label_spoofing_variants_rejected(lib):
    # A homoglyph, an invisible separator, a fullwidth spelling, an IP
    # literal and a spelled-out domain all read as the thing they imitate.
    assert lib["homoglyph"]["ok"] is False
    assert lib["soft_hyphen"]["ok"] is False
    assert lib["fullwidth_domain"]["ok"] is False
    assert lib["ipv4"]["ok"] is False
    assert lib["spelled_domain"]["ok"] is False
    assert lib["japanese_punct"]["ok"] is False


def test_returned_label_has_no_invisible_characters(lib):
    assert lib["bidi_label"]["ok"] is True
    assert lib["bidi_label"]["label"] == "Save button"


def test_sensitive_origin_edge_forms(lib):
    assert lib["origin_trailing_dot"] is True
    assert lib["origin_blob_bank"] is True
    assert lib["origin_blob_normal"] is False
    assert lib["origin_idp"] is True
