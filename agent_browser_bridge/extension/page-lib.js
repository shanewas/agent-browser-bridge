// Agent Browser Bridge - shared page helpers, injected once per document into the extension's
// isolated world (chrome.scripting.executeScript with files). Functions in page.js reach
// them through window.__eabLib. Idempotent: re-injection is a no-op.
(function () {
  if (window.__eabLib) return;

  const INTERACTIVE_ROLES = new Set([
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "option",
    "switch", "slider", "spinbutton", "menuitem", "menuitemcheckbox", "menuitemradio", "tab",
    "treeitem", "file", "row"
  ]);

  // Containers group their children in the snapshot. Forms are left out on purpose: almost
  // every page has one and nesting under `form ""` hides the fields.
  const CONTAINER_TAGS = new Set(["TABLE", "DIALOG", "NAV", "MAIN", "ASIDE", "HEADER", "FOOTER", "FIELDSET", "IFRAME", "FRAME"]);

  function roleOf(el) {
    const explicit = (el.getAttribute("role") || "").trim().toLowerCase();
    if (explicit) return explicit;
    const tag = el.tagName.toUpperCase();
    if (tag === "A") return el.hasAttribute("href") ? "link" : "";
    if (tag === "BUTTON") return "button";
    if (tag === "INPUT") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "button" || t === "submit" || t === "reset" || t === "image") return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      if (t === "file") return "file";
      if (t === "range") return "slider";
      if (t === "number") return "spinbutton";
      if (t === "search") return "searchbox";
      if (t === "hidden") return "";
      return "textbox";
    }
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "SELECT") return el.multiple ? "listbox" : "combobox";
    if (tag === "OPTION") return "option";
    if (/^H[1-6]$/.test(tag)) return "heading";
    if (tag === "TABLE") return "table";
    if (tag === "TR") return "row";
    if (tag === "TD" || tag === "TH") return "cell";
    if (tag === "IFRAME" || tag === "FRAME") return "iframe";
    if (tag === "DIALOG") return "dialog";
    if (tag === "NAV") return "navigation";
    if (tag === "MAIN") return "main";
    if (tag === "FORM") return "form";
    if (tag === "FIELDSET") return "group";
    if (tag === "SUMMARY") return "button";
    if (el.isContentEditable && el.getAttribute("contenteditable") !== null) return "textbox";
    if (el.hasAttribute("onclick") || el.tabIndex >= 0 && (tag === "DIV" || tag === "SPAN") && window.getComputedStyle(el).cursor === "pointer") return "button";
    return "";
  }

  function ownText(el) {
    let out = "";
    for (const n of el.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) out += n.textContent;
    }
    return collapse(out);
  }

  function collapse(s) {
    return String(s || "").replace(/\s+/g, " ").trim();
  }

  function labelText(el) {
    if (el.labels && el.labels.length) return collapse(el.labels[0].innerText || el.labels[0].textContent);
    if (el.id) {
      const lab = el.getRootNode().querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return collapse(lab.innerText || lab.textContent);
    }
    const wrap = el.closest("label");
    if (wrap) return collapse(wrap.innerText || wrap.textContent);
    return "";
  }

  function nameOf(el, role) {
    const aria = el.getAttribute("aria-label");
    if (aria && collapse(aria)) return collapse(aria).slice(0, 80);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/).map(id => el.getRootNode().getElementById && el.getRootNode().getElementById(id)).filter(Boolean);
      const t = collapse(parts.map(p => p.innerText || p.textContent).join(" "));
      if (t) return t.slice(0, 80);
    }
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      const lab = labelText(el);
      if (lab) return lab.slice(0, 80);
      const ph = el.getAttribute("placeholder") || "";
      if (collapse(ph)) return collapse(ph).slice(0, 80);
      if (tag === "INPUT" && (role === "button")) return collapse(el.value).slice(0, 80);
      const nm = el.getAttribute("name") || el.title || "";
      if (collapse(nm)) return collapse(nm).slice(0, 80);
    }
    if (tag === "IMG") return collapse(el.alt || el.title).slice(0, 80);
    if (tag === "IFRAME" || tag === "FRAME") return collapse(el.title || el.name || el.getAttribute("src") || "").slice(0, 80);
    if (el.title && !el.innerText) return collapse(el.title).slice(0, 80);
    const svgTitle = el.querySelector && el.querySelector("svg title");
    const text = collapse(el.innerText || el.textContent || (svgTitle && svgTitle.textContent) || el.title || "");
    return text.slice(0, 80);
  }

  function isVisible(el) {
    if (el.checkVisibility) {
      if (!el.checkVisibility({ checkVisibilityCSS: true, checkOpacity: true })) return false;
      if (el.getAttribute("aria-hidden") === "true") return false;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return false;
      el._eabR = r;
      return true;
    }
    if (el.closest && el.closest('[aria-hidden="true"]')) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const cs = window.getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    el._eabR = r;
    return true;
  }

  function childElements(el) {
    const out = [];
    if (el.shadowRoot) {
      for (const c of el.shadowRoot.children) out.push(c);
    }
    for (const c of el.children) out.push(c);
    return out;
  }

  function queryAllDeep(root, selector) {
    let nodes = [];
    try { nodes = Array.from(root.querySelectorAll(selector)); } catch (e) {}
    try {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
      let n;
      while ((n = walker.nextNode())) {
        if (n.shadowRoot) nodes = nodes.concat(queryAllDeep(n.shadowRoot, selector));
      }
    } catch (e) {}
    return nodes;
  }

  function rectOf(el) {
    let r = el._eabR;
    if (r) {
      delete el._eabR;
      return r;
    }
    r = el.getBoundingClientRect();
    if ((r.width === 0 || r.height === 0) && el.firstElementChild) {
      r = el.firstElementChild.getBoundingClientRect();
    }
    return r;
  }

  function center(r) {
    const maxW = window.innerWidth || 1920;
    const maxH = window.innerHeight || 1080;
    const x = Math.max(0, Math.min(maxW - 1, Math.round(r.x + r.width / 2)));
    const y = Math.max(0, Math.min(maxH - 1, Math.round(r.y + r.height / 2)));
    return { x, y };
  }

  function highlight(r, label) {
    try {
      let ring = document.getElementById("__eab_ring");
      if (!ring) {
        ring = document.createElement("div");
        ring.id = "__eab_ring";
        ring.style.cssText = "position:fixed;pointer-events:none;z-index:2147483645;border:2px solid #0078d4;border-radius:3px;box-shadow:0 0 0 2px rgba(0,120,212,0.25);";
        const pill = document.createElement("span");
        pill.id = "__eab_ring_label";
        pill.style.cssText = "position:absolute;left:-2px;top:-18px;background:#0078d4;color:#fff;font:11px/16px system-ui,sans-serif;padding:0 5px;border-radius:3px 3px 0 0;white-space:nowrap;";
        ring.appendChild(pill);
        (document.body || document.documentElement).appendChild(ring);
      }
      ring.style.left = `${Math.round(r.left) - 2}px`;
      ring.style.top = `${Math.round(r.top) - 2}px`;
      ring.style.width = `${Math.round(r.width)}px`;
      ring.style.height = `${Math.round(r.height)}px`;
      ring.style.display = "block";
      const pill = document.getElementById("__eab_ring_label");
      if (pill) {
        pill.textContent = label || "";
        pill.style.display = label ? "block" : "none";
      }
      clearTimeout(window.__eab_ring_timer);
      window.__eab_ring_timer = setTimeout(() => { ring.remove(); }, 350);
    } catch (e) {}
  }

  // Offset of this frame's viewport inside the top frame, from the content-script handshake.
  function frameInfo() {
    if (window === window.top) return Promise.resolve({ x: 0, y: 0, path: [], ok: true });
    if (typeof window.__eabFrameInfo === "function") return window.__eabFrameInfo();
    return Promise.resolve({ x: 0, y: 0, path: [], ok: false });
  }

  function iframeIndex(el) {
    const list = typeof window.__eabAllIframes === "function" ? window.__eabAllIframes() : [];
    return list.indexOf(el);
  }

  // Hit-testing and focus stop at shadow boundaries: elementFromPoint returns
  // the host and document.activeElement is the host. Step into open shadow
  // roots until the real element stops changing (issue #7).
  function deepFromPoint(x, y) {
    let el = document.elementFromPoint(x, y);
    while (el && el.shadowRoot) {
      let inner = null;
      try { inner = el.shadowRoot.elementFromPoint(x, y); } catch (e) {}
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }

  function deepActive() {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    return el;
  }

  // The control a write means: the deep element at (x, y) or the deep focused element, with a
  // label resolved to its control in the label's own tree (a shadow root has its own ids).
  function controlAt(mode) {
    let el = !mode ? null : (mode.active ? deepActive() : deepFromPoint(mode.x, mode.y));
    if (el && el.tagName === "LABEL") {
      const root = el.getRootNode();
      const byFor = el.htmlFor ? ((root.getElementById && root.getElementById(el.htmlFor)) || document.getElementById(el.htmlFor)) : null;
      el = byFor || el.querySelector("input, textarea") || el;
    }
    return el;
  }

  // Spotlight safety: a field whose value must never be framed by an agent
  // treatment - password, one-time-code and payment inputs.
  const SENSITIVE_AUTOCOMPLETE = new Set([
    "current-password", "new-password", "one-time-code",
    "cc-number", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-csc", "cc-type", "cc-name",
  ]);
  const SENSITIVE_NAME_RX = /(card.?number|cc.?num|cvv|cvc|otp|one.?time|passcode|password|passwd|passphrase)/i;
  const SENSITIVE_TOKENS = new Set(["pwd", "pin", "pincode"]);
  // Hosted payment fields live in cross-origin iframes whose contents no page
  // script can inspect; the frame element itself is the only signal, so a
  // frame served by a payment processor counts as a sensitive field.
  const PAYMENT_FRAME_HOSTS = [
    "stripe.com", "adyen.com", "paypal.com", "checkout.com", "squareup.com",
    "braintreepayments.com", "worldpay.com", "payjp.jp", "paidy.com",
  ];

  function sensitiveHintOf(el) {
    const parts = [el.getAttribute("name"), el.getAttribute("id"),
                   el.getAttribute("aria-label"), el.getAttribute("placeholder")]
      .filter(Boolean);
    // A field's semantics often live only in its visible label: the
    // associated <label> element or the element aria-labelledby points at.
    try {
      const lab = labelText(el);
      if (lab) parts.push(lab);
    } catch (e) {}
    try {
      const lb = el.getAttribute("aria-labelledby");
      if (lb) {
        const root = el.getRootNode ? el.getRootNode() : null;
        for (const id of lb.split(/\s+/).filter(Boolean)) {
          const ref = (root && root.getElementById && root.getElementById(id))
            || (typeof document !== "undefined" && document.getElementById && document.getElementById(id));
          if (ref) parts.push(ref.innerText || ref.textContent || "");
        }
      }
    } catch (e) {}
    return parts.join(" ");
  }

  // Split camelCase ("pwdField" -> "pwd Field") before token matching, or
  // the token a name hides inside a compound word is never seen.
  function hintTokens(hint) {
    return String(hint)
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
      .toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  }

  function hintIsSensitive(hint) {
    if (SENSITIVE_NAME_RX.test(hint)) return true;
    const tokens = hintTokens(hint);
    if (tokens.some((t) => SENSITIVE_TOKENS.has(t))) return true;
    // A field named just "code" is ordinary (postal code, promo code); it
    // counts as a verification code only with a second signal in the hint:
    // a digit count ("6-digit code"), or verification/digit wording.
    if (tokens.includes("code")
        && (/\d+\s*-?\s*digit/i.test(hint)
            || tokens.includes("digit") || tokens.includes("digits")
            || tokens.includes("verification"))) return true;
    return false;
  }

  function isPaymentFrame(el) {
    if (!el || (el.tagName || "").toUpperCase() !== "IFRAME") return false;
    const title = `${el.getAttribute("name") || ""} ${el.getAttribute("title") || ""}`;
    if (/card/i.test(title)) return true;
    let host = "";
    const base = typeof location !== "undefined" ? location.href : "about:blank";
    try { host = new URL(el.getAttribute("src") || "", base).hostname.toLowerCase(); } catch (e) { return false; }
    return PAYMENT_FRAME_HOSTS.some((h) => host === h || host.endsWith("." + h));
  }

  function isSensitiveInput(el) {
    if (!el || !el.getAttribute) return false;
    const tag = (el.tagName || "").toUpperCase(); // XML documents keep authored case
    if (tag !== "INPUT") {
      // A card or OTP box built as an editable element is a field all the same.
      if (el.isContentEditable) return hintIsSensitive(sensitiveHintOf(el));
      return isPaymentFrame(el);
    }
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (type === "password") return true;
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/).pop();
    if (ac && SENSITIVE_AUTOCOMPLETE.has(ac)) return true;
    return hintIsSensitive(sensitiveHintOf(el));
  }

  // A same-origin frame's document is readable; a cross-origin one throws
  // on access and stays judged by the frame element alone (host list and
  // name/title in isPaymentFrame).
  function frameDocument(frame) {
    try { return frame.contentDocument || null; } catch (e) { return null; }
  }

  function containsSensitiveInput(el) {
    if (!el || !el.querySelectorAll) return false;
    if (isSensitiveInput(el)) return true;
    if ((el.tagName || "").toUpperCase() === "IFRAME") {
      const doc = frameDocument(el);
      if (doc && containsSensitiveInput(doc)) return true;
    }
    // queryAllDeep, not querySelectorAll: the resolver pierces open shadow
    // roots, so the safety scan must see the same elements it can resolve.
    if (queryAllDeep(el, "input").some(isSensitiveInput)) return true;
    const frames = queryAllDeep(el, "iframe");
    if (frames.some(isPaymentFrame)) return true;
    for (const frame of frames) {
      const doc = frameDocument(frame);
      if (doc && containsSensitiveInput(doc)) return true;
    }
    // Editable fields are not INPUT elements; a container wrapping one must
    // agree with the direct-target check, which flags them by their hints.
    return queryAllDeep(el, "[contenteditable]").some(isSensitiveInput);
  }

  // The sensitive fields under a root (document or element), with the same
  // semantics as containsSensitiveInput: inputs and editable fields flagged
  // by isSensitiveInput, payment frames as elements, and fields inside
  // same-origin frame documents the page can read.
  function enumerateSensitiveFields(root) {
    if (!root || !root.querySelectorAll) return [];
    const out = [];
    const seen = new Set();
    const add = (el) => { if (el && !seen.has(el)) { seen.add(el); out.push(el); } };
    if (isSensitiveInput(root)) add(root);
    for (const el of queryAllDeep(root, "input")) if (isSensitiveInput(el)) add(el);
    for (const el of queryAllDeep(root, "[contenteditable]")) if (isSensitiveInput(el)) add(el);
    for (const frame of queryAllDeep(root, "iframe")) {
      if (isPaymentFrame(frame)) { add(frame); continue; }
      const doc = frameDocument(frame);
      if (doc) for (const el of enumerateSensitiveFields(doc)) add(el);
    }
    if ((root.tagName || "").toUpperCase() === "IFRAME") {
      const doc = frameDocument(root);
      if (doc) for (const el of enumerateSensitiveFields(doc)) add(el);
    }
    return out;
  }

  function descriptorOf(el) {
    if (!el || !el.tagName) return { tag: "", role: "", name: "" };
    const role = roleOf(el);
    return { tag: el.tagName.toLowerCase(), role, name: nameOf(el, role) || labelText(el) || "" };
  }

  window.__eabLib = {
    INTERACTIVE_ROLES, CONTAINER_TAGS, roleOf, nameOf, ownText, collapse, labelText, isVisible,
    childElements, queryAllDeep, rectOf, center, highlight, frameInfo, iframeIndex,
    deepFromPoint, deepActive, controlAt, isSensitiveInput, containsSensitiveInput,
    enumerateSensitiveFields, descriptorOf
  };
})();
