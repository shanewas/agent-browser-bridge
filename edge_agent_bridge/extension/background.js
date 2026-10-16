// Edge Agent Bridge - service worker transport.
// One WebSocket to the local daemon; hello on open, pong on ping, commands go to dispatch().
import { dispatch } from "./actions.js";

const WS_URL = "ws://127.0.0.1:18999/ws";
const MAX_BACKOFF_MS = 5000;

let bridgeWs = null;
let pairingBlocked = false;
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

function connectWebSocket(force = false) {
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

    ws.onopen = async () => {
      wsBackoffMs = 1000;
      const hello = { version: chrome.runtime.getManifest().version, id: chrome.runtime.id };
      const token = await pairToken();
      if (token) hello.token = token;
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ hello }));
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
      if (msg.ping) {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ pong: true }));
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
      if (event && event.code === 4001) {
        // Pairing rejected: reconnecting only repeats the rejection every
        // second. Stay down until a token is saved or Reconnect is clicked
        // (both arrive as RECONNECT, which connects with force).
        pairingBlocked = true;
        try { chrome.storage.local.set({ pairingRejected: true }); } catch (e) {}
        log("Pairing rejected by the daemon; not reconnecting until a token is saved or Reconnect is clicked");
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
  if (msg && msg.type === "RECONNECT") {
    pairingBlocked = false;
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

connectWebSocket();
