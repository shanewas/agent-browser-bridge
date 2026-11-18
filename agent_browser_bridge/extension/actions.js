// Agent Browser Bridge - command router. dispatch() resolves the tab and serializes per tab;
// execute() is the action switch and is re-entrant (batch calls it directly).
import { ensureDebugger, getDebuggerError, cdpSend, runOnTab, nativeMove, nativeClick, nativeDrag, nativeScroll, nativeKey, nativeType, captureScreenshot, setFileInputFiles, isDetachError } from "./cdp.js";
import { enableEvents, getConsole, inflightUrls, getOpenDialog, setDialogPolicy } from "./events.js";
import * as page from "./page.js";
import { frameOfRef, probeSubframeIds, snapshotTab as framesSnapshotTab } from "./frames.js";
import { validateLabel, computeTtlMs, isSensitiveOrigin } from "./spotlight_lib.js";

// Attach the debugger and start event capture (console, network, dialogs) for the tab.
async function attach(tabId) {
  const ok = await ensureDebugger(tabId);
  if (ok) await enableEvents(tabId);
  return ok;
}

const NATIVE_VERBS = new Set(["click", "dblclick", "rightclick", "hover", "type"]);

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
  if (p.selector && !p.ref && !p.target) return "selector";
  return p.exact ? "exact" : "target";
}

// Resolve coordinates for an input action from x/y or a target expression.
async function locate(tabId, p, verb) {
  if (hasCoords(p)) {
    await bannerFadeForPoint(tabId, Number(p.x), Number(p.y));
    return { ok: true, x: Number(p.x), y: Number(p.y), info: {} };
  }
  const target = p.ref || p.target || p.selector || p.text;
  if (!target) return { ok: false, result: fail("bad_params", `${verb} requires ref, target, selector, text, or coordinates`) };
  const opts = { highlight: p.highlight, kind: targetKind(p) };
  let r;
  if (NATIVE_VERBS.has(verb)) {
    // Attach before measuring: the attach itself moves the page (see settleViewport). The
    // viewport can still shift during a load, so only trust a point measured while it held
    // still. A point near the bottom edge is pushed out by even a late shift, so for those
    // wait until the viewport is quiet and confirm it didn't move.
    await attach(tabId);
    const height = () => execInTab(tabId, () => window.innerHeight, []).catch(() => null);
    for (let i = 0; i < 3; i++) {
      const before = await height();
      r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, opts);
      if (!r || !r.found) break;
      const after = await height();
      if (before !== after) continue;
      if (typeof after !== "number" || r.y < after - 64) break;
      await execInTab(tabId, page.pageAwaitStill, [150, 600]).catch(() => {});
      if (await height() === after) break;
    }
  } else {
    r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, opts);
  }
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
  await bannerFadeForPoint(tabId, r.x, r.y);
  return { ok: true, x: r.x, y: r.y, info: r };
}

function showCursor(tabId, x, y, click, p) {
  if (p.highlight === false) return;
  execInTab(tabId, page.pageCursor, [x, y, Boolean(click)]).catch(() => {});
}

// Presence pill helpers: the pill lives in the content script's isolated world,
// which execInTab shares, so these calls are synchronous with the caller's queue.
async function bannerHideForCapture(tabId) {
  await execInTab(tabId, () => { if (window.__eabBanner) window.__eabBanner.hideForCapture(); }).catch(() => {});
}
async function bannerRestoreAfterCapture(tabId) {
  await execInTab(tabId, () => { if (window.__eabBanner) window.__eabBanner.restoreAfterCapture(); }).catch(() => {});
}

// Capture hiding as a guaranteed, counted pair: every hide increments, every
// restore decrements, and the overlays reappear only when the depth reaches
// zero. Callers wrap the whole capture in try/finally, so a failure between
// hide and restore (a failed clip resolution, a thrown capture) can no longer
// strand the banner or the Spotlight overlay hidden.
const captureHideDepth = new Map();
export async function captureHide(tabId) {
  const depth = (captureHideDepth.get(tabId) || 0) + 1;
  captureHideDepth.set(tabId, depth);
  if (depth > 1) return;
  await bannerHideForCapture(tabId);
  await execInTab(tabId, () => { if (window.__eabSpotlight) window.__eabSpotlight.hideForCapture(); }).catch(() => {});
}
export async function captureRestore(tabId) {
  const depth = (captureHideDepth.get(tabId) || 0) - 1;
  if (depth > 0) { captureHideDepth.set(tabId, depth); return; }
  captureHideDepth.delete(tabId);
  await bannerRestoreAfterCapture(tabId);
  await execInTab(tabId, () => { if (window.__eabSpotlight) window.__eabSpotlight.restoreAfterCapture(); }).catch(() => {});
}
async function bannerFadeForPoint(tabId, x, y) {
  await execInTab(tabId, (px, py) => { if (window.__eabBanner) window.__eabBanner.fadeForPoint(px, py); }, [x, y]).catch(() => {});
}

