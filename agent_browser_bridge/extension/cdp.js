// Agent Browser Bridge - chrome.debugger session cache, CDP input helpers, per-tab command queue.

const attachedTabs = new Set();
const debuggerErrors = new Map();

export function getDebuggerError(tabId) {
  return debuggerErrors.get(tabId) || null;
}

// Chromium's own wording when the session is gone; a page error that merely says
// "not attached" must not be mistaken for it.
export function isDetachError(msg) {
  return /Debugger is not attached|Detached while handling command/.test(msg || "");
}

// Attaching makes Edge show its "started debugging this browser" bar, which shrinks the
// viewport as it slides in, starting about 50-90 ms later. A point resolved before that is off
// by the bar's height and can fall below the viewport, where input is silently dropped. Wait
// until the height has held for 150 ms after changing, or 500 ms if it never changes. Polled
// over CDP rather than in the page, because the attach often comes right before a navigation.
async function settleViewport(tabId) {
  const height = () => new Promise((resolve) => {
    chrome.debugger.sendCommand({ tabId }, "Page.getLayoutMetrics", {}, (m) => {
      void chrome.runtime.lastError;
      resolve(m && m.cssLayoutViewport ? m.cssLayoutViewport.clientHeight : null);
    });
  });
  const start = Date.now();
  let last = await height();
  let changedAt = 0;
  while (Date.now() - start < 1500) {
    await new Promise(r => setTimeout(r, 30));
    const h = await height();
    if (h !== last) { last = h; changedAt = Date.now(); }
    else if (changedAt ? Date.now() - changedAt >= 150 : Date.now() - start >= 500) return;
  }
}

export async function ensureDebugger(tabId) {
  if (attachedTabs.has(tabId)) return true;
  const ok = await new Promise((resolve) => {
    chrome.debugger.attach({ tabId }, "1.3", () => {
      if (chrome.runtime.lastError) {
        const msg = chrome.runtime.lastError.message || "";
        const lower = msg.toLowerCase();
        if (lower.includes("already attached")) {
          attachedTabs.add(tabId);
          debuggerErrors.delete(tabId);
          return resolve(true);
        }
        attachedTabs.delete(tabId);
        debuggerErrors.set(tabId, msg);
        console.log(`[Edge Bridge] CDP attach failed for tab ${tabId}: ${msg}`);
        return resolve(false);
      }
      debuggerErrors.delete(tabId);
      resolve("fresh");
    });
  });
  if (ok === "fresh") {
    await settleViewport(tabId);
    attachedTabs.add(tabId);
  }
  return Boolean(ok);
}

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source && source.tabId) {
    attachedTabs.delete(source.tabId);
    debuggerErrors.delete(source.tabId);
    console.log(`[Edge Bridge] CDP detached from tab ${source.tabId}: ${reason}`);
  }
});

export async function cdpSend(tabId, method, params = {}) {
  const sendOnce = () => new Promise((resolve, reject) => {
    chrome.debugger.sendCommand({ tabId }, method, params, (result) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(result || {});
      }
    });
  });
  try {
    if (!attachedTabs.has(tabId)) {
      const ok = await ensureDebugger(tabId);
      if (!ok) {
        const err = new Error(debuggerErrors.get(tabId) || "Debugger attach failed");
        err.code = "debugger_unavailable";
        throw err;
      }
    }
    return await sendOnce();
  } catch (err) {
    if (isDetachError(err.message)) {
      attachedTabs.delete(tabId);
      const reattached = await ensureDebugger(tabId);
      if (reattached) {
        return await sendOnce();
      }
    }
    throw err;
  }
}

// --- Per-tab command queue (spec 7.2) ---
// One promise chain per tab; a failed command never rejects the chain, so the next
// command still runs. Different tabs run in parallel.
const tabQueues = new Map();

export function runOnTab(tabId, fn) {
  const prev = tabQueues.get(tabId) || Promise.resolve();
  const next = prev.catch(() => {}).then(() => fn());
  const stored = next.catch(() => {});
  tabQueues.set(tabId, stored);
  stored.then(() => {
    if (tabQueues.get(tabId) === stored) tabQueues.delete(tabId);
  });
  return next;
}

chrome.tabs.onRemoved.addListener((tabId) => {
  attachedTabs.delete(tabId);
  debuggerErrors.delete(tabId);
  tabQueues.delete(tabId);
});

// --- Native input (CDP Input domain) ---

export async function nativeMove(tabId, x, y) {
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
}

export async function nativeClick(tabId, x, y, button = "left", clickCount = 1) {
  const cx = Math.round(Number(x));
  const cy = Math.round(Number(y));
  const btnMask = button === "right" ? 2 : (button === "middle" ? 4 : 1);
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy });
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x: cx, y: cy, button, buttons: btnMask, clickCount });
  await new Promise(r => setTimeout(r, 40));
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x: cx, y: cy, button, buttons: 0, clickCount });
}

export async function nativeDrag(tabId, fromX, fromY, toX, toY, steps = 12, onStep = null) {
  const fX = Math.round(Number(fromX));
  const fY = Math.round(Number(fromY));
  const tX = Math.round(Number(toX));
  const tY = Math.round(Number(toY));
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x: fX, y: fY });
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x: fX, y: fY, button: "left", buttons: 1, clickCount: 1 });
  await new Promise(r => setTimeout(r, 50));
  for (let i = 1; i <= steps; i++) {
    const cx = Math.round(fX + (tX - fX) * (i / steps));
    const cy = Math.round(fY + (tY - fY) * (i / steps));
    await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy, button: "left", buttons: 1 });
    if (onStep) onStep(cx, cy);
    await new Promise(r => setTimeout(r, 20));
  }
  await cdpSend(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x: tX, y: tY, button: "left", buttons: 0, clickCount: 1 });
}

