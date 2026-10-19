// Edge Agent Bridge - command router. dispatch() resolves the tab and serializes per tab;
// execute() is the action switch and is re-entrant (batch calls it directly).
import { ensureDebugger, getDebuggerError, cdpSend, runOnTab, nativeMove, nativeClick, nativeDrag, nativeScroll, nativeKey, nativeType, captureScreenshot, setFileInputFiles } from "./cdp.js";
import { enableEvents, getConsole, inflightUrls, getOpenDialog, setDialogPolicy } from "./events.js";
import * as page from "./page.js";
import { frameOfRef, probeSubframeIds, snapshotTab as framesSnapshotTab } from "./frames.js";

// Attach the debugger and start event capture (console, network, dialogs) for the tab.
async function attach(tabId) {
  const ok = await ensureDebugger(tabId);
  if (ok) await enableEvents(tabId);
  return ok;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function log(msg) {
  console.log(`[Edge Bridge] ${msg}`);
  try { chrome.storage.local.set({ lastLog: `[${new Date().toLocaleTimeString()}] ${msg}` }); } catch (e) {}
}

function fail(code, error, extra = {}) {
  return { success: false, code, error, ...extra };
}

const UNSCRIPTABLE_PREFIXES = ["edge://", "chrome://", "chrome-extension://", "extension://", "view-source:"];

function isScriptable(url) {
  return Boolean(url) && !UNSCRIPTABLE_PREFIXES.some(p => url.startsWith(p));
}

async function checkFileAccess() {
  try {
    if (typeof chrome !== "undefined" && chrome?.extension?.isAllowedFileSchemeAccess) {
      return await new Promise((resolve) => {
        try {
          const res = chrome.extension.isAllowedFileSchemeAccess((val) => resolve(Boolean(val)));
          if (res && typeof res.then === "function") {
            res.then((val) => resolve(Boolean(val))).catch(() => resolve(false));
          }
        } catch {
          resolve(false);
        }
      });
    }
  } catch {
    return false;
  }
  return false;
}

export async function getTargetTab(tabId) {
  if (tabId !== undefined && tabId !== null && tabId !== "") {
    try {
      return await chrome.tabs.get(Number(tabId));
    } catch (e) {
      return null;
    }
  }
  // Never fall back to another window's tab: that fallback once navigated a live call away.
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab || null;
}

function tabInfo(tab) {
  return { id: tab.id, title: tab.title, url: tab.url };
}

// Resolve when the tab reports status "complete". Register before triggering a navigation
// (nav, back, reload) so the transition is not missed; for a freshly created tab pass
// checkNow so a page that already finished loading does not make us wait for the timeout.
function waitForTabLoad(tabId, timeoutMs = 15000, checkNow = false) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") finish(true);
    }
    chrome.tabs.onUpdated.addListener(listener);
    if (checkNow) {
      chrome.tabs.get(tabId).then(t => { if (t && t.status === "complete") finish(true); }).catch(() => finish(false));
    }
  });
}

function injectionTarget(tabId, frameId) {
  return (frameId === undefined || frameId === null) ? { tabId } : { tabId, frameIds: [Number(frameId)] };
}

// page-lib.js is idempotent; page functions answer code "lib_missing" until it is present.
async function injectLib(tabId, frameId) {
  await chrome.scripting.executeScript({ target: injectionTarget(tabId, frameId), files: ["page-lib.js"] });
}

export async function execInTab(tabId, func, args = [], world = "ISOLATED", frameId = undefined) {
  const target = injectionTarget(tabId, frameId);
  const cleanArgs = (args || []).map(a => (a === undefined ? null : a));
  const run = async () => {
    const results = await chrome.scripting.executeScript({ target, func, args: cleanArgs, world });
    if (results && results[0] && results[0].result !== undefined) return results[0].result;
    return fail("inject_failed", "Script injected but returned no result");
  };
  try {
    let result = await run();
    if (result && result.code === "lib_missing") {
      await injectLib(tabId, frameId);
      result = await run();
    }
    if (result === undefined) return fail("inject_failed", "Script injected but returned no result");
    return result;
  } catch (err) {
    return fail("inject_failed", err.message);
  }
}

const snapshotTab = (tabId, mode, withFrames, maxNodes) => framesSnapshotTab(tabId, mode, withFrames, maxNodes, execInTab);

async function resolveTargetWithWait(tabId, target, timeoutMs = 5000, opts = {}) {
  if (typeof target === "object" && target !== null && target.x !== undefined && target.y !== undefined) {
    return { found: true, x: Number(target.x), y: Number(target.y) };
  }
  const resolveOpts = { highlight: opts.highlight !== false, kind: opts.kind || "target", wantText: Boolean(opts.wantText) };
  const frameId = frameOfRef(target);
  if (frameId !== undefined) {
    const live = await probeSubframeIds(tabId);
    if (!live.includes(frameId)) {
      return { found: false, code: "stale_ref", error: `Frame ${frameId} for ref "${target}" is gone` };
    }
  }
  const start = Date.now();
  let last = null;
  while (Date.now() - start < timeoutMs) {
    last = await execInTab(tabId, page.pageResolve, [target, resolveOpts], "ISOLATED", frameId);
    if (last && last.found) {
      return last;
    }
    if (last && last.code === "bad_selector") return last;
    await new Promise(r => setTimeout(r, 100));
  }
  return { found: false, code: (last && last.code) || "target_not_found", error: (last && last.error) || `Target not found within ${timeoutMs}ms: "${target}"` };
}