// Image and viewport dimensions for a capture, so callers can convert between
// screenshot pixels and CSS pixels (Spotlight point targets depend on it).
// The two capture paths produce different scales; this reports the real one.
async function screenshotMeta(tabId, dataUrl, clip) {
  try {
    const vp = await execInTab(tabId, () => ({ w: window.innerWidth, h: window.innerHeight }));
    const blob = await (await fetch(dataUrl)).blob();
    const bmp = await createImageBitmap(blob);
    // viewport_* always describe the CSS viewport; a clip's own size stays in
    // the clip field and only feeds the scale computation.
    const cssW = clip ? clip.width : (vp && vp.w);
    return {
      image_width: bmp.width,
      image_height: bmp.height,
      viewport_width: (vp && vp.w) || null,
      viewport_height: (vp && vp.h) || null,
      scale_factor: cssW ? Math.round((bmp.width / cssW) * 1000) / 1000 : null,
    };
  } catch (e) {
    return {};
  }
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

// Presence pill hooks, registered by background.js (the worker owns pause state).
export const USER_TAKEOVER_ERROR = "The user took control of this tab. Do not retry. Tell the user and wait for them to resume.";
let pauseGate = null;
let activityHook = null;
export function setPauseGate(fn) { pauseGate = fn; }
export function setActivityHook(fn) { activityHook = fn; }
export function isPausedFor(tabId, action) {
  return Boolean(pauseGate && typeof tabId === "number" && pauseGate(tabId, action));
}

// ---------------------------------------------------------------------------
// Spotlight: the agent points at one thing in the page. The worker owns state
// (one highlight per tab), label/TTL/safety rules and the audit log; the
// content script (spotlight.js) owns rendering and the deadline. See
// DESIGN v3 notes in the repo history for the full contract.
// ---------------------------------------------------------------------------

const spotlightTabs = new Map(); // tabId -> {showing, generation, mode, label, clearedReason, clearedAt, lastDraw, labelAt, logId, rect, offscreen}
let spotlightGeneration = 0;
let spotlightSession = 1;
// Worker state must survive MV3 worker restarts: the epoch and generation
// reset would make live content scripts reject every later draw as stale,
// and per-tab state kept only in memory would leave status, clear and
// takeover blind to a highlight still on screen. chrome.storage.session
// outlives the worker but not the browser session, which is the right life.
const SPOTLIGHT_STATE_KEY = "spotlightStateV1";
function defaultSpotlightState() {
  return { showing: false, generation: 0, mode: null, label: null, clearedReason: null, clearedAt: null, lastDraw: null, labelAt: 0, logId: null, rect: null, offscreen: false };
}
function persistSpotlightState() {
  try {
    const tabs = {};
    for (const [tabId, st] of spotlightTabs) tabs[tabId] = st;
    const p = chrome.storage.session.set({
      [SPOTLIGHT_STATE_KEY]: { session: spotlightSession, generation: spotlightGeneration, tabs },
    });
    if (p && p.catch) p.catch(() => {});
  } catch (e) {}
}
try {
  chrome.storage.session.get([SPOTLIGHT_STATE_KEY, "spotlightSession"]).then((d) => {
    const saved = d && d[SPOTLIGHT_STATE_KEY];
    const legacy = d && typeof d.spotlightSession === "number" ? d.spotlightSession : 0;
    const savedSession = saved && typeof saved.session === "number" ? saved.session : 0;
    if (Math.max(legacy, savedSession) > spotlightSession) {
      spotlightSession = Math.max(legacy, savedSession);
    }
    if (saved && typeof saved.generation === "number" && saved.generation > spotlightGeneration) {
      spotlightGeneration = saved.generation;
    }
    if (saved && saved.tabs && typeof saved.tabs === "object") {
      for (const [tabId, st] of Object.entries(saved.tabs)) {
        const id = Number(tabId);
        if (st && typeof st === "object" && !spotlightTabs.has(id)) {
          spotlightTabs.set(id, { ...defaultSpotlightState(), ...st });
        }
      }
    }
  }).catch(() => {});
} catch (e) {}
const SPOTLIGHT_LOG_KEY = "spotlightLog";
const SPOTLIGHT_LOG_MAX_ENTRIES = 200;
const SPOTLIGHT_LOG_BUDGET_BYTES = 4 * 1024 * 1024;
const SPOTLIGHT_CAPTURE_MAX_BYTES = 40 * 1024;
const LABEL_REPLACE_WINDOW_MS = 2000;

async function spotlightSettings() {
  try {
    const d = await chrome.storage.local.get(["spotlightEnabled", "spotlightNeverDim", "spotlightDurationMultiplier", "spotlightAllowlist"]);
    return {
      enabled: d.spotlightEnabled !== false,
      neverDim: d.spotlightNeverDim === true,
      multiplier: typeof d.spotlightDurationMultiplier === "number" && d.spotlightDurationMultiplier > 0 ? d.spotlightDurationMultiplier : 1,
      allowlist: Array.isArray(d.spotlightAllowlist) ? d.spotlightAllowlist : [],
    };
  } catch (e) {
    return { enabled: true, neverDim: false, multiplier: 1, allowlist: [] };
  }
}

function spotlightSend(tabId, msg) {
  try {
    const p = chrome.tabs.sendMessage(tabId, { type: "SPOTLIGHT", ...msg });
    return p && p.catch ? p.catch(() => null) : Promise.resolve(null);
  } catch (e) {
    return Promise.resolve(null);
  }
}

function spotlightState(tabId) {
  let st = spotlightTabs.get(tabId);
  if (!st) {
    st = defaultSpotlightState();
    spotlightTabs.set(tabId, st);
  }
  return st;
}

function markSpotlightCleared(tabId, reason) {
  const st = spotlightState(tabId);
  st.showing = false;
  st.clearedReason = reason;
  st.clearedAt = Date.now();
  if (st.logId) spotlightLogUpdate(st.logId, { clearedReason: reason });
  persistSpotlightState();
}

// Log writes are serialized through one chain: append and update both do a
// storage get-then-set, and interleaved pairs would clobber each other (a
// capture landing while the next draw appends, a clear racing an append).
let spotlightLogChain = Promise.resolve();
function spotlightLogOp(fn) {
  const run = spotlightLogChain.then(fn, fn);
  spotlightLogChain = run.catch(() => {});
  return run;
}

async function spotlightLogAppend(entry) {
  return spotlightLogOp(async () => {
    let list = [];
    try {
      const d = await chrome.storage.local.get(SPOTLIGHT_LOG_KEY);
      list = Array.isArray(d[SPOTLIGHT_LOG_KEY]) ? d[SPOTLIGHT_LOG_KEY] : [];
      list.push(entry);
      while (list.length > SPOTLIGHT_LOG_MAX_ENTRIES) list.shift();
      let size = JSON.stringify(list).length;
      while (size > SPOTLIGHT_LOG_BUDGET_BYTES && list.length > 1) {
        list.shift();
        size = JSON.stringify(list).length;
      }
      await chrome.storage.local.set({ [SPOTLIGHT_LOG_KEY]: list });
    } catch (e) {
      // Quota: the capture is dropped, never the text entry - retry once with
      // stored captures stripped so the failure is visible in the log itself.
      try {
        const stripped = list.map((en) => (en.capture && String(en.capture).startsWith("data:")
          ? { ...en, capture: "skipped:storage_full" } : en));
        await chrome.storage.local.set({ [SPOTLIGHT_LOG_KEY]: stripped });
      } catch (e2) {
        log(`Spotlight log write failed: ${e.message}`);
      }
    }
  });
}

async function spotlightLogUpdate(id, patch) {
  return spotlightLogOp(async () => {
    try {
      const d = await chrome.storage.local.get(SPOTLIGHT_LOG_KEY);
      const list = Array.isArray(d[SPOTLIGHT_LOG_KEY]) ? d[SPOTLIGHT_LOG_KEY] : [];
      const entry = list.find((e) => e.id === id);
      if (!entry) return;
      Object.assign(entry, patch);
      await chrome.storage.local.set({ [SPOTLIGHT_LOG_KEY]: list });
    } catch (e) {}
  });
}

// One composited capture (page plus overlay) per labelled highlight, stored
// with its log entry. Deliberately separate from the agent's working
// screenshot: this path never hides the overlay. Never activates a tab.
async function spotlightAuditCapture(tabId, logId, generation) {
  await sleep(220);
  const st = spotlightTabs.get(tabId);
  if (!st || !st.showing || st.generation !== generation) return;
  if (st.offscreen) {
    await spotlightLogUpdate(logId, { capture: "skipped:target_offscreen" });
    return;
  }
  // An agent screenshot in flight capture-hides the overlays; capturing now
  // would record the page with the highlight hidden. Wait it out, briefly.
  for (let i = 0; i < 15 && (captureHideDepth.get(tabId) || 0) > 0; i++) await sleep(100);
  if ((captureHideDepth.get(tabId) || 0) > 0) {
    await spotlightLogUpdate(logId, { capture: "skipped:capture_busy" });
    return;
  }
  if (!st.showing || st.generation !== generation) return;
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch (e) { return; }
  if (!tab.active) {
    await spotlightLogUpdate(logId, { capture: "skipped:tab_not_active" });
    return;
  }
  const hasDbg = await attach(tabId);
  if (!hasDbg) {
    await spotlightLogUpdate(logId, { capture: "skipped:debugger_unavailable" });
    return;
  }
  try {
    const dataUrl = await captureScreenshot(tabId, { format: "jpeg", quality: 55 });
    const small = await downscaleCapture(dataUrl);
    if (small && small.length <= SPOTLIGHT_CAPTURE_MAX_BYTES) {
      await spotlightLogUpdate(logId, { capture: small });
    } else {
      await spotlightLogUpdate(logId, { capture: "skipped:capture_too_large" });
    }
  } catch (e) {
    await spotlightLogUpdate(logId, { capture: "skipped:capture_failed" });
  }
}

async function downscaleCapture(dataUrl) {
  try {
    const blob = await (await fetch(dataUrl)).blob();
    const bmp = await createImageBitmap(blob);
    const scale = Math.min(1, 640 / bmp.width);
    if (scale >= 1 && dataUrl.length <= SPOTLIGHT_CAPTURE_MAX_BYTES) return dataUrl;
    const canvas = new OffscreenCanvas(Math.max(1, Math.round(bmp.width * scale)), Math.max(1, Math.round(bmp.height * scale)));
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const out = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.55 });
    return await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(out);
    });
  } catch (e) {
    return null;
  }
}

