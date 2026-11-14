"""Screenshots through /execute (the server's /screenshot endpoint leaks an X client per
call): decoding, the controller's default path, its fallback, and the liveness checks."""
import base64
import io
import json

from PIL import Image

from osworld_science.guest import controller as ctl_mod
from osworld_science.guest.client import Guest
from osworld_science.guest.controller import DesktopController
from osworld_science.guest.screenshot import CAPTURE_COMMAND, CAPTURE_SNIPPET, decode_capture


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 4), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        return self._payload


def test_decode_capture():
    data = base64.b64encode(png()).decode()
    assert decode_capture({"returncode": 0, "output": data, "error": ""}) == png()
    assert decode_capture({"returncode": 1, "output": data}) is None
    assert decode_capture({"returncode": 0, "output": "not base64!"}) is None
    assert decode_capture({"returncode": 0, "output": base64.b64encode(b"GIF89a").decode()}) is None
    assert decode_capture(None) is None
    assert CAPTURE_COMMAND[:2] == ["python", "-c"] and "Xcursor()" in CAPTURE_SNIPPET and "XCloseDisplay" not in CAPTURE_SNIPPET


def test_controller_captures_through_execute_and_falls_back_to_the_endpoint(monkeypatch):
    calls = []

    def fake_post(url, headers=None, data=None, timeout=None, **kw):
        body = json.loads(data)
        calls.append(("post", url, body["command"][:2]))
        return _Resp(200, {"returncode": 0, "output": base64.b64encode(png()).decode(), "error": ""})

    def fake_get(url, timeout=None, **kw):
        calls.append(("get", url))
        return _Resp(200, content=b"ENDPOINT")

    monkeypatch.setattr(ctl_mod.requests, "post", fake_post)
    monkeypatch.setattr(ctl_mod.requests, "get", fake_get)
    c = DesktopController(5040)
    assert c.get_screenshot() == png()
    assert calls == [("post", "http://localhost:5040/execute", ["python", "-c"])]
    # the legacy path on request
    calls.clear()
    assert DesktopController(5040, screenshot_via="endpoint").get_screenshot() == b"ENDPOINT"
    assert calls == [("get", "http://localhost:5040/screenshot")]
    # /execute keeps failing -> after the retries, one try of the endpoint
    calls.clear()
    monkeypatch.setattr(ctl_mod.requests, "post",
                        lambda *a, **k: _Resp(200, {"returncode": 1, "output": "", "error": "no display"}))
    monkeypatch.setattr(ctl_mod.time, "sleep", lambda *_: None)
    c = DesktopController(5040, retry_times=2)
    assert c.get_screenshot() == b"ENDPOINT" and calls == [("get", "http://localhost:5040/screenshot")]


def test_alive_without_display_uses_execute(monkeypatch):
    g = Guest(5040)
    seen = []
    monkeypatch.setattr(g, "execute", lambda command, timeout=300: (seen.append(command) or {"returncode": 0}))
    monkeypatch.setattr(g, "screenshot", lambda timeout=60: None)
    assert g.alive(require_display=False) is True and seen == [["true"]]
    assert g.alive() is False                                  # the display check needs a screenshot
    monkeypatch.setattr(g, "screenshot", lambda timeout=60: png())
    assert g.alive() is True
