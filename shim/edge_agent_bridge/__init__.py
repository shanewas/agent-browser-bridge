"""edge-agent-bridge was renamed to agent-browser-bridge in 3.0.0. This package only keeps the
old import path alive; the code lives in agent_browser_bridge."""
from agent_browser_bridge import *  # noqa: F401,F403
from agent_browser_bridge import __all__, __version__  # noqa: F401