// Sends a prepared draw to the content script and updates state + audit log.
// drawArgs: {mode, targetKey, rect, point, label, generic, agent, descriptor,
//            query, kind, index, mediaTs, mediaKey, persist, ttlMs}
async function sendSpotlightDraw(tabId, drawArgs, settings) {
  const st = spotlightState(tabId);
  // The never-dim downgrade lives here, not only in the execute path, so
  // redraws and reshows replaying a stored draw honour it too.
  if (settings.neverDim && drawArgs.mode === "spotlight") drawArgs.mode = "ring";
  const ttlMs = computeTtlMs({ label: drawArgs.label, persist: drawArgs.persist, ttlMs: drawArgs.ttlMs, multiplier: settings.multiplier });
  const generation = ++spotlightGeneration;
  const highlightUntil = Date.now() + ttlMs;
  const resp = await spotlightSend(tabId, {
    op: "draw",
    mode: drawArgs.mode,
    targetKey: drawArgs.targetKey || null,
    rect: drawArgs.rect || null,
    point: drawArgs.point || null,
    label: drawArgs.generic ? null : drawArgs.label,
    generic: Boolean(drawArgs.generic),
    agent: drawArgs.agent || "Agent",
    descriptor: drawArgs.descriptor || null,
    shortLabel: drawArgs.descriptor && drawArgs.descriptor.name ? drawArgs.descriptor.name.slice(0, 40) : null,
    highlightUntil,
    generation,
    session: spotlightSession,
    mediaTs: typeof drawArgs.mediaTs === "number" ? drawArgs.mediaTs : null,
    mediaKey: drawArgs.mediaKey || null,
  });
  if (resp === null) {
    return fail("spotlight_unavailable", "The Spotlight content script did not answer on this tab; reload the tab and try again");
  }
  if (resp.applied === false) {
    // The content script dropped the draw (stale session or generation, or
    // the resolved target left the registry). State must not claim a
    // highlight that is not on screen.
    if (resp.reason === "target_missing") {
      return fail("target_not_found", "The resolved target left the page before the draw landed; resolve it again and retry");
    }
    return fail("spotlight_unavailable", "The Spotlight content script did not apply the draw (stale session); retry the draw");
  }
  const wasShowing = st.showing;
  const prevLogId = st.logId;
  st.showing = true;
  st.generation = generation;
  st.mode = drawArgs.mode;
  st.label = drawArgs.generic ? null : drawArgs.label;
  if (st.label) st.labelAt = Date.now(); // the rate window tracks labelled draws only
  st.offscreen = Boolean(resp.offscreen);
  st.clearedReason = null;
  st.clearedAt = null;
  st.lastDraw = { ...drawArgs };
  persistSpotlightState();
  if (wasShowing && prevLogId) spotlightLogUpdate(prevLogId, { clearedReason: "replaced" });
  const logId = `spot-${Date.now().toString(36)}-${generation}`;
  st.logId = logId;
  let url = "";
  try { url = (await chrome.tabs.get(tabId)).url || ""; } catch (e) {}
  spotlightLogAppend({
    id: logId, at: new Date().toISOString(), url, mode: drawArgs.mode,
    target: drawArgs.descriptor ? `${drawArgs.descriptor.tag} ${drawArgs.descriptor.name || ""}`.trim() : (drawArgs.point ? `point ${drawArgs.point.x},${drawArgs.point.y}` : "rect"),
    label: drawArgs.generic ? "(generic sensitive-field chip)" : drawArgs.label,
    ttlMs, clearedReason: null, capture: null,
  });
  if (st.label) spotlightAuditCapture(tabId, logId, generation);
  return {
    success: true,
    generation,
    ttl_ms: ttlMs,
    expires_at: new Date(highlightUntil).toISOString(),
    covered: Boolean(resp && resp.covered),
    offscreen: Boolean(resp && resp.offscreen),
  };
}

