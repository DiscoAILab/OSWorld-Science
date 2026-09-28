"""Executes the agent's pyautogui code inside the guest.

A port of the two `PythonController` methods the sweeps used
(`get_screenshot`, `execute_python_command`), with the exact retry/timeout
semantics: screenshots retry 3× with 60 s timeout and 5 s pauses; execution
retries 3× with a 90 s timeout, and a *read timeout* aborts the retries and
returns None (kept for parity with the recorded sweeps).

`PYAUTOGUI_PREFIX` is the prefix every sweep sent: it repairs pyautogui's
'<' key mapping on X11 (otherwise '<' is typed as '>', which turns a heredoc
`<< 'EOF'` into `>> 'EOF'`), disables the fail-safe and imports time.
"""
from __future__ import annotations

import json
import logging
import time

import requests

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
                 retry_times: int = 3, retry_interval: float = 5.0):
        self.http_server = f"http://{host}:{int(port)}"
        self.pkgs_prefix = pkgs_prefix
        self.retry_times = retry_times
        self.retry_interval = retry_interval

    def get_screenshot(self) -> bytes | None:
        for _ in range(self.retry_times):
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
