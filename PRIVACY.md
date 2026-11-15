# Privacy Policy for Agent Browser Bridge

**Last updated:** November 15, 2026

Agent Browser Bridge (formerly Edge Agent Bridge, "the Extension") is an open-source browser automation bridge for developers running local AI agents. This policy explains how information is handled by the Extension.

---

### 1. Zero Data Collection

- The Extension does **not** collect, store, track, or transmit any personally identifiable information (PII), web credentials, cookies, or telemetry. Browsing history is never collected or sent anywhere; it is read or deleted only when a local agent explicitly runs a history command, and the results stay on your machine (see Sections 3 and 4).
- No analytics or third-party tracking libraries are included in the Extension.

---

### 2. Localhost-Only Communication

- All communication occurs strictly on your local machine (`127.0.0.1:18999`) between the browser extension and the locally executed Python bridge daemon (`agent-bridge`).
- No data is ever transmitted to external servers, cloud services, or third parties.
- The local bridge daemon binds only to `127.0.0.1`, rejects requests whose `Host` header is not the local address, and requires a token stored in your user profile for every command it forwards to the Extension.

---

### 3. What the Extension does on a page when asked

Every action below happens only in response to a command from your local agent through the daemon; the Extension never acts on its own.

- Reads the structure of the page (roles, labels, current form values) to produce a snapshot for the agent. Password field values are masked in that snapshot.
- Dispatches mouse and keyboard input, selects options, and sets files on file inputs the agent names.
- Captures a screenshot of the tab when the agent asks for one. The image goes to the local agent only.
- Records recent console messages, uncaught errors, and native dialogs of the tab in memory (and in the browser's session storage, which is cleared when Edge closes) so the agent can read them.
- Draws a short-lived highlight ring and cursor overlay on the page so you can see what the agent is about to act on. This can be turned off per command.
- Shows a small pill at the top of the page naming the agent that is working in the tab, and watches for your own mouse and keyboard input on the page so it can pause the agent when you take over. The pill reports state only; the hands-on signal sent back is a boolean, with no key values or page content. Which tabs are paused, and where you have hidden the pill, is kept in the browser's session storage and cleared when the browser closes.
- Runs JavaScript in the page when the agent uses the `eval` action. This is arbitrary page JavaScript executed with your session, the same access the page itself has; it exists on purpose for cases the built-in actions do not cover, and the result goes to the local agent only.
- Searches your browsing history, or deletes a single history entry, when the agent uses the `history search` / `history delete` actions. Matching entries are returned to the local agent on your machine; nothing is read until the command is given, and nothing leaves the machine.
- Optionally opens new tabs in a tab group named by the agent (default "Agent") so you can tell them apart from your own.

---

### 4. Purpose of Requested Permissions

- **`debugger`**: dispatch native Chrome DevTools Protocol input events, capture screenshots, set files on file inputs, and receive console, network and dialog events for the tabs the agent works in.
- **`<all_urls>` & `tabs` / `activeTab`**: locate and interact with the tabs the agent is asked to automate.
- **`scripting`**: read page structure for snapshots and render the visual overlay.
- **`storage`**: persist the last log line, the optional pairing token, and per-tab console buffers inside your local browser profile, and keep per-tab pause and hidden-pill state in session storage (cleared when the browser closes).
- **`alarms`**: periodically re-check the connection to the local daemon.
- **`tabGroups`**: place agent-opened tabs in a named group.
- **`history`**: answer the `history search` and `history delete` actions described above. History is read or deleted only when the local agent issues one of those commands, and the results stay on your machine.

---

### 5. The Desktop Half (local daemon)

The same daemon also offers desktop actions on Windows, driven by the same local agent and the same token. They follow the same rule as everything above: nothing leaves your machine.

- Captures screenshots of your displays and moves the mouse and injects keyboard input at OS level when the agent issues a desktop action. A small on-screen strip shows while a desktop action runs.
- `desktop record` is opt-in: it starts only on an explicit `record start` command. While it runs it records your clicks and keystrokes — passwords included — plus a screenshot per click, and writes them to files (`events.jsonl` and a generated `SKILL.md`) in the bridge's data directory on your machine. A red strip stays on screen for the whole recording. Nothing is uploaded; delete the recording folder when it has served its purpose, and scrub it before sharing.

---

### 6. Open Source Transparency

The complete source code of Agent Browser Bridge is open source under the MIT License and available for independent public audit:

- **Repository**: https://github.com/shanewas/agent-browser-bridge

---

### 7. Contact

If you have any questions regarding this Privacy Policy, please open an issue on GitHub or contact:

- **Author**: Shanewas Ahmed
- **Email**: shanewasahmed@gmail.com
- **GitHub Issues**: https://github.com/shanewas/agent-browser-bridge/issues