export async function spotlightReshow(tabId) {
  const st = spotlightTabs.get(tabId);
  if (!st || !st.lastDraw) return;
  const settings = await spotlightSettings();
  if (!settings.enabled) return;
  // A reshow is a fresh draw for safety purposes: the page under the stored
  // query may have changed (an SPA step swap, a navigation the worker state
  // missed), so the origin gate and the sensitive check both run again.
  let tab = null;
  try { tab = await chrome.tabs.get(tabId); } catch (e) {}
  if (tab && isSensitiveOrigin(tab.url, settings.allowlist)) return;
  const draw = { ...st.lastDraw };
  if (draw.query) {
    const r = await execInTab(tabId, page.pageSpotlightResolve, [draw.query, { kind: draw.kind || "target", index: draw.index }]);
    if (!r || !r.found || r.sensitive) return;
    draw.targetKey = r.targetKey;
    draw.rect = null;
    draw.descriptor = r.descriptor;
  }
  await sendSpotlightDraw(tabId, draw, settings);
}

// Content-script and worker events, routed by background.js.
export async function spotlightEvent(tabId, evt) {
  const st = spotlightTabs.get(tabId);
  if (!st) return;
  // Events carry the generation of the highlight they describe; one from a
  // replaced draw must never act on the highlight that replaced it.
  if (typeof evt.generation === "number" && st.generation && evt.generation !== st.generation) return;
  if (evt.type === "cleared") {
    const label = st.showing ? st.label : null;
    if (st.showing) markSpotlightCleared(tabId, evt.reason);
    return { label };
  }
  if (evt.type === "user_scroll") {
    if (st.showing && st.mode === "spotlight") {
      await spotlightSend(tabId, { op: "clear", reason: "scroll" });
      markSpotlightCleared(tabId, "scroll");
    }
    return;
  }
  if (evt.type === "target_gone") {
    if (!st.showing || !st.lastDraw || !st.lastDraw.query) {
      if (st.showing) {
        await spotlightSend(tabId, { op: "clear", reason: "target_gone" });
        markSpotlightCleared(tabId, "target_gone");
      }
      return;
    }
    const r = await execInTab(tabId, page.pageSpotlightResolve, [st.lastDraw.query, { kind: st.lastDraw.kind || "target", index: st.lastDraw.index }]);
    // Rebind only to the same element: an index into a shifted list can land
    // on a different row, and pointing at the wrong element is worse than
    // reporting the target gone. The check needs a positive identity signal:
    // matching names when both sides have one, or an anchored query kind
    // (selector/ref) whose query itself is the identity. Two unnamed same-tag
    // elements prove nothing.
    const prevDesc = st.lastDraw.descriptor;
    let sameTarget = false;
    if (r && r.found && r.descriptor) {
      if (prevDesc && prevDesc.name && r.descriptor.name) {
        sameTarget = prevDesc.tag === r.descriptor.tag && prevDesc.name === r.descriptor.name;
      } else if (st.lastDraw.kind === "selector" || st.lastDraw.kind === "ref") {
        sameTarget = !prevDesc || prevDesc.tag === r.descriptor.tag;
      }
    }
    if (r && r.found && !r.sensitive && sameTarget) {
      const settings = await spotlightSettings();
      const draw = { ...st.lastDraw, targetKey: r.targetKey, descriptor: r.descriptor, rect: null };
      await sendSpotlightDraw(tabId, draw, settings);
    } else {
      await spotlightSend(tabId, { op: "clear", reason: "target_gone" });
      markSpotlightCleared(tabId, "target_gone");
    }
    return;
  }
  if (evt.type === "reshow") {
    await spotlightReshow(tabId);
  }
}

// Takeover: clear any showing highlight and hand back its label so the caller
// can offer the retained "Last point" re-show in the pill.
export async function spotlightTakeover(tabId, reason) {
  const st = spotlightTabs.get(tabId);
  if (!st || !st.showing) return null;
  const label = st.label;
  await spotlightSend(tabId, { op: "clear", reason });
  markSpotlightCleared(tabId, reason);
  return label;
}

