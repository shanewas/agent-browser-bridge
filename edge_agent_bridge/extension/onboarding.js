// Edge Agent Bridge onboarding: probe daemon status, provide copyable commands.

async function fetchStatus() {
  try {
    const res = await fetch("http://127.0.0.1:18999/status", { cache: "no-store" });
    if (res.ok) return await res.json();
  } catch (e) {}
  return null;
}

function updateUI(st) {
  const badge = document.getElementById("statusBadge");
  const title = document.getElementById("statusTitle");
  const desc = document.getElementById("statusDesc");
  const successPanel = document.getElementById("successPanel");
  const outdatedPanel = document.getElementById("outdatedPanel");
  const instructionsPanel = document.getElementById("instructionsPanel");

  const extVersion = chrome.runtime.getManifest ? chrome.runtime.getManifest().version : "2.6.0";

  if (st && st.extension_connected) {
    badge.className = "badge connected";
    badge.textContent = "Connected";
    title.textContent = "Daemon Connected";
    desc.textContent = `daemon ${st.daemon_version || "?"} · extension ${extVersion}`;
    successPanel.style.display = "block";
    outdatedPanel.style.display = "none";
    instructionsPanel.style.display = "none";
  } else if (st && st.extension_outdated) {
    badge.className = "badge outdated";
    badge.textContent = "Mismatch";
    title.textContent = "Version Mismatch";
    desc.textContent = `daemon ${st.daemon_version || "?"} vs extension ${extVersion}`;
    const outdatedDesc = document.getElementById("outdatedDesc");
    if (outdatedDesc) {
      outdatedDesc.textContent = `The extension is version ${extVersion} but the daemon is version ${st.daemon_version || "?"}. Please upgrade the Python package.`;
    }
    successPanel.style.display = "none";
    outdatedPanel.style.display = "block";
    instructionsPanel.style.display = "block";
  } else if (st) {
    badge.className = "badge checking";
    badge.textContent = "Waiting";
    title.textContent = "Daemon Active";
    desc.textContent = "Daemon is up on :18999; waiting for extension connection…";
    successPanel.style.display = "none";
    outdatedPanel.style.display = "none";
    instructionsPanel.style.display = "block";
  } else {
    badge.className = "badge disconnected";
    badge.textContent = "Offline";
    title.textContent = "Daemon Offline";
    desc.textContent = "127.0.0.1:18999 is unreachable. Follow the steps below to start it.";
    successPanel.style.display = "none";
    outdatedPanel.style.display = "none";
    instructionsPanel.style.display = "block";
  }
}

async function copyText(text, btn) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (e) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand("copy");
      ta.remove();
    } catch (e2) {}
  }
  if (copied && btn) {
    const orig = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }
}

document.querySelectorAll(".copy-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const text = btn.getAttribute("data-copy");
    if (text) copyText(text, btn);
  });
});

async function poll() {
  const st = await fetchStatus();
  updateUI(st);
}

poll();
setInterval(poll, 1500);
