// Agent Browser Bridge popup: shows daemon/extension state and the pairing field when the daemon asks for it.

const FIXES = {
  extension_offline: "Open Edge and check this popup shows Connected.",
  extension_outdated: "Update the extension from the Edge Add-ons store, or reload it unpacked.",
  tab_not_found: "The tab was closed. Switch to an open tab.",
  tab_closed: "The session tab closed. Run: agent-bridge switch <tabId>",
  dialog_open: "A page dialog is waiting. Answer it with: agent-bridge dialog accept|dismiss",
  timeout: "The page never settled. Check what is still loading, then retry.",
  tab_busy: "Another command holds this tab. Wait a moment and retry.",
  click_covered: "A sticky overlay covers the target. Dismiss it or scroll the target clear.",
  file_not_found: "The upload path does not exist. Pass an existing file.",
  file_access_required: "Turn on 'Allow access to file URLs' for Agent Browser Bridge in edge://extensions > Details.",
  unknown_session: "The session expired (daemon restarted). Run: agent-bridge session start",
  user_takeover: "You paused the agent on this tab. It resumes when you press Resume on the pill or in this popup.",
};

let lastStatus = null;

async function fetchStatus() {
  try {
    const res = await fetch("http://127.0.0.1:18999/status", { cache: "no-store" });
    if (res.ok) return await res.json();
  } catch (e) {}
  return null;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function show(id, visible) {
  const el = document.getElementById(id);
  if (el) el.style.display = visible ? "block" : "none";
}

async function updateUI() {
  try {
    chrome.runtime.sendMessage({ type: "KEEPALIVE" }).catch(() => {});
  } catch (e) {}

  const st = await fetchStatus();
  const online = Boolean(st && st.extension_connected);
  const badge = document.getElementById("statusBadge");
  if (badge) {
    badge.className = online ? "badge connected" : "badge disconnected";
    badge.textContent = online ? "Connected" : (st ? "Extension offline" : "Daemon offline");
  }

  const extVersion = chrome.runtime.getManifest().version;
  setText("versions", st ? `daemon ${st.daemon_version || "?"} · extension ${extVersion}` : "daemon not running");
  show("outdated", Boolean(st && st.extension_outdated));
  show("pairing", Boolean(st && st.pairing_required));
  if (chrome.extension && typeof chrome.extension.isAllowedFileSchemeAccess === "function") {
    try {
      chrome.extension.isAllowedFileSchemeAccess((allowed) => {
        show("fileAccessWarn", !allowed);
      });
    } catch (e) {
      show("fileAccessWarn", false);
    }
  }
  lastStatus = st;
  try {
    chrome.storage.local.get(["pairingRejected"], (data) => {
      if (data && data.pairingRejected) {
        show("pairing", true);
        setText("pairMsg", "Pairing rejected: paste the daemon token and save.");
      }
    });
  } catch (e) {}

  const pairInput = document.getElementById("pairToken");
  if (pairInput) {
    const dir = (st && st.data_dir) || "<data dir>";
    pairInput.placeholder = `paste the token from ${dir}${dir.endsWith("/") || dir.endsWith("\\") ? "" : "/"}token`;
  }

  chrome.storage.local.get(["lastLog"], (data) => {
    setText("logBox", data.lastLog || (online ? "Bridge online on 127.0.0.1:18999" : ""));
  });

  chrome.storage.local.get(["lastError"], (data) => {
    const errBox = document.getElementById("errSection");
    if (!errBox) return;
    const e = data.lastError;
    if (!e || !e.code) {
      errBox.style.display = "none";
      return;
    }
    errBox.style.display = "block";
    const fix = FIXES[e.code];
    setText("errBox", `[${e.at || "?"}] ${e.action || "?"} failed: ${e.code}\n${e.error || ""}${fix ? "\nFix: " + fix : ""}`);
  });

  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    setText("tabInfo", tab ? `${tab.title || "Tab"}\n(${tab.url || ""})` : "No active tab");
  } catch (e) {
    setText("tabInfo", "Tab info unavailable");
  }
}

document.getElementById("pairSave").addEventListener("click", async () => {
  const value = document.getElementById("pairToken").value.trim();
  await chrome.storage.local.set({ pairToken: value });
  try {
    chrome.runtime.sendMessage({ type: "RECONNECT" }).catch(() => {});
  } catch (e) {}
  setText("pairMsg", "Saved. Reconnecting…");
});

