// Edge Agent Bridge - functions injected into pages with chrome.scripting.executeScript.
// Every export is self-contained: no references to module scope, because the function
// body is serialized and runs in the tab's isolated world. Shared helpers live in
// page-lib.js (window.__eabLib), injected once per document by the worker.

export function pageCursor(x, y, isClick) {
  try {
    let style = document.getElementById("__eab_cursor_style");
    if (!style) {
      style = document.createElement("style");
      style.id = "__eab_cursor_style";
      style.textContent = `
        @keyframes __eab_click_pulse {
          0% { transform: translate(-50%, -50%) scale(0.3); opacity: 0.85; }
          100% { transform: translate(-50%, -50%) scale(2.0); opacity: 0; }
        }
        .__eab_cursor_wrap {
          position: fixed !important; pointer-events: none !important; z-index: 2147483647 !important;
          top: 0 !important; left: 0 !important; will-change: transform !important;
          transition: transform 0.08s cubic-bezier(0.2, 0, 0.2, 1) !important;
          filter: drop-shadow(0 2px 4px rgba(0,0,0,0.35)) drop-shadow(0 1px 2px rgba(0,0,0,0.25)) !important;
        }
        .__eab_cursor_svg { display: block !important; transform-origin: 0 0 !important; transition: transform 0.06s ease !important; }
        .__eab_cursor_pressed .__eab_cursor_svg { transform: scale(0.92) translate(1px, 1px) !important; }
        .__eab_cursor_ripple {
          position: fixed !important; pointer-events: none !important; z-index: 2147483646 !important;
          width: 24px !important; height: 24px !important; border-radius: 50% !important;
          border: 2px solid rgba(0, 120, 212, 0.85) !important; background: rgba(0, 120, 212, 0.15) !important;
          animation: __eab_click_pulse 0.35s cubic-bezier(0.1, 0.8, 0.3, 1) forwards !important;
        }
      `;
      (document.head || document.documentElement).appendChild(style);
    }

    let cur = document.getElementById("__eab_cursor");
    if (!cur) {
      cur = document.createElement("div");
      cur.id = "__eab_cursor";
      cur.className = "__eab_cursor_wrap";
      (document.body || document.documentElement).appendChild(cur);
    }

    const el = document.elementFromPoint(x, y);
    let cursorType = "arrow";
    let offsetX = 0;
    let offsetY = 0;
    if (el) {
      const comp = window.getComputedStyle(el);
      const isInput = el.matches("input[type='text'], input[type='password'], input[type='search'], input:not([type]), textarea, [contenteditable='true']");
      const isClickable = comp.cursor === "pointer" || el.matches("button, a, select, [role='button'], [role='checkbox'], [role='radio'], [role='menuitem'], [role='treeitem'], [role='tab'], svg");
      if (isInput) {
        cursorType = "text";
        offsetX = -8;
        offsetY = -12;
      } else if (isClickable) {
        cursorType = "pointer";
        offsetX = -5;
        offsetY = -1;
      }
    }

    let svgHtml = "";
    if (cursorType === "pointer") {
      svgHtml = `<svg class="__eab_cursor_svg" width="22" height="26" viewBox="0 0 22 26" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6.5 1.5C6.5 0.67 5.83 0 5 0C4.17 0 3.5 0.67 3.5 1.5V11.5L2.3 10.3C1.7 9.7 0.8 9.7 0.2 10.3C-0.3 10.8 -0.3 11.7 0.2 12.3L4.5 16.6C5.9 18 7.8 18.5 9.8 18.5H11.5C14.5 18.5 17 16 17 13V7.5C17 6.67 16.33 6 15.5 6C14.67 6 14 6.67 14 7.5V8.5C14 7.67 13.33 7 12.5 7C11.67 7 11 7.67 11 8.5V7C11 6.17 10.33 5.5 9.5 5.5C8.67 5.5 8 6.17 8 7V1.5C8 0.67 7.33 0 6.5 0" transform="translate(1, 1)" fill="#FFFFFF" stroke="#1A1A1A" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
    } else if (cursorType === "text") {
      svgHtml = `<svg class="__eab_cursor_svg" width="16" height="24" viewBox="0 0 16 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M4 2H12M8 2V22M4 22H12" stroke="#111111" stroke-width="2" stroke-linecap="round"/><path d="M5 3H11M8 3V21M5 21H11" stroke="#FFFFFF" stroke-width="0.8" stroke-linecap="round"/></svg>`;
    } else {
      svgHtml = `<svg class="__eab_cursor_svg" width="22" height="24" viewBox="0 0 22 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M1.5 1V19.8L5.9 15.6L9.1 22.8L12 21.5L8.8 14.3H15.1L1.5 1Z" fill="#FFFFFF" stroke="#181818" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
    }
    // Static SVG literals from this file only; nothing page-controlled reaches innerHTML.
    cur.innerHTML = svgHtml;
    cur.style.transform = `translate(${x + offsetX}px, ${y + offsetY}px)`;

    if (isClick) {
      cur.classList.add("__eab_cursor_pressed");
      setTimeout(() => cur.classList.remove("__eab_cursor_pressed"), 120);
      const rip = document.createElement("div");
      rip.className = "__eab_cursor_ripple";
      rip.style.left = x + "px";
      rip.style.top = y + "px";
      (document.body || document.documentElement).appendChild(rip);
      setTimeout(() => rip.remove(), 380);
    }
  } catch (e) {}
  return { success: true, x, y };
}

