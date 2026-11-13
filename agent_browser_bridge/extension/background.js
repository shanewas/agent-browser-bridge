// Agent Browser Bridge - service worker transport.
// One WebSocket to the local daemon; hello on open, pong on ping, commands go to dispatch().
import { dispatch, setActivityHook, setPauseGate } from "./actions.js";

const WS_URL = "ws://127.0.0.1:18999/ws";
const MAX_BACKOFF_MS = 5000;

let bridgeWs = null;
let pairingBlocked = false;
// Session storage outlives the MV3 worker, so a restarted worker doesn't retry a rejection.
const blockedLoaded = chrome.storage.session.get("pairingBlocked")
  .then((d) => { pairingBlocked = Boolean(d && d.pairingBlocked); })
  .catch((e) => console.warn("[Edge Bridge] pairingBlocked not restored:", e));
let wsReconnectTimer = null;
let wsBackoffMs = 1000;
let lastLog = "Initializing...";

console.log("[Edge Bridge] Service worker loaded");

function log(msg) {
  lastLog = `[${new Date().toLocaleTimeString()}] ${msg}`;
  console.log(`[Edge Bridge] ${msg}`);
  try {
    chrome.storage.local.set({ lastLog });
  } catch (e) {}
}

async function pairToken() {
  try {
    const data = await chrome.storage.local.get("pairToken");
    return data.pairToken || undefined;
  } catch (e) {
    return undefined;
  }
}

