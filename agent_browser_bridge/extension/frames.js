// Edge Agent Bridge - frame offset handshake, service-worker side.

import * as page from "./page.js";

function fail(code, error, extra = {}) {
  return { success: false, code, error, ...extra };
}

// Refs carry their frame: "f31e2" lives in frame 31, "e2" in the main frame.
export function frameOfRef(target) {
  const m = typeof target === "string" ? target.match(/^f(\d+)e\d+$/) : null;
  return m ? Number(m[1]) : undefined;
}

// Probe reachable subframes in the given tab.
export async function probeSubframeIds(tabId) {
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => window === window.top,
    });
    return probe.filter(r => r && r.result === false).map(r => r.frameId);
  } catch (e) {
    return [];
  }
}

// Snapshot the main frame and (unless frames:false) every reachable subframe, then nest each
// subframe's lines under the iframe line its handshake path points at.
// execInTab arrives as an argument, not an import: actions.js imports this module, so importing
// it back would close the cycle.
export async function snapshotTab(tabId, mode, withFrames, maxNodes = 400, execInTab) {
  const gen = Date.now();
  const main = await execInTab(tabId, page.pageSnapshot, [{ mode, gen, frameLabel: "", maxNodes }]);
  if (!main || main.success === false) return main || fail("inject_failed", "Snapshot returned nothing");
  main.frameId = 0;
  let subs = [];
  if (withFrames && main.iframes && main.iframes.length > 0) {
    const frameIds = await probeSubframeIds(tabId);
    const results = await Promise.all(frameIds.map(fid =>
      execInTab(tabId, page.pageSnapshot, [{ mode, gen, frameLabel: `f${fid}`, maxNodes }], "ISOLATED", fid)
        .then(r => (r && r.success ? { ...r, frameId: fid } : null))
        .catch(() => null)));
    subs = results.filter(Boolean);
  }

  const samePath = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
  const placed = new Set();
  const padUnit = mode === "compact" ? "" : "  ";
  function compose(snap) {
    const lines = snap.lines.slice();
    if (!withFrames) return lines;
    const marks = snap.iframes.slice().sort((a, b) => b.line - a.line);
    for (const m of marks) {
      const childPath = snap.path.concat(m.index);
      const child = subs.find(s => s.placed && samePath(s.path, childPath));
      if (!child) {
        lines[m.line] += " [unreachable]";
        continue;
      }
      placed.add(child.frameId);
      lines[m.line] += ` [f${child.frameId}]`;
      const sub = compose(child).map(l => padUnit.repeat(m.depth + 1) + l);
      lines.splice(m.line + 1, 0, ...sub);
    }
    return lines;
  }
  const lines = compose(main);
  for (const s of subs) {
    if (placed.has(s.frameId)) continue;
    lines.push(`- iframe "${s.title || s.url}" [f${s.frameId}] (position unknown)`);
    lines.push(...s.lines.map(l => padUnit + l));
  }
  const nodes = main.nodes.concat(...subs.map(s => s.nodes));
  const refs = main.refs + subs.reduce((n, s) => n + s.refs, 0);
  const truncated = (main.truncated || 0) + subs.reduce((n, s) => n + (s.truncated || 0), 0);
  const total = (main.total || 0) + subs.reduce((n, s) => n + (s.total || 0), 0);
  return { success: true, text: [`page "${main.title}" url=${main.url}`, ...lines].join("\n"), refs, nodes, url: main.url, title: main.title, truncated, total };
}