// Synthetic hover fallback: fires pointer/mouse enter-leave events on the element under (x, y).
export function pageMoveAt(x, y) {
  const el = document.elementFromPoint(x, y);
  if (!el) return { success: true, x, y };
  const lastEl = window.__eab_last_hovered_el;
  if (lastEl && lastEl !== el) {
    const outOpts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
    try {
      lastEl.dispatchEvent(new MouseEvent("mouseout", outOpts));
      lastEl.dispatchEvent(new MouseEvent("mouseleave", outOpts));
      lastEl.dispatchEvent(new PointerEvent("pointerout", outOpts));
      lastEl.dispatchEvent(new PointerEvent("pointerleave", outOpts));
    } catch (e) {}
  }
  window.__eab_last_hovered_el = el;
  const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
  el.dispatchEvent(new PointerEvent("pointerover", opts));
  el.dispatchEvent(new PointerEvent("pointerenter", opts));
  el.dispatchEvent(new MouseEvent("mouseover", opts));
  el.dispatchEvent(new MouseEvent("mouseenter", opts));
  el.dispatchEvent(new PointerEvent("pointermove", opts));
  el.dispatchEvent(new MouseEvent("mousemove", opts));
  return { success: true, x, y, tag: el.tagName, id: el.id, text: (el.innerText || el.value || "").substring(0, 50) };
}

export function pageClickAt(x, y) {
  const el = document.elementFromPoint(x, y);
  if (!el) return { success: false, code: "target_not_found", error: `No element at point (${x}, ${y})` };
  try { el.focus(); } catch (e) {}
  const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
  el.dispatchEvent(new PointerEvent("pointerdown", opts));
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new PointerEvent("pointerup", opts));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  el.dispatchEvent(new MouseEvent("click", opts));
  return { success: true, tag: el.tagName, id: el.id, className: el.className, text: (el.innerText || el.value || "").substring(0, 100), x, y };
}

export function pageDblclickAt(x, y) {
  const el = document.elementFromPoint(x, y);
  if (!el) return { success: false, code: "target_not_found", error: `No element at point (${x}, ${y})` };
  const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  el.dispatchEvent(new MouseEvent("click", opts));
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  el.dispatchEvent(new MouseEvent("click", opts));
  el.dispatchEvent(new MouseEvent("dblclick", opts));
  return { success: true, tag: el.tagName, id: el.id, text: (el.innerText || el.value || "").substring(0, 100), x, y };
}

export function pageRightClickAt(x, y) {
  const el = document.elementFromPoint(x, y);
  if (!el) return { success: false, code: "target_not_found", error: "No element at coordinates" };
  const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 2 };
  el.dispatchEvent(new MouseEvent("contextmenu", opts));
  return { success: true, tag: el.tagName, id: el.id };
}

export function pageClickElement(selector, text) {
  let el = null;
  if (selector) {
    el = document.querySelector(selector);
  }
  if (!el && text) {
    const lower = text.toLowerCase().trim();
    const candidates = Array.from(document.querySelectorAll("button, a, input[type='button'], input[type='submit'], [role='button'], label, .btn, span, td, tr"));
    el = candidates.find(c => (c.innerText || c.value || c.textContent || "").toLowerCase().trim() === lower) ||
         candidates.find(c => (c.innerText || c.value || c.textContent || "").toLowerCase().includes(lower));
  }
  if (!el) {
    return { success: false, code: "target_not_found", error: `Element not found: selector="${selector}", text="${text}"` };
  }
  try { el.scrollIntoView({ behavior: "instant", block: "center" }); } catch (e) {}
  try { el.focus(); } catch (e) {}
  const mouseOpts = { bubbles: true, cancelable: true, view: window };
  el.dispatchEvent(new MouseEvent("mousedown", mouseOpts));
  el.dispatchEvent(new MouseEvent("mouseup", mouseOpts));
  el.dispatchEvent(new MouseEvent("click", mouseOpts));
  return { success: true, tag: el.tagName, id: el.id, text: (el.innerText || el.value || "").substring(0, 100) };
}