async function getOrCreateInstanceId() {
  try {
    const data = await chrome.storage.local.get("instanceId");
    if (data && data.instanceId) return data.instanceId;
    const id = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `inst-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    await chrome.storage.local.set({ instanceId: id });
    return id;
  } catch (e) {
    return undefined;
  }
}

function setPairingBlocked(value) {
  pairingBlocked = value;
  chrome.storage.session.set({ pairingBlocked: value })
    .catch((e) => console.warn("[Edge Bridge] pairingBlocked not saved:", e));
}

async function connectWebSocket(force = false) {
  if (!force) await blockedLoaded;
  if (pairingBlocked && !force) {
    return;
  }
  if (bridgeWs && (bridgeWs.readyState === WebSocket.CONNECTING || bridgeWs.readyState === WebSocket.OPEN)) {
    return;
  }
  if (wsReconnectTimer && !force) {
    return;
  }
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
  try {
    const ws = new WebSocket(WS_URL);
    bridgeWs = ws;
    let accepted = false;

    ws.onopen = async () => {
      wsBackoffMs = 1000;
      const hello = { version: chrome.runtime.getManifest().version, id: chrome.runtime.id };
      const token = await pairToken();
      if (token) hello.token = token;
      const inst = await getOrCreateInstanceId();
      if (inst) hello.instance = inst;
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ hello }));
      sendPauseSnapshot();
      log("Connected to bridge daemon");
    };

    ws.onmessage = async (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      if (!msg || typeof msg !== "object") return;
      if (!accepted) {
        // Any message means the daemon kept us after hello: a stale rejection notice is wrong now.
        accepted = true;
        try { chrome.storage.local.set({ pairingRejected: false }); } catch (e) {}
      }
      if (msg.ping) {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ pong: true }));
        return;
      }
      if (msg.control_ack) {
        const pend = pendingControl.get(msg.control_ack);
        if (pend) {
          clearTimeout(pend.timer);
          pendingControl.delete(msg.control_ack);
        }
        return;
      }
      if (msg.control === "state" && typeof msg.tabId === "number") {
        // The daemon cleared (or set) a pause, e.g. session_stop: mirror it here.
        applyDaemonPauseState(msg.tabId, Boolean(msg.paused));
        return;
      }
      if (msg.id && msg.action) {
        let result;
        try {
          result = await dispatch(msg);
        } catch (err) {
          result = { success: false, code: "internal_error", error: err.message };
        }
        if (result && result.success === false) {
          try {
            chrome.storage.local.set({ lastError: {
              code: result.code || "error",
              error: String(result.error || "").slice(0, 300),
              action: msg.action,
              at: new Date().toLocaleTimeString(),
            }});
          } catch (e) {}
        }
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ id: msg.id, result: result || {} }));
        } else {
          log(`Result for ${msg.id} dropped: socket closed`);
        }
      }
    };

    ws.onclose = (event) => {
      bridgeWs = null;
      if (event && (event.code === 4001 || event.code === 4002)) {
        // Rejected (4001 pairing, 4002 another browser holds the bridge): reconnecting only
        // repeats the rejection, including from the watchdog alarm. Stay down until a token
        // is saved or Reconnect is clicked (both arrive as RECONNECT, which connects with force).
        setPairingBlocked(true);
        if (event.code === 4001) {
          try { chrome.storage.local.set({ pairingRejected: true }); } catch (e) {}
        }
        log(`Bridge closed with ${event.code} (${event.reason || "connection rejected"}); not reconnecting until a token is saved or Reconnect is clicked`);
        return;
      }
      scheduleWsReconnect();
    };

    ws.onerror = () => {
      try { ws.close(); } catch (e) {}
    };
  } catch (e) {
    scheduleWsReconnect();
  }
}

function scheduleWsReconnect() {
  if (wsReconnectTimer) return;
  const delay = wsBackoffMs;
  wsBackoffMs = Math.min(Math.round(wsBackoffMs * 1.5), MAX_BACKOFF_MS);
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectWebSocket(true);
  }, delay);
}

function wsConnected() {
  return Boolean(bridgeWs && bridgeWs.readyState === WebSocket.OPEN);
}

// Content-script keepalive and popup messages: their only job is to wake this worker.
// connectWebSocket() is a no-op while the socket is open or a reconnect timer is pending.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const bannerHandled = msg ? handleBannerMessage(msg, sender, sendResponse) : false;
  if (bannerHandled === "async") return true;
  if (bannerHandled) return false;
  if (msg && msg.type === "RECONNECT") {
    setPairingBlocked(false);
    try { chrome.storage.local.set({ pairingRejected: false }); } catch (e) {}
    if (bridgeWs) {
      try { bridgeWs.close(); } catch (e) {}
    } else {
      connectWebSocket(true);
    }
    sendResponse({ ok: true });
    return false;
  }
  connectWebSocket();
  sendResponse({ ok: true, wsConnected: wsConnected(), lastLog });
  return false;
});

// Fallback wake-up when no web tab is open to send keepalives (30 s floor for packed extensions).
chrome.alarms.create("sw_watchdog", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "sw_watchdog") connectWebSocket();
});

// Open onboarding tab on initial extension install
chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    chrome.tabs.create({ url: chrome.runtime.getURL("onboarding.html") });
  }
});

// ---------------------------------------------------------------------------
// Presence pill ("banner"): per-tab working state, takeover pause, icon mirror.
// The worker owns the paused set (persisted in session storage so a restart
// keeps a pause) and transient working state (memory only). Deadlines ride each
// BANNER message as workingUntil; the content script hides the pill locally
// when the deadline passes, so worker suspension cannot strand a stale pill.
// ---------------------------------------------------------------------------

const BANNER_METADATA = new Set(["ping", "status", "tabs", "list_tabs", "tab", "get_active_tab"]);
const BANNER_READONLY = new Set(["snapshot", "text", "elements", "interactive", "console", "wait", "wait_for", "history_search", "group_list"]);
const VERB_FOR = {
  click: "Clicking", dblclick: "Clicking", rightclick: "Clicking", drag: "Dragging",
  fill: "Typing", type: "Typing", key: "Typing", nav: "Navigating", back: "Navigating",
  forward: "Navigating", reload: "Navigating", snapshot: "Reading", text: "Reading",
  get_text: "Reading", elements: "Reading", interactive: "Reading", wait: "Waiting",
  wait_for: "Waiting", upload: "Uploading", screenshot: "Reading", eval: "Working",
  hover: "Moving", move: "Moving", scroll: "Scrolling", select: "Selecting",
  check: "Selecting", check_radio: "Selecting", select_radio: "Selecting",
};

let pausedTabs = new Set();
let hiddenBannerTabs = new Set();
const pausedReady = Promise.all([
  chrome.storage.session.get("pausedTabs").then((d) => {
    if (d && Array.isArray(d.pausedTabs)) pausedTabs = new Set(d.pausedTabs.filter((n) => typeof n === "number"));
  }).catch(() => {}),
  chrome.storage.session.get("hiddenBannerTabs").then((d) => {
    if (d && Array.isArray(d.hiddenBannerTabs)) hiddenBannerTabs = new Set(d.hiddenBannerTabs.filter((n) => typeof n === "number"));
  }).catch(() => {}),
]);
pausedReady.then(() => sendPauseSnapshot());
const workingTabs = new Map(); // tabId -> {agent, verb, inflight, until, readTimer, detailTimer, shown}
const pendingControl = new Map(); // control id -> {frame, timer, tries}
let controlSeq = 0;
let bannerTookOver = false;
let desktopPaused = false;
chrome.storage.local.get("bannerTookOver").then((d) => { bannerTookOver = Boolean(d && d.bannerTookOver); }).catch(() => {});

function persistPaused() {
  chrome.storage.session.set({ pausedTabs: [...pausedTabs], hiddenBannerTabs: [...hiddenBannerTabs] }).catch(() => {});
}

async function bannerEnabled() {
  try {
    const d = await chrome.storage.local.get("bannerEnabled");
    if (d && d.bannerEnabled === false) return false;
    return true;
  } catch (e) { return true; }
}

function bannerSend(tabId, msg) {
  bannerEnabled().then((on) => {
    if (!on && msg.state !== "paused") return;
    try {
      const p = chrome.tabs.sendMessage(tabId, { type: "BANNER", ...msg });
      if (p && p.catch) p.catch(() => {});
    } catch (e) {}
  });
}

function iconFor(tabId) {
  const kind = pausedTabs.has(tabId) ? "paused" : (workingTabs.has(tabId) ? "working" : null);
  const path = kind
    ? { 16: `icons/icon-${kind}16.png`, 48: `icons/icon-${kind}48.png`, 128: `icons/icon-${kind}128.png` }
    : { 16: "icons/icon16.png", 48: "icons/icon48.png", 128: "icons/icon128.png" };
  try {
    const p = chrome.action.setIcon({ tabId, path });
    if (p && p.catch) p.catch(() => {});
  } catch (e) {}
}

function pushWorking(tabId) {
  const w = workingTabs.get(tabId);
  if (!w || pausedTabs.has(tabId) || hiddenBannerTabs.has(tabId)) return;
  bannerSend(tabId, {
    state: "working", agent: w.agent || undefined, verb: w.detail ? (w.verb || undefined) : undefined,
    workingUntil: w.until, mutating: w.mutating, teachTooltip: !bannerTookOver,
  });
  iconFor(tabId);
}

function bannerActivity(tabId, action, phase, agent) {
  if (typeof tabId !== "number") return;
  let w = workingTabs.get(tabId);
  if (phase === "start") {
    if (!w) {
      w = { agent: undefined, verb: undefined, inflight: 0, until: 0, readTimer: null, detailTimer: null, shown: false, mutating: false, detail: false };
      workingTabs.set(tabId, w);
    }
    if (agent) w.agent = agent;
    w.verb = VERB_FOR[action] || w.verb;
    w.inflight += 1;
    const mutating = !BANNER_METADATA.has(action) && !BANNER_READONLY.has(action);
    if (mutating) {
      w.mutating = true;
      w.until = Math.max(w.until, Date.now() + 1500);
      if (w.readTimer) { clearTimeout(w.readTimer); w.readTimer = null; }
      w.shown = true;
      pushWorking(tabId);
    } else if (!w.shown && !w.readTimer) {
      // Read-only bursts announce only if they last: a fast snapshot stays silent.
      w.readTimer = setTimeout(() => { w.readTimer = null; w.shown = true; pushWorking(tabId); }, 300);
    }
    if (!w.detailTimer) {
      w.detailTimer = setTimeout(() => { w.detailTimer = null; w.detail = true; if (w.inflight > 0) pushWorking(tabId); }, 600);
    }
    return;
  }
  // phase "end"
  if (!w) return;
  w.inflight = Math.max(0, w.inflight - 1);
  if (w.inflight > 0) return;
  if (w.readTimer) { clearTimeout(w.readTimer); w.readTimer = null; }
  if (w.detailTimer) { clearTimeout(w.detailTimer); w.detailTimer = null; }
  w.until = Date.now() + 1500;
  if (w.shown) pushWorking(tabId);
  const stale = w;
  setTimeout(() => { if (workingTabs.get(tabId) === stale && stale.inflight === 0) { workingTabs.delete(tabId); iconFor(tabId); } }, 1600);
}

function bannerPauseGate(tabId, action) {
  if (!pausedTabs.has(tabId)) return false;
  if (BANNER_METADATA.has(action)) return false;
  return true; // everything else, dialog included: a paused agent does not touch the page
}

function sendControl(frame) {
  const id = `ctl-${Date.now().toString(36)}-${++controlSeq}`;
  const full = { ...frame, id };
  const entry = { frame: full, tries: 0, timer: null };
  pendingControl.set(id, entry);
  const attempt = () => {
    if (!pendingControl.has(id)) return;
    if (bridgeWs && bridgeWs.readyState === WebSocket.OPEN) {
      try { bridgeWs.send(JSON.stringify(full)); } catch (e) {}
    }
    entry.tries += 1;
    if (entry.tries < 4) entry.timer = setTimeout(attempt, 1500);
    else pendingControl.delete(id);
  };
  attempt();
}

function sendPauseSnapshot() {
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN) return;
  try {
    bridgeWs.send(JSON.stringify({ control: "snapshot", pausedTabs: [...pausedTabs] }));
  } catch (e) {}
}

async function setPaused(tabId, paused, sendToDaemon = true) {
  await pausedReady;
  if (paused) pausedTabs.add(tabId); else pausedTabs.delete(tabId);
  persistPaused();
  if (paused) {
    bannerTookOver = true;
    try { chrome.storage.local.set({ bannerTookOver: true }); } catch (e) {}
    const w = workingTabs.get(tabId);
    bannerSend(tabId, { state: "takenover", agent: w && w.agent });
  } else {
    bannerSend(tabId, { state: "hidden" });
  }
  iconFor(tabId);
  if (sendToDaemon) sendControl({ control: "takeover", tabId, paused });
}

function applyDaemonPauseState(tabId, paused) {
  if (paused) pausedTabs.add(tabId); else pausedTabs.delete(tabId);
  persistPaused();
  bannerSend(tabId, paused ? { state: "paused", agent: (workingTabs.get(tabId) || {}).agent } : { state: "hidden" });
  iconFor(tabId);
}

function handleBannerMessage(msg, sender, sendResponse) {
  const tabId = sender && sender.tab && typeof sender.tab.id === "number" ? sender.tab.id : (typeof msg.tabId === "number" ? msg.tabId : null);
  switch (msg.type) {
    case "TAKEOVER":
      if (tabId !== null) setPaused(tabId, true);
      sendResponse({ ok: true });
      return true;
    case "RESUME":
      if (tabId !== null) setPaused(tabId, false);
      sendResponse({ ok: true });
      return true;
    case "USER_INPUT": {
      // Genuine user hands on the page while the agent works, with no command in
      // flight (the agent's own CDP input lands during flight): that is a takeover.
      const w = tabId !== null ? workingTabs.get(tabId) : null;
      if (tabId !== null && w && w.inflight === 0 && !pausedTabs.has(tabId)) setPaused(tabId, true);
      sendResponse({ ok: true });
      return true;
    }
    case "BANNER_HIDE_SESSION":
      if (tabId !== null) { hiddenBannerTabs.add(tabId); persistPaused(); }
      sendResponse({ ok: true });
      return true;
    case "BANNER_QUERY":
      if (tabId !== null) {
        if (pausedTabs.has(tabId)) bannerSend(tabId, { state: "paused", agent: (workingTabs.get(tabId) || {}).agent });
        else if (workingTabs.has(tabId)) pushWorking(tabId);
      }
      sendResponse({ ok: true });
      return true;
    case "PAUSE_TAB":
    case "RESUME_TAB": {
      const target = tabId !== null ? tabId : undefined;
      if (target !== undefined) {
        setPaused(target, msg.type === "PAUSE_TAB");
        sendResponse({ ok: true });
        return true;
      }
      chrome.tabs.query({ active: true, lastFocusedWindow: true }).then((tabs) => {
        if (tabs && tabs[0]) setPaused(tabs[0].id, msg.type === "PAUSE_TAB");
        sendResponse({ ok: true });
      }).catch(() => sendResponse({ ok: false }));
      return "async";
    }
    case "FORCE_CLEAR": {
      const all = [...pausedTabs];
      pausedTabs.clear();
      persistPaused();
      for (const t of all) { bannerSend(t, { state: "hidden" }); iconFor(t); }
      sendPauseSnapshot();
      sendControl({ control: "desktop", paused: false });
      desktopPaused = false;
      sendResponse({ ok: true, cleared: all.length });
      return true;
    }
    case "DESKTOP_PAUSE":
      desktopPaused = Boolean(msg.paused);
      sendControl({ control: "desktop", paused: desktopPaused });
      sendResponse({ ok: true, desktopPaused });
      return true;
    case "BANNER_STATE_QUERY": {
      chrome.tabs.query({ active: true, lastFocusedWindow: true }).then((tabs) => {
        const t = tabs && tabs[0] ? tabs[0].id : null;
        const w = t !== null ? workingTabs.get(t) : null;
        sendResponse({ ok: true, tabId: t, paused: t !== null && pausedTabs.has(t), working: Boolean(w && w.inflight > 0), agent: w ? (w.agent || null) : null, pausedCount: pausedTabs.size, desktopPaused });
      }).catch(() => sendResponse({ ok: false }));
      return "async";
    }
    default:
      return false;
  }
}

chrome.commands.onCommand.addListener((command) => {
  if (command !== "takeover-toggle") return;
  chrome.tabs.query({ active: true, lastFocusedWindow: true }).then((tabs) => {
    if (tabs && tabs[0]) setPaused(tabs[0].id, !pausedTabs.has(tabs[0].id));
  }).catch(() => {});
});

chrome.tabs.onRemoved.addListener((tabId) => {
  workingTabs.delete(tabId);
  if (hiddenBannerTabs.delete(tabId)) persistPaused();
  // A paused entry for a closed tab is evicted daemon-side on tab_not_found;
  // the next snapshot resync keeps both sides converged.
});

setActivityHook(bannerActivity);
setPauseGate(bannerPauseGate);

connectWebSocket();
