"""The guest desktop as an MCP server for Codex (`python -m osworld_science.agents.codex.desktop_mcp`).

Codex speaks to tools over the Model Context Protocol, so the benchmark desktop is handed to it
as a stdio MCP server that Codex itself starts (``[mcp_servers.desktop]`` in the generated
config.toml). Every GUI tool runs through the same PyAutoGUI driver as the ``openai-cua`` agent
(``PyAutoGUIComputer``: the guest's /execute endpoint, ``shots/cua_NNN.png`` for every screenshot)
and returns the resulting desktop screenshot as an MCP image content block, which Codex forwards
to the model as an ``input_image`` (``_meta.codex/imageDetail`` picks the detail level). The
optional ``run_command`` tool runs a shell command inside the guest, not on this host.

The server also enforces the task's step budget: every tool call is one step, and once
``--max-steps`` calls have been made every further call fails with a message telling the model to
stop and summarise (the agent process reads the budget state back from Codex's event stream).
Everything the server does is appended to ``<run-dir>/codex_mcp.jsonl``.

Codex starts MCP servers with a scrubbed environment (HOME, PATH, LANG, …), so everything the
server needs comes in as command-line arguments.

No ``from __future__ import annotations`` here: the MCP library evaluates the tool signatures
with the module's globals, and the descriptions in `register()` refer to the instance.
"""
import argparse
import base64
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import mcp.types as t
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ...guest.client import Guest
from ...guest.controller import DesktopController
from ..openai_cua.agent import PyAutoGUIComputer

SERVER_NAME = "desktop"
BUDGET_MARKER = "STEP_BUDGET_EXHAUSTED"
TYPE_CHUNK_CHARS = 400            # the guest's /execute has a 90 s limit; long text is typed in chunks
MAX_WAIT_S = 60.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

log = logging.getLogger("osci.codex.desktop_mcp")


