// Agent Browser Bridge - Spotlight overlay (agent pointing), top frame only.
// Plain content script (no modules): assigns window.__eabSpotlight in load order.
// The worker resolves targets and sends {type:"SPOTLIGHT", op:"draw"|"clear"|...};
// this script owns rendering, the TTL deadline, live element binding and clear
// reporting. Pointer-events are none everywhere except nothing at all: the
// overlay never intercepts the user's input. Esc clears locally; takeover and
// session boundaries arrive as explicit worker messages, never raw input.
(function () {
  if (window.__eabSpotlight) return;

  var HOST_ID = "__eab_spotlight_host";
  var LIVE_ID = "__eab_spotlight_live";

  var cur = null; // the showing highlight, see draw()
  var generation = 0;
  var session = 0;
  var hideDepth = 0;
  var hideGeneration = -1;
  var hideWatchdog = null;
  var writing = false; // re-entrancy guard around our own DOM writes
  var expiryTimer = null;
  var fadeTimer = null;
  var rafPending = false;
  var mediaTimer = null;
  var watchdogTimer = null;
  var goneLatch = -1; // generation a target-gone was already sent for
  var lastScrollSent = 0;

  function cleanName(s) {
    var t = String(s == null ? "" : s).replace(/[\u0000-\u001f\u007f]/g, "").trim();
    return t ? t.slice(0, 24) : "Agent";
  }
  function send(msg) {
    try {
      var p = chrome.runtime.sendMessage(msg);
      if (p && p.catch) p.catch(function () {});
    } catch (e) {}
  }

  // ---- DOM -------------------------------------------------------------------
  var host = document.createElement("div");
  host.id = HOST_ID;
  host.style.cssText = "position:fixed!important;inset:0!important;z-index:2147483645!important;pointer-events:none!important;margin:0!important;padding:0!important;border:0!important;";
  var shadow = host.attachShadow({ mode: "closed" });
  var style = document.createElement("style");
  style.textContent = [
    ".stage{position:fixed;inset:0;pointer-events:none;opacity:0;transition:opacity .12s ease-out;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}",
    ".stage.on{opacity:1}",
    ".stage.out{opacity:0;transition:opacity .15s ease-out}",
    ".ring{position:absolute;border-radius:10px;pointer-events:none;background:linear-gradient(120deg,rgba(99,102,241,.95),rgba(34,211,238,.9),rgba(167,139,250,.95)) border-box;border:3px solid transparent;-webkit-mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);mask-composite:exclude;box-shadow:0 0 0 2px rgba(10,10,10,.55),0 0 0 4px rgba(255,255,255,.75),0 0 18px rgba(99,102,241,.45)}",
    ".ring.pulse{animation:eabpulse .5s ease-out 1}",
    "@keyframes eabpulse{0%{transform:scale(1.04)}100%{transform:scale(1)}}",
    ".hole{position:absolute;border-radius:10px;pointer-events:none;box-shadow:0 0 0 9999px rgba(8,8,16,.45)}",
    ".dot{position:absolute;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;pointer-events:none;background:radial-gradient(circle at 50% 50%,rgba(34,211,238,.95) 0 5px,rgba(99,102,241,.9) 6px 9px,transparent 10px);box-shadow:0 0 0 2px rgba(10,10,10,.55),0 0 0 4px rgba(255,255,255,.8),0 0 14px rgba(99,102,241,.5)}",
    ".chip{position:absolute;max-width:320px;pointer-events:none;background:rgba(17,17,17,.94);color:#fff;border-radius:10px;padding:8px 11px;box-shadow:0 6px 20px rgba(0,0,0,.4);font-size:14px;line-height:1.35}",
    ".chip .who{font-size:11px;font-weight:700;letter-spacing:.02em;opacity:.75;text-transform:uppercase;margin-bottom:2px}",
    ".chip .txt{word-wrap:break-word}",
    ".edge{position:absolute;pointer-events:none;background:rgba(17,17,17,.94);color:#fff;border-radius:999px;padding:6px 12px;font-size:13px;font-weight:600;box-shadow:0 4px 14px rgba(0,0,0,.35);white-space:nowrap}",
    "svg.leader{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}",
    "@media (prefers-reduced-motion: reduce){.stage{transition:none}.ring.pulse{animation:none}}"
  ].join("\n");
  shadow.appendChild(style);
  var stage = document.createElement("div");
  stage.className = "stage";
  var leaderSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  leaderSvg.setAttribute("class", "leader");
  var leaderLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
  leaderLine.setAttribute("stroke", "rgba(120,130,240,.85)");
  leaderLine.setAttribute("stroke-width", "1.5");
  leaderSvg.appendChild(leaderLine);
  var hole = document.createElement("div");
  hole.className = "hole";
  var ring = document.createElement("div");
  ring.className = "ring";
  var dot = document.createElement("div");
  dot.className = "dot";
  var chip = document.createElement("div");
  chip.className = "chip";
  var chipWho = document.createElement("div");
  chipWho.className = "who";
  var chipTxt = document.createElement("div");
  chipTxt.className = "txt";
  chip.appendChild(chipWho);
  chip.appendChild(chipTxt);
  var edge = document.createElement("div");
  edge.className = "edge";
  stage.appendChild(leaderSvg);
  stage.appendChild(hole);
  stage.appendChild(ring);
  stage.appendChild(dot);
  stage.appendChild(chip);
  stage.appendChild(edge);
  shadow.appendChild(stage);

  // Screen-reader mirror in the light DOM: live regions inside closed shadow
  // roots are unreliable. Pre-created empty so the first announcement lands;
  // text is set in a later task than creation.
  var live = document.createElement("div");
  live.id = LIVE_ID;
  live.setAttribute("aria-live", "polite");
  live.style.cssText = "position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;clip:rect(0 0 0 0)!important;white-space:nowrap!important;border:0!important;padding:0!important;margin:-1px!important;";

  var printStyle = document.createElement("style");
  printStyle.id = "__eab_spotlight_print";
  printStyle.textContent = "@media print{#" + HOST_ID + "{display:none!important}}";

  function mount() {
    if (!document.documentElement) return;
    if (!host.isConnected) document.documentElement.appendChild(host);
    if (!live.isConnected) document.documentElement.appendChild(live);
    if (!printStyle.isConnected && document.head) document.head.appendChild(printStyle);
  }
  mount();
  var hostGuard = new MutationObserver(function () {
    if (!host.isConnected || !live.isConnected || !printStyle.isConnected) mount();
  });
  // Observe the document, not the documentElement captured at load: a page
  // that replaces its root element would otherwise detach the guard forever.
  hostGuard.observe(document, { childList: true });

  function announce(text) {
    setTimeout(function () {
      try { live.textContent = text; } catch (e) {}
    }, 30);
  }

  // ---- Geometry ----------------------------------------------------------------
  function viewport() { return { w: window.innerWidth, h: window.innerHeight }; }

  function currentRect() {
    if (!cur) return null;
    if (cur.el && cur.el.isConnected) {
      var r = cur.el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }
    if (cur.docRect) {
      return {
        x: cur.docRect.x - window.scrollX, y: cur.docRect.y - window.scrollY,
        width: cur.docRect.width, height: cur.docRect.height
      };
    }
    return null;
  }

  function inViewOf(rect) {
    var v = viewport();
    return rect.bottom > 0 && rect.right > 0 && rect.top < v.h && rect.left < v.w;
  }
  function withEdges(rect) {
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height,
             left: rect.x, top: rect.y, right: rect.x + rect.width, bottom: rect.y + rect.height };
  }

  // Chip placement: sides in order right, left, below, above; clamped into the
  // viewport; never over the target; out of the pill's top-centre zone. The
  // named exception (inside a large target) reports covered=true.
  function placeChip(rect, chipW, chipH) {
    var v = viewport();
    var M = 8, GAP = 12;
    var pill = { left: v.w / 2 - 190, right: v.w / 2 + 190, top: 0, bottom: 66 };
    function hits(a, b) {
      return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    }
    function box(x, y) { return { left: x, top: y, right: x + chipW, bottom: y + chipH }; }
    function clamp(x, y) {
      return {
        x: Math.max(M, Math.min(v.w - chipW - M, x)),
        y: Math.max(M, Math.min(v.h - chipH - M, y))
      };
    }
    // Prefer empty space: a side whose box would sit on top of another
    // interactive element loses to a later side that is clear of them.
    function coversInteractive(b) {
      if (!document.elementsFromPoint) return false;
      var L = window.__eabLib;
      var pts = [
        [b.left + (b.right - b.left) / 2, b.top + (b.bottom - b.top) / 2],
        [b.left + 4, b.top + 4], [b.right - 4, b.top + 4],
        [b.left + 4, b.bottom - 4], [b.right - 4, b.bottom - 4]
      ];
      for (var i = 0; i < pts.length; i++) {
        var stack = document.elementsFromPoint(pts[i][0], pts[i][1]) || [];
        var top = stack[0];
        if (!top || !top.tagName) continue;
        var tag = top.tagName.toUpperCase();
        if (tag === "A" || tag === "BUTTON" || tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return true;
        if (L && L.INTERACTIVE_ROLES && L.INTERACTIVE_ROLES.has(L.roleOf(top))) return true;
      }
      return false;
    }
    var t = withEdges(rect);
    var sides = [
      { x: t.right + GAP, y: t.top },
      { x: t.left - GAP - chipW, y: t.top },
      { x: t.left, y: t.bottom + GAP },
      { x: t.left, y: t.top - GAP - chipH }
    ];
    var firstFree = null;
    for (var i = 0; i < sides.length; i++) {
      var c = clamp(sides[i].x, sides[i].y);
      var b = box(c.x, c.y);
      if (hits(b, t) || hits(b, pill)) continue;
      if (!firstFree) firstFree = { x: c.x, y: c.y, covered: false };
      if (!coversInteractive(b)) return { x: c.x, y: c.y, covered: false };
    }
    if (firstFree) return firstFree;
    // No free side: inside the target's top edge, clear of its first rows where
    // the target is tall enough; otherwise the viewport's bottom centre.
    if (t.height > chipH + 56 && t.width > chipW + 16) {
      var ix = Math.max(M, Math.min(v.w - chipW - M, t.left + 10));
      var iy = Math.max(M, Math.min(v.h - chipH - M, t.top + 34));
      return { x: ix, y: iy, covered: true };
    }
    var fb = box(Math.max(M, v.w / 2 - chipW / 2), v.h - chipH - M);
    return { x: fb.left, y: fb.top, covered: hits(fb, t) };
  }

  // ---- Rendering -----------------------------------------------------------------
  function render(pulse) {
    if (!cur) return;
    writing = true;
    try {
      var rect = currentRect();
      var v = viewport();
      // A bound element collapsed to zero area (display:none and friends) is
      // gone, not a 12px ring at the viewport origin.
      if (cur.el && rect && rect.width === 0 && rect.height === 0) { onTargetGone(); return; }
      var hasRect = Boolean(rect) && rect.width >= 0;
      var rectE = hasRect ? withEdges(rect) : null;
      var visible = hasRect && inViewOf(rectE);
      var mediaHidden = false;
      if (cur.mode === "point" && cur.mediaTs !== null && cur.mediaEl) {
        var ct = cur.mediaEl.currentTime;
        mediaHidden = typeof ct !== "number" || Math.abs(ct - cur.mediaTs) > 2;
      }
      stage.style.display = "";
      ring.style.display = "none";
      hole.style.display = "none";
      dot.style.display = "none";
      chip.style.display = "none";
      edge.style.display = "none";
      leaderSvg.style.display = "none";

      if (!visible && hasRect) {
        // Edge indicator: arrow + short text at the nearest edge.
        var cx = Math.max(0, Math.min(v.w, rectE.left + rectE.width / 2));
        var cy = Math.max(0, Math.min(v.h, rectE.top + rectE.height / 2));
        var arrow = "\u2192", dirWord = "to the right", ex = 0, ey = cy - 16;
        if (rectE.right < 0) { arrow = "\u2190"; dirWord = "to the left"; }
        else if (rectE.bottom < 0) { arrow = "\u2191"; dirWord = "above"; ey = 10; }
        else if (rectE.top > v.h) { arrow = "\u2193"; dirWord = "below"; ey = v.h - 42; }
        edge.textContent = arrow + " " + (cur.shortLabel || "Pointed item");
        edge.style.display = "";
        // Measure the pill: a hardcoded width clips longer short labels.
        var ew = edge.offsetWidth || 150;
        if (dirWord === "to the right") ex = v.w - ew - 10;
        else if (dirWord === "to the left") ex = 10;
        else ex = cx - ew / 2;
        edge.style.left = Math.max(8, Math.min(v.w - ew - 8, ex)) + "px";
        edge.style.top = Math.max(8, Math.min(v.h - 42, ey)) + "px";
        cur.offscreen = true;
        cur.direction = dirWord;
        return;
      }
      cur.direction = null;
      cur.offscreen = false;
      if (!hasRect) return;

      if (cur.mode === "spotlight") {
        hole.style.left = rectE.left + "px";
        hole.style.top = rectE.top + "px";
        hole.style.width = rectE.width + "px";
        hole.style.height = rectE.height + "px";
        hole.style.display = "";
        // The cut-out edge itself carries the gradient (no second ring on top):
        // the ring element sits exactly on the cut-out bounds in this mode.
        ring.style.left = rectE.left + "px";
        ring.style.top = rectE.top + "px";
        ring.style.width = rectE.width + "px";
        ring.style.height = rectE.height + "px";
        ring.style.display = "";
        if (pulse) {
          ring.classList.remove("pulse");
          void ring.offsetWidth;
          ring.classList.add("pulse");
        }
      }
      if (cur.mode === "point") {
        if (!mediaHidden) {
          dot.style.left = (rectE.left + rectE.width / 2) + "px";
          dot.style.top = (rectE.top + rectE.height / 2) + "px";
          dot.style.display = "";
        }
      } else if (cur.mode !== "spotlight") {
        ring.style.left = (rectE.left - 6) + "px";
        ring.style.top = (rectE.top - 6) + "px";
        ring.style.width = (rectE.width + 12) + "px";
        ring.style.height = (rectE.height + 12) + "px";
        ring.style.display = "";
        if (pulse) {
          ring.classList.remove("pulse");
          void ring.offsetWidth;
          ring.classList.add("pulse");
        }
      }

      if (cur.label || cur.generic) {
        chipWho.textContent = "\u25c6 " + cleanName(cur.agent) + " \u00b7 agent";
        chipTxt.textContent = cur.generic ? "Agent is pointing at a sensitive field" : cur.label;
        chip.style.left = "-9999px";
        chip.style.top = "0px";
        chip.style.display = "";
        var cw = chip.offsetWidth, ch = chip.offsetHeight;
        var placed = placeChip(rectE, cw, ch);
        chip.style.left = placed.x + "px";
        chip.style.top = placed.y + "px";
        cur.covered = placed.covered;
        // Leader line when the chip sits away from the target.
        var ccx = placed.x + cw / 2, ccy = placed.y + ch / 2;
        var tx = Math.max(rectE.left, Math.min(rectE.right, ccx));
        var ty = Math.max(rectE.top, Math.min(rectE.bottom, ccy));
        var dist = Math.hypot(ccx - tx, ccy - ty);
        if (dist > 28) {
          leaderLine.setAttribute("x1", ccx); leaderLine.setAttribute("y1", ccy);
          leaderLine.setAttribute("x2", tx); leaderLine.setAttribute("y2", ty);
          leaderSvg.style.display = "";
        }
      }
    } finally {
      writing = false;
    }
  }

  function scheduleRender() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(function () {
      rafPending = false;
      if (cur) render(false);
    });
  }

  // ---- Observers -------------------------------------------------------------------
  var targetMO = null;
  var targetRO = null;
  function armObservers() {
    disarmObservers();
    if (!cur || !cur.el) return;
    targetMO = new MutationObserver(function (records) {
      if (writing || !cur) return;
      for (var i = 0; i < records.length; i++) {
        var rec = records[i];
        if (rec.type === "childList") {
          // Only the target's own removal counts as gone; a sibling change
          // under the same parent leaves the target untouched.
          if (!cur.el || !cur.el.isConnected) { onTargetGone(); return; }
          var removed = rec.removedNodes || [];
          for (var j = 0; j < removed.length; j++) {
            if (removed[j] === cur.el) { onTargetGone(); return; }
          }
          scheduleRender();
        }
        if (rec.type === "attributes") {
          // Material change: the element's accessible identity moved.
          var L = window.__eabLib;
          if (L && cur.el && cur.descriptor) {
            var now = L.descriptorOf(cur.el);
            if (now.name && cur.descriptor.name && now.name !== cur.descriptor.name) { onTargetGone(); return; }
          }
          scheduleRender();
        }
      }
    });
    try {
      targetMO.observe(cur.el, { attributes: true, attributeFilter: ["class", "style"] });
      if (cur.el.parentElement) targetMO.observe(cur.el.parentElement, { childList: true });
    } catch (e) {}
    if (typeof ResizeObserver !== "undefined") {
      targetRO = new ResizeObserver(function () { scheduleRender(); });
      try { targetRO.observe(cur.el); } catch (e) {}
    }
  }
  function disarmObservers() {
    if (targetMO) { try { targetMO.disconnect(); } catch (e) {} targetMO = null; }
    if (targetRO) { try { targetRO.disconnect(); } catch (e) {} targetRO = null; }
  }

  function onTargetGone() {
    if (!cur) return;
    // Latched per generation: while the target stays disconnected, further
    // mutation batches must not re-send and make the worker re-resolve again.
    if (goneLatch === cur.generation) return;
    goneLatch = cur.generation;
    // Hide now; the worker re-resolves against the stored query and either
    // redraws (new generation) or confirms the clear.
    stage.classList.remove("on");
    send({ type: "SPOTLIGHT_TARGET_GONE", generation: cur.generation });
  }

  // ---- Lifecycle ---------------------------------------------------------------------
  function clearCurrent(reason, opts) {
    if (!cur) return;
    var done = cur;
    cur = null;
    disarmObservers();
    if (expiryTimer) { clearTimeout(expiryTimer); expiryTimer = null; }
    if (mediaTimer) { clearInterval(mediaTimer); mediaTimer = null; }
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
    if (opts && opts.immediate) {
      stage.classList.remove("on");
      stage.style.display = "none";
    } else {
      stage.classList.add("out");
      stage.classList.remove("on");
      // The fade timer must never hide a later draw: it is cancelled by draw()
      // and re-checks that nothing is showing before touching the classes.
      if (fadeTimer) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(function () {
        fadeTimer = null;
        if (!cur) { stage.classList.remove("out"); stage.classList.remove("on"); }
      }, 160);
    }
    // Every clear is announced, labelled or not; a replacement is followed
    // immediately by the new draw's own announcement, so it stays quiet.
    if (reason !== "replaced") announce("Agent highlight cleared");
    if (!opts || !opts.fromWorker) {
      send({ type: "SPOTLIGHT_CLEARED", reason: reason, generation: done.generation });
    }
  }

  // Returns { applied } so the worker can tell a landed draw from one dropped
  // for a stale session/generation or an evicted target - the two states must
  // never be indistinguishable.
  function draw(msg) {
    if (typeof msg.session === "number") {
      if (msg.session < session) return { applied: false, reason: "stale_session" };
      if (msg.session > session) {
        if (cur) clearCurrent("session_end", { fromWorker: true, immediate: true });
        session = msg.session;
      }
    }
    if (typeof msg.generation === "number") {
      if (msg.generation < generation) return { applied: false, reason: "stale_generation" };
      generation = msg.generation;
    }
    var el = null;
    if (msg.targetKey && window.__eabSpotlightTargets) {
      el = window.__eabSpotlightTargets.get(msg.targetKey) || null;
    }
    var mediaEl = null;
    if (msg.mediaKey && window.__eabSpotlightTargets) {
      mediaEl = window.__eabSpotlightTargets.get(msg.mediaKey) || null;
    }
    if (msg.targetKey && !el && !msg.rect && !msg.point) {
      return { applied: false, reason: "target_missing" };
    }
    if (cur) clearCurrent("replaced", { fromWorker: true, immediate: true });
    goneLatch = -1;
    cur = {
      mode: msg.mode || "ring",
      el: el,
      targetKey: msg.targetKey || null,
      docRect: null,
      label: msg.label || null,
      shortLabel: msg.shortLabel || null,
      generic: Boolean(msg.generic),
      agent: msg.agent || "Agent",
      descriptor: msg.descriptor || null,
      until: typeof msg.highlightUntil === "number" ? msg.highlightUntil : Date.now() + 4000,
      generation: generation,
      mediaTs: typeof msg.mediaTs === "number" ? msg.mediaTs : null,
      mediaEl: mediaEl || el,
      covered: false,
      offscreen: false
    };
    if (!el && msg.rect) {
      cur.docRect = {
        x: msg.rect.x + window.scrollX, y: msg.rect.y + window.scrollY,
        width: msg.rect.width, height: msg.rect.height
      };
    }
    if (!el && msg.point) {
      cur.docRect = {
        x: msg.point.x + window.scrollX, y: msg.point.y + window.scrollY,
        width: 0, height: 0
      };
    }
    if (fadeTimer) { clearTimeout(fadeTimer); fadeTimer = null; }
    stage.classList.remove("out");
    stage.classList.add("on");
    render(true);
    armObservers();
    if (expiryTimer) clearTimeout(expiryTimer);
    expiryTimer = setTimeout(function () { clearCurrent("expired"); }, Math.max(0, cur.until - Date.now()));
    if (cur.mediaTs !== null) {
      mediaTimer = setInterval(function () { if (cur) render(false); }, 300);
    }
    // The observers only watch the target and its parent; an SPA swap that
    // removes a higher ancestor fires neither. A slow liveness watchdog
    // bounds how long a ring can sit frozen over unrelated content.
    if (watchdogTimer) clearInterval(watchdogTimer);
    watchdogTimer = setInterval(function () {
      if (cur && cur.el && !cur.el.isConnected) onTargetGone();
    }, 1000);
    if (cur.label || cur.generic) {
      var where = cur.descriptor && cur.descriptor.name ? cur.descriptor.name : "an element";
      var text = "Agent is pointing at " + where + (cur.label ? ": " + cur.label : "");
      if (cur.offscreen) text += " (off-screen" + (cur.direction ? ", " + cur.direction : "") + ")";
      announce(text);
    }
    return { applied: true };
  }

  // ---- Events ---------------------------------------------------------------------------
  window.addEventListener("scroll", scheduleRender, true);
  window.addEventListener("resize", scheduleRender);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", scheduleRender);
    window.visualViewport.addEventListener("scroll", scheduleRender);
  }
  window.addEventListener("keydown", function (e) {
    // Esc clears locally and immediately; the page still receives the key.
    if (e.key === "Escape" && cur) clearCurrent("esc");
  }, true);
  function onUserScrollGesture() {
    if (!cur || cur.mode !== "spotlight") return;
    var now = Date.now();
    if (now - lastScrollSent < 1000) return;
    lastScrollSent = now;
    send({ type: "SPOTLIGHT_USER_SCROLL" });
  }
  window.addEventListener("wheel", onUserScrollGesture, { capture: true, passive: true });
  window.addEventListener("touchmove", onUserScrollGesture, { capture: true, passive: true });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && cur && Date.now() > cur.until) {
      clearCurrent("expired", { immediate: true });
    }
  });

  // Worker port: if the worker dies for good, highlights must not outlive the
  // session. A short grace period covers routine MV3 worker restarts.
  var port = null;
  var portGrace = null;
  function connectPort() {
    try {
      port = chrome.runtime.connect({ name: "spotlight" });
      port.onDisconnect.addListener(function () {
        port = null;
        portGrace = setTimeout(function () {
          if (!port && cur) clearCurrent("session_end", { immediate: true });
        }, 4000);
        setTimeout(connectPort, 500);
      });
      if (portGrace) { clearTimeout(portGrace); portGrace = null; }
    } catch (e) {}
  }
  connectPort();

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (!msg || msg.type !== "SPOTLIGHT") return;
    switch (msg.op) {
      case "draw": {
        var res = draw(msg);
        if (sendResponse) sendResponse({
          applied: res.applied, reason: res.reason || null,
          covered: cur ? cur.covered : false, offscreen: cur ? cur.offscreen : false
        });
        break;
      }
      case "clear":
        clearCurrent(msg.reason || "cleared", { fromWorker: true, immediate: Boolean(msg.immediate) });
        break;
      case "session_end":
        if (cur) clearCurrent("session_end", { fromWorker: true, immediate: true });
        if (typeof msg.session === "number") session = msg.session;
        break;
      case "settings":
        if (msg.enabled === false) {
          if (cur) clearCurrent("settings", { fromWorker: true });
        } else if (msg.neverDim === true && cur && cur.mode === "spotlight") {
          cur.mode = "ring";
          render(false);
        }
        break;
    }
  });

  window.__eabSpotlight = {
    hideForCapture: function () {
      if (hideDepth === 0) {
        hideGeneration = generation;
        // A capture that never completes must not strand the overlay hidden.
        if (hideWatchdog) clearTimeout(hideWatchdog);
        hideWatchdog = setTimeout(function () {
          hideWatchdog = null;
          hideDepth = 0;
          host.style.visibility = "";
        }, 2000);
      }
      hideDepth += 1;
      host.style.visibility = "hidden";
    },
    restoreAfterCapture: function () {
      hideDepth = Math.max(0, hideDepth - 1);
      if (hideDepth > 0) return;
      if (hideWatchdog) { clearTimeout(hideWatchdog); hideWatchdog = null; }
      host.style.visibility = "";
      // The deadline re-check applies only to the highlight that was hidden;
      // one drawn meanwhile has its own timer.
      if (cur && cur.generation === hideGeneration && Date.now() > cur.until) {
        clearCurrent("expired", { immediate: true });
      }
    },
    // Takeover exemption support for the banner's input signal: interaction
    // inside the highlighted rect, or typing into the highlighted element,
    // is the user doing what the agent pointed at - not a takeover.
    interactionInside: function (x, y) {
      if (!cur) return false;
      var r = currentRect();
      if (!r) return false;
      return x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height;
    },
    targetFocused: function () {
      if (!cur || !cur.el) return false;
      var a = document.activeElement;
      return Boolean(a) && (a === cur.el || Boolean(cur.el.contains && cur.el.contains(a)));
    },
    _state: function () {
      return cur ? { showing: true, mode: cur.mode, generation: cur.generation } : { showing: false };
    }
  };
})();