export function spotlightSessionEnd() {
  spotlightSession += 1;
  for (const [tabId, st] of spotlightTabs) {
    if (st.showing) {
      spotlightSend(tabId, { op: "session_end", session: spotlightSession });
      markSpotlightCleared(tabId, "session_end");
    }
  }
  persistSpotlightState();
}

export async function spotlightSettingsChanged() {
  const settings = await spotlightSettings();
  for (const [tabId, st] of spotlightTabs) {
    if (!st.showing) continue;
    if (!settings.enabled) {
      await spotlightSend(tabId, { op: "settings", enabled: false });
      markSpotlightCleared(tabId, "settings");
    } else if (settings.neverDim && st.mode === "spotlight") {
      await spotlightSend(tabId, { op: "settings", neverDim: true });
      st.mode = "ring";
    } else {
      // Revoking the current origin's allowlist ends a showing highlight
      // with the settings reason, like the enable toggle does.
      let tab = null;
      try { tab = await chrome.tabs.get(tabId); } catch (e) {}
      if (tab && isSensitiveOrigin(tab.url, settings.allowlist)) {
        await spotlightSend(tabId, { op: "clear", reason: "settings" });
        markSpotlightCleared(tabId, "settings");
      }
    }
  }
}

export function spotlightTabClosed(tabId) {
  spotlightTabs.delete(tabId);
  persistSpotlightState();
}

// Top-frame navigation ends the document the highlight was drawn into. The
// worker state must end with it - and the stored query must go too, so a
// later reshow can never re-resolve it against a different origin.
export function spotlightNavigation(tabId) {
  const st = spotlightTabs.get(tabId);
  if (!st) return;
  if (st.showing) markSpotlightCleared(tabId, "navigation");
  st.lastDraw = null;
  st.label = null;
  st.rect = null;
  persistSpotlightState();
}

export function spotlightModeOf(tabId) {
  const st = spotlightTabs.get(tabId);
  return st && st.showing ? st.mode : null;
}

// Lifetime precedence: an agent action whose target is the highlighted
// element clears the highlight, ~800ms after the action executes, so "about
// to click Save" is not erased by the click it announces. Read-only actions
// never clear. Called from dispatch() with the resolved tab and the result.
const SPOTLIGHT_ACTION_CLEAR_MS = 800;
const TARGETED_MUTATIONS = new Set([
  "click", "dblclick", "rightclick", "drag", "type", "fill",
  "check", "check_radio", "select_radio", "select", "upload",
]);
function maybeScheduleSpotlightActionClear(tabId, cmd) {
  const st = spotlightTabs.get(tabId);
  if (!st || !st.showing || !st.lastDraw) return;
  if (!TARGETED_MUTATIONS.has(cmd.action)) return;
  const p = cmd.params || {};
  const q = p.target || p.selector || p.ref || p.text || p.of || null;
  let onTarget = Boolean(q && st.lastDraw.query && String(q) === String(st.lastDraw.query));
  if (!onTarget && st.rect && hasCoords(p)) {
    const x = Number(p.x), y = Number(p.y);
    onTarget = x >= st.rect.x && x <= st.rect.x + st.rect.width
      && y >= st.rect.y && y <= st.rect.y + st.rect.height;
  }
  if (!onTarget) return;
  const generation = st.generation;
  setTimeout(() => {
    const now = spotlightTabs.get(tabId);
    if (now && now.showing && now.generation === generation) {
      spotlightSend(tabId, { op: "clear", reason: "action" });
      markSpotlightCleared(tabId, "action");
    }
  }, SPOTLIGHT_ACTION_CLEAR_MS);
}