export function pageDblclickElement(selector, text) {
  let el = null;
  if (selector) {
    el = document.querySelector(selector);
  }
  if (!el && text) {
    const lower = text.toLowerCase().trim();
    const all = Array.from(document.querySelectorAll("tr, div, td, span, button, a"));
    el = all.find(c => (c.innerText || "").trim() === lower) ||
         all.find(c => (c.innerText || "").includes(lower));
  }
  if (!el) {
    return { success: false, code: "target_not_found", error: `Element not found: selector="${selector}", text="${text}"` };
  }
  try { el.scrollIntoView({ behavior: "instant", block: "center" }); } catch (e) {}
  try { el.focus(); } catch (e) {}
  const mouseOpts = { bubbles: true, cancelable: true, view: window };
  el.dispatchEvent(new MouseEvent("mousedown", mouseOpts));
  el.dispatchEvent(new MouseEvent("mouseup", mouseOpts));
  el.dispatchEvent(new MouseEvent("click", mouseOpts));
  el.dispatchEvent(new MouseEvent("mousedown", mouseOpts));
  el.dispatchEvent(new MouseEvent("mouseup", mouseOpts));
  el.dispatchEvent(new MouseEvent("click", mouseOpts));
  el.dispatchEvent(new MouseEvent("dblclick", mouseOpts));
  return { success: true, tag: el.tagName, id: el.id, text: (el.innerText || "").substring(0, 100) };
}

export function pageTypeText(selector, text, clear) {
  let el = null;
  if (selector) {
    el = document.querySelector(selector);
  }
  if (!el) {
    const a = document.activeElement;
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)) {
      el = a;
    }
  }
  if (!el) {
    return { success: false, code: "target_not_found", error: `Input element not found: selector="${selector}"` };
  }
  try { el.scrollIntoView({ behavior: "instant", block: "center" }); } catch (e) {}
  try { el.focus(); } catch (e) {}
  const targetInput = el.shadowRoot ? (el.shadowRoot.querySelector("input, textarea") || el) : el;
  if (clear) {
    el.value = "";
    if (targetInput !== el) targetInput.value = "";
  }
  const val = (clear ? "" : (targetInput.value || el.value || "")) + text;
  el.value = val;
  if (targetInput !== el) targetInput.value = val;
  if (el.setAttribute) el.setAttribute("current-value", val);
  targetInput.dispatchEvent(new Event("input", { bubbles: true }));
  targetInput.dispatchEvent(new Event("change", { bubbles: true }));
  if (targetInput !== el) {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  return { success: true, tag: el.tagName, id: el.id, name: el.name || el.getAttribute("name") || "", value: val };
}

// Native fill, step 1: focus the input under (x, y) (or the label's input) and clear it.
export function pageFillPrepare(cx, cy, shouldClear) {
  let el = document.elementFromPoint(cx, cy);
  if (el && el.tagName === "LABEL") {
    if (el.htmlFor) el = document.getElementById(el.htmlFor) || el;
    else el = el.querySelector("input, textarea") || el;
  }
  if (!el || (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA" && !el.isContentEditable)) {
    const a = document.activeElement;
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)) {
      el = a;
    }
  }
  if (!el) return { success: false, code: "target_not_found", error: "No input at point" };
  const targetInput = el.shadowRoot ? (el.shadowRoot.querySelector("input, textarea") || el) : el;
  targetInput.focus();
  if (shouldClear) {
    if (targetInput.isContentEditable) {
      const sel = window.getSelection();
      sel.selectAllChildren(targetInput);
      sel.deleteFromDocument();
    } else {
      if (targetInput.select) targetInput.select();
      targetInput.value = "";
      if (el !== targetInput) el.value = "";
    }
  }
  return { success: true };
}

