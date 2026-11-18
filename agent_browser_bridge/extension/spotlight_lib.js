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
  "公式のお知らせ", "セッションが切れました", "パスワードを入力",
  "パスワードを再入力", "本人確認が必要です", "アカウントが停止",
];

const ZERO_WIDTH = /[\u200b-\u200f\u202a-\u202e\u2060\ufeff]/g;

export function normalizeLabelText(s) {
  return String(s == null ? "" : s)
    .normalize("NFKC")
    .replace(ZERO_WIDTH, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function tokenSet(normalized) {
  return new Set(normalized.split(/[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+/i).filter(Boolean));
}

// Returns { ok: true, label } or { ok: false, code: "label_rejected", error }.
export function validateLabel(raw) {
  if (raw === undefined || raw === null) return { ok: true, label: null };
  const label = String(raw).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  if (!label) return { ok: true, label: null };
  if (label.length > LABEL_MAX_CHARS) {
    return { ok: false, code: "label_rejected", error: `Label is ${label.length} characters; the cap is ${LABEL_MAX_CHARS}. Shorten it.` };
  }
  if (/https?:\/\/|www\.[^\s]+|\b[a-z0-9-]+\.(com|net|org|io|jp|co|dev|app)\b/i.test(label)) {
    return { ok: false, code: "label_rejected", error: "Labels may not contain URLs or domains. Describe the element instead." };
  }
  const normalized = normalizeLabelText(label);
  const tokens = tokenSet(normalized);
  for (const phrase of BLOCKED_PHRASES) {
    const pNorm = normalizeLabelText(phrase);
    if (/[\u3040-\u9fff]/.test(pNorm)) {
      // Japanese phrases have no token boundaries; substring on the normalized text.
      if (normalized.includes(pNorm)) {
        return { ok: false, code: "label_rejected", error: "Labels may not make trust, urgency or credential claims. Describe what the element is, not why the user should trust it." };
      }
      continue;
    }
    const pTokens = pNorm.split(" ").filter(Boolean);
    if (pTokens.length > 1) {
      // Multi-word phrase: require the words adjacent in the token stream.
      const stream = normalized.split(/[^a-z0-9]+/i).filter(Boolean);
      let hit = false;
      for (let i = 0; i + pTokens.length <= stream.length; i++) {
        if (pTokens.every((t, j) => stream[i + j] === t)) { hit = true; break; }
      }
      if (hit) {
        return { ok: false, code: "label_rejected", error: "Labels may not make trust, urgency or credential claims. Describe what the element is, not why the user should trust it." };
      }
    } else if (pTokens.length === 1 && tokens.has(pTokens[0])) {
      return { ok: false, code: "label_rejected", error: "Labels may not make trust, urgency or credential claims. Describe what the element is, not why the user should trust it." };
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
  let host = "";
  try { host = new URL(String(url || "")).hostname.toLowerCase(); } catch (e) { return false; }
  if (!host) return false;
  const allowed = (allowlist || []).map((h) => String(h).toLowerCase().trim()).filter(Boolean);
  const matches = (list) => list.some((h) => host === h || host.endsWith("." + h));
  if (matches(allowed)) return false;
  return matches(SENSITIVE_ORIGIN_HOSTS);
}
