// Agent Browser Bridge - Spotlight shared logic (worker side, pure functions).
// Label validation, TTL computation and the sensitive-origin list live here so
// the rules are enforced in the extension no matter which client sent the call,
// and so they can be unit-tested under Node without a DOM.

export const LABEL_MAX_CHARS = 120;
export const LABEL_MIN_TTL_MS = 4000;
export const LABEL_TTL_PER_WORD_MS = 350;
export const LABEL_TTL_CAP_MS = 30000;
export const UNLABELLED_TTL_MS = 4000;
export const PERSIST_CAP_MS = 300000;
export const TTL_FLOOR_MS = 1000;

// Origins where Spotlight stays off unless the user allowlists them: banks,
// payment processors, password managers, webmail. Suffix match on the host.
export const SENSITIVE_ORIGIN_HOSTS = [
  "paypal.com", "stripe.com", "checkout.stripe.com", "wise.com", "revolut.com",
  "paypay.ne.jp", "rakuten-bank.co.jp", "mizuhobank.co.jp", "smbc.co.jp",
  "mufg.jp", "japanpostbank-ydc.jp", "chase.com", "bankofamerica.com",
  "wellsfargo.com", "hsbc.com", "barclays.co.uk", "bracbank.com",
  "1password.com", "bitwarden.com", "lastpass.com", "dashlane.com",
  "mail.google.com", "outlook.live.com", "mail.yahoo.com",
  "accounts.google.com", "login.microsoftonline.com", "appleid.apple.com",
  "login.live.com",
];

// Trust / urgency / credential phrases an agent may not put in a label. Matched
// on token boundaries after normalization, so honest descriptive text that
// merely contains a word ("the official documentation section") still passes.
// The pattern set is fixed in code, never user-supplied.
const BLOCKED_PHRASES = [
  "verified safe", "official notice", "official warning", "session expired",
  "session has expired", "re-enter your password", "enter your password",
  "enter your credentials", "confirm your password", "verify your identity",
  "verify your account", "ignore the warning", "ignore this warning",
  "this is safe", "safe to click", "you must click", "click now to avoid",
  "account suspended", "account will be locked", "urgent action required",
  "type your password here", "password required to continue",
  "session timed out", "sign in again to continue",
  "payment failed update your card",
  "公式のお知らせ", "セッションが切れました", "パスワードを入力",
  "パスワードを再入力", "本人確認が必要です", "アカウントが停止",
  "暗証番号を入力", "暗証番号の入力", "暗証番号を教えて",
  "セッションの有効期限が切れました", "本人確認をお願いします",
];

// Every Unicode format character (zero-width spaces and joiners, bidi marks
// and overrides, the soft hyphen, the word joiner) is invisible in the chip
// but splits the token stream, so all of them are stripped before matching -
// and from the label that is returned for display.
const INVISIBLES = /[\p{Cf}\u180e\u202a-\u202e\u2066-\u2069]/gu;

// Cyrillic and Greek letters that render identically to Latin ones. A blocked
// phrase spelled with a homoglyph ("verifiеd" with a Cyrillic е) must fold to
// the Latin spelling before tokenizing, or the phrase never matches while the
// chip reads as the plain phrase.
const CONFUSABLES = {
  "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
  "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "к": "k", "м": "m", "н": "h",
  "т": "t", "в": "b", "ԁ": "d",
  "А": "a", "Е": "e", "О": "o", "Р": "p", "С": "c", "У": "y", "Х": "x",
  "І": "i", "Ј": "j", "Ѕ": "s", "Һ": "h", "К": "k", "М": "m", "Н": "h",
  "Т": "t", "В": "b",
  "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
  "τ": "t", "υ": "y", "χ": "x", "ω": "w", "Α": "a", "Β": "b", "Ε": "e",
  "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o", "Ρ": "p",
  "Τ": "t", "Χ": "x", "Υ": "y", "Ζ": "z",
  // Latin small capitals (UTS#39 skeleton folds them to the plain letter;
  // NFKC does not), Greek lunate sigma, Latin alpha, dotless i.
  "\u1d00": "a", "\u0299": "b", "\u1d04": "c", "\u1d05": "d", "\u1d07": "e",
  "\ua730": "f", "\u0262": "g", "\u029c": "h", "\u026a": "i", "\u1d0a": "j",
  "\u1d0b": "k", "\u029f": "l", "\u1d0d": "m", "\u0274": "n", "\u1d0f": "o",
  "\u1d10": "o", "\u1d18": "p", "\ua7af": "q", "\u0280": "r", "\ua731": "s",
  "\u1d1b": "t", "\u1d1c": "u", "\u1d20": "v", "\u1d21": "w", "\u028f": "y",
  "\u1d22": "z",
  // NFKC folds the lunate sigma to final sigma (ς) before this map runs,
  // so both forms are listed.
  "\u03f2": "c", "\u03f9": "c", "\u03c2": "c", "\u0251": "a", "\u2c6d": "a", "\u0131": "i",
};
const CONFUSABLE_RE = new RegExp("[" + Object.keys(CONFUSABLES).join("") + "]", "g");