// Native fill, step 2: make sure the value landed and fire the framework events.
export function pageFillCommit(cx, cy, tVal) {
  let el = document.elementFromPoint(cx, cy);
  if (el && el.tagName === "LABEL") {
    if (el.htmlFor) el = document.getElementById(el.htmlFor) || el;
    else el = el.querySelector("input, textarea") || el;
  }
  if (!el || (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA" && !el.isContentEditable)) {
    if (document.activeElement) el = document.activeElement;
  }
  if (!el) return { success: false, code: "target_not_found", error: "No input at point" };
  const targetInput = el.shadowRoot ? (el.shadowRoot.querySelector("input, textarea") || el) : el;
  if (!targetInput.isContentEditable && targetInput.value !== tVal) {
    targetInput.value = tVal;
    if (el !== targetInput) el.value = tVal;
  }
  targetInput.dispatchEvent(new Event("input", { bubbles: true }));
  targetInput.dispatchEvent(new Event("change", { bubbles: true }));
  if (el !== targetInput) {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  return { success: true };
}

export function pagePressKey(key) {
  const el = document.activeElement || document.body;
  const opts = { key, code: key, bubbles: true, cancelable: true, view: window };
  el.dispatchEvent(new KeyboardEvent("keydown", opts));
  el.dispatchEvent(new KeyboardEvent("keypress", opts));
  el.dispatchEvent(new KeyboardEvent("keyup", opts));
  if (key === "Enter" && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) {
    el.dispatchEvent(new Event("change", { bubbles: true }));
    const form = el.closest("form");
    if (form) {
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
  }
  return { success: true, key, target: el.tagName };
}

export function pageScroll(x, y) {
  window.scrollBy(x, y);
  return { success: true, scrollX: window.scrollX, scrollY: window.scrollY };
}

export function pageTextAt(cx, cy) {
  let el = document.elementFromPoint(cx, cy);
  if (el && el.tagName === "LABEL") {
    if (el.htmlFor) el = document.getElementById(el.htmlFor) || el;
    else el = el.querySelector("input, textarea") || el;
  }
  const val = el ? (el.value || el.innerText || el.textContent || "") : "";
  return { success: true, text: val.trim() };
}

// Ref snapshot (spec 7.4). Needs window.__eabLib (page-lib.js); returns code "lib_missing"
// so the worker injects the lib and retries.
export async function pageSnapshot(opts) {
  const L = window.__eabLib;
  if (!L) return { success: false, code: "lib_missing" };
  const o = opts || {};
  const mode = o.mode === "full" ? "full" : (o.mode === "compact" ? "compact" : "interactive");
  const compact = mode === "compact";
  const prefix = o.frameLabel || "";
  const MAX_NODES = o.maxNodes === 0 ? Infinity : (o.maxNodes || 400);
  const refs = new Map();
  const lines = [];
  const nodes = [];
  const iframes = [];
  const info = await L.frameInfo();
  let counter = 0;
  let truncated = 0;

  function newRef(el) {
    counter += 1;
    const ref = `${prefix}e${counter}`;
    refs.set(ref, el);
    return ref;
  }

  function quote(s) {
    return String(s).replace(/"/g, '\\"');
  }

  function attrsFor(el, role) {
    const parts = [];
    const tag = el.tagName.toUpperCase();
    const inner = el.shadowRoot ? el.shadowRoot.querySelector("input, textarea, select") : null;
    const src = inner || el;
    const type = ((src.getAttribute && src.getAttribute("type")) || "").toLowerCase();
    if (role === "textbox" || role === "searchbox" || role === "spinbutton" || role === "slider") {
      const v = src.isContentEditable ? L.collapse(src.innerText) : String(src.value ?? "");
      if (type === "password") {
        parts.push("type=password");
        parts.push(`value="${v ? "••••" : ""}"`);
      } else {
        parts.push(`value="${quote(v.slice(0, 80))}"`);
      }
      const ph = src.getAttribute && src.getAttribute("placeholder");
      if (ph && L.collapse(ph) && L.collapse(ph) !== L.nameOf(el, role)) parts.push(`placeholder="${quote(L.collapse(ph).slice(0, 60))}"`);
    } else if (role === "combobox" || role === "listbox") {
      const sel = tag === "SELECT" ? el : (inner && inner.tagName === "SELECT" ? inner : null);
      if (sel) {
        const chosen = Array.from(sel.selectedOptions || []).map(op => L.collapse(op.textContent)).join(", ");
        parts.push(`value="${quote(chosen.slice(0, 80))}"`);
        parts.push(`options=${sel.options.length}`);
      } else if (src.value !== undefined) {
        parts.push(`value="${quote(String(src.value).slice(0, 80))}"`);
      }
    } else if (role === "checkbox" || role === "radio" || role === "switch" || role === "menuitemcheckbox" || role === "menuitemradio") {
      const checked = src.checked !== undefined ? src.checked : (el.getAttribute("aria-checked") === "true");
      if (checked) parts.push("checked");
    } else if (role === "link") {
      const href = el.getAttribute("href");
      if (href) parts.push(`href=${href.slice(0, 80)}`);
    } else if (role === "file") {
      if (!L.isVisible(el)) parts.push("hidden");
    }
    if (el.disabled || el.getAttribute("aria-disabled") === "true") parts.push("disabled");
    return parts.length ? " " + parts.join(" ") : "";
  }

  function emitNode(el, role, depth) {
    if (counter >= MAX_NODES) { truncated += 1; return; }
    const ref = newRef(el);
    const name = L.nameOf(el, role);
    if (compact) {
      lines.push(`${ref} ${role} "${quote(name)}"`);
    } else {
      lines.push(`${"  ".repeat(depth)}- ${role} "${quote(name)}" [${ref}]${attrsFor(el, role)}`);
    }
    const r = L.rectOf(el);
    const c = L.center(r);
    nodes.push({ ref, role, name, x: c.x + info.x, y: c.y + info.y, w: Math.round(r.width), h: Math.round(r.height), id: el.id || "" });
  }

  function rowHasInteractive(tr) {
    return L.queryAllDeep(tr, "a[href], button, input, select, textarea, [role], [onclick]").some(x => {
      const rr = L.roleOf(x);
      return rr && L.INTERACTIVE_ROLES.has(rr) && rr !== "row" && (rr === "file" || L.isVisible(x));
    });
  }

  function walk(el, depth) {
    for (const child of L.childElements(el)) {
      const tag = child.tagName.toUpperCase();
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "TEMPLATE" || child.id === "__eab_cursor" || child.id === "__eab_ring") continue;
      const role = L.roleOf(child);
      const isFile = role === "file";
      if (!isFile && !L.isVisible(child)) continue;

      if (role === "heading") {
        if (compact) continue;
        const level = Number((child.tagName.match(/^H([1-6])$/) || [])[1] || child.getAttribute("aria-level") || 2);
        lines.push(`${"  ".repeat(depth)}- heading "${quote(L.nameOf(child, role))}" level=${level}`);
        continue;
      }
      if (role === "iframe") {
        iframes.push({ line: lines.length, index: L.iframeIndex(child), depth });
        lines.push(`${"  ".repeat(depth)}- iframe "${quote(L.nameOf(child, role))}"`);
        continue;
      }
      if (role === "row") {
        if (rowHasInteractive(child)) {
          emitNode(child, "row", depth);
          walk(child, depth + 1);
        } else if (mode === "full") {
          const t = L.collapse(child.innerText);
          if (t) lines.push(`${"  ".repeat(depth)}- row "${quote(t.slice(0, 120))}"`);
        }
        continue;
      }
      if (role && L.INTERACTIVE_ROLES.has(role)) {
        emitNode(child, role, depth);
        continue;
      }
      if (L.CONTAINER_TAGS.has(tag) || role === "dialog" || role === "navigation" || role === "main" || role === "group" || role === "table") {
        if (compact) { walk(child, depth); continue; }
        const before = lines.length;
        lines.push("");
        walk(child, depth + 1);
        if (lines.length === before + 1) {
          lines.pop();
        } else {
          const label = (role === "table" || tag === "TABLE") ? "table" : (role || tag.toLowerCase());
          const caption = tag === "TABLE" && child.caption ? child.caption.innerText : "";
          const cname = L.collapse(child.getAttribute("aria-label") || child.title || child.getAttribute("name") || caption || "");
          lines[before] = `${"  ".repeat(depth)}- ${label} "${quote(cname.slice(0, 60))}"`;
        }
        continue;
      }
      if (mode === "full") {
        const own = L.ownText(child);
        if (own) lines.push(`${"  ".repeat(depth)}- text "${quote(own.slice(0, 120))}"`);
      }
      walk(child, depth);
    }
  }

  walk(document.body || document.documentElement, 0);
  if (truncated) lines.push(`… ${truncated} more nodes, use mode=full or filter`);
  window.__eab = { gen: o.gen || 0, refs };
  // total counts interactive nodes seen: refs kept plus refs dropped by MAX_NODES.
  return {
    success: true, lines, refs: counter, nodes, iframes, title: document.title, url: location.href,
    path: info.path, placed: info.ok, offset: { x: info.x, y: info.y },
    truncated, total: counter + truncated
  };
}

// Resolve a target expression to viewport coordinates (spec 7.4): ref, CSS selector, exact
// accessible name on interactive roles, associated label, substring on interactive roles.
export async function pageResolve(query, opts) {
  const L = window.__eabLib;
  if (!L) return { found: false, code: "lib_missing" };
  const o = opts || {};
  if (query === undefined || query === null || query === "") return { found: false, code: "bad_params", error: "Empty target query" };
  if (typeof query === "object" && query.x !== undefined && query.y !== undefined) {
    return { found: true, x: Number(query.x), y: Number(query.y) };
  }
  const info = await L.frameInfo();
  const q = String(query).trim();
  const qLower = q.toLowerCase();
  let el = null;
  let ref = "";

  if (/^(f\d+)?e\d+$/.test(q)) {
    if (!window.__eab || !window.__eab.refs) {
      return { found: false, code: "stale_snapshot", error: "snapshot is stale, call snapshot again" };
    }
    el = window.__eab.refs.get(q);
    if (!el) return { found: false, code: "stale_snapshot", error: `ref ${q} is not in the current snapshot, call snapshot again` };
    if (!el.isConnected) return { found: false, code: "stale_ref", error: `ref ${q} was removed from the page, call snapshot again` };
    ref = q;
  }

  const looksLikeSelector = /^[#.\[:]/.test(q) || q.includes(">") || q.includes("[") || (/^[a-z][\w-]*(\s*[>+~]\s*|\s+)[a-z#.\[]/i.test(q) && !/["']/.test(q));
  if (!el && looksLikeSelector) {
    try {
      el = document.querySelector(q) || L.queryAllDeep(document, q)[0] || null;
    } catch (e) {
      el = null;
    }
  }

  function interactiveCandidates() {
    return L.queryAllDeep(document, "a[href], button, input, select, textarea, [role], [onclick], [contenteditable], summary")
      .map(c => ({ el: c, role: L.roleOf(c) }))
      .filter(x => x.role && L.INTERACTIVE_ROLES.has(x.role) && x.role !== "row" && L.isVisible(x.el));
  }

  function smallest(list) {
    if (list.length === 0) return null;
    let best = list[0];
    let bestArea = Infinity;
    for (const c of list) {
      const r = c.getBoundingClientRect();
      const area = r.width * r.height;
      if (area > 0 && area < bestArea) { best = c; bestArea = area; }
    }
    return best;
  }

  if (!el) {
    const cands = interactiveCandidates();
    el = smallest(cands.filter(x => L.nameOf(x.el, x.role).toLowerCase() === qLower).map(x => x.el));
    if (!el) {
      const labels = L.queryAllDeep(document, "label");
      const matched = labels.filter(l => L.collapse(l.innerText || l.textContent).toLowerCase() === qLower)
        .concat(labels.filter(l => L.collapse(l.innerText || l.textContent).toLowerCase().includes(qLower)));
      for (const lab of matched) {
        const root = lab.getRootNode();
        let target = lab.htmlFor ? ((root.getElementById && root.getElementById(lab.htmlFor)) || document.getElementById(lab.htmlFor)) : null;
        if (!target) target = lab.querySelector("input, select, textarea, button");
        if (target) { el = target; break; }
      }
    }
    if (!el) {
      el = smallest(cands.filter(x => L.nameOf(x.el, x.role).toLowerCase().includes(qLower)).map(x => x.el));
    }
  }

  if (!el) return { found: false, code: "target_not_found", error: `Target not found: "${q}"` };

  try {
    el.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
  } catch (e) {}
  let r = L.rectOf(el);
  let c = L.center(r);
  // Targets with no clickable box (display:none upload inputs exist for programmatic
  // use) skip the covering check: hit-testing them is meaningless.
  let rendered = r.width > 0 && r.height > 0;
  if (rendered) {
    try {
      const cs = window.getComputedStyle(el);
      rendered = cs.display !== "none" && cs.visibility !== "hidden";
    } catch (e) { rendered = false; }
  }
  // Covering check: the click point must hit the target, not a sticky overlay.
  // A top element counts as a hit when it is the target, sits inside it, or is a
  // shadow host above it (browser hit-testing pierces open shadow trees). A plain
  // light-DOM ancestor means the target has pointer-events:none and would miss.
  const hitReaches = (top) => {
    if (!top) return false;
    if (top === el || el.contains(top)) return true;
    let node = el;
    for (;;) {
      const root = node.getRootNode ? node.getRootNode() : null;
      if (!root || !root.host) return false;
      if (root.host === top) return true;
      node = root.host;
    }
  };
  if (rendered) {
    let coveredBy = "";
    for (let attempt = 0; attempt < 2; attempt++) {
      const top = document.elementFromPoint(c.x, c.y);
      if (hitReaches(top)) { coveredBy = ""; break; }
      coveredBy = top && top.tagName ? top.tagName.toLowerCase() : "unknown";
      if (attempt === 0 && top) {
        const cr = top.getBoundingClientRect();
        const vh = window.innerHeight || document.documentElement.clientHeight || 800;
        if (vh - cr.bottom >= cr.top) {
          window.scrollBy(0, -(cr.bottom - c.y + 8));
        } else {
          window.scrollBy(0, c.y - cr.top + 8);
        }
        r = L.rectOf(el);
        c = L.center(r);
      }
    }
    if (coveredBy) {
      return { found: false, code: "click_covered",
               error: `Target "${q}" is covered by <${coveredBy}>; scroll it into view or dismiss the overlay` };
    }
  }
  if (o.highlight !== false) L.highlight(r, ref);
  const role = L.roleOf(el);
  return {
    found: true,
    rendered,
    x: c.x + info.x,
    y: c.y + info.y,
    // Frame-local center for hit-testing inside the ref's own frame; x/y are top-level for CDP.
    local: { x: c.x, y: c.y },
    width: Math.round(r.width),
    height: Math.round(r.height),
    tag: el.tagName.toLowerCase(),
    id: el.id || "",
    ref,
    role,
    text: L.nameOf(el, role).slice(0, 60)
  };
}

// Main-frame hit-test for a subframe target: at main-frame coords the top element must
// be an iframe (possibly behind one shadow host), not a main-frame overlay.
export function pageCoverCheck(cx, cy) {
  let top = null;
  try { top = document.elementFromPoint(cx, cy); } catch (e) {}
  if (top && top.shadowRoot) {
    try { top = top.shadowRoot.elementFromPoint(cx, cy) || top; } catch (e) {}
  }
  if (!top) return { covered: true, covering: "unknown" };
  const tag = top.tagName ? top.tagName.toLowerCase() : "unknown";
  if (tag === "iframe") return { covered: false };
  return { covered: true, covering: tag };
}

// Pick an <option> on the <select> found by ref or at (cx, cy). Matches value first, then
// label (exact, then substring, case-insensitive).
export function pageSelect(ref, cx, cy, value, label) {
  let el = null;
  if (ref && window.__eab && window.__eab.refs) el = window.__eab.refs.get(ref) || null;
  if (!el) el = document.elementFromPoint(cx, cy);
  if (el && el.tagName === "LABEL") el = (el.htmlFor && document.getElementById(el.htmlFor)) || el.querySelector("select") || el;
  if (el && el.tagName !== "SELECT") {
    el = (el.closest && el.closest("select")) || (el.shadowRoot && el.shadowRoot.querySelector("select")) || (el.querySelector && el.querySelector("select")) || null;
  }
  if (!el) return { success: false, code: "not_select", error: "Target is not a <select>" };
  const options = Array.from(el.options);
  const labels = options.map(o => (o.textContent || "").trim());
  const norm = s => String(s ?? "").trim().toLowerCase();
  let opt = null;
  if (value !== undefined && value !== null && value !== "") {
    opt = options.find(o => o.value === String(value)) || options.find(o => norm(o.value) === norm(value));
  }
  if (!opt && label !== undefined && label !== null && label !== "") {
    opt = options.find(o => norm(o.textContent) === norm(label)) || options.find(o => norm(o.textContent).includes(norm(label)));
  }
  if (!opt && value !== undefined && value !== null && value !== "") {
    opt = options.find(o => norm(o.textContent) === norm(value)) || options.find(o => norm(o.textContent).includes(norm(value)));
  }
  if (!opt) return { success: false, code: "option_not_found", error: `No option matches value=${JSON.stringify(value ?? null)} label=${JSON.stringify(label ?? null)}`, options: labels };
  el.focus();
  el.value = opt.value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return { success: true, value: opt.value, label: (opt.textContent || "").trim(), options: labels };
}

// Find the file input for an upload target and mark it so the worker can address it over CDP.
// Pierces open shadow roots (Salesforce Lightning, custom elements); temp-reveals hidden inputs.
export function pageMarkUpload(ref, cx, cy) {
  let el = null;
  if (ref && window.__eab && window.__eab.refs) el = window.__eab.refs.get(ref) || null;
  if (!el) el = document.elementFromPoint(cx, cy);
  if (el && el.shadowRoot) {
    try { el = el.shadowRoot.elementFromPoint(cx, cy) || el; } catch (e) {}
  }
  if (!el) return { success: false, code: "target_not_found", error: "No element at point" };
  const isFile = n => n && n.tagName === "INPUT" && (n.getAttribute("type") || "").toLowerCase() === "file";
  const deepQuery = (root, sel) => {
    let found = null;
    try { found = root.querySelector(sel); } catch (e) {}
    if (found) return found;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = walker.nextNode())) {
      if (n.shadowRoot) {
        found = deepQuery(n.shadowRoot, sel);
        if (found) return found;
      }
    }
    return null;
  };
  let input = isFile(el) ? el : null;
  if (!input && el.tagName === "LABEL") {
    const c = (el.htmlFor && document.getElementById(el.htmlFor)) || el.querySelector("input[type=file]");
    if (isFile(c)) input = c;
  }
  if (!input && el.id) {
    const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    const c = lab && lab.htmlFor && document.getElementById(lab.htmlFor);
    if (isFile(c)) input = c;
  }
  if (!input) {
    const scope = el.closest("form") || (el.parentElement && el.parentElement !== document.body ? el.parentElement : null);
    if (scope) input = scope.querySelector("input[type=file]") || deepQuery(scope, "input[type=file]");
  }
  if (!input && el.shadowRoot) input = deepQuery(el.shadowRoot, "input[type=file]");
  if (!input) return { success: false, code: "no_file_input", error: "Target is not a file input and none was found in its form or parent" };
  document.querySelectorAll("[data-eab-upload]").forEach(n => n.removeAttribute("data-eab-upload"));
  input.setAttribute("data-eab-upload", "1");
  const cs = window.getComputedStyle(input);
  if (cs.display === "none" || cs.visibility === "hidden" || (input.offsetWidth === 0 && input.offsetHeight === 0)) {
    input.dataset.eabPrevStyle = input.getAttribute("style") || "";
    input.style.setProperty("display", "block", "important");
    input.style.setProperty("visibility", "visible", "important");
    input.style.setProperty("position", "fixed", "important");
    input.style.setProperty("left", "0", "important");
    input.style.setProperty("top", "0", "important");
    input.style.setProperty("width", "4px", "important");
    input.style.setProperty("height", "4px", "important");
    input.style.setProperty("opacity", "0", "important");
  }
  return { success: true, id: input.id || "", name: input.getAttribute("name") || "" };
}

export function pageUnmarkUpload() {
  const restore = root => {
    root.querySelectorAll("[data-eab-upload]").forEach(n => {
      n.removeAttribute("data-eab-upload");
      if (n.dataset.eabPrevStyle !== undefined) {
        const prev = n.dataset.eabPrevStyle;
        delete n.dataset.eabPrevStyle;
        if (prev) n.setAttribute("style", prev);
        else n.removeAttribute("style");
      }
    });
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = walker.nextNode())) {
      if (n.shadowRoot) restore(n.shadowRoot);
    }
  };
  restore(document);
  return { success: true };
}

