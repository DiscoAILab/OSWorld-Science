"""A stand-in for the OSWorld guest server (port 5000 in the VM) for tests without a VM.

Serves `/execute` the way DesktopController and Guest use it: the screenshot capture command
answers with a PNG, every other command answers with returncode 0 and a canned output (matched
by substring from `outputs`), and every command is recorded. Also serves `GET /screenshot`.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from osworld_science.guest.screenshot import CAPTURE_SNIPPET


class FakeGuestServer:
    def __init__(self, png: bytes, outputs: dict[str, str] | None = None):
        self.png = png
        self.outputs = dict(outputs or {})
        self.commands: list = []
        self.screenshot_calls = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 - quiet
                pass

            def _json(self, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/screenshot":
                    server.screenshot_calls += 1
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(server.png)))
                    self.end_headers()
                    self.wfile.write(server.png)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != "/execute":
                    self.send_response(404)
                    self.end_headers()
                    return
                command = body.get("command")
                if isinstance(command, list) and CAPTURE_SNIPPET in command:
                    server.screenshot_calls += 1
                    self._json({"output": base64.b64encode(server.png).decode(), "error": "", "returncode": 0})
                    return
                text = " ".join(command) if isinstance(command, list) else str(command)
                server.commands.append(command)
                out = next((v for k, v in server.outputs.items() if k in text), "")
                self._json({"output": out, "error": "", "returncode": 0})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> FakeGuestServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeGuestServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
