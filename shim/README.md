# edge-agent-bridge is now agent-browser-bridge

Edge Agent Bridge was renamed to Agent Browser Bridge in 3.0.0. This package only depends on
`agent-browser-bridge` and keeps the old entry points working:

- `edge-bridge` and `edge` run the same CLI as `agent-bridge`
- `python -m edge_agent_bridge` runs the same CLI, so existing MCP registrations keep working
- `from edge_agent_bridge import Edge` re-exports the `agent_browser_bridge` API

Upgrade with `pip install -U agent-browser-bridge`. New installs: `pip install agent-browser-bridge`.
Project: https://github.com/shanewas/agent-browser-bridge
