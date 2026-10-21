# Privacy Policy for Agent Browser Bridge

**Last updated:** September 7, 2026

Agent Browser Bridge (formerly Edge Agent Bridge, "the Extension") is an open-source browser automation bridge for developers running local AI agents. This policy explains how information is handled by the Extension.

---

### 1. Zero Data Collection

- The Extension does **not** collect, store, track, or transmit any personally identifiable information (PII), browsing history, web credentials, cookies, or telemetry.
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
- Optionally opens new tabs in a tab group named by the agent (default "Agent") so you can tell them apart from your own.

---

### 4. Purpose of Requested Permissions

- **`debugger`**: dispatch native Chrome DevTools Protocol input events, capture screenshots, set files on file inputs, and receive console, network and dialog events for the tabs the agent works in.
- **`<all_urls>` & `tabs` / `activeTab`**: locate and interact with the tabs the agent is asked to automate.
- **`scripting`**: read page structure for snapshots and render the visual overlay.
- **`storage`**: persist the last log line, the optional pairing token, and per-tab console buffers inside your local browser profile.
- **`alarms`**: periodically re-check the connection to the local daemon.
- **`tabGroups`**: place agent-opened tabs in a named group.

---

### 5. Open Source Transparency

The complete source code of Agent Browser Bridge is open source under the MIT License and available for independent public audit:

- **Repository**: https://github.com/shanewas/agent-browser-bridge

---

### 6. Contact

If you have any questions regarding this Privacy Policy, please open an issue on GitHub or contact:

- **Author**: Shanewas Ahmed
- **Email**: shanewasahmed@gmail.com
- **GitHub Issues**: https://github.com/shanewas/agent-browser-bridge/issues