class JsonlTrace:
    """`trace(kind, payload)` appending one JSON line per event, the shape of the runner's TraceLog."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, kind: str, payload: Any) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "kind": kind, "payload": payload}, ensure_ascii=False) + "\n")
        except OSError:
            pass


class DesktopTools:
    """The tool implementations behind the MCP server; `register()` wires them to a server.

    `computer` executes the GUI actions and captures screenshots; `guest` runs shell commands.
    Coordinates the model sends refer to the screenshot it was shown: when `max_edge` shrinks
    the screenshot, they are scaled back to screen pixels here."""

    def __init__(self, computer: PyAutoGUIComputer, guest: Guest | None, *, max_steps: int,
                 screenshot_after_action: bool = True, screenshot_detail: str | None = "high",
                 max_edge: int | None = None, command_timeout: int = 120, output_limit: int = 20000,
                 trace: JsonlTrace | None = None):
        self.computer = computer
        self.guest = guest
        self.max_steps = int(max_steps)
        self.screenshot_after_action = bool(screenshot_after_action)
        self.screenshot_detail = screenshot_detail or None
        self.max_edge = int(max_edge) if max_edge else None
        self.command_timeout = int(command_timeout)
        self.output_limit = int(output_limit)
        self.trace = trace or (lambda kind, payload: None)
        self.count = 0                  # tool calls made (the step budget counts every one)
        self.exhausted = False
        self.sent_size = self._sent_size(computer.dimensions)

    # ── geometry ──
    def _sent_size(self, dims: tuple[int, int]) -> tuple[int, int]:
        w, h = dims
        if not self.max_edge or max(w, h) <= self.max_edge:
            return (w, h)
        s = self.max_edge / max(w, h)
        return (max(1, round(w * s)), max(1, round(h * s)))

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """A point of the screenshot the model saw → screen pixels."""
        w, h = self.computer.dimensions
        sw, sh = self.sent_size
        if (sw, sh) == (w, h):
            return int(round(x)), int(round(y))
        return int(round(x * w / sw)), int(round(y * h / sh))

    # ── budget and bookkeeping ──
    def _begin(self, tool: str, args: dict[str, Any]) -> int:
        if self.count >= self.max_steps:
            self.exhausted = True
            self.trace("budget_exhausted", {"tool": tool, "max_steps": self.max_steps})
            raise ToolError(f"{BUDGET_MARKER}: the budget of {self.max_steps} tool calls for this task is used up. "
                            f"No further desktop actions are possible. Reply now with a concise summary of what "
                            f"was accomplished and what remains undone.")
        self.count += 1
        self.trace("tool_call", {"index": self.count, "tool": tool, "args": args})
        return self.count

    @staticmethod
    def _fail(tool: str, e: Exception) -> ToolError:
        return ToolError(f"{tool} failed: {type(e).__name__}: {str(e)[:600]}")

    # ── screenshots ──
    def screenshot_block(self) -> t.ImageContent:
        encoded = self.computer.screenshot()         # saves shots/cua_NNN.png, returns base64 PNG
        if self.sent_size != self.computer.dimensions:
            from PIL import Image
            png = base64.b64decode(encoded)
            im = Image.open(io.BytesIO(png))
            im = im.resize(self.sent_size, Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=False)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        meta = {"codex/imageDetail": self.screenshot_detail} if self.screenshot_detail else None
        return t.ImageContent(type="image", data=encoded, mime_type="image/png", _meta=meta)

    def _after_action(self, text: str) -> list[t.TextContent | t.ImageContent]:
        out: list[t.TextContent | t.ImageContent] = [t.TextContent(type="text", text=text)]
        if self.screenshot_after_action:
            out.append(self.screenshot_block())
        return out

    # ── tools ──
    def screenshot(self) -> list[t.TextContent | t.ImageContent]:
        n = self._begin("screenshot", {})
        try:
            block = self.screenshot_block()
        except Exception as e:  # noqa: BLE001
            raise self._fail("screenshot", e) from None
        sw, sh = self.sent_size
        return [t.TextContent(type="text", text=f"[step {n}/{self.max_steps}] screenshot {sw}x{sh}"), block]

    def click(self, x: int, y: int, button: str = "left", keys: list[str] | None = None):
        n = self._begin("click", {"x": x, "y": y, "button": button, "keys": keys})
        if button not in ("left", "right", "middle", "back", "forward"):
            raise ToolError("button must be one of left, right, middle, back, forward")
        sx, sy = self.to_screen(x, y)
        try:
            self.computer.click(sx, sy, button, keys=keys)
        except Exception as e:  # noqa: BLE001
            raise self._fail("click", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] {button} click at ({x}, {y})")

    def double_click(self, x: int, y: int, keys: list[str] | None = None):
        n = self._begin("double_click", {"x": x, "y": y, "keys": keys})
        sx, sy = self.to_screen(x, y)
        try:
            self.computer.double_click(sx, sy, keys=keys)
        except Exception as e:  # noqa: BLE001
            raise self._fail("double_click", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] double click at ({x}, {y})")

    def move(self, x: int, y: int):
        n = self._begin("move", {"x": x, "y": y})
        sx, sy = self.to_screen(x, y)
        try:
            self.computer.move(sx, sy)
        except Exception as e:  # noqa: BLE001
            raise self._fail("move", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] pointer moved to ({x}, {y})")

    def drag(self, path: list[list[int]], keys: list[str] | None = None):
        n = self._begin("drag", {"path": path, "keys": keys})
        if len(path) < 2 or any(len(p) != 2 for p in path):
            raise ToolError("path must hold at least two [x, y] points")
        try:
            self.computer.drag([self.to_screen(px, py) for px, py in path], keys=keys)
        except Exception as e:  # noqa: BLE001
            raise self._fail("drag", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] dragged through {len(path)} points")

    def scroll(self, x: int, y: int, scroll_y: int, scroll_x: int = 0):
        n = self._begin("scroll", {"x": x, "y": y, "scroll_y": scroll_y, "scroll_x": scroll_x})
        sx, sy = self.to_screen(x, y)
        try:
            self.computer.scroll(sx, sy, scroll_x, scroll_y)
        except Exception as e:  # noqa: BLE001
            raise self._fail("scroll", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] scrolled dy={scroll_y} dx={scroll_x} at ({x}, {y})")

    def type_text(self, text: str):
        n = self._begin("type", {"chars": len(text)})
        try:
            for k in range(0, len(text), TYPE_CHUNK_CHARS):
                self.computer.type(text[k:k + TYPE_CHUNK_CHARS])
        except Exception as e:  # noqa: BLE001
            raise self._fail("type", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] typed {len(text)} characters")

    def keypress(self, keys: list[str]):
        n = self._begin("keypress", {"keys": keys})
        if not keys:
            raise ToolError("keys must name at least one key")
        try:
            self.computer.keypress(keys)
        except Exception as e:  # noqa: BLE001
            raise self._fail("keypress", e) from None
        return self._after_action(f"[step {n}/{self.max_steps}] pressed {'+'.join(keys)}")

    def wait(self, seconds: float = 1.0):
        n = self._begin("wait", {"seconds": seconds})
        seconds = min(max(0.0, float(seconds)), MAX_WAIT_S)
        self.computer.action_count += 1
        time.sleep(seconds)
        return self._after_action(f"[step {n}/{self.max_steps}] waited {seconds:g} s")

    def run_command(self, command: str, cwd: str | None = None, timeout: int | None = None):
        n = self._begin("run_command", {"command": command[:2000], "cwd": cwd, "timeout": timeout})
        if self.guest is None:
            raise ToolError("the shell tool is disabled for this run")
        limit = min(int(timeout or self.command_timeout), self.command_timeout)
        shell = f"cd {cwd} && {command}" if cwd else command
        t0 = time.time()
        try:
            r = self.guest.execute(["bash", "-lc", shell], limit + 10)
        except Exception as e:  # noqa: BLE001
            self.computer.failed_action_count += 1
            raise self._fail("run_command", e) from None
        rc = r.get("returncode")
        out, err = str(r.get("output") or ""), str(r.get("error") or "")
        if rc not in (0, None):
            self.computer.failed_action_count += 1
        self.trace("run_command", {"index": n, "returncode": rc, "seconds": round(time.time() - t0, 2),
                                   "stdout": out[:4000], "stderr": err[:2000]})

        def clip(s: str) -> str:
            return s if len(s) <= self.output_limit else s[:self.output_limit] + f"\n… [{len(s) - self.output_limit} more characters truncated]"
        text = (f"[step {n}/{self.max_steps}] exit code: {rc}\n"
                f"--- stdout ---\n{clip(out)}\n--- stderr ---\n{clip(err)}")
        return [t.TextContent(type="text", text=text)]

    # ── registration ──
    def register(self, server: MCPServer, *, guest_shell: bool = True) -> MCPServer:
        after = (" Returns a fresh screenshot of the whole desktop after the action."
                 if self.screenshot_after_action else " Call `screenshot` to see the result.")
        sw, sh = self.sent_size
        coords = f"Coordinates are pixels of the {sw}x{sh} screenshot you were shown (origin top-left)."
        tools = self
        # Codex decides whether an MCP tool needs approval from these hints (a tool without
        # destructiveHint=false asks, and `codex exec` can never grant); the server config also
        # says default_tools_approval_mode = "approve", so either path lets the calls through.
        acts = t.ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
        looks = t.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

        @server.tool(name="screenshot", description=f"Capture the current {sw}x{sh} desktop screenshot.", annotations=looks)
        def screenshot() -> list[t.TextContent | t.ImageContent]:
            return tools.screenshot()

        @server.tool(name="click", description=f"Click the mouse at (x, y). {coords}{after}", annotations=acts)
        def click(x: Annotated[int, Field(description="x pixel of the screenshot")],
                  y: Annotated[int, Field(description="y pixel of the screenshot")],
                  button: Annotated[str, Field(description="left | right | middle | back | forward")] = "left",
                  keys: Annotated[list[str] | None, Field(description="modifier keys held during the click, e.g. ['ctrl']")] = None):
            return tools.click(x, y, button, keys)

        @server.tool(name="double_click", description=f"Double-click the left button at (x, y). {coords}{after}", annotations=acts)
        def double_click(x: int, y: int,
                         keys: Annotated[list[str] | None, Field(description="modifier keys held")] = None):
            return tools.double_click(x, y, keys)

        @server.tool(name="move", description=f"Move the pointer to (x, y) without clicking (hover). {coords}{after}", annotations=acts)
        def move(x: int, y: int):
            return tools.move(x, y)

        @server.tool(name="drag", description=f"Press the left button at the first point, move through the others, release at the last. {coords}{after}", annotations=acts)
        def drag(path: Annotated[list[list[int]], Field(description="[[x, y], [x, y], ...], at least two points")],
                 keys: Annotated[list[str] | None, Field(description="modifier keys held")] = None):
            return tools.drag(path, keys)

        @server.tool(name="scroll", description=f"Scroll the mouse wheel at (x, y): scroll_y > 0 scrolls down, < 0 up; scroll_x > 0 right. {coords}{after}", annotations=acts)
        def scroll(x: int, y: int,
                   scroll_y: Annotated[int, Field(description="vertical wheel clicks, positive = down")],
                   scroll_x: Annotated[int, Field(description="horizontal wheel clicks, positive = right")] = 0):
            return tools.scroll(x, y, scroll_y, scroll_x)

        @server.tool(name="type", description=f"Type text at the current keyboard focus (click the field first). Use `keypress` for Enter, Tab and shortcuts.{after}", annotations=acts)
        def type_text(text: Annotated[str, Field(description="the text to type, as is")]):
            return tools.type_text(text)

        @server.tool(name="keypress", description=f"Press a key or a chord and release it, e.g. ['enter'], ['ctrl', 's'], ['alt', 'f4'] (pyautogui key names).{after}", annotations=acts)
        def keypress(keys: Annotated[list[str], Field(description="keys pressed together, released in reverse order")]):
            return tools.keypress(keys)

        @server.tool(name="wait", description=f"Wait for the interface to settle (default 1 s, at most {MAX_WAIT_S:g} s).{after}", annotations=acts)
        def wait(seconds: Annotated[float, Field(description="seconds to wait")] = 1.0):
            return tools.wait(seconds)

        if guest_shell:
            @server.tool(name="run_command",
                         description=("Run a bash command INSIDE the desktop machine (the same Ubuntu system the screenshots show, "
                                      f"as its desktop user, `bash -lc`). Returns exit code, stdout and stderr. The command is killed "
                                      f"after {tools.command_timeout} s: start long jobs with nohup/setsid and poll. Files you write "
                                      "here are the files the task's deliverables are collected from. GUI programs started here "
                                      "need DISPLAY=:0 and `setsid nohup … &`."), annotations=acts)
            def run_command(command: Annotated[str, Field(description="the bash command line")],
                            cwd: Annotated[str | None, Field(description="working directory inside the desktop machine")] = None,
                            timeout: Annotated[int | None, Field(description=f"seconds before the command is killed (max {tools.command_timeout})")] = None):
                return tools.run_command(command, cwd, timeout)
        return server


def build_server(tools: DesktopTools, *, guest_shell: bool = True) -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions="Tools that operate the benchmark's Ubuntu desktop.")
    tools.register(server, guest_shell=guest_shell)
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, required=True, help="the guest's control port on this host")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--run-dir", required=True, help="the cell's agent/ directory (shots/, codex_mcp.jsonl)")
    ap.add_argument("--max-steps", type=int, required=True, help="tool calls allowed before every call fails")
    ap.add_argument("--screen-size", default="1920x1080", help="guest resolution WxH")
    ap.add_argument("--screenshot-via", default="execute", choices=["execute", "endpoint"])
    ap.add_argument("--screenshot-detail", default="high", help="codex/imageDetail on returned screenshots (auto|low|high|original|none)")
    ap.add_argument("--no-screenshot-after-action", action="store_true", help="GUI tools answer with text only")
    ap.add_argument("--max-edge", type=int, default=0, help="downscale screenshots so the long edge is at most this (0 = as is)")
    ap.add_argument("--no-guest-shell", action="store_true", help="do not offer run_command")
    ap.add_argument("--command-timeout", type=int, default=120)
    ap.add_argument("--output-limit", type=int, default=20000)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    run_dir = Path(a.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="desktop_mcp %(levelname)s %(message)s")
    w, h = (int(v) for v in a.screen_size.lower().split("x"))
    trace = JsonlTrace(run_dir / "codex_mcp.jsonl")
    controller = DesktopController(a.port, host=a.host, screenshot_via=a.screenshot_via)
    guest = None if a.no_guest_shell else Guest(a.port, host=a.host)
    computer = PyAutoGUIComputer((w, h), run_dir)
    tools = DesktopTools(computer, guest, max_steps=a.max_steps,
                         screenshot_after_action=not a.no_screenshot_after_action,
                         screenshot_detail=None if a.screenshot_detail in ("", "none") else a.screenshot_detail,
                         max_edge=a.max_edge or None, command_timeout=a.command_timeout,
                         output_limit=a.output_limit, trace=trace)
    computer.bind(controller, trace=trace, step_index=lambda: tools.count)
    trace("server_start", {"port": a.port, "max_steps": a.max_steps, "screen": [w, h], "sent": list(tools.sent_size),
                           "guest_shell": guest is not None, "screenshot_after_action": tools.screenshot_after_action})
    server = build_server(tools, guest_shell=guest is not None)
    try:
        server.run(transport="stdio")
    finally:
        trace("server_stop", {"tool_calls": tools.count, "exhausted": tools.exhausted,
                              "failed_actions": computer.failed_action_count, "screenshots": computer.screenshot_count})
    return 0


if __name__ == "__main__":
    sys.exit(main())
