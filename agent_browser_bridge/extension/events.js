// Edge Agent Bridge - per-tab CDP event buffers: console/exception/log entries, in-flight
// network requests for idle detection, and JavaScript dialog policy.
import { cdpSend } from "./cdp.js";

const MAX_ENTRIES = 200;
const MAX_TEXT = 1000;
const INFLIGHT_MAX_AGE_MS = 30000;
const FLUSH_DEBOUNCE_MS = 500;

const consoleBuf = new Map();    // tabId -> entries
const inflight = new Map();      // tabId -> Map(requestId -> {url, t})
const dialogPolicy = new Map();  // tabId -> {policy, promptText}
const openDialog = new Map();    // tabId -> {type, message, url}
const enabledTabs = new Set();
const flushTimers = new Map();

function clamp(text) {
  const s = String(text ?? "");
  return s.length > MAX_TEXT ? s.slice(0, MAX_TEXT) + "…" : s;
}

function buf(tabId) {
  if (!consoleBuf.has(tabId)) consoleBuf.set(tabId, []);
  return consoleBuf.get(tabId);
}

function push(tabId, entry) {
  const b = buf(tabId);
  b.push(entry);
  if (b.length > MAX_ENTRIES) b.splice(0, b.length - MAX_ENTRIES);
  scheduleFlush(tabId);
}

function scheduleFlush(tabId) {
  if (flushTimers.has(tabId)) return;
  flushTimers.set(tabId, setTimeout(() => {
    flushTimers.delete(tabId);
    try {
      chrome.storage.session.set({ [`console:${tabId}`]: buf(tabId) });
    } catch (e) {}
  }, FLUSH_DEBOUNCE_MS));
}

async function restore(tabId) {
  if (consoleBuf.has(tabId)) return;
  try {
    const data = await chrome.storage.session.get(`console:${tabId}`);
    const saved = data[`console:${tabId}`];
    consoleBuf.set(tabId, Array.isArray(saved) ? saved : []);
  } catch (e) {
    consoleBuf.set(tabId, []);
  }
}

function previewArg(a) {
  if (!a) return "";
  if (a.type === "string") return a.value;
  if (a.value !== undefined) return String(a.value);
  if (a.unserializableValue) return a.unserializableValue;
  if (a.description) return a.description;
  if (a.preview && a.preview.properties) {
    return "{" + a.preview.properties.map(p => `${p.name}: ${p.value}`).join(", ") + "}";
  }
  return a.type || "";
}

export async function enableEvents(tabId) {
  if (enabledTabs.has(tabId)) return;
  enabledTabs.add(tabId);
  await restore(tabId);
  for (const domain of ["Runtime", "Page", "Network", "Log"]) {
    try {
      await cdpSend(tabId, `${domain}.enable`, {});
    } catch (e) {}
  }
}

export function getConsole(tabId, clear = false) {
  const entries = buf(tabId).slice();
  if (clear) {
    consoleBuf.set(tabId, []);
    scheduleFlush(tabId);
  }
  return entries;
}

export function inflightUrls(tabId) {
  const m = inflight.get(tabId);
  if (!m) return [];
  const now = Date.now();
  for (const [id, r] of m) {
    if (now - r.t > INFLIGHT_MAX_AGE_MS) m.delete(id);
  }
  return Array.from(m.values()).map(r => r.url);
}

export function getOpenDialog(tabId) {
  return openDialog.get(tabId) || null;
}

export async function setDialogPolicy(tabId, policy, promptText) {
  const p = ["accept", "dismiss", "manual"].includes(policy) ? policy : "accept";
  dialogPolicy.set(tabId, { policy: p, promptText });
  const open = openDialog.get(tabId);
  if (open && p !== "manual") {
    await handleDialog(tabId, p, promptText);
  }
  return { policy: p, open: openDialog.get(tabId) || null };
}

async function handleDialog(tabId, policy, promptText) {
  try {
    const params = { accept: policy === "accept" };
    if (promptText !== undefined && promptText !== null) params.promptText = String(promptText);
    await cdpSend(tabId, "Page.handleJavaScriptDialog", params);
    openDialog.delete(tabId);
  } catch (e) {}
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  const tabId = source && source.tabId;
  if (!tabId) return;
  switch (method) {
    case "Runtime.consoleAPICalled": {
      const frame = params.stackTrace && params.stackTrace.callFrames && params.stackTrace.callFrames[0];
      push(tabId, {
        t: Date.now(), level: params.type,
        text: clamp((params.args || []).map(previewArg).join(" ")),
        url: frame ? frame.url : "", line: frame ? frame.lineNumber + 1 : null
      });
      break;
    }
    case "Runtime.exceptionThrown": {
      const d = params.exceptionDetails || {};
      const desc = (d.exception && (d.exception.description || d.exception.value)) || d.text || "Uncaught exception";
      push(tabId, { t: Date.now(), level: "error", text: clamp(desc), url: d.url || "", line: d.lineNumber != null ? d.lineNumber + 1 : null });
      break;
    }
    case "Log.entryAdded": {
      const e = params.entry || {};
      push(tabId, { t: Date.now(), level: e.level || "info", text: clamp(`${e.source ? e.source + ": " : ""}${e.text || ""}`), url: e.url || "", line: e.lineNumber != null ? e.lineNumber + 1 : null });
      break;
    }
    case "Network.requestWillBeSent": {
      const type = params.type || "";
      // Documents are covered by the load wait; subframe documents (sandboxed or out-of-process)
      // do not always report a finish event on the tab session and would leak into idle checks.
      if (type === "WebSocket" || type === "EventSource" || type === "Document") break;
      if (!inflight.has(tabId)) inflight.set(tabId, new Map());
      inflight.get(tabId).set(params.requestId, { url: params.request ? params.request.url : "", t: Date.now() });
      break;
    }
    case "Network.loadingFinished":
    case "Network.loadingFailed":
    case "Network.requestServedFromCache": {
      const m = inflight.get(tabId);
      if (m) m.delete(params.requestId);
      break;
    }
    case "Page.javascriptDialogOpening": {
      const policy = dialogPolicy.get(tabId) || { policy: "accept" };
      push(tabId, { t: Date.now(), level: "dialog", text: clamp(`${params.type}: ${params.message || ""}`), url: params.url || "", line: null });
      openDialog.set(tabId, { type: params.type, message: params.message || "", url: params.url || "", defaultPrompt: params.defaultPrompt });
      if (policy.policy !== "manual") handleDialog(tabId, policy.policy, policy.promptText);
      break;
    }
    case "Page.javascriptDialogClosed":
      openDialog.delete(tabId);
      break;
    case "Page.frameNavigated":
      if (params.frame && !params.frame.parentId) inflight.delete(tabId);
      break;
    default:
      break;
  }
});

chrome.debugger.onDetach.addListener((source) => {
  if (source && source.tabId) enabledTabs.delete(source.tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  consoleBuf.delete(tabId);
  inflight.delete(tabId);
  dialogPolicy.delete(tabId);
  openDialog.delete(tabId);
  enabledTabs.delete(tabId);
  try { chrome.storage.session.remove(`console:${tabId}`); } catch (e) {}
});
