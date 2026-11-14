"""The codex agent without Codex: the generated config.toml, the desktop MCP tools against a fake
controller, and the agent driven by a scripted `codex` stand-in that prints the JSONL events the
real `codex exec --json` prints (no network, no VM)."""
from __future__ import annotations

import io
import json
import os
import stat
import tomllib
from pathlib import Path

import pytest
from PIL import Image

from osworld_science.agents.base import AgentRunConfig
from osworld_science.agents.codex import (
    build_codex_agent,
    codex_config,
    probe_codex,
    provider_table,
    toml_dumps,
)
from osworld_science.agents.codex.agent import (
    CodexAgent,
    rollout_usage,
    strip_images,
    usage_from_codex,
)
from osworld_science.agents.codex.desktop_mcp import BUDGET_MARKER, DesktopTools, build_server
from osworld_science.agents.config import AgentConfig
from osworld_science.agents.openai_cua import PyAutoGUIComputer
from osworld_science.agents.registry import agent_names, get_agent_factory, get_agent_preflight
from osworld_science.llm.models import BackendSpec, ModelSpec, ResolvedModel
from osworld_science.runner.episode import run_episode

AZURE = BackendSpec(name="azure", kind="openai_chat", base_url_template="https://x/openai/v1", key_env="OPENAI_API_KEY")
OPENROUTER = BackendSpec(name="openrouter", kind="openai_chat", base_url_template="https://openrouter.ai/api/v1",
                         key_env="OPENROUTER_API_KEY", usage_include=True)
ANTHROPIC = BackendSpec(name="anthropic", kind="anthropic_messages", base_url_template="https://a", key_env="ANTHROPIC_API_KEY")
PRICE = {"in": 1.0, "out": 10.0, "cache_read": 0.1}


def model(backend=AZURE, model_id="gpt-5.6-luna", **kw) -> ResolvedModel:
    spec = ModelSpec(name="m", backend=backend, model_id=model_id, price=PRICE, agents=("prompt", "codex"),
                     default_agent="codex", **kw)
    return ResolvedModel(spec=spec, base_url=backend.base_url_template, key="sk-test")


