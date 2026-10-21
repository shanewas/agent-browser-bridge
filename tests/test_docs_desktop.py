"""The docs name only desktop verbs the CLI actually has."""
import re
from pathlib import Path

from agent_browser_bridge import cli

ROOT = Path(__file__).resolve().parents[1]


def test_every_desktop_verb_in_the_docs_exists():
    parser = cli.main.__globals__["argparse"].ArgumentParser(add_help=False)
    cli._add_desktop_commands(parser, cli.main.__globals__["argparse"].ArgumentParser(add_help=False))
    verbs = set(parser._subparsers._group_actions[0].choices)
    taught = set()
    for doc in ("README.md", "docs/agent-guide.md", "docs/recipes.md"):
        taught |= set(re.findall(r"agent-bridge desktop ([a-z]+)", (ROOT / doc).read_text(encoding="utf-8")))
    assert taught and taught - verbs == set(), taught - verbs


def test_every_desktop_tool_in_the_docs_exists():
    from agent_browser_bridge.mcp import TOOLS
    from agent_browser_bridge.desktop import ACTIONS
    names = {t["name"] for t in TOOLS if t["name"].startswith("desktop_")}
    taught = set()
    for doc in ("README.md", "docs/agent-guide.md", "docs/recipes.md"):
        taught |= set(re.findall(r"`(desktop_[a-z]+)", (ROOT / doc).read_text(encoding="utf-8")))
    taught -= {"desktop_" + code for code in ("unsupported", "unavailable", "busy", "error", "aborted")}
    assert taught - names - {"desktop_wait"} == set(), taught - names
    assert {"desktop_" + a for a in ACTIONS} - {"desktop_wait"} == names