// The `eval` action is the product: agents run page JavaScript on purpose. Reachable only
// through the daemon's token-gated /exec, never from page script.
export async function pageEval(code) {
  try {
    let result = eval(code);
    if (result && typeof result.then === "function") result = await result;
    return { success: true, result };
  } catch (e) {
    return { success: false, code: "eval_error", error: e.toString() };
  }
}

// check_radio: check a radio button or checkbox by selector, label text, or value.
export function pageCheckRadio(selector, text, value) {
  let el = null;
  if (selector) el = document.querySelector(selector);
  if (!el && value !== undefined && value !== null) {
    el = document.querySelector(`input[type="radio"][value="${value}"], input[type="checkbox"][value="${value}"]`);
  }
  if (!el && text) {
    const lower = text.toLowerCase().trim();
    const labels = Array.from(document.querySelectorAll("label, [role='radio'], [role='checkbox']"));
    const matched = labels.find(l => (l.innerText || "").trim().toLowerCase() === lower || (l.innerText || "").toLowerCase().includes(lower));
    if (matched) {
      if (matched.htmlFor) el = document.getElementById(matched.htmlFor);
      if (!el) el = matched.querySelector("input[type='radio'], input[type='checkbox']");
      if (!el) el = matched;
    }
  }
  if (!el) return { success: false, error: `Radio/checkbox not found: selector="${selector}", text="${text}", value="${value}"` };

  try { el.scrollIntoView({ behavior: "instant", block: "center" }); } catch (e) {}
  try { el.focus(); } catch (e) {}

  const label = el.closest("label") || (el.id ? document.querySelector(`label[for="${el.id}"]`) : null);
  if (label && label !== el) {
    label.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true }));
    label.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    label.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true }));
    label.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
    label.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  }

  const mo = { bubbles: true, cancelable: true, view: window };
  el.dispatchEvent(new PointerEvent("pointerdown", mo));
  el.dispatchEvent(new MouseEvent("mousedown", mo));
  el.dispatchEvent(new PointerEvent("pointerup", mo));
  el.dispatchEvent(new MouseEvent("mouseup", mo));
  el.dispatchEvent(new MouseEvent("click", mo));

  const inp = el.tagName === "INPUT" ? el : el.querySelector("input");
  if (inp) {
    if (!inp.checked) inp.checked = true;
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  return {
    success: true,
    tag: el.tagName,
    id: el.id || (inp ? inp.id : ""),
    name: el.name || (inp ? inp.name : ""),
    value: (inp ? inp.value : el.value) || "",
    checked: inp ? inp.checked : true,
  };
}