def png(colour=(200, 0, 0), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


class FakeController:
    def __init__(self, screenshot: bytes):
        self.png = screenshot
        self.commands: list[str] = []
        self.screenshot_calls = 0

    def execute_python_command(self, command: str):
        self.commands.append(command)
        return {"returncode": 0, "error": ""}

    def get_screenshot(self):
        self.screenshot_calls += 1
        return self.png


class FakeGuest:
    port, hostname = 5999, "localhost"

    def __init__(self):
        self.calls: list = []

    def execute(self, command, timeout=300):
        self.calls.append((command, timeout))
        return {"output": "hello\n", "error": "", "returncode": 0}


class FakeTask:
    def prompt(self) -> str:
        return "Open the report and save it as /home/user/out.csv."


# ── config.toml ──
def test_toml_writer_round_trips_through_tomllib():
    doc = {"model": "gpt", "flag": True, "n": 3, "f": 1.5, "arr": ["a", "b"], "skip": None,
           "t": {"k": "v", "sub": {"api-key": "X", "weird key": 1}}, "only_subtables": {"a": {"x": 1}}, "empty": {}}
    text = toml_dumps(doc)
    back = tomllib.loads(text)
    assert back == {"model": "gpt", "flag": True, "n": 3, "f": 1.5, "arr": ["a", "b"],
                    "t": {"k": "v", "sub": {"api-key": "X", "weird key": 1}}, "only_subtables": {"a": {"x": 1}}, "empty": {}}
    assert "[only_subtables]\n" not in text and "[empty]" in text       # no header without keys, except an empty table
    with pytest.raises(ValueError):
        toml_dumps({"bad": [{"x": 1}]})


def test_provider_table_per_backend():
    azure = provider_table(model(), {})
    assert azure["base_url"] == "https://x/openai/v1" and azure["env_key"] == "OPENAI_API_KEY"
    assert azure["wire_api"] == "responses" and azure["env_http_headers"] == {"api-key": "OPENAI_API_KEY"}
    assert "capabilities" not in azure                       # Codex <= 0.156 rejects the key; opt-in only
    assert provider_table(model(), {"remote_compaction": "unsupported"})["capabilities"] == {"remote_compaction": "unsupported"}
    router = provider_table(model(OPENROUTER, "openai/gpt-5.6-luna"), {"provider_overrides": {"query_params": {"a": "b"}}})
    assert "env_http_headers" not in router and "capabilities" not in router and router["query_params"] == {"a": "b"}
    assert router["env_key"] == "OPENROUTER_API_KEY"
    with pytest.raises(ValueError, match="Responses API only"):
        provider_table(model(ANTHROPIC, "claude"), {})


def test_codex_config_defaults_and_overrides():
    doc = codex_config(model(), {}, reasoning_effort="high", developer_instructions="dev", mcp_server={"command": "py"})
    assert doc["model"] == "gpt-5.6-luna" and doc["model_provider"] == "osci" and doc["model_reasoning_effort"] == "high"
    assert doc["approval_policy"] == "never" and doc["sandbox_mode"] == "read-only" and doc["web_search"] == "disabled"
    assert doc["project_doc_max_bytes"] == 0 and doc["features"] == {"shell_tool": False, "unified_exec": False}
    assert doc["mcp_servers"] == {"desktop": {"command": "py"}} and doc["developer_instructions"] == "dev"
    assert "model_auto_compact_token_limit" not in doc                      # None keys are dropped
    assert tomllib.loads(toml_dumps(doc))["model_providers"]["osci"]["env_key"] == "OPENAI_API_KEY"
    doc = codex_config(model(), {"host_shell": True, "features": {"view_image": False}, "web_search": "cached",
                                 "model_auto_compact_token_limit": 60000,
                                 "config_overrides": {"features": {"memories": False}, "model_verbosity": "low"}},
                       reasoning_effort="", developer_instructions=None, mcp_server=None)
    assert doc["features"] == {"shell_tool": True, "unified_exec": True, "view_image": False, "memories": False}
    assert doc["web_search"] == "cached" and doc["model_auto_compact_token_limit"] == 60000 and doc["model_verbosity"] == "low"
    assert "mcp_servers" not in doc and "model_reasoning_effort" not in doc and "developer_instructions" not in doc
    chat = codex_config(model(), {"auth": "chatgpt"}, reasoning_effort="low", developer_instructions=None, mcp_server=None)
    assert chat["model_provider"] == "openai" and "model_providers" not in chat
    with pytest.raises(ValueError, match="auth"):
        codex_config(model(), {"auth": "magic"}, reasoning_effort="", developer_instructions=None, mcp_server=None)


# ── the desktop MCP tools ──
def tools_for(tmp_path, controller, **kw) -> tuple[DesktopTools, PyAutoGUIComputer]:
    computer = PyAutoGUIComputer((1920, 1080), tmp_path)
    tools = DesktopTools(computer, FakeGuest(), max_steps=kw.pop("max_steps", 3), **kw)
    computer.bind(controller, step_index=lambda: tools.count)
    return tools, computer


def test_gui_tools_execute_scale_and_return_screenshots(tmp_path):
    controller = FakeController(png())
    tools, computer = tools_for(tmp_path, controller, max_edge=960, screenshot_detail="low", max_steps=10)
    assert tools.sent_size == (960, 540)
    out = tools.click(480, 270, "right")
    assert "pyautogui.click(960, 540, button='right')" in controller.commands[0]        # scaled back to screen pixels
    assert out[0].type == "text" and "[step 1/10]" in out[0].text
    assert out[1].type == "image" and out[1].mime_type == "image/png" and out[1].meta == {"codex/imageDetail": "low"}
    assert (tmp_path / "shots" / "cua_001.png").exists() and (tmp_path / "shots" / "step_001.png").exists()
    sent = Image.open(io.BytesIO(__import__("base64").b64decode(out[1].data)))
    assert sent.size == (960, 540)                                                     # the model sees the downscaled image
    tools.type_text("x" * 900)
    assert sum("pyautogui.write(" in c for c in controller.commands) == 3               # 400-character chunks
    tools.keypress(["ctrl", "s"])
    assert "pyautogui.keyDown('ctrl')" in controller.commands[-1]
    tools.scroll(10, 10, 5)
    assert "pyautogui.scroll(-5)" in controller.commands[-1]
    tools.drag([[0, 0], [100, 100]])
    assert "pyautogui.mouseDown(button='left')" in controller.commands[-1]
    assert computer.action_count == 7 and tools.count == 5                              # type is one call, three actions
    plain = DesktopTools(computer, None, max_steps=5, screenshot_after_action=False, screenshot_detail=None)
    assert [c.type for c in plain.move(1, 2)] == ["text"]                               # text only when asked
    with pytest.raises(Exception, match="disabled"):
        plain.run_command("ls")


def test_budget_refuses_calls_beyond_max_steps(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError
    tools, _ = tools_for(tmp_path, FakeController(png()), max_steps=2)
    tools.screenshot()
    tools.click(1, 1)
    with pytest.raises(ToolError, match=BUDGET_MARKER):
        tools.click(2, 2)
    assert tools.exhausted and tools.count == 2
    log = [json.loads(line) for line in (tmp_path / "codex_mcp.jsonl").read_text().splitlines()] \
        if (tmp_path / "codex_mcp.jsonl").exists() else []
    assert log == []      # no trace was given to this DesktopTools; the server's main() wires one


def test_run_command_goes_to_the_guest_shell(tmp_path):
    tools, computer = tools_for(tmp_path, FakeController(png()), command_timeout=50, output_limit=5)
    out = tools.run_command("ls -la", cwd="/home/user", timeout=500)
    command, timeout = tools.guest.calls[0]
    assert command == ["bash", "-lc", "cd /home/user && ls -la"] and timeout == 60     # capped at command_timeout + 10
    assert out[0].text.startswith("[step 1/3] exit code: 0") and "hello" in out[0].text and "truncated" in out[0].text


def test_mcp_server_lists_tools_and_reports_errors(tmp_path):
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError
    tools, _ = tools_for(tmp_path, FakeController(png()), max_steps=1)
    server = build_server(tools, guest_shell=True)

    async def drive():
        names = {t.name for t in await server.list_tools()}
        bare = build_server(DesktopTools(tools.computer, None, max_steps=1), guest_shell=False)
        bare_names = {t.name for t in await bare.list_tools()}
        result = await server.call_tool("click", {"x": 1, "y": 2})
        try:
            await server.call_tool("screenshot", {})
        except ToolError as e:
            error = str(e)
        else:
            error = ""
        return names, bare_names, result, error

    names, bare_names, result, error = asyncio.run(drive())
    assert names == {"screenshot", "click", "double_click", "move", "drag", "scroll", "type", "keypress", "wait", "run_command"}
    assert bare_names == names - {"run_command"}
    assert [c.type for c in result.content] == ["text", "image"]
    assert BUDGET_MARKER in error


# ── the agent, driven by a scripted codex ──
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if "--version" in args:
    print("codex-cli 0.0.0-test"); sys.exit(0)
prompt = sys.stdin.read()
home = os.environ["CODEX_HOME"]
json.dump({"args": args, "prompt": prompt, "cwd": os.getcwd(), "key": os.environ.get("OPENAI_API_KEY"),
           "config": open(os.path.join(home, "config.toml")).read()}, open(os.path.join(home, "invocation.json"), "w"))
scenario = os.environ.get("FAKE_CODEX_SCENARIO", "ok")
def emit(ev): print(json.dumps(ev)); sys.stdout.flush()
img = {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png", "_meta": {"codex/imageDetail": "high"}}
def call(i, tool, arguments, status="completed", error=None, text="ok"):
    item = {"id": f"item_{i}", "type": "mcp_tool_call", "server": "desktop", "tool": tool, "arguments": arguments}
    emit({"type": "item.started", "item": {**item, "status": "in_progress", "result": None, "error": None}})
    result = None if error else {"content": [{"type": "text", "text": text}, img], "structured_content": None}
    emit({"type": "item.completed", "item": {**item, "status": status, "result": result, "error": error}})
emit({"type": "thread.started", "thread_id": "thr_test"})
emit({"type": "turn.started"})
if scenario == "fail":
    emit({"type": "turn.failed", "error": {"message": "boom from upstream"}}); sys.exit(1)
call(0, "screenshot", {}, text="[step 1/3] screenshot")
emit({"type": "item.completed", "item": {"id": "item_1", "type": "reasoning", "text": "Clicking the button."}})
call(2, "click", {"x": 10, "y": 20}, text="[step 2/3] left click")
if scenario == "budget":
    call(3, "type", {"text": "x"}, status="failed", error={"message": "STEP_BUDGET_EXHAUSTED: stop now"})
if scenario == "toolfail":
    call(3, "type", {"text": "x"}, status="failed", error={"message": "type failed: RuntimeError: xdotool"})
emit({"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": "Done: saved out.csv"}})
emit({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "cache_write_input_tokens": 0,
                                           "output_tokens": 120, "reasoning_output_tokens": 50}})
if "-o" in args:
    open(args[args.index("-o") + 1], "w").write("Done: saved out.csv\n")
if scenario == "rollout":
    d = os.path.join(home, "sessions", "2026", "10", "26"); os.makedirs(d)
    totals = [{"input_tokens": 400, "cached_input_tokens": 100, "cache_write_input_tokens": 0, "output_tokens": 50,
               "reasoning_output_tokens": 20, "total_tokens": 450},
              {"input_tokens": 1000, "cached_input_tokens": 400, "cache_write_input_tokens": 0, "output_tokens": 120,
               "reasoning_output_tokens": 50, "total_tokens": 1120}]
    with open(os.path.join(d, "rollout-2026-10-26T00-00-00-thr_test.jsonl"), "w") as f:
        f.write(json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "thr_test"}}) + "\n")
        for total in totals:
            for rate in (None, {"limit": 1}):          # the second is a rate-limit refresh: same totals, must not count
                f.write(json.dumps({"timestamp": "t", "type": "event_msg", "payload": {"type": "token_count",
                        "info": {"total_token_usage": total, "last_token_usage": total, "model_context_window": 272000},
                        "rate_limits": rate}}) + "\n")
