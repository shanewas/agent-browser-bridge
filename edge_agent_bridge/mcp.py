"""Model Context Protocol (MCP) server for Edge Agent Bridge.

Implements MCP stdio transport (spec 2025-11-25) exposing 34 browser automation tools.
"""
import json
import logging
import sys
from typing import Any

from . import __version__
from .client import Edge

SUPPORTED_PROTOCOL_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}

COMMON_TOOL_PROPERTIES = {
    "tabId": {"type": "integer", "description": "Optional tab ID to target. If omitted, uses pinned or active tab."},
    "highlight": {"type": "boolean", "description": "Whether to draw visual highlight ring and cursor. Default true."},
    "fallback": {"type": "boolean", "description": "Enable ref→text→scan→coords fallback ladder. Default true."},
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "edge_status",
        "description": "Get status of the daemon, extension connection, and open tabs.",
        "inputSchema": {"type": "object", "properties": {**COMMON_TOOL_PROPERTIES}},
    },
    {
        "name": "edge_tabs",
        "description": "List all open tabs across all Edge windows.",
        "inputSchema": {"type": "object", "properties": {**COMMON_TOOL_PROPERTIES}},
    },
    {
        "name": "edge_tab_switch",
        "description": "Switch active tab and re-pin the session to it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tabId": {"type": "integer", "description": "Tab ID to switch to."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["tabId"],
        },
    },
    {
        "name": "edge_tab_new",
        "description": "Open a new tab with the given URL and re-pin session to it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to. Default about:blank.", "default": "about:blank"},
                "group": {"type": "string", "description": "Optional tab group name to join."},
                "window": {"type": "boolean", "description": "Open in a new window. Default false."},
                "active": {"type": "boolean", "description": "Activate the new tab. Default true."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_tab_close",
        "description": "Close a specific tab. Requires tabId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tabId": {"type": "integer", "description": "ID of tab to close."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["tabId"],
        },
    },
    {
        "name": "edge_navigate",
        "description": "Navigate active tab to a URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target URL."},
                "wait": {"type": "string", "enum": ["load", "none"], "default": "load"},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["url"],
        },
    },
    {
        "name": "edge_back",
        "description": "Navigate backwards in tab history.",
        "inputSchema": {"type": "object", "properties": {**COMMON_TOOL_PROPERTIES}},
    },
    {
        "name": "edge_forward",
        "description": "Navigate forward in tab history.",
        "inputSchema": {"type": "object", "properties": {**COMMON_TOOL_PROPERTIES}},
    },
    {
        "name": "edge_reload",
        "description": "Reload the current page.",
        "inputSchema": {"type": "object", "properties": {**COMMON_TOOL_PROPERTIES}},
    },
    {
        "name": "edge_snapshot",
        "description": "Take semantic accessibility snapshot returning element roles, names, and reference IDs (e.g. e1, f1e2).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["interactive", "full", "compact"], "default": "interactive"},
                "frames": {"type": "boolean", "default": True},
                "maxNodes": {"type": "integer", "default": 400, "description": "Truncate after N nodes (0 = unlimited)."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_click",
        "description": "Click an element by ref (e.g. 'e1'), CSS selector, text, or coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Reference ID from snapshot (e.g. 'e1')."},
                "target": {"type": "string", "description": "Target text, CSS selector, or ref."},
                "selector": {"type": "string", "description": "CSS selector."},
                "text": {"type": "string", "description": "Visible element text."},
                "x": {"type": "number", "description": "X coordinate in viewport pixels."},
                "y": {"type": "number", "description": "Y coordinate in viewport pixels."},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_dblclick",
        "description": "Double-click an element by ref, selector, text, or coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "target": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_rightclick",
        "description": "Right-click an element by ref, selector, text, or coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "target": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_hover",
        "description": "Move cursor over an element by ref, selector, text, or coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "target": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_drag",
        "description": "Drag from one target element/coordinate to another.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"description": "Source target or {x, y} coordinate."},
                "to": {"description": "Destination target or {x, y} coordinate."},
                "steps": {"type": "integer", "default": 10},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["from", "to"],
        },
    },
    {
        "name": "edge_fill",
        "description": "Set text value of an input or textarea element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Target ref (e.g. 'e1')."},
                "target": {"type": "string", "description": "Target text, selector, or ref."},
                "text": {"type": "string", "description": "Text to fill."},
                "append": {"type": "boolean", "default": False},
                "clear": {"type": "boolean", "default": True},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["text"],
        },
    },
    {
        "name": "edge_type",
        "description": "Type text character-by-character with realistic key events.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
                "delay": {"type": "integer", "description": "Delay between keystrokes in ms. Default 20.", "default": 20},
                "ref": {"type": "string"},
                "target": {"type": "string"},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["text"],
        },
    },
    {
        "name": "edge_key",
        "description": "Press a keyboard key or shortcut (e.g. 'Enter', 'Tab', 'Escape', 'Ctrl+A').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key identifier or combination."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["key"],
        },
    },
    {
        "name": "edge_select",
        "description": "Select an option in a dropdown (<select>) element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "target": {"type": "string"},
                "value": {"type": "string", "description": "Option value attribute."},
                "label": {"type": "string", "description": "Visible option label text."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_check_radio",
        "description": "Check a radio button or checkbox by selector, label text, or value attribute.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input element."},
                "target": {"type": "string", "description": "Label text or selector shorthand."},
                "text": {"type": "string", "description": "Label text to match."},
                "value": {"type": "string", "description": "Value attribute of the radio/checkbox."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_upload",
        "description": "Upload file(s) to a file input.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "target": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Absolute paths of files to upload."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["files"],
        },
    },
    {
        "name": "edge_scroll",
        "description": "Scroll the viewport or an element into view.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "ref": {"type": "string"},
                "target": {"type": "string"},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_wait",
        "description": "Wait for a condition: text, selector, ref, url regex, load, or network idle.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "selector": {"type": "string"},
                "ref": {"type": "string"},
                "url": {"type": "string"},
                "load": {"type": "boolean"},
                "idle": {"type": "boolean"},
                "timeout": {"type": "integer", "default": 5000},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_text",
        "description": "Read text content of an element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "target": {"type": "string"},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_eval",
        "description": "Evaluate a JavaScript expression in the main frame context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript code to evaluate."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["code"],
        },
    },
    {
        "name": "edge_screenshot",
        "description": "Capture screenshot of the viewport or a specific element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["jpeg", "png"], "default": "jpeg"},
                "quality": {"type": "integer", "default": 80},
                "clip": {"type": "object", "description": "Bounding box {x, y, width, height}"},
                "of": {"type": "string", "description": "Target element ref or selector to clip to."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_console",
        "description": "Get recent console messages, errors, and unhandled exceptions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clear": {"type": "boolean", "default": False},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_dialog",
        "description": "Configure browser dialog handling policy (accept, dismiss, manual).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "policy": {"type": "string", "enum": ["accept", "dismiss", "manual"], "default": "accept"},
                "promptText": {"type": "string"},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["policy"],
        },
    },
    {
        "name": "edge_history_search",
        "description": "Search browsing history by text query over URLs and titles.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to search for in history URLs and titles."},
                "maxResults": {"type": "integer", "description": "Maximum results to return. Default 20.", "default": 20},
                "startTime": {"type": "number", "description": "Only items visited after this time (ms since epoch)."},
                "endTime": {"type": "number", "description": "Only items visited before this time (ms since epoch)."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_history_delete",
        "description": "Delete a URL from browsing history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to remove from history."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["url"],
        },
    },
    {
        "name": "edge_group_list",
        "description": "List open tab groups with their titles, colors, and window IDs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "windowId": {"type": "integer", "description": "Only list groups in this window."},
                **COMMON_TOOL_PROPERTIES,
            },
        },
    },
    {
        "name": "edge_group_move",
        "description": "Move tabs into a tab group (existing groupId, or a new group with optional title/color).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tabIds": {"type": "array", "items": {"type": "integer"}, "description": "IDs of tabs to group."},
                "groupId": {"type": "integer", "description": "Existing group ID to move into. Omit to create a new group."},
                "title": {"type": "string", "description": "Title to set on the group."},
                "color": {"type": "string", "description": "Color to set on the group (grey, blue, red, yellow, green, pink, purple, cyan, orange)."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["tabIds"],
        },
    },
    {
        "name": "edge_group_ungroup",
        "description": "Remove tabs from their tab group(s).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tabIds": {"type": "array", "items": {"type": "integer"}, "description": "IDs of tabs to ungroup."},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["tabIds"],
        },
    },
    {
        "name": "edge_batch",
        "description": "Execute a list of action steps sequentially in a single call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "description": "List of action objects: [{action, params}] or flat [{action, ...params}]; a params object is merged under the flat keys"},
                **COMMON_TOOL_PROPERTIES,
            },
            "required": ["steps"],
        },
    },
]