export async function pageAssertFocus(mode, uuid, checkOnly) {
  const lib = window.__eabLib;
  if (!lib) return { success: false, code: "lib_missing" };
  let el = null;
  if (mode && mode.active) {
    el = document.activeElement;
  } else if (mode) {
    el = document.elementFromPoint(mode.x, mode.y);
  }
  if (el && el.tagName === "LABEL") {
    if (el.htmlFor) el = document.getElementById(el.htmlFor) || el;
    else el = el.querySelector("input, textarea") || el;
  }
  if (!el) return { success: true, focused: false, focusable: false };
  const tag = el.tagName || "";
  const focusable = !el.disabled && (el.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el.tabIndex >= 0 && typeof el.focus === "function"));
  if (!checkOnly) {
    if (!focusable) return { success: true, focused: false, focusable: false };
    try { el.focus(); } catch (e) { return { success: true, focused: false, focusable: false }; }
    await new Promise(r => setTimeout(r, 50));
    try { el.dataset.bridgeAssert = uuid; } catch (e) {}
  }
  const active = document.activeElement;
  let focused = active === el || (!!active && !!active.dataset && active.dataset.bridgeAssert === uuid);
  if (!focused && active && active !== document.body) {
    const owns = ((el.getAttribute && el.getAttribute("aria-owns") || "") + " " + (el.getAttribute && el.getAttribute("aria-controls") || "")).split(/\s+/);
    if (active.id && owns.indexOf(active.id) !== -1) focused = true;
    const role = ((active.getAttribute && active.getAttribute("role")) || "").toLowerCase();
    if (role === "listbox" || role === "option") focused = true;
  }
  if (!focused && !checkOnly) {
    try { delete el.dataset.bridgeAssert; } catch (e) {}
  }
  return { success: true, focused, focusable };
}