function hasCoords(p) {
  return p.x !== undefined && p.y !== undefined && p.x !== null && p.y !== null;
}

// An explicit --selector is always CSS; ref, target and text go through name matching first.
function targetKind(p) {
  return p.selector && !p.ref && !p.target ? "selector" : "target";
}

// Resolve coordinates for an input action from x/y or a target expression.
async function locate(tabId, p, verb) {
  if (hasCoords(p)) return { ok: true, x: Number(p.x), y: Number(p.y), info: {} };
  const target = p.ref || p.target || p.selector || p.text;
  if (!target) return { ok: false, result: fail("bad_params", `${verb} requires ref, target, selector, text, or coordinates`) };
  const r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, { highlight: p.highlight, kind: targetKind(p) });
  if (!r || !r.found) return { ok: false, result: fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: ${target}`), target };
  if (frameOfRef(target) !== undefined && r.rendered !== false) {
    // pageResolve ran inside the subframe, blind to main-frame overlays. The top
    // element at main-frame coords must be an iframe; anything else eats the click.
    // Non-rendered targets (hidden inputs) skip this like the in-frame check.
    const cover = await execInTab(tabId, page.pageCoverCheck, [r.x, r.y]);
    if (cover && cover.covered) {
      return { ok: false, result: fail("click_covered", `Target "${target}" is covered by <${cover.covering}> in the main frame`), target };
    }
  }
  return { ok: true, x: r.x, y: r.y, info: r };
}

function showCursor(tabId, x, y, click, p) {
  if (p.highlight === false) return;
  execInTab(tabId, page.pageCursor, [x, y, Boolean(click)]).catch(() => {});
}

const FOCUS_BACKOFF_MS = [50, 100, 200];

function focusOpId() {
  return "eab" + Date.now().toString(36) + Math.floor(Math.random() * 0xffffffff).toString(36);
}

function normalizeReadValue(v) {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "";
  return String(v);
}

// Rich editors pad innerText with newlines and store typed spaces as U+00A0.
function writeMatches(readback, written, field) {
  if (field !== "contenteditable") return readback === written;
  const norm = (s) => s.replace(/\u00a0/g, " ").replace(/^\n+|\n+$/g, "");
  return norm(readback) === norm(written);
}

// Pre-assert focus on mode ({x,y} or {active:true}) in frameId. Returns {ok} or {ok:false, result}.
async function preAssertFocus(tabId, frameId, mode, uuid) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) await sleep(FOCUS_BACKOFF_MS[attempt - 1]);
    const r = await execInTab(tabId, page.pageAssertFocus, [mode, uuid, false], "ISOLATED", frameId);
    if (!r || r.success === false) {
      return { ok: false, result: fail("focus_unverifiable", `Focus assertion failed in frame ${frameId === undefined ? "main" : frameId}: ${(r && r.error) || "injection failed"}`) };
    }
    if (r.focusable === false) {
      return { ok: false, result: fail("focus_lost", "Target cannot take focus") };
    }
    if (r.focused) return { ok: true };
  }
  return { ok: false, result: fail("focus_lost", "Target did not take focus after 3 attempts") };
}

async function releaseAssert(tabId, frameId, uuid) {
  try {
    await execInTab(tabId, page.pageReleaseAssert, [uuid], "ISOLATED", frameId);
  } catch (e) {}
}

export async function execute(cmd) {
  const action = cmd.action;
  const p = cmd.params || {};
  const tabId = p.tabId;
  log(`Executing: ${action} (${JSON.stringify(p)})`);

  try {
    switch (action) {
      case "ping":
        return { success: true, message: "pong", version: chrome.runtime.getManifest().version, fileAccess: await checkFileAccess() };

      case "reload_extension":
        setTimeout(() => { chrome.runtime.reload(); }, 100);
        return { success: true, message: "Extension reloading..." };

      case "status": {
        return { success: true, version: chrome.runtime.getManifest().version, id: chrome.runtime.id };
      }

      case "get_active_tab":
      case "tab": {
        const tab = await getTargetTab(tabId);
        if (!tab) return fail("no_active_tab", "No active tab found");
        return { success: true, tab: { ...tabInfo(tab), status: tab.status } };
      }

      case "list_tabs":
      case "tabs": {
        const tabs = await chrome.tabs.query({});
        return {
          success: true,
          tabs: tabs.map(t => ({ id: t.id, title: t.title, url: t.url, active: t.active, windowId: t.windowId, groupId: t.groupId }))
        };
      }

      case "switch_tab":
      case "tab_switch": {
        if (p.tabId === undefined || p.tabId === null) return fail("tab_required", "tab_switch requires tabId");
        let tab;
        try {
          tab = await chrome.tabs.update(Number(p.tabId), { active: true });
        } catch (e) {
          return fail("tab_not_found", `Tab ${p.tabId} no longer exists`);
        }
        if (tab && tab.windowId) {
          try { await chrome.windows.update(tab.windowId, { focused: true }); } catch (e) {}
        }
        return { success: true, tabId: tab.id, tab: tabInfo(tab) };
      }

      case "close_tab":
      case "tab_close": {
        if (p.tabId === undefined || p.tabId === null || p.tabId === "") {
          return fail("tab_required", "tab_close requires an explicit tabId");
        }
        const tab = await getTargetTab(p.tabId);
        if (!tab) return fail("tab_not_found", `Tab ${p.tabId} no longer exists`);
        await chrome.tabs.remove(tab.id);
        return { success: true, closedTabId: tab.id, tab: tabInfo(tab) };
      }

      case "nav": {
        const url = p.url;
        if (!url) return fail("bad_params", "nav requires url");
        if (typeof url === "string" && url.startsWith("file://") && !await checkFileAccess()) {
          return fail("file_access_required", "Turn on 'Allow access to file URLs' for Edge Agent Bridge in edge://extensions > Details.");
        }
        let tab = await getTargetTab(tabId);
        if (tab) await attach(tab.id);
        if (!tab) {
          tab = await chrome.tabs.create({ url });
          const loaded = await waitForTabLoad(tab.id, 15000, true);
          const created = await chrome.tabs.get(tab.id);
          if (!loaded) return fail("nav_timeout", `Navigation to ${url} timed out`, { url: created.url, tab: tabInfo(created) });
          return { success: true, title: created.title, url: created.url, tab: tabInfo(created) };
        } else {
          const pending = waitForTabLoad(tab.id);
          await chrome.tabs.update(tab.id, { url });
          if (p.wait === "none") {
            const t0 = await chrome.tabs.get(tab.id);
            return { success: true, title: t0.title, url: t0.url, tab: tabInfo(t0) };
          }
          const ok = await pending;
          const t = await chrome.tabs.get(tab.id);
          if (!ok) return fail("nav_timeout", `Navigation to ${url} timed out`, { url: t.url, tab: tabInfo(t) });
          return { success: true, title: t.title, url: t.url, tab: tabInfo(t) };
        }
        const loaded = await waitForTabLoad(tab.id);
        const updatedTab = await chrome.tabs.get(tab.id);
        if (!loaded) return fail("nav_timeout", `Navigation to ${url} timed out`, { url: updatedTab.url, tab: tabInfo(updatedTab) });
        return { success: true, title: updatedTab.title, url: updatedTab.url, tab: tabInfo(updatedTab) };
      }

      case "tab_new": {
        const url = p.url || "about:blank";
        if (typeof url === "string" && url.startsWith("file://") && !await checkFileAccess()) {
          return fail("file_access_required", "Turn on 'Allow access to file URLs' for Edge Agent Bridge in edge://extensions > Details.");
        }
        const active = p.active !== false;
        let tab;
        if (p.window) {
          const win = await chrome.windows.create({ url, focused: active });
          tab = (win.tabs && win.tabs[0]) || (await chrome.tabs.query({ windowId: win.id }))[0];
        } else {
          tab = await chrome.tabs.create({ url, active });
        }
        let group = null;
        if (p.group) {
          const name = typeof p.group === "string" ? p.group : "Agent";
          try {
            if (!chrome.tabs.group || !chrome.tabGroups) throw new Error("tab groups API unavailable");
            const existing = await chrome.tabGroups.query({ title: name, windowId: tab.windowId });
            let groupId;
            if (existing.length) {
              groupId = await chrome.tabs.group({ tabIds: tab.id, groupId: existing[0].id });
            } else {
              groupId = await chrome.tabs.group({ tabIds: tab.id });
              await chrome.tabGroups.update(groupId, { title: name, color: "blue" });
            }
            group = { id: groupId, title: name };
          } catch (e) {
            group = { code: "group_unsupported", error: e.message };
          }
        }
        if (p.wait !== "none" && url !== "about:blank") await waitForTabLoad(tab.id, 15000, true);
        const t = await chrome.tabs.get(tab.id);
        return { success: true, tab: tabInfo(t), groupId: t.groupId, windowId: t.windowId, group };
      }

      case "group_list": {
        try {
          const query = (p.windowId !== undefined && p.windowId !== null) ? { windowId: Number(p.windowId) } : {};
          const groups = await chrome.tabGroups.query(query);
          return {
            success: true,
            count: groups.length,
            groups: groups.map(g => ({ id: g.id, title: g.title, color: g.color, collapsed: g.collapsed, windowId: g.windowId }))
          };
        } catch (e) {
          return fail("group_failed", e.message);
        }
      }

      case "group_move": {
        const rawIds = p.tabIds !== undefined ? p.tabIds : (p.tabId !== undefined ? [p.tabId] : []);
        const tabIds = (Array.isArray(rawIds) ? rawIds : [rawIds]).map(Number).filter(n => !Number.isNaN(n));
        if (!tabIds.length) return fail("bad_params", "group_move requires tabIds");
        try {
          let groupId;
          if (p.groupId !== undefined && p.groupId !== null && p.groupId !== "") {
            groupId = await chrome.tabs.group({ tabIds, groupId: Number(p.groupId) });
          } else {
            groupId = await chrome.tabs.group({ tabIds });
          }
          const props = {};
          if (p.title !== undefined) props.title = String(p.title);
          if (p.color !== undefined) props.color = String(p.color);
          if (Object.keys(props).length) await chrome.tabGroups.update(groupId, props);
          const g = await chrome.tabGroups.get(groupId);
          return { success: true, groupId, tabIds, group: { id: g.id, title: g.title, color: g.color, collapsed: g.collapsed, windowId: g.windowId } };
        } catch (e) {
          return fail("group_failed", e.message);
        }
      }

      case "group_ungroup": {
        const rawIds = p.tabIds !== undefined ? p.tabIds : (p.tabId !== undefined ? [p.tabId] : []);
        const tabIds = (Array.isArray(rawIds) ? rawIds : [rawIds]).map(Number).filter(n => !Number.isNaN(n));
        if (!tabIds.length) return fail("bad_params", "group_ungroup requires tabIds");
        try {
          await chrome.tabs.ungroup(tabIds);
          return { success: true, tabIds };
        } catch (e) {
          return fail("group_failed", e.message);
        }
      }

      case "back":
      case "forward":
      case "reload": {
        const pending = p.wait === "none" ? null : waitForTabLoad(tabId);
        try {
          if (action === "reload") {
            await chrome.tabs.reload(tabId);
          } else {
            // chrome.tabs.goBack refuses entries Chromium marked skippable (navigations it
            // started without a user gesture), so address the history entry directly.
            const hasDbg = await attach(tabId);
            if (hasDbg) {
              const hist = await cdpSend(tabId, "Page.getNavigationHistory", {});
              const idx = hist.currentIndex + (action === "back" ? -1 : 1);
              const entry = hist.entries[idx];
              if (!entry) return fail("no_history", `No ${action === "back" ? "previous" : "next"} page in history`);
              await cdpSend(tabId, "Page.navigateToHistoryEntry", { entryId: entry.id });
            } else if (action === "back") {
              await chrome.tabs.goBack(tabId);
            } else {
              await chrome.tabs.goForward(tabId);
            }
          }
        } catch (e) {
          return fail("no_history", e.message);
        }
        if (pending) await pending;
        const t = await chrome.tabs.get(tabId);
        return { success: true, url: t.url, title: t.title, tab: tabInfo(t) };
      }

      case "select": {
        const loc = await locate(tabId, p, "select");
        if (!loc.ok) return loc.result;
        const res = await execInTab(tabId, page.pageSelect, [p.ref || null, loc.x, loc.y, p.value, p.label], "ISOLATED", frameOfRef(p.ref));
        return res && res.success ? { ...res, ...loc.info, x: loc.x, y: loc.y } : res;
      }

      case "upload": {
        const files = Array.isArray(p.files) ? p.files.map(String) : (p.file ? [String(p.file)] : []);
        if (!files.length) return fail("bad_params", "upload requires files: [absolute paths]");
        const relative = files.filter(f => !/^([a-zA-Z]:[\\/]|\/|\\\\)/.test(f));
        if (relative.length) return fail("bad_params", `upload needs absolute paths, got: ${relative.join(", ")}`);
        if (!await checkFileAccess()) {
          return fail("file_access_required", "Turn on 'Allow access to file URLs' for Edge Agent Bridge in edge://extensions > Details.");
        }
        const loc = await locate(tabId, p, "upload");
        if (!loc.ok) return loc.result;
        const mark = await execInTab(tabId, page.pageMarkUpload, [p.ref || null, loc.x, loc.y]);
        if (!mark || mark.success === false) return mark || fail("no_file_input", "No file input found");
        try {
          const hasDbg = await attach(tabId);
          if (!hasDbg) return fail("debugger_unavailable", getDebuggerError(tabId) || "upload needs the debugger permission");
          await setFileInputFiles(tabId, '[data-eab-upload="1"]', files);
        } catch (e) {
          if (e.message && (e.message.includes("Debugger is not attached") || e.message.includes("not attached"))) {
            return fail("debugger_unavailable", e.message);
          }
          return fail("upload_failed", e.message);
        } finally {
          execInTab(tabId, page.pageUnmarkUpload, []).catch(() => {});
        }
        return { success: true, files, inputId: mark.id, ...loc.info };
      }

      case "history_search": {
        const text = p.text !== undefined ? String(p.text) : "";
        const maxResults = Math.max(1, Math.min(100, Number(p.maxResults ?? p.limit ?? 20) || 20));
        const query = { text, maxResults };
        if (p.startTime !== undefined && p.startTime !== null) query.startTime = Number(p.startTime);
        if (p.endTime !== undefined && p.endTime !== null) query.endTime = Number(p.endTime);
        try {
          const items = await chrome.history.search(query);
          return { success: true, count: items.length, items };
        } catch (e) {
          return fail("history_failed", e.message);
        }
      }

      case "history_delete": {
        const url = p.url;
        if (!url) return fail("bad_params", "history_delete requires url");
        try {
          await chrome.history.deleteUrl({ url: String(url) });
          return { success: true, url: String(url) };
        } catch (e) {
          return fail("history_failed", e.message);
        }
      }

      case "screenshot": {
        const tab = await chrome.tabs.get(tabId);
        const format = p.format === "png" ? "png" : "jpeg";
        let clip = p.clip || null;
        if (!clip && p.of) {
          const r = await resolveTargetWithWait(tabId, p.of, p.timeout || 5000, { highlight: false });
          if (!r || !r.found) return fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: ${p.of}`);
          clip = { x: r.x - r.width / 2, y: r.y - r.height / 2, width: r.width, height: r.height };
        }
        const hasDbg = await attach(tabId);
        if (hasDbg) {
          try {
            const dataUrl = await captureScreenshot(tabId, { format, quality: p.quality, clip });
            return { success: true, dataUrl, format, clip, tabId: tab.id, title: tab.title, url: tab.url };
          } catch (e) {
            log(`Page.captureScreenshot failed (${e.message}); activating tab for captureVisibleTab`);
          }
        }
        const [prev] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
        if (!tab.active) {
          await chrome.tabs.update(tab.id, { active: true });
          await new Promise(r => setTimeout(r, 150));
        }
        const opts = format === "jpeg" ? { format, quality: Math.max(1, Math.min(100, Number(p.quality) || 80)) } : { format };
        const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, opts);
        if (prev && prev.id !== tab.id) {
          try { await chrome.tabs.update(prev.id, { active: true }); } catch (e) {}
        }
        const out = { success: true, dataUrl, format, clip: null, tabId: tab.id, title: tab.title, url: tab.url, activated: !tab.active };
        if (!hasDbg) {
          out.native = false;
          out.debuggerError = getDebuggerError(tabId);
        }
        return out;
      }

      case "click":
      case "dblclick":
      case "rightclick": {
        const loc = await locate(tabId, p, action);
        if (!loc.ok) {
          if (action !== "rightclick" && (p.selector || p.text)) {
            const fn = action === "click" ? page.pageClickElement : page.pageDblclickElement;
            return await execInTab(tabId, fn, [p.selector, p.text]);
          }
          return loc.result;
        }
        const { x, y, info } = loc;
        const hasDbg = await attach(tabId);
        if (hasDbg) {
          const button = action === "rightclick" ? "right" : (p.button || "left");
          await nativeClick(tabId, x, y, button, 1);
          if (action === "dblclick") {
            await new Promise(res => setTimeout(res, 50));
            await nativeClick(tabId, x, y, "left", 2);
          }
          showCursor(tabId, x, y, true, p);
          return { success: true, x, y, native: true, ...info };
        }
        const fallback = action === "click" ? page.pageClickAt : (action === "dblclick" ? page.pageDblclickAt : page.pageRightClickAt);
        const res = await execInTab(tabId, fallback, [x, y]);
        return res && res.success ? { ...res, native: false, debuggerError: getDebuggerError(tabId), ...info } : res;
      }

      case "move":
      case "hover": {
        const loc = await locate(tabId, p, "hover");
        if (!loc.ok) return loc.result;
        const { x, y, info } = loc;
        const hasDbg = await attach(tabId);
        if (hasDbg) {
          await nativeMove(tabId, x, y);
          showCursor(tabId, x, y, false, p);
          if (p.duration) {
            await new Promise(res => setTimeout(res, Number(p.duration)));
          }
          return { success: true, x, y, native: true, ...info };
        }
        const res = await execInTab(tabId, page.pageMoveAt, [x, y]);
        return res && res.success ? { ...res, native: false, debuggerError: getDebuggerError(tabId), ...info } : res;
      }

      case "drag": {
        let fromX = p.fromX, fromY = p.fromY;
        if (!hasCoords({ x: fromX, y: fromY })) {
          const rFrom = await resolveTargetWithWait(tabId, p.from || p.source, p.timeout || 5000, { highlight: p.highlight });
          if (!rFrom || !rFrom.found) return fail("target_not_found", `Drag source not found: ${p.from || p.source}`);
          fromX = rFrom.x;
          fromY = rFrom.y;
        }
        let toX = p.toX, toY = p.toY;
        if (!hasCoords({ x: toX, y: toY })) {
          const rTo = await resolveTargetWithWait(tabId, p.to || p.target, p.timeout || 5000, { highlight: p.highlight });
          if (!rTo || !rTo.found) return fail("target_not_found", `Drag target not found: ${p.to || p.target}`);
          toX = rTo.x;
          toY = rTo.y;
        }
        const hasDbg = await attach(tabId);
        if (!hasDbg) return fail("debugger_unavailable", getDebuggerError(tabId) || "Native drag requires debugger permission");
        const onStep = p.highlight === false ? null : (cx, cy) => execInTab(tabId, page.pageCursor, [cx, cy, false]).catch(() => {});
        await nativeDrag(tabId, Number(fromX), Number(fromY), Number(toX), Number(toY), Number(p.steps || 12), onStep);
        return { success: true, fromX: Number(fromX), fromY: Number(fromY), toX: Number(toX), toY: Number(toY), native: true };
      }

      case "interactive":
      case "elements": {
        const snap = await snapshotTab(tabId, "interactive", p.frames !== false, 0);
        if (!snap.success) return snap;
        let els = snap.nodes;
        if (p.filter) {
          const f = String(p.filter).toLowerCase();
          els = els.filter(e => (e.name || "").toLowerCase().includes(f) || (e.id || "").toLowerCase().includes(f));
        }
        return { success: true, count: els.length, elements: els.map(e => ({ ...e, tag: e.role, text: e.name })) };
      }

      case "wait_for":
      case "wait": {
        const timeout = Number(p.timeout || 5000);
        const start = Date.now();
        const waited = () => Date.now() - start;
        if (p.idle) {
          await attach(tabId);
          const idleMs = Number(p.idleMs || 500);
          let quietSince = null;
          while (waited() < timeout) {
            if (inflightUrls(tabId).length === 0) {
              if (quietSince === null) quietSince = Date.now();
              if (Date.now() - quietSince >= idleMs) return { success: true, idle: true, waited: waited() };
            } else {
              quietSince = null;
            }
            await sleep(50);
          }
          return fail("timeout", `network not idle within ${timeout}ms`, { waited: waited(), inflight: inflightUrls(tabId) });
        }
        if (p.url) {
          const pattern = String(p.url);
          const rx = /^\/.*\/[a-z]*$/.test(pattern) ? new RegExp(pattern.slice(1, pattern.lastIndexOf("/")), pattern.slice(pattern.lastIndexOf("/") + 1)) : null;
          while (waited() < timeout) {
            const t = await chrome.tabs.get(tabId);
            const url = t.url || "";
            if (rx ? rx.test(url) : url.includes(pattern)) return { success: true, url, waited: waited() };
            await sleep(100);
          }
          const t = await chrome.tabs.get(tabId);
          return fail("timeout", `url did not match ${pattern} within ${timeout}ms`, { waited: waited(), url: t.url });
        }
        if (p.load) {
          while (waited() < timeout) {
            const t = await chrome.tabs.get(tabId);
            if (t.status === "complete") {
              const rs = await execInTab(tabId, () => document.readyState, []);
              if (rs === "complete") return { success: true, waited: waited() };
            }
            await sleep(100);
          }
          return fail("timeout", `page did not finish loading within ${timeout}ms`, { waited: waited() });
        }
        const r = await resolveTargetWithWait(tabId, p.ref || p.target || p.selector || p.text, timeout, { highlight: false, kind: targetKind(p) });
        if (r && r.found) return { success: true, waited: waited(), ...r };
        return fail(r ? r.code : "target_not_found", r ? r.error : "Target not found", { waited: waited() });
      }

      case "console": {
        await attach(tabId);
        return { success: true, entries: getConsole(tabId, p.clear === true) };
      }

      case "dialog": {
        await attach(tabId);
        const res = await setDialogPolicy(tabId, p.policy || "accept", p.promptText);
        return { success: true, ...res };
      }

      case "type": {
        const text = p.text !== undefined ? String(p.text) : "";
        const chars = Array.from(text);
        const deadlineMs = Number(p.deadlineMs) || 0;
        const target = p.ref || p.target || p.selector;
        const pastDeadline = () => deadlineMs && Date.now() > deadlineMs;
        if (pastDeadline()) {
          return fail("deadline_exceeded", "Write deadline passed before dispatch", { progress: { typedSoFar: 0, remaining: text } });
        }
        let mode;
        let pt;
        let frameId;
        if (target || hasCoords(p)) {
          const loc = await locate(tabId, p, "type");
          if (!loc.ok) return loc.result;
          pt = (hasCoords(p) && !target) ? { x: Number(p.x), y: Number(p.y) } : { x: loc.x, y: loc.y };
          mode = loc.info.local || pt;
          frameId = frameOfRef(p.ref || p.target || p.selector);
        } else {
          mode = { active: true };
          frameId = undefined;
        }
        const uuid = focusOpId();
        const hasDbg = await attach(tabId);
        try {
          if (hasDbg && (target || hasCoords(p))) {
            await nativeClick(tabId, pt.x, pt.y, "left", 1);
            showCursor(tabId, pt.x, pt.y, true, p);
          }
          const before = await execInTab(tabId, page.pageReadback, [mode], "ISOLATED", frameId);
          const valueBefore = (before && before.success && before.verifiable) ? normalizeReadValue(before.value) : null;
          const asserted = await preAssertFocus(tabId, frameId, mode, uuid);
          if (!asserted.ok) return asserted.result;
          const onProgress = async (n) => {
            const chk = await execInTab(tabId, page.pageAssertFocus, [mode, uuid, true], "ISOLATED", frameId);
            if (!chk || chk.success === false || chk.focused === false) {
              const err = new Error("focus stolen mid-type");
              err.code = "focus_stolen";
              err.typedSoFar = n;
              throw err;
            }
          };
          try {
            if (hasDbg) {
              await nativeType(tabId, text, p.delay === undefined ? 20 : Number(p.delay), { onProgress, deadlineMs });
            } else {
              await execInTab(tabId, page.pageTypeText, [p.selector || null, text, false], "ISOLATED", frameId);
            }
          } catch (e) {
            if (e && e.code === "focus_stolen") {
              return fail("focus_stolen", `Focus moved after ${e.typedSoFar} chars; resume with "remaining"`, {
                typedSoFar: e.typedSoFar, remaining: chars.slice(e.typedSoFar).join(""),
              });
            }
            if (e && e.code === "deadline_exceeded") {
              return fail("deadline_exceeded", "Write deadline passed mid-type", {
                progress: { typedSoFar: e.typedSoFar || 0, remaining: chars.slice(e.typedSoFar || 0).join("") },
              });
            }
            throw e;
          }
          if (pastDeadline()) {
            return fail("deadline_exceeded", "Write deadline passed before readback", {
              progress: { typedSoFar: chars.length, remaining: "" },
            });
          }
          await sleep(100);
          const rb = await execInTab(tabId, page.pageReadback, [mode], "ISOLATED", frameId);
          if (!rb || rb.success === false || rb.verifiable === false || rb.present === false) {
            const out = { success: true, text, native: hasDbg, chars: chars.length, written: valueBefore === null ? text : valueBefore + text, readback: null, match: "unknown" };
            if (!hasDbg) out.debuggerError = getDebuggerError(tabId);
            return out;
          }
          const written = valueBefore === null ? text : valueBefore + text;
          const readback = normalizeReadValue(rb.value);
          const out = { success: true, text, native: hasDbg, chars: chars.length, written, readback, match: writeMatches(readback, written, rb.field), field: rb.field };
          if (!hasDbg) out.debuggerError = getDebuggerError(tabId);
          return out;
        } finally {
          await releaseAssert(tabId, frameId, uuid);
        }
      }

      case "fill": {
        const target = p.ref || p.target || p.selector;
        const text = p.text !== undefined ? String(p.text) : "";
        const shouldClear = p.clear !== false;
        const deadlineMs = Number(p.deadlineMs) || 0;
        if (deadlineMs && Date.now() > deadlineMs) {
          return fail("deadline_exceeded", "Write deadline passed before dispatch", { progress: { written: text } });
        }
        let mode = null;
        let pt = null;
        let frameId = frameOfRef(p.ref || p.target || p.selector);
        let targetInfo = {};
        if (hasCoords(p) && !target) {
          mode = pt = { x: Number(p.x), y: Number(p.y) };
        } else if (target) {
          const r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, { highlight: p.highlight, kind: targetKind(p) });
          if (!r || !r.found) return fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: ${target}`);
          pt = { x: r.x, y: r.y };
          mode = r.local || pt;
          targetInfo = r;
        } else {
          mode = { active: true };
          frameId = undefined;
        }
        const uuid = focusOpId();
        const hasDbg = await attach(tabId);
        const hasPoint = mode.x !== undefined;
        try {
          const before = await execInTab(tabId, page.pageReadback, [mode], "ISOLATED", frameId);
          const valueBefore = (before && before.success && before.verifiable) ? normalizeReadValue(before.value) : "";
          const asserted = await preAssertFocus(tabId, frameId, mode, uuid);
          if (!asserted.ok) return asserted.result;
          let native = false;
          let fillDbgError = null;
          if (hasDbg && hasPoint) {
            try {
              await nativeClick(tabId, pt.x, pt.y, "left", 1);
              showCursor(tabId, pt.x, pt.y, true, p);
              const prep = await execInTab(tabId, page.pageFillPrepare, [mode.x, mode.y, shouldClear], "ISOLATED", frameId);
              if (prep && prep.success === false) return prep;
              await cdpSend(tabId, "Input.insertText", { text });
              await execInTab(tabId, page.pageFillCommit, [mode.x, mode.y, text], "ISOLATED", frameId);
              native = true;
            } catch (e) {
              log(`Native fill failed (${e.message}), falling back to synthetic DOM`);
              fillDbgError = e.message;
            }
          }
          if (!native) {
            const syn = await execInTab(tabId, page.pageTypeText, [p.selector || null, text, shouldClear], "ISOLATED", frameId);
            if (syn && syn.success === false) return syn;
          }
          if (deadlineMs && Date.now() > deadlineMs) {
            return fail("deadline_exceeded", "Write deadline passed before readback", { progress: { written: shouldClear ? text : valueBefore + text } });
          }
          await sleep(100);
          const rb = await execInTab(tabId, page.pageReadback, [mode], "ISOLATED", frameId);
          const written = shouldClear ? text : valueBefore + text;
          if (!rb || rb.success === false || rb.verifiable === false || rb.present === false) {
            const out = { success: true, text, native, written, readback: null, match: "unknown", ...targetInfo };
            if (!native) out.debuggerError = getDebuggerError(tabId) || fillDbgError;
            return out;
          }
          const readback = normalizeReadValue(rb.value);
          const out = { success: true, text, native, written, readback, match: writeMatches(readback, written, rb.field), field: rb.field, ...targetInfo };
          if (!native) out.debuggerError = getDebuggerError(tabId) || fillDbgError;
          return out;
        } finally {
          await releaseAssert(tabId, frameId, uuid);
        }
      }

      case "key": {
        if (!p.key) return fail("bad_params", "key requires key");
        const hasDbg = await attach(tabId);
        if (hasDbg) {
          await nativeKey(tabId, p.key);
          return { success: true, key: p.key, native: true };
        }
        const res = await execInTab(tabId, page.pagePressKey, [p.key]);
        return res && res.success ? { ...res, native: false, debuggerError: getDebuggerError(tabId) } : res;
      }

      case "scroll": {
        const hasX = p.x !== undefined && p.x !== null;
        const hasY = p.y !== undefined && p.y !== null;
        const hasDeltas = hasX || hasY;
        // Defaults apply only when both deltas are absent, so an explicit 0 stays 0.
        const dx = hasX ? Number(p.x) : 0;
        const dy = hasY ? Number(p.y) : (hasDeltas ? 0 : 300);
        const target = p.ref || p.target || p.selector;
        if (target) {
          const r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, { highlight: p.highlight });
          if (!r || !r.found) return fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: ${target}`);
          if (!hasDeltas) {
            // pageResolve already scrolled the element into view.
            return { success: true, scrolledIntoView: true, x: r.x, y: r.y };
          }
          const hasDbgT = await attach(tabId);
          if (hasDbgT) {
            await nativeScroll(tabId, r.x, r.y, dx, dy);
            return { success: true, scrollX: dx, scrollY: dy, native: true };
          }
          return await execInTab(tabId, page.pageScroll, [dx, dy]);
        }
        const hasDbg = await attach(tabId);
        if (hasDbg) {
          await nativeScroll(tabId, 500, 300, dx, dy);
          return { success: true, scrollX: dx, scrollY: dy, native: true };
        }
        const res = await execInTab(tabId, page.pageScroll, [dx, dy]);
        return res && res.success ? { ...res, native: false, debuggerError: getDebuggerError(tabId) } : res;
      }

      case "snapshot": {
        await attach(tabId);
        const snap = await snapshotTab(tabId, p.mode || "interactive", p.frames !== false, p.maxNodes);
        if (!snap.success) return snap;
        return { success: true, text: snap.text, refs: snap.refs, url: snap.url, title: snap.title,
                 truncated: (snap.truncated || 0) > 0, total: snap.total || 0 };
      }

      case "batch": {
        const tab = await getTargetTab(p.tabId);
        if (!tab) return fail("no_active_tab", "No active tab");
        const steps = p.steps || [];
        return await runOnTab(tab.id, async () => {
          const results = [];
          for (const step of steps) {
            if (step.action === "sleep" || step.action === "wait_ms") {
              await new Promise(r => setTimeout(r, Number(step.ms || 100)));
              results.push({ action: "sleep", success: true, ms: step.ms });
              continue;
            }
            const stepCmd = { action: step.action, params: { ...step, tabId: step.tabId || tab.id } };
            const r = await execute(stepCmd);
            results.push(r);
            if (r && r.success === false && step.stopOnError !== false) {
              return fail(r.code || "step_failed", r.error, { stoppedAt: step.action, results, tab: tabInfo(tab) });
            }
          }
          return { success: true, count: results.length, results, tab: tabInfo(tab) };
        });
      }

      case "eval": {
        if (p.code === undefined) return fail("bad_params", "eval requires code");
        const hasDbg = await ensureDebugger(tabId);
        let cdpError = null;
        if (hasDbg) {
          try {
            const res = await cdpSend(tabId, "Runtime.evaluate", {
              expression: p.code,
              returnByValue: true,
              awaitPromise: true,
            });
            if (res.exceptionDetails) {
              const desc = res.exceptionDetails.exception
                ? res.exceptionDetails.exception.description || res.exceptionDetails.text
                : res.exceptionDetails.text;
              return fail("eval_error", desc);
            }
            return { success: true, result: res.result ? res.result.value : undefined };
          } catch (cdpErr) {
            cdpError = cdpErr.message;
            // CDP not available for this tab — fall through to execInTab
          }
        } else {
          cdpError = getDebuggerError(tabId);
        }
        const r = await execInTab(tabId, page.pageEval, [p.code], "MAIN");
        if (r && r.success === false && cdpError) {
          r.debuggerError = cdpError;
        }
        return r;
      }

      case "check_radio":
      case "check":
      case "select_radio": {
        const target = p.ref || p.target || p.selector || p.text;
        const isExplicitSelector = Boolean(p.selector && !p.ref && !p.target);
        return await execInTab(tabId, page.pageCheckRadio, [target, p.value, isExplicitSelector], "ISOLATED", frameOfRef(p.ref));
      }

      case "get_text":
      case "text": {
        const target = p.ref || p.target || p.selector;
        const r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, { highlight: p.highlight, kind: targetKind(p), wantText: true });
        if (!r || !r.found) return fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: "${target}"`);
        return { ...r, success: true, text: r.ownText !== null && r.ownText !== undefined ? r.ownText : "" };
      }

      default:
        return fail("unknown_action", `Unknown action: ${action}`);
    }
  } catch (err) {
    log(`Error in ${action}: ${err.message}`);
    const msg = err.message || "";
    if (msg.includes("No tab with given id")) {
      return fail("tab_not_found", msg);
    }
    if (err.code === "debugger_unavailable" || msg.includes("Debugger is not attached") || msg.includes("not attached")) {
      return fail("debugger_unavailable", msg);
    }
    return fail("internal_error", msg);
  }
}

// Read-only or dialog-handling actions skip the per-tab queue so they work while a command is blocked on a dialog.
const NOQUEUE = new Set(["console", "dialog", "tab", "get_active_tab"]);
const TABLESS = new Set(["ping", "status", "tabs", "list_tabs", "reload_extension", "batch", "tab_switch", "switch_tab", "tab_close", "close_tab", "tab_new", "history_search", "history_delete", "group_list", "group_move", "group_ungroup"]);
// These never inject into the page (wait polls until a page it can read arrives), so any URL is fine.
const TAB_LEVEL = new Set(["nav", "back", "forward", "reload", "tab", "get_active_tab", "wait", "wait_for", "dialog", "console"]);

export async function dispatch(cmd) {
  const p = cmd.params || {};
  if (TABLESS.has(cmd.action)) return execute(cmd);
  const tab = await getTargetTab(p.tabId);
  if (!tab) {
    return (p.tabId !== undefined && p.tabId !== null && p.tabId !== "")
      ? fail("tab_not_found", `Tab ${p.tabId} no longer exists`)
      : fail("no_active_tab", "No active tab");
  }
  const url = tab.pendingUrl || tab.url;
  if (!TAB_LEVEL.has(cmd.action) && !isScriptable(url)) {
    return fail("tab_not_scriptable", `Tab ${tab.id} shows ${url || "no page yet"}, which extensions can't script; switch to the tab you mean or navigate this one`, { tab: tabInfo(tab) });
  }
  cmd.params = { ...p, tabId: tab.id };
  const open = getOpenDialog(tab.id);
  if (open && !NOQUEUE.has(cmd.action)) {
    return fail("dialog_open", `A ${open.type} dialog is open: "${open.message}". Call dialog with policy accept or dismiss.`, { dialog: open, tab: tabInfo(tab) });
  }
  const result = NOQUEUE.has(cmd.action) ? await execute(cmd) : await runOnTab(tab.id, () => execute(cmd));
  if (result && typeof result === "object" && !result.tab) {
    let t = tab;
    try { t = await chrome.tabs.get(tab.id); } catch (e) {}
    result.tab = tabInfo(t);
  }
  return result;
}