def handle(msg: dict, edge: Edge) -> dict | None:
    """Handle a single JSON-RPC 2.0 message."""
    method = msg.get("method")
    msg_id = msg.get("id")

    # Notifications
    if msg_id is None or method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "initialize":
        client_ver = msg.get("params", {}).get("protocolVersion")
        proto_ver = client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS else "2025-11-25"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": proto_ver,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "edge-agent-bridge", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        # Route tool call
        if tool_name == "edge_status":
            res = edge.status()
        elif tool_name in ("edge_tabs", "edge_tab"):
            res = edge.tab() if tool_name == "edge_tab" else edge.tabs()
        elif tool_name == "edge_tab_switch":
            res = edge.tab_switch(tab_id=args.get("tabId"))
        elif tool_name == "edge_tab_new":
            res = edge.tab_new(
                url=args.get("url", "about:blank"),
                group=args.get("group"),
                window=args.get("window", False),
                active=args.get("active", True),
            )
        elif tool_name == "edge_tab_close":
            res = edge.tab_close(tab_id=args.get("tabId"))
        elif tool_name in ("edge_navigate", "edge_nav"):
            res = edge.nav(url=args.get("url", ""), wait=args.get("wait", "load"), tab_id=args.get("tabId"))
        elif tool_name == "edge_back":
            res = edge.back(wait=args.get("wait", "load"), tab_id=args.get("tabId"))
        elif tool_name == "edge_forward":
            res = edge.forward(wait=args.get("wait", "load"), tab_id=args.get("tabId"))
        elif tool_name == "edge_reload":
            res = edge.reload(wait=args.get("wait", "load"), tab_id=args.get("tabId"))
        elif tool_name == "edge_snapshot":
            res = edge.snapshot(
                mode=args.get("mode", "interactive"),
                frames=args.get("frames", True),
                tab_id=args.get("tabId"),
                max_nodes=args.get("maxNodes", 400),
            )
        elif tool_name == "edge_click":
            res = edge.click(
                target=args.get("target"),
                selector=args.get("selector"),
                text=args.get("text"),
                ref=args.get("ref"),
                x=args.get("x"),
                y=args.get("y"),
                button=args.get("button", "left"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_dblclick":
            res = edge.dblclick(
                target=args.get("target"),
                selector=args.get("selector"),
                text=args.get("text"),
                ref=args.get("ref"),
                x=args.get("x"),
                y=args.get("y"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_rightclick":
            res = edge.rightclick(
                target=args.get("target"),
                selector=args.get("selector"),
                text=args.get("text"),
                ref=args.get("ref"),
                x=args.get("x"),
                y=args.get("y"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_hover":
            res = edge.hover(
                target=args.get("target"),
                selector=args.get("selector"),
                text=args.get("text"),
                ref=args.get("ref"),
                x=args.get("x"),
                y=args.get("y"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_drag":
            res = edge.drag(
                from_target=args.get("from"),
                to_target=args.get("to"),
                steps=args.get("steps", 10),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_fill":
            res = edge.fill(
                target=args.get("target"),
                text=args.get("text", ""),
                ref=args.get("ref"),
                append=args.get("append", False),
                clear=args.get("clear", True),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_type":
            res = edge.type(
                text=args.get("text", ""),
                delay=args.get("delay", 20),
                ref=args.get("ref"),
                target=args.get("target"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_key":
            res = edge.key(key=args.get("key", ""), tab_id=args.get("tabId"))
        elif tool_name == "edge_select":
            res = edge.select(
                target=args.get("target"),
                ref=args.get("ref"),
                value=args.get("value"),
                label=args.get("label"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_check_radio":
            res = edge.check_radio(
                selector=args.get("selector"),
                target=args.get("target"),
                text=args.get("text"),
                value=args.get("value"),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_upload":
            res = edge.upload(
                target=args.get("target"),
                ref=args.get("ref"),
                files=args.get("files", []),
                tab_id=args.get("tabId"),
                fallback=args.get("fallback", True),
            )
        elif tool_name == "edge_scroll":
            res = edge.scroll(
                x=args.get("x"),
                y=args.get("y"),
                ref=args.get("ref"),
                target=args.get("target"),
                tab_id=args.get("tabId"),
            )
        elif tool_name == "edge_wait":
            res = edge.wait(
                text=args.get("text"),
                selector=args.get("selector"),
                ref=args.get("ref"),
                url=args.get("url"),
                load=args.get("load", False),
                idle=args.get("idle", False),
                timeout=args.get("timeout", 5000),
                tab_id=args.get("tabId"),
            )
        elif tool_name == "edge_text":
            res = edge.text(target=args.get("target"), ref=args.get("ref"), tab_id=args.get("tabId"))
        elif tool_name == "edge_eval":
            res = edge.eval(code=args.get("code", ""), tab_id=args.get("tabId"))
        elif tool_name == "edge_screenshot":
            res = edge.screenshot(
                format=args.get("format", "jpeg"),
                quality=args.get("quality", 80),
                clip=args.get("clip"),
                of=args.get("of"),
                tab_id=args.get("tabId"),
            )
        elif tool_name == "edge_console":
            res = edge.console(clear=args.get("clear", False), tab_id=args.get("tabId"))
        elif tool_name == "edge_dialog":
            res = edge.dialog(
                policy=args.get("policy", "accept"),
                prompt_text=args.get("promptText"),
                tab_id=args.get("tabId"),
            )
        elif tool_name == "edge_history_search":
            res = edge.history_search(
                text=args.get("text", ""),
                max_results=args.get("maxResults", 20),
                start_time=args.get("startTime"),
                end_time=args.get("endTime"),
            )
        elif tool_name == "edge_history_delete":
            res = edge.history_delete(url=args.get("url", ""))
        elif tool_name == "edge_group_list":
            res = edge.group_list(window_id=args.get("windowId"))
        elif tool_name == "edge_group_move":
            res = edge.group_move(
                tab_ids=args.get("tabIds", []),
                group_id=args.get("groupId"),
                title=args.get("title"),
                color=args.get("color"),
            )
        elif tool_name == "edge_group_ungroup":
            res = edge.group_ungroup(tab_ids=args.get("tabIds", []))
        elif tool_name == "edge_batch":
            res = edge.batch(steps=args.get("steps", []), tab_id=args.get("tabId"))
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
            }

        # Content mapping
        is_error = not res.get("success", False)
        if tool_name == "edge_snapshot" and not is_error:
            tab_line = ""
            if res.get("tab"):
                t = res["tab"]
                tab_line = f"tab {t.get('id')} \"{t.get('title', '')}\" {t.get('url', '')}\n"
            content = [{"type": "text", "text": tab_line + res.get("text", "")}]
        elif tool_name == "edge_screenshot" and not is_error and res.get("dataUrl"):
            data_str = res["dataUrl"]
            if "," in data_str:
                data_str = data_str.split(",", 1)[1]
            mime_type = "image/png" if (res.get("format") or args.get("format")) == "png" else "image/jpeg"
            content = [
                {"type": "image", "data": data_str, "mimeType": mime_type},
                {"type": "text", "text": f"Screenshot: {len(data_str)} bytes base64"},
            ]
        else:
            content = [{"type": "text", "text": json.dumps(res, indent=2)}]

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": content,
                "isError": is_error,
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve(stdin=None, stdout=None) -> None:
    """Serve the MCP server on stdio using binary streams to avoid CRLF mutation on Windows."""
    in_stream = stdin or sys.stdin.buffer
    out_stream = stdout or sys.stdout.buffer

    edge = Edge(pin=True, auto_start=True)
    mint = edge.send("session_start", {"name": "mcp"})
    if mint.get("success") and mint.get("sessionToken"):
        edge.session_token = mint["sessionToken"]
    else:
        print(f"edge-bridge-mcp: no daemon session ({mint.get('code')}); running tokenless",
              file=sys.stderr)

    try:
        while True:
            line = in_stream.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except Exception:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                out_stream.write(json.dumps(err_resp).encode("utf-8") + b"\n")
                out_stream.flush()
                continue

            try:
                resp = handle(msg, edge)
            except Exception as e:
                logging.getLogger("edge_bridge.mcp").exception("tool call failed")
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32603, "message": f"Internal error: {e}"},
                }
            if resp is not None:
                out_stream.write(json.dumps(resp).encode("utf-8") + b"\n")
                out_stream.flush()
    finally:
        edge.close()


def main(argv=None) -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