export function pageReadback(mode) {
  const lib = window.__eabLib;
  if (!lib) return { success: false, code: "lib_missing" };
  let el = null;
  if (mode && mode.active) {
    el = document.activeElement;
  } else if (mode) {
    el = document.elementFromPoint(mode.x, mode.y);
  }
  if (el && el.tagName === "LABEL") {
    if (el.htmlFor) el = document.getElementById(el.htmlFor) || el;
    else el = el.querySelector("input, textarea") || el;
  }
  if (!el || el === document.body || el === document.documentElement) {
    return { success: true, present: false, verifiable: false };
  }
  const tag = el.tagName || "";
  const type = (el.getAttribute && el.getAttribute("type") || "").toLowerCase();
  if (el.isContentEditable) return { success: true, present: true, verifiable: true, value: el.innerText };
  if (tag === "INPUT" && (type === "checkbox" || type === "radio")) {
    return { success: true, present: true, verifiable: true, value: el.checked };
  }
  if (tag === "SELECT" && !el.multiple) return { success: true, present: true, verifiable: true, value: el.value };
  if ("value" in el) return { success: true, present: true, verifiable: true, value: el.value };
  return { success: true, present: true, verifiable: false, value: null };
}

export function pageReleaseAssert(uuid) {
  try {
    const marked = document.querySelectorAll('[data-bridge-assert="' + uuid + '"]');
    marked.forEach(m => { delete m.dataset.bridgeAssert; });
  } catch (e) {}
  return { success: true };
}