'''


@pytest.fixture
def fake_codex(tmp_path) -> Path:
    path = tmp_path / "bin" / "codex"
    path.parent.mkdir()
    path.write_text(FAKE_CODEX)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def make_agent(tmp_path, fake_codex, max_steps=3, **options) -> CodexAgent:
    run_dir = tmp_path / "agent"
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = AgentRunConfig(max_steps=max_steps, max_tokens=16000, run_dir=run_dir, api="responses",
                         options={"codex_bin": str(fake_codex), **options})
    return build_codex_agent(model(), FakeTask(), cfg, run_dir / "llm_raw.jsonl", log=lambda m: None)


def test_registered_and_factory_validates(tmp_path, fake_codex):
    assert "codex" in agent_names() and get_agent_factory("codex") is build_codex_agent
    assert get_agent_preflight("codex") is probe_codex
    cfg = AgentRunConfig(max_steps=3, max_tokens=1, run_dir=tmp_path, api="chat_completions", options={"codex_bin": str(fake_codex)})
    with pytest.raises(ValueError, match="Responses API only"):
        build_codex_agent(model(), FakeTask(), cfg, tmp_path / "raw.jsonl")
    cfg = AgentRunConfig(max_steps=3, max_tokens=1, run_dir=tmp_path, api="responses", options={"codex_bin": str(fake_codex)})
    with pytest.raises(ValueError, match="Anthropic"):
        build_codex_agent(model(ANTHROPIC, "claude"), FakeTask(), cfg, tmp_path / "raw.jsonl")
    cfg = AgentRunConfig(max_steps=3, max_tokens=1, run_dir=tmp_path, api="responses", options={"codex_bin": "/nonexistent/codex"})
    with pytest.raises(FileNotFoundError, match="codex binary"):
        build_codex_agent(model(), FakeTask(), cfg, tmp_path / "raw.jsonl")


def test_episode_with_scripted_codex(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok")
    agent = make_agent(tmp_path, fake_codex, model_auto_compact_token_limit=60000)
    controller, guest = FakeController(png()), FakeGuest()
    result = run_episode(agent, FakeTask(), controller, guest, agent.run_dir, max_steps=3, track_windows=False,
                         log=lambda m: None)
    assert result["status"] == "done" and result["steps_used"] == 2 and result["n_failed_actions"] == 0
    invocation = json.loads((agent.codex_home / "invocation.json").read_text())
    assert invocation["prompt"] == FakeTask().prompt() and invocation["key"] == "sk-test"
    assert invocation["args"][:3] == ["exec", "--json", "--skip-git-repo-check"] and invocation["args"][-1] == "-"
    assert any(a.startswith("--image=") and a.endswith("shots/seed.png") for a in invocation["args"])
    assert invocation["cwd"] == str(agent.workspace)
    config = tomllib.loads(invocation["config"])
    assert config["model"] == "gpt-5.6-luna" and config["model_auto_compact_token_limit"] == 60000
    desktop = config["mcp_servers"]["desktop"]
    assert desktop["required"] is True and "--max-steps" in desktop["args"] and desktop["args"][desktop["args"].index("--port") + 1] == "5999"
    assert desktop["default_tools_approval_mode"] == "approve"      # codex exec can never grant an approval request
    assert "osworld_science.agents.codex.desktop_mcp" in desktop["args"] and desktop["env"]["PYTHONPATH"].endswith("/src")
    assert "at most 3 tool calls" in config["developer_instructions"] and "run_command" in config["developer_instructions"]
    stats = agent.token_stats()
    assert stats["n_llm_calls"] == 1 and stats["input_tokens"] == 1000 and stats["cached_input_tokens"] == 400
    assert stats["output_tokens"] == 120 and stats["reasoning_tokens"] == 50 and agent.usage_source == "turn_completed"
    assert stats["cost_usd"] == pytest.approx((600 * 1.0 + 400 * 0.1 + 120 * 10.0) / 1e6)
    trace = [json.loads(line) for line in (agent.run_dir / "trace.jsonl").read_text().splitlines()]
    kinds = [t["kind"] for t in trace]
    assert kinds.count("cua_step") == 2 and kinds.count("cua_action") == 2 and "cua_reasoning" in kinds and "codex_turn" in kinds
    steps = [t["payload"] for t in trace if t["kind"] == "cua_step"]
    assert steps[0]["labels"] == ["desktop.screenshot"] and steps[1]["labels"] == ["desktop.click"] and steps[1]["images"] == 1
    raw = [json.loads(line) for line in (agent.run_dir / "llm_raw.jsonl").read_text().splitlines()]
    assert sum(r["kind"] == "model_response" for r in raw) == 1
    events = [r for r in raw if r["kind"] == "codex_event"]
    completed = [r for r in events if r["type"] == "item.completed" and r["item"]["type"] == "mcp_tool_call"]
    assert completed[0]["item"]["result"]["content"][1] == {"type": "image", "mimeType": "image/png", "data_bytes": 8,
                                                             "_meta": {"codex/imageDetail": "high"}}    # images stripped
    described = agent.describe()
    assert described["cua_actions"] == 2 and described["cua_screenshots"] == 2 and described["codex_thread_id"] == "thr_test"
    assert described["agent_sdk_version"] == "0.0.0-test" and described["step_accounting"] == "codex_tool_calls"
    assert described["codex_items"] == {"mcp_tool_call": 2, "reasoning": 1, "agent_message": 1}
    assert (agent.run_dir / "codex_last_message.txt").read_text().strip() == "Done: saved out.csv"


def test_budget_hit_reports_max_steps_and_failures_count(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "budget")
    agent = make_agent(tmp_path, fake_codex)
    result = run_episode(agent, FakeTask(), FakeController(png()), FakeGuest(), agent.run_dir, max_steps=3,
                         track_windows=False, log=lambda m: None)
    assert result["status"] == "max_steps" and result["steps_used"] == 3 and result["n_failed_actions"] == 0
    assert agent.budget_hit and agent.describe()["codex_budget_hit"]
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "toolfail")
    agent = make_agent(tmp_path / "b", fake_codex, max_steps=5)
    result = run_episode(agent, FakeTask(), FakeController(png()), FakeGuest(), agent.run_dir, max_steps=5,
                         track_windows=False, log=lambda m: None)
    assert result["status"] == "done" and result["steps_used"] == 3 and result["n_failed_actions"] == 1


def test_turn_failure_raises_and_keeps_nothing_counted(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "fail")
    agent = make_agent(tmp_path, fake_codex)
    result = run_episode(agent, FakeTask(), FakeController(png()), FakeGuest(), agent.run_dir, max_steps=3,
                         track_windows=False, log=lambda m: None)
    assert result["status"].startswith("predict_error:") and "boom from upstream" in result["status"]
    assert agent.token_stats() == {} and agent.exit_code == 1


def test_rollout_gives_per_call_usage(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "rollout")
    agent = make_agent(tmp_path, fake_codex)
    run_episode(agent, FakeTask(), FakeController(png()), FakeGuest(), agent.run_dir, max_steps=3, track_windows=False,
                log=lambda m: None)
    stats = agent.token_stats()
    assert agent.usage_source == "rollout_token_count" and stats["n_llm_calls"] == 2
    assert stats["input_tokens"] == 1000 and stats["output_tokens"] == 120 and stats["cached_input_tokens"] == 400
    calls = rollout_usage(agent.codex_home)
    assert [c["input_tokens"] for c in calls] == [400, 600] and calls[1]["output_tokens_details"]["reasoning_tokens"] == 30
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok")
    ephemeral = make_agent(tmp_path / "e", fake_codex, ephemeral=True)
    run_episode(ephemeral, FakeTask(), FakeController(png()), FakeGuest(), ephemeral.run_dir, max_steps=3,
                track_windows=False, log=lambda m: None)
    invocation = json.loads((ephemeral.codex_home / "invocation.json").read_text())
    assert "--ephemeral" in invocation["args"] and ephemeral.usage_source == "turn_completed"


def test_usage_mapping_and_image_stripping():
    u = usage_from_codex({"input_tokens": 10, "cached_input_tokens": 4, "cache_write_input_tokens": 1,
                          "output_tokens": 3, "reasoning_output_tokens": 2})
    assert u == {"input_tokens": 10, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 1},
                 "output_tokens_details": {"reasoning_tokens": 2}}
    ev = {"a": [{"type": "image", "data": "x" * 100, "mimeType": "image/png"}, {"type": "text", "text": "t"}]}
    assert strip_images(ev) == {"a": [{"type": "image", "mimeType": "image/png", "data_bytes": 100}, {"type": "text", "text": "t"}]}


def test_preflight_with_scripted_codex(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok")
    ok, why = probe_codex(model(), AgentConfig(name="codex", api="responses", options={"codex_bin": str(fake_codex)}), log=lambda m: None)
    assert ok and why.startswith("codex 0.0.0-test: exec turn ok") and "1000 in / 120 out" in why
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "fail")
    ok, why = probe_codex(model(), AgentConfig(name="codex", api="responses", options={"codex_bin": str(fake_codex)}), log=lambda m: None)
    assert not ok and "boom from upstream" in why
    ok, why = probe_codex(model(), AgentConfig(name="codex", api="responses", options={"codex_bin": "/no/such/codex"}))
    assert not ok and "codex binary" in why
    ok, why = probe_codex(model(ANTHROPIC, "claude"), AgentConfig(name="codex", api="responses", options={"codex_bin": str(fake_codex)}))
    assert not ok and "Responses API only" in why


def test_stdio_server_against_fake_guest(tmp_path):
    """The real desktop_mcp process over stdio, talking to a stand-in guest server."""
    import asyncio
    import sys

    from _fake_guest import FakeGuestServer
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:  # the client API moved
        pytest.skip(f"mcp client API not importable: {e}")
    shot = png((0, 120, 0), (1920, 1080))
    with FakeGuestServer(shot, outputs={"whoami": "user\n"}) as guest:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "osworld_science.agents.codex.desktop_mcp", "--port", str(guest.port), "--host", "127.0.0.1",
                  "--run-dir", str(tmp_path), "--max-steps", "3", "--screen-size", "1920x1080", "--max-edge", "960"],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")})

        async def drive():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    r1 = await session.call_tool("click", {"x": 480, "y": 270})
                    r2 = await session.call_tool("run_command", {"command": "whoami"})
                    r3 = await session.call_tool("screenshot", {})
                    r4 = await session.call_tool("screenshot", {})
                    return names, r1, r2, r3, r4

        names, r1, r2, r3, r4 = asyncio.run(drive())
    assert "click" in names and "run_command" in names
    assert not r1.is_error and [c.type for c in r1.content] == ["text", "image"]
    assert any("pyautogui.click(960, 540" in " ".join(c) for c in guest.commands if isinstance(c, list))
    assert not r2.is_error and "user" in r2.content[0].text
    assert not r3.is_error and r4.is_error and BUDGET_MARKER in r4.content[0].text
    assert (tmp_path / "shots" / "cua_001.png").exists() and (tmp_path / "codex_mcp.jsonl").exists()
    log = [json.loads(line) for line in (tmp_path / "codex_mcp.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in log][:2] == ["server_start", "tool_call"] and any(e["kind"] == "budget_exhausted" for e in log)
