"""Screenshots that do not exhaust the guest's X server.

The guest's OSWorld server answers `GET /screenshot` by building a new
`Xcursor()` (pyxcursor.py: `XOpenDisplay`, never `XCloseDisplay`) on every
request to paste the cursor onto the capture, so the server process keeps one
X connection per screenshot it ever served. Xorg's default `MaxClients` is 256
and a desktop session already holds ~25, so after ~220 screenshots every new X
client in the guest fails with "Maximum number of clients reached": pyautogui
actions, app launches, and the endpoint itself. A 1500-step task cannot be run
through that endpoint.

`capture()` takes the same picture through `/execute` instead: a short-lived
python process grabs the screen the way the server does (PIL's ImageGrab),
pastes the cursor the same way, and prints the PNG as base64. The process
exits, its X connection closes, nothing accumulates. Measured on a guest: the
image is pixel-identical to the endpoint's and takes the same 0.4 s.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# What the server's Linux branch does (capture_screen_with_cursor), in a process that ends.
# pyautogui.screenshot() is PIL's ImageGrab.grab() on this guest (pyscreeze with Pillow >= 9.2),
# and the XFixes cursor record carries the pointer position, so pyautogui (0.3 s to import)
# is only a fallback.
CAPTURE_SNIPPET = (
    "import sys, io, base64\n"
    "sys.path.insert(0, '/home/user/server')\n"
    "from PIL import Image\n"
    "try:\n"
    "    from PIL import ImageGrab\n"
    "    im = ImageGrab.grab()\n"
    "except Exception:\n"
    "    import pyautogui\n"
    "    im = pyautogui.screenshot()\n"
    "try:\n"
    "    from pyxcursor import Xcursor\n"
    "    c = Xcursor()\n"
    "    d = c.getCursorImageData()\n"
    "    cur = Image.fromarray(c.getCursorImageArrayFast())\n"
    "    im.paste(cur, (d.x, d.y), cur)\n"
    "except Exception:\n"
    "    pass\n"
    "buf = io.BytesIO()\n"
    "im.save(buf, 'PNG')\n"
    "sys.stdout.write(base64.b64encode(buf.getvalue()).decode('ascii'))\n"
)

CAPTURE_COMMAND = ["python", "-c", CAPTURE_SNIPPET]


def decode_capture(result: dict | None) -> bytes | None:
    """The PNG bytes out of an /execute result of CAPTURE_COMMAND, or None."""
    if not isinstance(result, dict) or result.get("returncode") not in (0, None):
        return None
    try:
        png = base64.b64decode((result.get("output") or "").strip(), validate=True)
    except (ValueError, TypeError):
        return None
    return png if png.startswith(PNG_MAGIC) else None


def capture(host_url: str, timeout: int = 60) -> bytes | None:
    """One screenshot through `<host_url>/execute`; None when the guest did not deliver one."""
    req = urllib.request.Request(f"{host_url}/execute",
                                 data=json.dumps({"command": CAPTURE_COMMAND, "shell": False}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read())
    except (urllib.error.URLError, ValueError, OSError):
        return None
    return decode_capture(result)