export async function execute(cmd) {
  const action = cmd.action;
  const p = cmd.params || {};
  const tabId = p.tabId;
  // Action and tab only: params carry typed text and eval source, which do not
  // belong in the worker console log.
  log(`Executing: ${action} (tab ${tabId === undefined || tabId === null ? "?" : tabId})`);
  // Execution-time gate: checked here, inside the queue, so a command admitted
  // before the user took over still stops before it acts. Batch steps funnel
  // through execute() too, so the batch loop inherits the check per step.
  // Spotlight draws get their own code so the agent can tell a pause from a
  // takeover of the page itself; clearing and status stay available.
  if (action === "spotlight" && isPausedFor(tabId, action)) {
    return fail("tab_paused", "The user has paused this tab; Spotlight draws wait until they resume. spotlight_clear and spotlight_status still work.");
  }
  if (isPausedFor(tabId, action) && action !== "spotlight_clear" && action !== "spotlight_status") {
    return fail("user_takeover", USER_TAKEOVER_ERROR);
  }

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
          return fail("file_access_required", "Turn on 'Allow access to file URLs' for Agent Browser Bridge in edge://extensions > Details.");
        }
        const loadTimeout = p.deadlineMs ? Math.min(15000, Math.max(100, p.deadlineMs - Date.now())) : 15000;
        let tab = await getTargetTab(tabId);
        if (tab) await attach(tab.id);
        if (!tab) {
          tab = await chrome.tabs.create({ url });
          const loaded = await waitForTabLoad(tab.id, loadTimeout, true);
          const created = await chrome.tabs.get(tab.id);
          if (!loaded) return fail("nav_timeout", `Navigation to ${url} timed out`, { url: created.url, tab: tabInfo(created) });
          return { success: true, title: created.title, url: created.url, tab: tabInfo(created) };
        } else {
          const pending = waitForTabLoad(tab.id, loadTimeout);
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
      }

      case "tab_new": {
        const url = p.url || "about:blank";
        if (typeof url === "string" && url.startsWith("file://") && !await checkFileAccess()) {
          return fail("file_access_required", "Turn on 'Allow access to file URLs' for Agent Browser Bridge in edge://extensions > Details.");
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
        const loadTimeout = p.deadlineMs ? Math.min(15000, Math.max(100, p.deadlineMs - Date.now())) : 15000;
        if (p.wait !== "none" && url !== "about:blank") await waitForTabLoad(tab.id, loadTimeout, true);
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
        const loadTimeout = p.deadlineMs ? Math.min(15000, Math.max(100, p.deadlineMs - Date.now())) : 15000;
        const pending = p.wait === "none" ? null : waitForTabLoad(tabId, loadTimeout);
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
        if (pending) {
          const ok = await pending;
          if (!ok) {
            const t = await chrome.tabs.get(tabId);
            return fail("nav_timeout", `${action} timed out waiting for page load`, { url: t.url, tab: tabInfo(t) });
          }
        }
        const t = await chrome.tabs.get(tabId);
        return { success: true, url: t.url, title: t.title, tab: tabInfo(t) };
      }

      case "select": {
        const loc = await locate(tabId, p, "select");
        if (!loc.ok) return loc.result;
        const target = p.ref || p.target || p.selector || p.text || null;
        const frameId = hasCoords(p) ? undefined : frameOfRef(target);
        const at = frameId !== undefined ? loc.info.local : loc;
        const res = await execInTab(tabId, page.pageSelect, [target, at.x, at.y, p.value, p.label], "ISOLATED", frameId);
        return res && res.success ? { ...res, ...loc.info, x: loc.x, y: loc.y } : res;
      }

      case "upload": {
        const files = Array.isArray(p.files) ? p.files.map(String) : (p.file ? [String(p.file)] : []);
        if (!files.length) return fail("bad_params", "upload requires files: [absolute paths]");
        const relative = files.filter(f => !/^([a-zA-Z]:[\\/]|\/|\\\\)/.test(f));
        if (relative.length) return fail("bad_params", `upload needs absolute paths, got: ${relative.join(", ")}`);
        if (!await checkFileAccess()) {
          return fail("file_access_required", "Turn on 'Allow access to file URLs' for Agent Browser Bridge in edge://extensions > Details.");
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
          if (isDetachError(e.message)) {
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

      case "spotlight": {
        const settings = await spotlightSettings();
        if (!settings.enabled) return fail("spotlight_disabled", "Spotlight is turned off in the extension popup.");
        const tab = await getTargetTab(tabId);
        if (!tab) return fail("no_active_tab", "No active tab found");
        const tid = tab.id;
        if (isSensitiveOrigin(tab.url, settings.allowlist)) {
          return fail("sensitive_origin", "Spotlight is off on this origin (banking, payment, password-manager or webmail site). The user can allowlist it in the extension popup.");
        }
        const generic = p.generic === true;
        const lv = validateLabel(p.label);
        if (!lv.ok) return fail(lv.code, lv.error);
        const label = generic ? null : lv.label;

        const elementQuery = p.ref || p.target || p.selector || p.text || null;
        const hasPoint = hasCoords(p);
        const hasRect = p.rect && typeof p.rect === "object" && p.rect.width !== undefined && p.rect.height !== undefined;
        let mode = p.mode || null;
        if (mode && !["ring", "spotlight", "point"].includes(mode)) {
          return fail("bad_params", `Unknown spotlight mode "${mode}"; use ring, spotlight or point`);
        }
        if (elementQuery && hasPoint) return fail("bad_params", "spotlight takes either an element target or coordinates, not both");
        if (!elementQuery && !hasPoint && !hasRect) return fail("bad_params", "spotlight requires ref, target, selector, text, x/y, or rect");
        if (p.mode === "point" && hasRect) return fail("bad_params", "point mode takes coordinates, not a rect");
        if (!mode) mode = elementQuery || hasRect ? "ring" : "point";
        if (mode === "point" && elementQuery) return fail("bad_params", "point mode takes coordinates, not an element target");
        if (mode !== "point" && !elementQuery && hasRect === false && hasPoint) {
          return fail("bad_params", `${mode} mode needs an element target or a rect; bare coordinates are point mode`);
        }
        if (mode === "point" && !label && !generic) {
          return fail("bad_params", "point mode requires a label: a bare dot on a chart or image is too easy to miss or misread");
        }

        const st = spotlightState(tid);
        if (label && st.labelAt && Date.now() - st.labelAt < LABEL_REPLACE_WINDOW_MS) {
          return fail("rate_limited", "A labelled highlight was drawn on this tab less than 2s ago; labelled draws are limited to one per 2s per tab. Wait for the window to pass, then retry.");
        }

        let downgradedReason = null;
        if (settings.neverDim && mode === "spotlight") { mode = "ring"; downgradedReason = "never_dim"; }
        const drawArgs = {
          mode, label, generic, agent: typeof cmd.agent === "string" ? cmd.agent : "Agent",
          persist: p.persist === true, ttlMs: typeof p.ttl_ms === "number" ? p.ttl_ms : undefined,
          targetKey: null, rect: null, point: null, descriptor: null, query: null, kind: null, index: null,
          mediaTs: null, mediaKey: null,
        };
        let resultExtra = {};

        if (elementQuery) {
          const kind = p.selector && !p.ref && !p.target ? "selector" : (p.ref ? "ref" : "target");
          const index = (p.index === undefined || p.index === null) ? null : Number(p.index);
          const r = await execInTab(tid, page.pageSpotlightResolve, [elementQuery, { kind, index }]);
          if (!r || r.success === false || !r.found) {
            return fail((r && r.code) || "target_not_found", (r && r.error) || `Target not found: ${elementQuery}`,
              { count: r && r.count, candidates: r && r.candidates });
          }
          if (frameOfRef(elementQuery) !== undefined) {
            return fail("unsupported_frame", "Spotlight draws in the main frame only for now; subframe targets are not supported yet");
          }
          drawArgs.targetKey = r.targetKey;
          drawArgs.descriptor = r.descriptor;
          drawArgs.query = elementQuery;
          drawArgs.kind = kind;
          drawArgs.index = index;
          resultExtra = { descriptor: r.descriptor, rect: r.rect, count: r.count, in_view: r.inView };
          if (r.sensitive && !generic) {
            return fail("sensitive_target",
              "That target is, or contains, a password, one-time-code or payment field, so Spotlight will not frame it. Point at its visible label or error message instead, or pass generic:true to point at the field itself with a fixed extension-written chip.",
              { labelText: r.labelText || null, descriptor: r.descriptor });
          }
          if (mode === "spotlight" && r.viewport) {
            const area = (r.rect.width * r.rect.height) / Math.max(1, r.viewport.w * r.viewport.h);
            if (area > 0.85) { mode = "ring"; drawArgs.mode = "ring"; downgradedReason = "viewport_fill"; }
          }
        } else if (hasPoint) {
          const x = Number(p.x), y = Number(p.y);
          const vp = await execInTab(tid, () => ({ w: window.innerWidth, h: window.innerHeight }));
          if (vp && (x < 0 || y < 0 || x > vp.w || y > vp.h)) {
            return fail("bad_params", `Point ${x},${y} is outside the viewport (${vp.w}x${vp.h})`);
          }
          const hit = await execInTab(tid, page.pageSpotlightHitTest, [x, y]);
          if (hit && hit.sensitive && !generic) {
            return fail("sensitive_target", "A sensitive field (password, one-time-code or payment) sits under that point. Point at its label or error text instead, or pass generic:true for a fixed extension-written chip.", { descriptor: hit.descriptor });
          }
          drawArgs.point = { x, y };
          drawArgs.descriptor = hit ? hit.descriptor : null;
          if (hit && hit.media) {
            drawArgs.mediaKey = hit.targetKey;
            if (typeof p.media_time === "number") drawArgs.mediaTs = Number(p.media_time);
          }
          resultExtra.descriptor = drawArgs.descriptor;
          resultExtra.rect = { x, y, width: 0, height: 0 };
          resultExtra.in_view = true;
        } else if (hasRect) {
          const rect = { x: Number(p.rect.x) || 0, y: Number(p.rect.y) || 0, width: Number(p.rect.width), height: Number(p.rect.height) };
          if (!(rect.width > 0 && rect.height > 0)) return fail("bad_params", "rect needs positive width and height");
          const vp = await execInTab(tid, () => ({ w: window.innerWidth, h: window.innerHeight }));
          // The safety probes and the bad_params rule both work on the part
          // of the rect the user can actually see.
          const ix = Math.max(0, rect.x), iy = Math.max(0, rect.y);
          const ix2 = vp ? Math.min(vp.w, rect.x + rect.width) : rect.x + rect.width;
          const iy2 = vp ? Math.min(vp.h, rect.y + rect.height) : rect.y + rect.height;
          if (vp && (ix2 <= ix || iy2 <= iy)) {
            return fail("bad_params", `Rect ${rect.x},${rect.y} ${rect.width}x${rect.height} is outside the viewport (${vp.w}x${vp.h})`);
          }
          // Grid probes over the visible intersection: five fixed points let
          // a field sitting between them be framed completely unchecked.
          const probes = [];
          const cols = Math.max(2, Math.min(4, Math.ceil((ix2 - ix) / 160) + 1));
          const rows = Math.max(2, Math.min(4, Math.ceil((iy2 - iy) / 160) + 1));
          for (let cx = 0; cx < cols; cx++) {
            for (let cy = 0; cy < rows; cy++) {
              probes.push([
                Math.min(ix2 - 1, ix + ((ix2 - ix) * (cx + 0.5)) / cols),
                Math.min(iy2 - 1, iy + ((iy2 - iy) * (cy + 0.5)) / rows),
              ]);
            }
          }
          for (const [px, py] of probes) {
            const hit = await execInTab(tid, page.pageSpotlightHitTest, [px, py]);
            if (hit && hit.sensitive && !generic) {
              return fail("sensitive_target", "That rect overlaps a sensitive field (password, one-time-code or payment). Shrink the rect to the label or error text, or pass generic:true for a fixed extension-written chip.", { descriptor: hit.descriptor });
            }
          }
          drawArgs.rect = rect;
          drawArgs.descriptor = { tag: "rect", role: "", name: "" };
          resultExtra = { rect, in_view: ix === rect.x && iy === rect.y && ix2 === rect.x + rect.width && iy2 === rect.y + rect.height };
        }
        if (typeof p.scale_factor === "number") resultExtra.scale_factor = Number(p.scale_factor);

        const drawn = await sendSpotlightDraw(tid, drawArgs, settings);
        if (!drawn.success) return drawn;
        // The content script's post-draw view of the world wins: a target can
        // sit partly off-screen even when the resolver saw it in view.
        resultExtra.in_view = !drawn.offscreen;
        spotlightState(tid).rect = resultExtra.rect || null;
        return {
          success: true,
          mode_effective: drawArgs.mode,
          downgraded_reason: downgradedReason,
          ttl_ms: drawn.ttl_ms,
          expires_at: drawn.expires_at,
          label_truncated: false,
          ...resultExtra,
          covered: drawn.covered,
        };
      }

      case "spotlight_clear": {
        const tab = await getTargetTab(tabId);
        if (!tab) return fail("no_active_tab", "No active tab found");
        const st = spotlightState(tab.id);
        const wasShowing = st.showing;
        if (wasShowing) {
          await spotlightSend(tab.id, { op: "clear", reason: "cleared" });
          markSpotlightCleared(tab.id, "cleared");
        }
        return { success: true, was_showing: wasShowing };
      }

      case "spotlight_status": {
        const tab = await getTargetTab(tabId);
        if (!tab) return fail("no_active_tab", "No active tab found");
        const st = spotlightState(tab.id);
        return {
          success: true,
          showing: st.showing,
          mode: st.showing ? st.mode : null,
          cleared_reason: st.showing ? null : st.clearedReason,
          cleared_at: st.clearedAt ? new Date(st.clearedAt).toISOString() : null,
        };
      }

      case "screenshot": {
        const tab = await chrome.tabs.get(tabId);
        await captureHide(tabId);
        const format = p.format === "png" ? "png" : "jpeg";
        let clip = p.clip || null;
        try {
          if (!clip && p.of) {
            const r = await resolveTargetWithWait(tabId, p.of, p.timeout || 5000, { highlight: false });
            if (!r || !r.found) return fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: ${p.of}`);
            clip = { x: r.x - r.width / 2, y: r.y - r.height / 2, width: r.width, height: r.height };
          }
          const hasDbg = await attach(tabId);
          let cdpError = null;
          if (hasDbg) {
            try {
              const dataUrl = await captureScreenshot(tabId, { format, quality: p.quality, clip });
              const meta = await screenshotMeta(tabId, dataUrl, clip);
              return { success: true, dataUrl, format, clip, tabId: tab.id, title: tab.title, url: tab.url, ...meta };
            } catch (e) {
              cdpError = e.message;
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
          const meta = await screenshotMeta(tabId, dataUrl, null);
          const out = { success: true, dataUrl, format, clip: null, tabId: tab.id, title: tab.title, url: tab.url, activated: !tab.active, ...meta };
          if (!hasDbg || cdpError) {
            out.native = false;
            out.debuggerError = cdpError || getDebuggerError(tabId);
          }
          return out;
        } finally {
          await captureRestore(tabId);
        }
      }

      case "click":
      case "dblclick":
      case "rightclick": {
        const loc = await locate(tabId, p, action);
        if (!loc.ok) {
          if (action !== "rightclick" && (p.selector || p.text) && loc.result.code !== "bad_selector") {
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
        await attach(tabId);
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
        let timeout = Number(p.timeout || 5000);
        if (p.deadlineMs) timeout = Math.min(timeout, Math.max(0, p.deadlineMs - Date.now()));
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
        if (p.text && !p.ref && !p.target && !p.selector) {
          // Status copy ("Saved", "Done") sits in a <p> or <span>, which the target resolver skips.
          while (waited() < timeout) {
            const hit = await execInTab(tabId, page.pageHasText, [String(p.text)]);
            if (hit && hit.found) return { success: true, waited: waited(), ...hit };
            await sleep(100);
          }
          return fail("target_not_found", `Text not found within ${timeout}ms: "${p.text}"`, { waited: waited() });
        }
        const r = await resolveTargetWithWait(tabId, p.ref || p.target || p.selector, timeout, { highlight: false, kind: targetKind(p) });
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
              const prep = await execInTab(tabId, page.pageFillPrepare, [mode, shouldClear], "ISOLATED", frameId);
              if (prep && prep.success === false) return prep;
              await cdpSend(tabId, "Input.insertText", { text });
              await execInTab(tabId, page.pageFillCommit, [mode, text], "ISOLATED", frameId);
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
          const resT = await execInTab(tabId, page.pageScroll, [dx, dy, r.local.x, r.local.y], "ISOLATED", frameOfRef(target));
          return resT && resT.success ? { ...resT, native: false, debuggerError: getDebuggerError(tabId) } : resT;
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
            if (isPausedFor(tab.id, step.action)) {
              const r = fail("user_takeover", USER_TAKEOVER_ERROR);
              results.push(r);
              return fail(r.code, r.error, { stoppedAt: step.action, results, tab: tabInfo(tab) });
            }
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
        return await execInTab(tabId, page.pageCheckRadio, [target, p.value, isExplicitSelector], "ISOLATED", frameOfRef(target));
      }

      case "get_text":
      case "text": {
        const target = p.ref || p.target || p.selector;
        const r = await resolveTargetWithWait(tabId, target, p.timeout || 5000, { highlight: p.highlight, kind: targetKind(p), wantText: true });
        if (!r || !r.found) return fail(r ? r.code : "target_not_found", r ? r.error : `Target not found: "${target}"`);
        const { ownText, ...rest } = r;
        return { ...rest, success: true, text: ownText ?? "" };
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
    if (err.code === "debugger_unavailable" || isDetachError(msg)) {
      return fail("debugger_unavailable", msg);
    }
    return fail("internal_error", msg);
  }
}

const executeImpl = execute;
export async function executeTracked(cmd) {
  const p = cmd.params || {};
  const tabId = typeof p.tabId === "number" ? p.tabId : undefined;
  if (activityHook && tabId !== undefined) {
    activityHook(tabId, cmd.action, "start", typeof cmd.agent === "string" ? cmd.agent : undefined);
  }
  try {
    return await executeImpl(cmd);
  } finally {
    if (activityHook && tabId !== undefined) activityHook(tabId, cmd.action, "end");
  }
}

// Read-only or dialog-handling actions skip the per-tab queue so they work while a command is blocked on a dialog.
const NOQUEUE = new Set(["console", "dialog", "tab", "get_active_tab", "spotlight_clear", "spotlight_status"]);
const TABLESS = new Set(["ping", "status", "tabs", "list_tabs", "reload_extension", "batch", "tab_switch", "switch_tab", "tab_close", "close_tab", "tab_new", "history_search", "history_delete", "group_list", "group_move", "group_ungroup"]);
// These never inject into the page (wait polls until a page it can read arrives), so any URL is fine.
const TAB_LEVEL = new Set(["nav", "back", "forward", "reload", "tab", "get_active_tab", "wait", "wait_for", "dialog", "console"]);

export async function dispatch(cmd) {
  const p = cmd.params || {};
  if (TABLESS.has(cmd.action)) return executeTracked(cmd);
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
  const result = NOQUEUE.has(cmd.action) ? await executeTracked(cmd) : await runOnTab(tab.id, () => executeTracked(cmd));
  if (result && result.success) maybeScheduleSpotlightActionClear(tab.id, cmd);
  if (result && typeof result === "object" && !result.tab) {
    let t = tab;
    try { t = await chrome.tabs.get(tab.id); } catch (e) {}
    result.tab = tabInfo(t);
  }
  return result;
}
