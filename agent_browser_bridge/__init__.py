"""
Agent Browser Bridge
Real-time AI agent control for the browser you already have open, using active
browser sessions, CDP hardware events, and WebSockets.
"""
__version__ = "3.0.0"

from .client import Edge, EdgeClient, send_cmd, ensure_bridge_running
from .doppelhand import Doppelhand
from .bridge import run_server
from . import config

__all__ = ["__version__", "Edge", "EdgeClient", "send_cmd", "ensure_bridge_running", "run_server", "config", "Doppelhand"]
