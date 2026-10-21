// Edge Agent Bridge - content script, runs in every frame.
// 1. Frame offset handshake: a child frame posts {__eab:"whoami"} to its parent; the parent
//    finds the <iframe> whose contentWindow is the sender (light DOM and shadow roots) and
//    replies with that iframe's content-box origin in top-frame CSS pixels plus the child's
//    index path. Injected page functions reach this through window.__eabFrameInfo (shared
//    isolated world).
// 2. Top-level frames send a keepalive every 10 s so the service worker wakes and reconnects
//    after a daemon restart.
(function () {
  if (window.__eabContentLoaded) return;
  window.__eabContentLoaded = true;

  function allIframes(root) {
    const out = [];
    function visit(node) {
      if (node.shadowRoot) {
        for (const c of node.shadowRoot.children) visit(c);
      }
      if (node.tagName === "IFRAME" || node.tagName === "FRAME") out.push(node);
      for (const c of node.children) visit(c);
    }
    if (root.documentElement) visit(root.documentElement);
    return out;
  }
  window.__eabAllIframes = () => allIframes(document);

  let cached = null;
  let cachedAt = 0;
  const NONE = { x: 0, y: 0, path: [], ok: false };

  function ownInfo() {
    if (window === window.top) return Promise.resolve({ x: 0, y: 0, path: [], ok: true });
    if (cached && Date.now() - cachedAt < 100) return Promise.resolve(cached);
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        window.removeEventListener("message", onReply);
        resolve(NONE);
      }, 300);
      function onReply(e) {
        const d = e.data;
        if (!d || d.__eab !== "offset" || e.source !== window.parent) return;
        clearTimeout(timer);
        window.removeEventListener("message", onReply);
        cached = { x: d.x, y: d.y, path: d.path || [], ok: d.ok !== false };
        cachedAt = Date.now();
        resolve(cached);
      }
      window.addEventListener("message", onReply);
      try {
        window.parent.postMessage({ __eab: "whoami" }, "*");
      } catch (e) {
        clearTimeout(timer);
        window.removeEventListener("message", onReply);
        resolve(NONE);
      }
    });
  }
  window.__eabFrameInfo = ownInfo;

  window.addEventListener("message", (event) => {
    const d = event.data;
    if (!d || d.__eab !== "whoami") return;
    const frames = allIframes(document);
    const index = frames.findIndex(f => f.contentWindow === event.source);
    if (index < 0) return;
    const owner = frames[index];
    ownInfo().then((info) => {
      const r = owner.getBoundingClientRect();
      try {
        event.source.postMessage({
          __eab: "offset",
          x: info.x + r.left + owner.clientLeft,
          y: info.y + r.top + owner.clientTop,
          path: info.path.concat(index),
          ok: info.ok
        }, "*");
      } catch (e) {}
    });
  });

  if (window !== window.top) return;

  function pingWorker() {
    try {
      if (chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "KEEPALIVE" }).catch(() => {});
      }
    } catch (e) {}
  }

  pingWorker();
  setInterval(pingWorker, 10000);
})();