export function normalizeLabelText(s) {
  return String(s == null ? "" : s)
    .normalize("NFKC")
    // The ideographic full stop is a dot in a domain; NFKC leaves it alone.
    .replace(/\u3002/g, ".")
    .replace(INVISIBLES, "")
    .replace(CONFUSABLE_RE, (ch) => CONFUSABLES[ch] || ch)
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function tokenSet(normalized) {
  return new Set(normalized.split(/[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+/i).filter(Boolean));
}

const LABEL_REJECT_URL = "Labels may not contain URLs or domains. Describe the element instead.";
const LABEL_REJECT_TRUST = "Labels may not make trust, urgency or credential claims. Describe what the element is, not why the user should trust it.";
// A fuller common-TLD list, not an any-token rule: "dot" followed by an
// ordinary word ("the dot on the chart") must still pass, so only real
// TLD spellings count. Common-word ccTLDs (in, at, be) stay out on purpose.
const SPELLED_TLDS = new Set([
  "com", "co", "net", "org", "jp", "io", "gov", "edu", "dev", "app", "me",
  "ai", "xyz", "info", "biz", "tv", "cc", "us", "uk", "de", "fr", "it",
  "es", "nl", "ch", "se", "no", "fi", "dk", "ie", "nz", "au", "ca", "kr",
  "cn", "tw", "hk", "sg", "my", "id", "th", "vn", "ph", "br", "mx", "ar",
  "za", "il", "ae", "pk", "bd", "ru", "ua", "pl", "cz", "hu", "ro", "gr",
  "tr", "pt", "eu", "site", "online", "tech", "store", "blog", "news",
  "cloud", "pro", "name", "asia", "gg", "ly", "gl", "gd", "gy", "fm",
]);

// Known shortener hosts spelled with the dot dropped ("bit ly slash x9"):
// the host's dot-split tokens appearing adjacently is the readable domain.
const SPACED_SHORTENERS = [
  ["bit", "ly"], ["tinyurl", "com"], ["t", "co"], ["rb", "gy"],
  ["ow", "ly"], ["buff", "ly"], ["goo", "gl"], ["is", "gd"], ["cutt", "ly"],
];

// Punctuation and spacing carry no meaning inside a Japanese phrase; matching
// on the squashed text keeps an inserted 、 or space from defeating the rule.
function squashCjk(s) {
  return s.replace(/[\s、。・,.!！?？:：;；「」『』（）()【】\[\]"'“”‘’]/g, "");
}

// Returns { ok: true, label } or { ok: false, code: "label_rejected", error }.
// The returned label is the agent's own text with control and invisible
// characters removed, so what the worker validated is what the chip renders.
export function validateLabel(raw) {
  if (raw === undefined || raw === null) return { ok: true, label: null };
  const label = String(raw)
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, " ")
    .replace(INVISIBLES, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!label) return { ok: true, label: null };
  if (label.length > LABEL_MAX_CHARS) {
    return { ok: false, code: "label_rejected", error: `Label is ${label.length} characters; the cap is ${LABEL_MAX_CHARS}. Shorten it.` };
  }
  const normalized = normalizeLabelText(label);
  const stream = normalized.split(/[^a-z0-9]+/i).filter(Boolean);
  // URL and domain checks run on the normalized text: a fullwidth domain
  // NFKC-folds to its ASCII spelling, an IPv4 literal needs no TLD, and a
  // spelled-out "example dot com" is caught as a token pair. Spaces around
  // a dot carry no meaning in a read-aloud domain ("example . com"), so
  // they are collapsed before the domain pattern runs.
  const domainText = normalized.replace(/\s*\.\s*/g, ".");
  if (/https?:\/\/|www\.[^\s]+|\b[a-z0-9-]+\.[a-z]{2,}\b|\b\d{1,3}(?:\.\d{1,3}){3}\b/.test(domainText)) {
    return { ok: false, code: "label_rejected", error: LABEL_REJECT_URL };
  }
  for (let i = 0; i + 1 < stream.length; i++) {
    if (stream[i] === "dot" && SPELLED_TLDS.has(stream[i + 1])) {
      return { ok: false, code: "label_rejected", error: LABEL_REJECT_URL };
    }
  }
  for (const seq of SPACED_SHORTENERS) {
    for (let i = 0; i + seq.length <= stream.length; i++) {
      if (seq.every((t, j) => stream[i + j] === t)) {
        return { ok: false, code: "label_rejected", error: LABEL_REJECT_URL };
      }
    }
  }
  const tokens = tokenSet(normalized);
  const squashed = squashCjk(normalized);
  for (const phrase of BLOCKED_PHRASES) {
    const pNorm = normalizeLabelText(phrase);
    if (/[\u3040-\u9fff]/.test(pNorm)) {
      if (squashed.includes(squashCjk(pNorm))) {
        return { ok: false, code: "label_rejected", error: LABEL_REJECT_TRUST };
      }
      continue;
    }
    const pTokens = pNorm.split(" ").filter(Boolean);
    if (pTokens.length > 1) {
      // Multi-word phrase: require the words adjacent in the token stream.
      let hit = false;
      for (let i = 0; i + pTokens.length <= stream.length; i++) {
        if (pTokens.every((t, j) => stream[i + j] === t)) { hit = true; break; }
      }
      if (hit) {
        return { ok: false, code: "label_rejected", error: LABEL_REJECT_TRUST };
      }
    } else if (pTokens.length === 1 && tokens.has(pTokens[0])) {
      return { ok: false, code: "label_rejected", error: LABEL_REJECT_TRUST };
    }
  }
  return { ok: true, label };
}

// Effective TTL for a draw. persist ignores the computed value (the caller caps
// it at PERSIST_CAP_MS); ttlMs is honoured inside [TTL_FLOOR_MS, CAP].
export function computeTtlMs({ label, persist, ttlMs, multiplier }) {
  const mult = typeof multiplier === "number" && multiplier > 0 ? multiplier : 1;
  if (persist) return PERSIST_CAP_MS;
  if (typeof ttlMs === "number" && ttlMs > 0) {
    return Math.max(TTL_FLOOR_MS, Math.min(LABEL_TTL_CAP_MS, Math.round(ttlMs)));
  }
  if (!label) return UNLABELLED_TTL_MS;
  const words = label.split(/\s+/).filter(Boolean).length;
  const base = Math.max(LABEL_MIN_TTL_MS, Math.round(words * LABEL_TTL_PER_WORD_MS * mult));
  return Math.min(LABEL_TTL_CAP_MS, base);
}

export function isSensitiveOrigin(url, allowlist) {
  const raw = String(url || "");
  let host = "";
  try {
    const u = new URL(raw);
    host = u.hostname.toLowerCase();
    if (!host && u.protocol === "blob:") {
      // A blob: page inherits its creator's origin; the URL carries it inline.
      host = new URL(raw.slice(raw.indexOf(":") + 1)).hostname.toLowerCase();
    }
  } catch (e) { return false; }
  host = host.replace(/\.+$/, ""); // a trailing-dot FQDN is the same origin
  if (!host) return false;
  const allowed = (allowlist || [])
    .map((h) => String(h).toLowerCase().trim().replace(/\.+$/, ""))
    .filter(Boolean);
  const matches = (list) => list.some((h) => host === h || host.endsWith("." + h));
  if (matches(allowed)) return false;
  return matches(SENSITIVE_ORIGIN_HOSTS);
}
