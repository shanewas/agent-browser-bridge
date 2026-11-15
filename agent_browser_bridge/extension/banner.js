// Agent Browser Bridge - presence pill ("banner"), top frame only.
// Plain content script (no modules): assigns window.__eabBanner in load order.
// Draws the states the service worker sends ({type:"BANNER", ...}) inside a closed
// shadow root, reports takeover / resume / hide clicks back as runtime messages, and
// reports genuine user page input as a boolean signal ({type:"USER_INPUT"}) - never a
// key value, a target, or text. The worker decides what that signal means; the agent's
// own CDP input also arrives trusted, so the in-flight guard lives worker-side.
// A "working" pill stays until the worker sends paused/hidden - visibility follows
// the agent's real activity, never a local display timer. While working, a subtle
// animated gradient frame also rings the viewport (its own host, below), so the tab
// the agent is driving is recognisable at a glance.
(function () {
  if (window.__eabBanner) return;

  var HOST_ID = "__eab_banner_host";
  var FRAME_HOST_ID = "__eab_frame_host";
  var PALETTE = ["#5b6abf", "#3f7f6a", "#8a6d3b", "#7a4f8c", "#4f6d8c", "#8c4f5e", "#5e7a3a", "#6a5acd"];
  var state = "hidden"; // hidden | working | paused
  var agentName = "Agent";
  var verb = null;
  var shownAt = 0;
  var minUntil = 0;
  var hideTimer = null;
  var tooltipTimer = null;
  var lastInputSent = 0;
  var tookOverOnce = false;

  try { tookOverOnce = Boolean(localStorage.getItem("__eabTookOver")); } catch (e) {}

  function cleanLabel(s) {
    var t = String(s == null ? "" : s).replace(/[\u0000-\u001f\u007f]/g, "").trim();
    if (!t) t = "Agent";
    return t.slice(0, 24);
  }

  function colorFor(name) {
    var h = 0;
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  // ---- DOM -------------------------------------------------------------------
  var host = document.createElement("div");
  host.id = HOST_ID;
  host.style.cssText = "position:fixed!important;top:12px!important;left:50%!important;transform:translateX(-50%)!important;z-index:2147483647!important;pointer-events:none!important;margin:0!important;padding:0!important;border:0!important;width:auto!important;height:auto!important;";

  var shadow = host.attachShadow({ mode: "closed" });
  var style = document.createElement("style");
  style.textContent = [
    ".wrap{display:flex;flex-direction:column;align-items:center;gap:6px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;opacity:0;transform:translateY(-6px);transition:opacity .15s ease-out,transform .15s ease-out;pointer-events:none}",
    ".wrap.on{opacity:1;transform:translateY(0);pointer-events:none}",
    ".pill{display:flex;align-items:center;gap:8px;height:38px;padding:0 12px;border-radius:999px;background:rgba(17,17,17,.92);backdrop-filter:blur(6px);color:#fff;font-size:13px;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.35),0 1px 4px rgba(0,0,0,.25);pointer-events:auto;cursor:pointer;white-space:nowrap;user-select:none;-webkit-user-select:none}",
    ".pill.paused{background:#f5f0e6;color:#1c1a15;cursor:default}",
    ".avatar{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex:none}",
    ".dots i{font-style:normal;animation:blink 1.2s infinite}",
    ".dots i:nth-child(2){animation-delay:.4s}.dots i:nth-child(3){animation-delay:.8s}",
    "@keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}",
    ".verb{font-weight:400;opacity:.75}",
    ".x{background:none;border:0;color:rgba(255,255,255,.6);font-size:15px;line-height:1;cursor:pointer;padding:2px 3px;flex:none}",
    ".x:hover{color:#fff}",
    ".pill.paused .x{color:rgba(28,26,21,.55)}.pill.paused .x:hover{color:#1c1a15}",
    ".resume{background:#b45309;color:#fff;border:0;border-radius:999px;font-size:12px;font-weight:600;padding:5px 11px;cursor:pointer;flex:none}",
    ".resume:hover{background:#92400e}",
    ".tip{height:30px;display:flex;align-items:center;padding:0 11px;border-radius:999px;background:rgba(17,17,17,.92);color:#fff;font-size:12px;font-weight:500;box-shadow:0 4px 16px rgba(0,0,0,.35);opacity:0;transition:opacity .12s ease-out;pointer-events:none;white-space:nowrap}",
    ".tip.on{opacity:1}",
    ".wrap.dot .pill{padding:0 8px}.wrap.dot .label,.wrap.dot .verb,.wrap.dot .x,.wrap.dot .resume,.wrap.dot .tip{display:none}",
    "@media (prefers-reduced-motion: reduce){.wrap{transition:none}.dots i{animation:none;opacity:1}}"
  ].join("\n");
  shadow.appendChild(style);

  var wrap = document.createElement("div");
  wrap.className = "wrap";
  var pill = document.createElement("div");
  pill.className = "pill";
  var avatar = document.createElement("span");
  avatar.className = "avatar";
  var label = document.createElement("span");
  label.className = "label";
  var verbEl = document.createElement("span");
  verbEl.className = "verb";
  var resumeBtn = document.createElement("button");
  resumeBtn.className = "resume";
  resumeBtn.textContent = "Done";
  resumeBtn.style.display = "none";
  var closeBtn = document.createElement("button");
  closeBtn.className = "x";
  closeBtn.textContent = "\u00d7";
  closeBtn.setAttribute("aria-label", "Hide");
  pill.appendChild(avatar);
  pill.appendChild(label);
  pill.appendChild(verbEl);
  pill.appendChild(resumeBtn);
  pill.appendChild(closeBtn);
  var tip = document.createElement("div");
  tip.className = "tip";
  tip.textContent = "Click to take control";
  wrap.appendChild(pill);
  wrap.appendChild(tip);
  shadow.appendChild(wrap);

  // ---- Viewport frame ------------------------------------------------------------
  // Own host: the pill host is transformed (translateX(-50%)), which would make a
  // fixed-position child resolve against the pill box instead of the viewport.
  // pointer-events:none throughout, so the frame never intercepts a click or a
  // selection, and fixed positioning keeps it out of document flow.
  var frameHost = document.createElement("div");
  frameHost.id = FRAME_HOST_ID;
  frameHost.style.cssText = "position:fixed!important;inset:0!important;z-index:2147483646!important;pointer-events:none!important;margin:0!important;padding:0!important;border:0!important;";
  var frameShadow = frameHost.attachShadow({ mode: "closed" });
  var frameStyle = document.createElement("style");
  frameStyle.textContent = [
    ".frame{position:fixed;inset:0;pointer-events:none;opacity:0;transition:opacity .25s ease-out;border:2px solid transparent;background:linear-gradient(120deg,rgba(99,102,241,.50),rgba(34,211,238,.40),rgba(167,139,250,.50),rgba(99,102,241,.50)) border-box;background-size:300% 300%;-webkit-mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);mask-composite:exclude;animation:eabframeflow 6s ease-in-out infinite}",
    ".frame.on{opacity:1}",
    "@keyframes eabframeflow{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}",
    "@media (prefers-reduced-motion: reduce){.frame{animation:none}}"
  ].join("\n");
  frameShadow.appendChild(frameStyle);
  var frame = document.createElement("div");
  frame.className = "frame";
  frameShadow.appendChild(frame);

  function syncFrame() {
    frame.classList.toggle("on", state === "working");
  }

  var printStyle = document.createElement("style");
  printStyle.id = "__eab_banner_print";
  printStyle.textContent = "@media print{#" + HOST_ID + ",#" + FRAME_HOST_ID + "{display:none!important}}";

  function mount() {
    if (!document.documentElement) return;
    if (!host.isConnected) document.documentElement.appendChild(host);
    if (!frameHost.isConnected) document.documentElement.appendChild(frameHost);
    if (!printStyle.isConnected && document.head) document.head.appendChild(printStyle);
  }
  mount();
  // A hostile or careless page can remove the host; put it back. The badge in the
  // browser toolbar stays the ground truth - this only covers ordinary breakage.
  var mo = new MutationObserver(function () {
    if (!host.isConnected || !frameHost.isConnected) mount();
  });
  function armObserver() {
    if (document.documentElement) mo.observe(document.documentElement, { childList: true });
  }
  armObserver();

  // ---- Rendering ---------------------------------------------------------------
  function render() {
    agentName = cleanLabel(agentName);
    avatar.textContent = agentName.charAt(0).toUpperCase();
    avatar.style.background = colorFor(agentName);
    if (state === "paused") {
      pill.classList.add("paused");
      label.textContent = "You\u2019re in control";
      verbEl.textContent = "";
      resumeBtn.style.display = "";
      tip.classList.remove("on");
    } else {
      pill.classList.remove("paused");
      label.textContent = agentName + " Working";
      var dots = document.createElement("span");
      dots.className = "dots";
      dots.innerHTML = "<i>.</i><i>.</i><i>.</i>";
      label.appendChild(dots);
      verbEl.textContent = verb ? " \u00b7 " + verb : "";
      resumeBtn.style.display = "none";
    }
    wrap.classList.toggle("dot", Boolean(document.fullscreenElement));
    wrap.classList.add("on");
    syncFrame();
  }

  function show(nextState, msg) {
    if (msg && msg.agent) agentName = msg.agent;
    verb = (msg && msg.verb) || null;
    var wasHidden = state === "hidden";
    state = nextState;
    if (wasHidden) shownAt = Date.now();
    if (msg && msg.mutating) minUntil = Math.max(minUntil, shownAt + 800);
    // Fresh activity cancels a deferred hide (see hide()): the pill now stays
    // for as long as the worker keeps reporting state.
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    render();
    if (state === "working" && msg && msg.teachTooltip) {
      showTip();
      setTimeout(function () { tip.classList.remove("on"); }, 2500);
    }
  }

  function hide(force) {
    // A mutating action keeps the pill up for its minimum display time even if
    // the worker's hide lands sooner; a local hide (the x button) is immediate.
    if (!force && minUntil && Date.now() < minUntil) {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(function () { hide(true); }, minUntil - Date.now());
      return;
    }
    state = "hidden";
    minUntil = 0;
    wrap.classList.remove("on");
    tip.classList.remove("on");
    syncFrame();
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  }

  function showTip() {
    tip.classList.add("on");
  }
  function hideTipSoon() {
    setTimeout(function () { tip.classList.remove("on"); }, 60);
  }

  // ---- User actions --------------------------------------------------------------
  function send(msg) {
    try {
      var p = chrome.runtime.sendMessage(msg);
      if (p && p.catch) p.catch(function () {});
    } catch (e) {}
  }

  pill.addEventListener("mouseenter", function () {
    if (state !== "working") return;
    tooltipTimer = setTimeout(showTip, 150);
  });
  pill.addEventListener("mouseleave", function () {
    if (tooltipTimer) { clearTimeout(tooltipTimer); tooltipTimer = null; }
    hideTipSoon();
  });

  pill.addEventListener("click", function (e) {
    if (!e.isTrusted) return;
    if (e.target === closeBtn || e.target === resumeBtn) return;
    if (state === "working") send({ type: "TAKEOVER" });
  });
  closeBtn.addEventListener("click", function (e) {
    if (!e.isTrusted) return;
    e.stopPropagation();
    hide(true);
    send({ type: "BANNER_HIDE_SESSION" });
  });
  resumeBtn.addEventListener("click", function (e) {
    if (!e.isTrusted) return;
    e.stopPropagation();
    send({ type: "RESUME" });
  });

  // Genuine user input on the page: boolean signal only. Trusted CDP input from the
  // agent also lands here - the worker applies the in-flight guard before acting.
  function onUserInput(e) {
    if (!e.isTrusted) return;
    if (state === "hidden") return;
    var now = Date.now();
    if (now - lastInputSent < 1000) return;
    lastInputSent = now;
    send({ type: "USER_INPUT" });
  }
  // A press or a key yields; moving the mouse and scrolling are just watching,
  // so they get no listener at all. pointerdown covers mouse/touch/pen and
  // mousedown the plain mouse press - the 1 s throttle folds the pair into one
  // signal when both fire for the same click.
  window.addEventListener("pointerdown", onUserInput, true);
  window.addEventListener("mousedown", onUserInput, true);
  window.addEventListener("keydown", onUserInput, true);

  document.addEventListener("fullscreenchange", function () {
    if (state !== "hidden") render();
  });

  // ---- Worker messages -------------------------------------------------------------
  chrome.runtime.onMessage.addListener(function (msg) {
    if (!msg || msg.type !== "BANNER") return;
    if (msg.state === "working") show("working", msg);
    else if (msg.state === "paused") show("paused", msg);
    else if (msg.state === "takenover") {
      // The worker confirms a takeover this tab asked for.
      try { localStorage.setItem("__eabTookOver", "1"); } catch (e) {}
      tookOverOnce = true;
      show("paused", msg);
    } else if (msg.state === "hidden") hide();
  });

  // Ask the worker for the current state on (re)load, so a paused tab that the user
  // reloads comes back showing the paused pill.
  send({ type: "BANNER_QUERY" });

  var fadeTimer = null;
  window.__eabBanner = {
    hideForCapture: function () { host.style.visibility = "hidden"; frameHost.style.visibility = "hidden"; },
    restoreAfterCapture: function () { host.style.visibility = ""; frameHost.style.visibility = ""; },
    // An agent action is heading for the point under the pill: get out of its way.
    fadeForPoint: function (x, y) {
      var r = host.getBoundingClientRect();
      if (x < r.left - 4 || x > r.right + 4 || y < r.top - 4 || y > r.bottom + 4) return;
      host.style.opacity = "0.25";
      pill.style.pointerEvents = "none";
      if (fadeTimer) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(function () {
        host.style.opacity = "";
        pill.style.pointerEvents = "";
      }, 700);
    },
    _state: function () { return state; }
  };
})();