export async function nativeScroll(tabId, x, y, deltaX, deltaY) {
  await cdpSend(tabId, "Input.dispatchMouseEvent", {
    type: "mouseWheel",
    x: Math.round(Number(x)),
    y: Math.round(Number(y)),
    deltaX: Number(deltaX || 0),
    deltaY: Number(deltaY || 0)
  });
}

const KEY_CODES = {
  Enter: 13, Tab: 9, Escape: 27, Backspace: 8, Space: 32,
  ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
  Delete: 46, Home: 36, End: 35, PageUp: 33, PageDown: 34
};

export function parseKey(keyStr) {
  let modifiers = 0;
  let key = String(keyStr || "");
  // Split on the last "+" so that "+" and "Ctrl++" name the plus key rather than an empty one.
  const sep = key.lastIndexOf("+");
  if (sep > 0) {
    const parts = key.slice(0, sep).split("+");
    key = key.slice(sep + 1) || "+";
    for (const mod of parts) {
      const m = mod.toLowerCase();
      if (m === "ctrl" || m === "control") modifiers |= 2;
      else if (m === "alt") modifiers |= 1;
      else if (m === "shift") modifiers |= 4;
      else if (m === "meta" || m === "cmd" || m === "win") modifiers |= 8;
    }
  }
  const vk = KEY_CODES[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
  return { key, modifiers, vk };
}

// Type text one character at a time with real key events (autocomplete widgets need keydown).
// opts.onProgress(n) runs after the nth char is dispatched and may throw to abort;
// opts.deadlineMs (epoch ms) aborts with a deadline_exceeded error carrying typedSoFar.
export async function nativeType(tabId, text, delayMs = 20, opts = {}) {
  const chars = Array.from(String(text));
  const { onProgress = null, deadlineMs = 0 } = opts || {};
  for (let i = 0; i < chars.length; i++) {
    if (deadlineMs && Date.now() > Number(deadlineMs)) {
      const err = new Error("deadline exceeded");
      err.code = "deadline_exceeded";
      err.typedSoFar = i;
      throw err;
    }
    const ch = chars[i];
    if (ch === "\n" || ch === "\r") {
      await nativeKey(tabId, "Enter");
    } else {
      const upper = ch.length === 1 && /[a-z0-9]/i.test(ch) ? ch.toUpperCase().charCodeAt(0) : 0;
      await cdpSend(tabId, "Input.dispatchKeyEvent", { type: "keyDown", key: ch, text: ch, unmodifiedText: ch, windowsVirtualKeyCode: upper, nativeVirtualKeyCode: upper });
      await cdpSend(tabId, "Input.dispatchKeyEvent", { type: "keyUp", key: ch, windowsVirtualKeyCode: upper, nativeVirtualKeyCode: upper });
    }
    if (delayMs > 0) await new Promise(r => setTimeout(r, delayMs));
    if (onProgress && (i === 0 || (i + 1) % 8 === 0)) {
      await onProgress(i + 1);
    }
  }
  return chars.length;
}

export async function captureScreenshot(tabId, { format = "jpeg", quality = 80, clip = null } = {}) {
  const params = { format, captureBeyondViewport: false };
  if (format === "jpeg") params.quality = Math.max(1, Math.min(100, Number(quality) || 80));
  if (clip) params.clip = { x: clip.x, y: clip.y, width: clip.width, height: clip.height, scale: 1 };
  const res = await cdpSend(tabId, "Page.captureScreenshot", params);
  return `data:image/${format};base64,${res.data}`;
}

export async function setFileInputFiles(tabId, selector, files) {
  const doc = await cdpSend(tabId, "DOM.getDocument", { depth: 0 });
  let nodeId = null;
  try {
    const hit = await cdpSend(tabId, "DOM.querySelector", { nodeId: doc.root.nodeId, selector });
    nodeId = hit && hit.nodeId ? hit.nodeId : null;
  } catch (e) {}
  if (!nodeId) {
    const flat = await cdpSend(tabId, "DOM.getFlattenedDocument", { depth: -1, pierce: true });
    const attr = selector.replace(/^\[|\]$/g, "").split("=")[0];
    for (const n of (flat && flat.nodes) || []) {
      const attrs = n.attributes || [];
      for (let i = 0; i + 1 < attrs.length; i += 2) {
        if (attrs[i] === attr) { nodeId = n.nodeId; break; }
      }
      if (nodeId) break;
    }
  }
  if (!nodeId) throw new Error("marked file input not found in the document or its shadow trees");
  await cdpSend(tabId, "DOM.setFileInputFiles", { nodeId, files });
}

export async function nativeKey(tabId, keyStr) {
  const { key, modifiers, vk } = parseKey(keyStr);
  await cdpSend(tabId, "Input.dispatchKeyEvent", {
    type: "rawKeyDown", key, code: key, modifiers, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk
  });
  if (key.length === 1 || key === "Enter") {
    const txt = key === "Enter" ? "\r" : key;
    await cdpSend(tabId, "Input.dispatchKeyEvent", { type: "char", text: txt, unmodifiedText: txt, modifiers });
  }
  await cdpSend(tabId, "Input.dispatchKeyEvent", {
    type: "keyUp", key, code: key, modifiers, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk
  });
}
