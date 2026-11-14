"""Executes the agent's pyautogui code inside the guest.

A port of the two `PythonController` methods the sweeps used
(`get_screenshot`, `execute_python_command`), with the exact retry/timeout
semantics: screenshots retry 3× with 60 s timeout and 5 s pauses; execution
retries 3× with a 90 s timeout, and a *read timeout* aborts the retries and
returns None (kept for parity with the recorded sweeps).

`PYAUTOGUI_PREFIX` is the prefix every sweep sent: it repairs pyautogui's
'<' key mapping on X11 (otherwise '<' is typed as '>', which turns a heredoc
`<< 'EOF'` into `>> 'EOF'`), disables the fail-safe and imports time.

Screenshots go through `/execute` (see guest/screenshot.py): the server's
`/screenshot` endpoint leaks one X client per call and kills the guest's
display after ~220 of them. `screenshot_via="endpoint"` restores the old path.
"""
from __future__ import annotations

import json
import logging
import time

import requests

from .screenshot import CAPTURE_COMMAND, decode_capture

logger = logging.getLogger("osci.controller")

PYAUTOGUI_PREFIX = ("import pyautogui\n"
                    "import time\n"
                    "import pyautogui._pyautogui_x11 as _x11\n"
                    "pyautogui.FAILSAFE = False\n"
                    "if _x11.keyboardMapping.get('<') != _x11.keyboardMapping.get(','):\n"
                    "    _x11.keyboardMapping['<'] = _x11.keyboardMapping[',']\n"
                    "{command}")


class DesktopController:
    def __init__(self, port: int, host: str = "localhost", pkgs_prefix: str = PYAUTOGUI_PREFIX,
                 retry_times: int = 3, retry_interval: float = 5.0, screenshot_via: str = "execute"):
        if screenshot_via not in ("execute", "endpoint"):
            raise ValueError(f"screenshot_via must be execute or endpoint, not {screenshot_via!r}")
        self.http_server = f"http://{host}:{int(port)}"
        self.pkgs_prefix = pkgs_prefix
        self.retry_times = retry_times
        self.retry_interval = retry_interval
        self.screenshot_via = screenshot_via

    def get_screenshot(self) -> bytes | None:
        if self.screenshot_via == "endpoint":
            return self._endpoint_screenshot(self.retry_times)
        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/execute",
                                         headers={"Content-Type": "application/json"},
                                         data=json.dumps({"command": CAPTURE_COMMAND, "shell": False}), timeout=60)
                if response.status_code == 200:
                    result = response.json()
                    png = decode_capture(result)
                    if png is not None:
                        return png
                    logger.error("screenshot via /execute failed (returncode=%s): %s",
                                 result.get("returncode"), (result.get("error") or "")[-300:])
                else:
                    logger.error("screenshot via /execute: HTTP %d", response.status_code)
            except Exception as e:  # noqa: BLE001
                logger.error("An error occurred while taking the screenshot via /execute: %s", e)
            time.sleep(self.retry_interval)
        logger.error("Failed to get screenshot via /execute; trying the /screenshot endpoint once")
        return self._endpoint_screenshot(1)

    def _endpoint_screenshot(self, attempts: int) -> bytes | None:
        """The server's own endpoint: one leaked X client per call (guest/screenshot.py)."""
        for _ in range(attempts):
            try:
                response = requests.get(self.http_server + "/screenshot", timeout=60)
                if response.status_code == 200:
                    return response.content
                logger.error("Failed to get screenshot. Status code: %d", response.status_code)
            except Exception as e:  # noqa: BLE001
                logger.error("An error occurred while trying to get the screenshot: %s", e)
            time.sleep(self.retry_interval)
        logger.error("Failed to get screenshot.")
        return None

    def execute_python_command(self, command: str) -> dict | None:
        command_list = ["python", "-c", self.pkgs_prefix.format(command=command)]
        payload = json.dumps({"command": command_list, "shell": False})
        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/execute",
                                         headers={"Content-Type": "application/json"},
                                         data=payload, timeout=90)
                if response.status_code == 200:
                    return response.json()
                logger.error("Failed to execute command. Status code: %d", response.status_code)
            except requests.exceptions.ReadTimeout:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("An error occurred while trying to execute the command: %s", e)
            time.sleep(self.retry_interval)
        logger.error("Failed to execute command.")
        return None
