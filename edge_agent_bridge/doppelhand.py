"""Minimal client for a local doppelhand server (native mouse/keyboard/screen).

Windows only: the doppelhand server runs on Windows. Like Edge, calls return
the raw result dict and never raise on a failed call: connection failures
return code "doppelhand_unreachable", a server too slow to answer
"doppelhand_timeout", a body that isn't JSON "doppelhand_bad_response", and
HTTP failures "doppelhand_http_<status>" with the server's own error message
when it sent one.

Doppelhand drives OS-level dialogs the bridge cannot reach (file choosers).
All mutating calls use POST; the server rejects GET with "use POST".
Coords are in doppelhand view space (see /screen "view").
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:53234"
TOKEN_HEADER = "X-Doppelhand-Token"


def _json_or_none(body):
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return None


class Doppelhand:
    def __init__(self, base_url=None, token=None, timeout=15):
        self.base_url = base_url or os.environ.get("DOPPELHAND_URL", DEFAULT_URL)
        self.token = token or os.environ.get("DOPPELHAND_TOKEN", "")
        self.timeout = timeout

    def _call(self, path, method="GET", timeout=None):
        req = urllib.request.Request(
            self.base_url + path, method=method,
            headers={TOKEN_HEADER: self.token} if self.token else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = _json_or_none(e.read())
            error = detail.get("error") if isinstance(detail, dict) else None
            return {"success": False, "code": f"doppelhand_http_{e.code}",
                    "error": error or f"doppelhand server answered HTTP {e.code}"}
        except (urllib.error.URLError, OSError) as e:
            if isinstance(getattr(e, "reason", e), TimeoutError):
                return {"success": False, "code": "doppelhand_timeout",
                        "error": f"doppelhand server at {self.base_url} did not answer {path} in time"}
            return {"success": False, "code": "doppelhand_unreachable",
                    "error": f"doppelhand server unreachable at {self.base_url}: {e}"}
        result = _json_or_none(body)
        if result is None:
            return {"success": False, "code": "doppelhand_bad_response",
                    "error": f"doppelhand server answered {path} with a body that is not JSON"}
        return result

    def screen(self):
        """Display layout: monitors, view size, scale. Read-only."""
        return self._call("/screen")

    def shot(self):
        """Screenshot to the server-side path. Returns metadata incl. path."""
        return self._call("/shot")

    def click(self, x, y):
        return self._call(f"/click?at={int(x)},{int(y)}", method="POST")

    def move(self, x, y):
        return self._call(f"/move?at={int(x)},{int(y)}", method="POST")

    def type(self, text):
        return self._call("/type?text=" + urllib.parse.quote(text, safe=""), method="POST")

    def key(self, combo):
        return self._call("/key?combo=" + urllib.parse.quote(combo, safe=""), method="POST")

    def type_file_and_confirm(self, filename):
        """Type a filename into a focused file dialog and press Return."""
        typed = self.type(filename)
        pressed = self.key("Return")
        return {"typed": typed, "pressed": pressed}

    @staticmethod
    def css_to_view(rect, viewport, view):
        """Map a page CSS rect center to doppelhand view coords.

        rect: dict with x/y/width/height (CSS px, viewport-relative).
        viewport: (innerWidth, innerHeight). view: (viewW, viewH) from /screen.
        NOTE: ignores browser chrome offset; verify with a screenshot.
        """
        cx = rect["x"] + rect.get("width", 0) / 2
        cy = rect["y"] + rect.get("height", 0) / 2
        return (round(cx / viewport[0] * view[0]), round(cy / viewport[1] * view[1]))