document.getElementById("reconnectBtn").addEventListener("click", async () => {
  try {
    chrome.runtime.sendMessage({ type: "RECONNECT" }).catch(() => {});
  } catch (e) {}
  setText("actionMsg", "Reconnecting…");
  setTimeout(() => setText("actionMsg", ""), 2000);
});

// Presence pill controls: pause/resume the agent on the active tab or the
// desktop, force-clear every pause, and the show-pill toggle.
const bannerEls = {
  status: document.getElementById("bannerStatus"),
  pauseBtn: document.getElementById("pauseBtn"),
  desktopBtn: document.getElementById("desktopPauseBtn"),
  forceRow: document.getElementById("forceClearRow"),
  forceBtn: document.getElementById("forceClearBtn"),
  toggle: document.getElementById("bannerToggle"),
};
let bannerState = { paused: false, working: false, agent: null, tabId: null, pausedCount: 0, desktopPaused: false };

async function refreshBanner() {
  try {
    const st = await chrome.runtime.sendMessage({ type: "BANNER_STATE_QUERY" });
    if (st && st.ok) bannerState = st;
  } catch (e) {}
  if (bannerEls.status) {
    if (bannerState.paused) bannerEls.status.textContent = "Paused — you are in control of this tab";
    else if (bannerState.working) bannerEls.status.textContent = `${bannerState.agent || "Agent"} is working on this tab`;
    else bannerEls.status.textContent = bannerState.pausedCount > 0
      ? `${bannerState.pausedCount} tab(s) paused`
      : (bannerState.desktopPaused ? "Desktop paused" : "Idle");
  }
  if (bannerEls.pauseBtn) bannerEls.pauseBtn.textContent = bannerState.paused ? "Resume this tab" : "Pause this tab";
  if (bannerEls.desktopBtn) bannerEls.desktopBtn.textContent = bannerState.desktopPaused ? "Resume desktop" : "Pause desktop";
  if (bannerEls.forceRow) bannerEls.forceRow.style.display = bannerState.pausedCount > 0 ? "" : "none";
}

if (bannerEls.pauseBtn) bannerEls.pauseBtn.addEventListener("click", async () => {
  const type = bannerState.paused ? "RESUME_TAB" : "PAUSE_TAB";
  try { await chrome.runtime.sendMessage({ type, tabId: bannerState.tabId }); } catch (e) {}
  refreshBanner();
});
if (bannerEls.desktopBtn) bannerEls.desktopBtn.addEventListener("click", async () => {
  try { await chrome.runtime.sendMessage({ type: "DESKTOP_PAUSE", paused: !bannerState.desktopPaused }); } catch (e) {}
  refreshBanner();
});
if (bannerEls.forceBtn) bannerEls.forceBtn.addEventListener("click", async () => {
  try { await chrome.runtime.sendMessage({ type: "FORCE_CLEAR" }); } catch (e) {}
  refreshBanner();
});
if (bannerEls.toggle) {
  chrome.storage.local.get("bannerEnabled").then((d) => {
    bannerEls.toggle.checked = !(d && d.bannerEnabled === false);
  }).catch(() => {});
  bannerEls.toggle.addEventListener("change", () => {
    chrome.storage.local.set({ bannerEnabled: bannerEls.toggle.checked }).catch(() => {});
  });
}
refreshBanner();

document.getElementById("diagBtn").addEventListener("click", async () => {
  const st = lastStatus || {};
  const payload = JSON.stringify({
    daemon_version: st.daemon_version || null,
    extension_version: chrome.runtime.getManifest().version,
    extension_id: chrome.runtime.id,
    websocket_active: Boolean(st.websocket_active),
    pairing_required: Boolean(st.pairing_required),
    data_dir: st.data_dir || null,
  }, null, 2);
  let copied = false;
  try {
    await navigator.clipboard.writeText(payload);
    copied = true;
  } catch (e) {
    try {
      const ta = document.createElement("textarea");
      ta.value = payload;
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand("copy");
      ta.remove();
    } catch (e2) {}
  }
  setText("actionMsg", copied ? "Diagnostics copied." : "Copy failed.");
  setTimeout(() => setText("actionMsg", ""), 2000);
});

updateUI();
setInterval(updateUI, 1000);
