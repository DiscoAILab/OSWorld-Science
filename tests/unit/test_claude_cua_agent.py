"""claude-cua without a network: the toolset driver's pyautogui mapping, screenshot sizing,
the batch contract (halt after a failure, screenshot appended), history pruning, cache
breakpoints, usage accounting, the dated-tool shape and the episode outcome."""
import io
import json
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock, BetaUsage
from PIL import Image

from osworld_science.agents.base import AgentRunConfig
from osworld_science.agents.claude_cua import (
    OSWorldComputer,
    build_claude_cua_agent,
    probe_computer_tool,
)
from osworld_science.agents.claude_cua.agent import (
    NOT_EXECUTED,
    _image_slots,
    parse_chord,
    target_image_size,
)
from osworld_science.agents.config import AgentConfig
from osworld_science.agents.registry import agent_names, get_agent_preflight
from osworld_science.llm.models import BackendSpec, ModelSpec, ResolvedModel
from osworld_science.runner.episode import run_episode


def png(size=(64, 48), colour=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


class FakeController:
    def __init__(self, screenshot: bytes, output: str = ""):
        self.png, self.output = screenshot, output
        self.commands: list[str] = []
        self.screenshot_calls = 0
        self.fail_on: str | None = None

    def execute_python_command(self, command: str):
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            return {"returncode": 1, "error": "boom", "output": ""}
        return {"returncode": 0, "error": "", "output": self.output}

    def get_screenshot(self):
        self.screenshot_calls += 1
        return self.png


class FakeTask:
    def prompt(self) -> str:
        return "Open the editor and save the file."


def _model(kind="anthropic_messages", backend="anthropic") -> ResolvedModel:
    spec = ModelSpec(name="claude-test", backend=BackendSpec(name=backend, kind=kind, base_url_template="", key_env="K"),
                     model_id="claude-sonnet-5", price={"in": 2.0, "out": 10.0}, max_out=128000,
                     agents=("prompt", "claude-cua"), default_agent="prompt")
    return ResolvedModel(spec=spec, base_url="https://unused.invalid", key="k")


def _cfg(tmp_path, **options) -> AgentRunConfig:
    return AgentRunConfig(max_steps=10, max_tokens=16000, run_dir=tmp_path, api="messages",
                          screen_size=(64, 48), options=options)


def tool_use(name: str, input: dict, id_: str = "toolu_1", toolset: str | None = "computer") -> BetaToolUseBlock:
    return BetaToolUseBlock(id=id_, name=name, input=input, type="tool_use", toolset_name=toolset)


def message(blocks, stop_reason="tool_use", usage=(100, 20, 0, 0)) -> BetaMessage:
    inp, out, read, written = usage
    return BetaMessage(id="msg", type="message", role="assistant", model="claude-sonnet-5", stop_reason=stop_reason,
                       stop_sequence=None, content=blocks,
                       usage=BetaUsage(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read,
                                       cache_creation_input_tokens=written))


class FakeClient:
    """`client.beta.messages.with_raw_response.create(**body)` returning scripted messages and
    keeping every request body."""

    def __init__(self, replies):
        self.replies, self.bodies = list(replies), []
        outer = self

        class _Raw:
            def __init__(self, msg):
                self.msg = msg
                self.http_response = type("R", (), {"status_code": 200})()
                self.headers = {"request-id": "req_1"}

            def parse(self):
                return self.msg

        class _WithRaw:
            def create(self, **body):
                outer.bodies.append(json.loads(json.dumps(body, default=str)))
                reply = outer.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return _Raw(reply)

        class _Messages:
            with_raw_response = _WithRaw()

            def create(self, **body):
                return _WithRaw().create(**body).parse()

        self.beta = type("B", (), {"messages": _Messages()})()


# ── sizing and keys ────────────────────────────────────────────────────────
def test_target_image_size_matches_the_api_limits():
    assert target_image_size(1920, 1080) == (1920, 1080)                       # 69×39 = 2691 tokens, fits
    w, h = target_image_size(3840, 2160)
    assert w <= 2576 and ((w - 1) // 28 + 1) * ((h - 1) // 28 + 1) <= 4784 and abs(w / h - 16 / 9) < 0.01
    assert target_image_size(1920, 1080, max_edge_px=1568, max_tokens=1568) == (1456, 819)   # the pre-4.7 limits
    assert target_image_size(1080, 1920)[::-1] == target_image_size(1920, 1080)   # portrait transposes


def test_parse_chord_maps_xdotool_names_to_pyautogui():
    assert parse_chord("ctrl+shift+s") == ["ctrl", "shift", "s"]
    assert parse_chord("Return") == ["enter"] and parse_chord("Page_Down") == ["pagedown"]
    assert parse_chord("super+a") == ["win", "a"] and parse_chord("alt+Tab") == ["alt", "tab"]
    assert parse_chord("Escape") == ["esc"] and parse_chord("F5") == ["f5"] and parse_chord(None) == []


# ── the driver ─────────────────────────────────────────────────────────────
def test_members_become_pyautogui_snippets_and_results_carry_toolset_name(tmp_path):
    comp = OSWorldComputer((64, 48), tmp_path, {}, log=lambda *_: None)
    ctl = FakeController(png(), output="12 34")
    comp.bind(ctl, step_index=lambda: 2)
    r = comp.tool_result(tool_use("left_click", {"coordinate": [10, 20], "text": "shift"}))
    assert r["toolset_name"] == "computer" and not r.get("is_error")
    assert "pyautogui.keyDown('shift')" in ctl.commands[-1] and "pyautogui.click(clicks=1" in ctl.commands[-1]
    assert "pyautogui.moveTo(10, 20)" in ctl.commands[-1] and "pyautogui.keyUp('shift')" in ctl.commands[-1]
    comp.tool_result(tool_use("triple_click", {"coordinate": [1, 1]}))
    assert "clicks=3" in ctl.commands[-1]
    comp.tool_result(tool_use("key", {"text": "ctrl+s", "repeat": 3}))
    assert ctl.commands[-1].startswith("for _ in range(3):") and "keyDown('ctrl')" in ctl.commands[-1]
    comp.tool_result(tool_use("scroll", {"coordinate": [5, 5], "scroll_direction": "down", "scroll_amount": 4}))
    assert "pyautogui.scroll(-4)" in ctl.commands[-1]
    comp.tool_result(tool_use("left_click_drag", {"start_coordinate": [1, 2], "coordinate": [30, 40]}))
    assert "mouseDown(button='left')" in ctl.commands[-1] and "moveTo(30, 40, duration=0.3)" in ctl.commands[-1]
    comp.type_chunk = 5
    comp.tool_result(tool_use("type", {"text": "hello world"}))
    assert ctl.commands[-3:] == ["pyautogui.write('hello', interval=0.01)", "pyautogui.write(' worl', interval=0.01)",
                                 "pyautogui.write('d', interval=0.01)"]
    r = comp.tool_result(tool_use("cursor_position", {}))
    assert r["content"][0]["text"] == "X=12,Y=34"
    comp.tool_result(tool_use("left_mouse_down", {}))
    assert ctl.commands[-1] == "pyautogui.mouseDown(button='left')"
    comp.tool_result(tool_use("hold_key", {"text": "shift", "duration": 1}))
    assert "time.sleep(1.0)" in ctl.commands[-1] and "keyUp('shift')" in ctl.commands[-1]
    # a screenshot: what was sent under cua_, the raw PNG under the harness's step_ name
    r = comp.tool_result(tool_use("screenshot", {}))
    assert r["content"][0]["type"] == "image" and r["content"][0]["source"]["media_type"] == "image/webp"
    assert (tmp_path / "shots" / "cua_001.webp").is_file() and (tmp_path / "shots" / "step_002.png").read_bytes() == png()
    sent = Image.open(io.BytesIO(__import__("base64").b64decode(r["content"][0]["source"]["data"])))
    assert sent.convert("RGB").tobytes() == Image.open(io.BytesIO(png())).convert("RGB").tobytes()   # lossless
    # zoom: a crop of the region resized to fit the frame, aspect kept
    r = comp.tool_result(tool_use("zoom", {"region": [0, 0, 32, 48]}))
    im = Image.open(io.BytesIO(__import__("base64").b64decode(r["content"][0]["source"]["data"])))
    assert im.size == (32, 48) and (tmp_path / "shots" / "zoom_001.webp").is_file()
    assert comp.action_count == 11 and comp.screenshot_count == 1 and comp.zoom_count == 1   # type = 3 chunks


def test_driver_errors_are_is_error_results(tmp_path):
    comp = OSWorldComputer((64, 48), tmp_path, {}, log=lambda *_: None)
    ctl = FakeController(png())
    ctl.fail_on = "pyautogui.click"
    comp.bind(ctl)
    r = comp.tool_result(tool_use("left_click", {"coordinate": [1, 1]}))
    assert r["is_error"] and "boom" in r["content"][0]["text"] and r["toolset_name"] == "computer"
    assert comp.failed_action_count == 1
    r = comp.tool_result(tool_use("left_click", {"coordinate": [999, 1]}))          # outside the screenshot
    assert r["is_error"] and "outside" in r["content"][0]["text"]
    r = comp.tool_result(tool_use("zoom", {"region": [5, 5, 1, 1]}))
    assert r["is_error"]
    r = comp.tool_result(tool_use("left_click", {"coordinate": [1]}))               # the SDK's shape check
    assert r["is_error"]


def test_coordinates_scale_when_the_screenshot_is_downsized(tmp_path):
    comp = OSWorldComputer((3840, 2160), tmp_path, {"max_edge_px": 1920, "max_image_tokens": 100000},
                           log=lambda *_: None)
    assert (comp.sent_w, comp.sent_h) == (1920, 1080) and comp.scale == 0.5
    ctl = FakeController(png((3840, 2160)))
    comp.bind(ctl)
    comp.tool_result(tool_use("mouse_move", {"coordinate": [100, 50]}))
    assert "pyautogui.moveTo(200, 100" in ctl.commands[-1]
    assert comp.to_image(200, 100) == (100, 50)
    r = comp.tool_result(tool_use("screenshot", {}))
    im = Image.open(io.BytesIO(__import__("base64").b64decode(r["content"][0]["source"]["data"])))
    assert im.size == (1920, 1080)
    disabled = OSWorldComputer((64, 48), tmp_path, {"disabled_members": ["zoom"]}, log=lambda *_: None)
    assert disabled.to_dict()["configs"]["zoom"] == {"enabled": False}


# ── pruning helpers ────────────────────────────────────────────────────────
def _result(i, with_image=True):
    content = [{"type": "text", "text": "Clicked."}]
    if with_image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}})
    return {"type": "tool_result", "tool_use_id": f"t{i}", "toolset_name": "computer", "content": content}


def test_image_slots_count_the_seed_and_every_tool_result_image():
    seed = {"role": "user", "content": [{"type": "text", "text": "task"},
                                        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                     "data": "x"}}]}
    msgs = [seed, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [_result(0), _result(1, with_image=False), _result(2)]}]
    assert [sid for sid, _, _ in _image_slots(msgs)] == [(0, 1, -1), (2, 0, 1), (2, 2, 1)]


def test_window_prunes_with_hysteresis_and_the_size_cap(tmp_path):
    agent, _ = _agent(tmp_path, [], screenshot_window={"limit": 3, "keep": 1})
    msgs, sizes = [], []
    for i in range(7):                                   # one new tool-result image per turn
        msgs.append({"role": "user", "content": [_result(i)]})
        agent._prune(msgs)
        sizes.append(len(_image_slots(msgs)))
    assert sizes == [1, 2, 3, 2, 3, 2, 3]
    assert msgs[0]["content"][0]["content"][1] == {"type": "text", "text": "[Image Omitted]"}
    assert agent.describe()["screenshot_window"] == {"limit": 3, "keep": 1, "prune": "drop_oldest"}
    assert agent.describe()["screenshots_pruned"] == 4
    # over max_request_mb the window's algorithm cuts to two earlier screenshots at once
    big, _ = _agent(tmp_path, [], screenshot_window={"limit": 50, "keep": 40}, max_request_mb=0.000001)
    msgs = [{"role": "user", "content": [_result(i)]} for i in range(6)]
    big._prune(msgs)
    assert len(_image_slots(msgs)) == 3
    with pytest.raises(ValueError, match="replaced by screenshot_window"):
        _agent(tmp_path, [], prune="interval", prune_interval=25)


# ── the loop ───────────────────────────────────────────────────────────────
def _agent(tmp_path, replies, log=None, **options):
    cfg = _cfg(tmp_path, **options)
    raw = tmp_path / "llm_raw.jsonl"
    agent = build_claude_cua_agent(_model(), FakeTask(), cfg, raw, log=log or (lambda *_: None))
    agent.client = FakeClient(replies)
    return agent, cfg


def test_loop_runs_batches_with_the_toolset_contract(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    replies = [
        # turn 1: two actions, no screenshot -> the harness appends one to the last result
        message([tool_use("left_click", {"coordinate": [3, 4]}, "t1"), tool_use("type", {"text": "hi"}, "t2")]),
        # turn 2: a bad zoom fails, the click after it is not executed
        message([tool_use("screenshot", {}, "t3"), tool_use("zoom", {"region": [9, 9, 1, 1]}, "t4"),
                 tool_use("left_click", {"coordinate": [1, 1]}, "t5")]),
        # turn 3: an empty reply is nudged, then the final answer
        message([], stop_reason="end_turn"),
        message([BetaTextBlock(type="text", text="Saved the file.")], stop_reason="end_turn", usage=(50, 10, 40, 0)),
    ]
    messages: list[str] = []
    agent, cfg = _agent(agent_dir, replies, log=messages.append, screenshot_window={"limit": 1, "keep": 0})
    ctl = FakeController(png())
    outcome = run_episode(agent, FakeTask(), ctl, object(), agent_dir, max_steps=10, track_windows=False,
                          log=messages.append)
    assert outcome["status"] == "done" and outcome["steps_used"] == 4 and outcome["n_failed_actions"] == 1
    bodies = agent.client.bodies
    assert len(bodies) == 4 and bodies[0]["tools"] == [{"type": "computer_toolset_20260801"}]
    assert bodies[0]["system"][0]["cache_control"] == {"type": "ephemeral"} and "betas" not in bodies[0]
    first_user = bodies[0]["messages"][0]["content"]
    assert first_user[0]["type"] == "text" and first_user[1]["type"] == "image"      # instruction, then the seed
    # turn 1's results: both executed, a screenshot appended to the last one
    results = bodies[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and all(r["toolset_name"] == "computer" for r in results)
    assert results[1]["content"][-1]["type"] == "image" and results[1]["cache_control"] == {"type": "ephemeral"}
    # turn 2's results: screenshot ok, zoom failed, the click answered with the halt text
    results = bodies[2]["messages"][4]["content"]
    assert [bool(r.get("is_error")) for r in results] == [False, True, True]
    assert results[2]["content"][0]["text"] == NOT_EXECUTED and results[2]["toolset_name"] == "computer"
    assert "pyautogui.click" not in ctl.commands[-1]
    # limit 1 / keep 0: only the latest screenshot stays an image in the request
    images = [c for m in bodies[3]["messages"] for b in (m["content"] if isinstance(m["content"], list) else [])
              if isinstance(b, dict) and b.get("type") == "tool_result" for c in b["content"] if c["type"] == "image"]
    assert len(images) == 1 and bodies[3]["messages"][-1]["content"][0]["text"].startswith("Please continue")
    # accounting, trace and files
    stats = agent.token_stats()
    assert stats["n_llm_calls"] == 4 and stats["input_tokens"] == 100 * 3 + 90 and stats["cached_input_tokens"] == 40
    assert stats["cost_usd"] == pytest.approx((390 * 2 + 70 * 10) / 1e6)
    trace = [json.loads(line) for line in (agent_dir / "trace.jsonl").read_text().splitlines()]
    kinds = [t["kind"] for t in trace]
    assert kinds.count("cua_step") == 4 and kinds.count("cua_action") == 4 and kinds.count("cua_screenshot") == 2
    shots = {p.name for p in (agent_dir / "shots").iterdir()}
    assert {"step_001.png", "cua_001.webp", "step_002.png", "cua_002.webp", "step_003.png"} <= shots
    raw = [json.loads(line) for line in (agent_dir / "llm_raw.jsonl").read_text().splitlines()]
    # limit 1: the seed screenshot counts as well, so the second request carries 1 image, not seed + 1
    assert [r["kind"] for r in raw] == ["model_response"] * 4 and raw[1]["n_images"] == 1
    assert agent.describe()["screenshot_window"] == {"limit": 1, "keep": 0, "prune": "drop_oldest"}
    assert agent.describe()["api"] == "messages"
    assert [m[:4] for m in messages if m.startswith("[")] == ["[01]", "[02]", "[03]", "[04]"]
    assert any(m.startswith("     !! zoom") for m in messages)          # the failed member is logged


def test_budget_and_interruption_are_reported_as_such(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    always = [message([tool_use("screenshot", {}, f"t{i}")]) for i in range(5)]
    agent, _ = _agent(agent_dir, always)
    agent.cfg.max_steps = 3
    outcome = run_episode(agent, FakeTask(), FakeController(png()), object(), agent_dir, max_steps=3,
                          track_windows=False, log=lambda *_: None)
    assert outcome["status"] == "max_steps" and outcome["steps_used"] == 3
    import threading
    stop = threading.Event()
    stop.set()
    agent2, _ = _agent(agent_dir, always)
    outcome = run_episode(agent2, FakeTask(), FakeController(png()), object(), agent_dir, max_steps=3,
                          track_windows=False, stop_event=stop, log=lambda *_: None)
    assert outcome["status"] == "interrupted"


def test_dated_tool_version_uses_the_single_tool_shape(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    replies = [message([tool_use("computer", {"action": "left_click", "coordinate": [2, 3], "key": "ctrl"}, "t1",
                                 toolset=None)]),
               message([BetaTextBlock(type="text", text="done")], stop_reason="end_turn")]
    agent, _ = _agent(agent_dir, replies, tool_version="computer_20251124", screenshot_after_batch=False)
    ctl = FakeController(png())
    outcome = run_episode(agent, FakeTask(), ctl, object(), agent_dir, max_steps=5, track_windows=False,
                          log=lambda *_: None)
    assert outcome["status"] == "done"
    body = agent.client.bodies[0]
    assert body["tools"] == [{"type": "computer_20251124", "name": "computer", "display_width_px": 64,
                              "display_height_px": 48, "enable_zoom": True}]
    assert body["betas"] == ["computer-use-2025-11-24"]
    assert "keyDown('ctrl')" in ctl.commands[-1] and "moveTo(2, 3)" in ctl.commands[-1]
    result = agent.client.bodies[1]["messages"][2]["content"][0]
    assert result["tool_use_id"] == "t1" and "toolset_name" not in result and not result.get("is_error")


def test_thinking_and_context_management_requests(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    edits = {"edits": [{"type": "clear_tool_uses_20250919", "keep": {"type": "tool_uses", "value": 5}}]}
    agent, _ = _agent(agent_dir, [message([BetaTextBlock(type="text", text="ok")], stop_reason="end_turn")],
                      thinking="adaptive", thinking_effort="low", context_management=edits, prompt_caching=False)
    agent.bind_runtime(FakeController(png()), object())
    agent.predict("task", type("Obs", (), {"screenshot": png()})())
    body = agent.client.bodies[0]
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "low"}
    assert body["context_management"] == edits and body["betas"] == ["context-management-2025-06-27"]
    assert "cache_control" not in body["system"][0]
    agent, _ = _agent(agent_dir, [message([BetaTextBlock(type="text", text="ok")], stop_reason="end_turn")],
                      thinking="extended", thinking_budget=2000)
    agent.bind_runtime(FakeController(png()), object())
    agent.predict("task", type("Obs", (), {"screenshot": png()})())
    assert agent.client.bodies[0]["thinking"] == {"type": "enabled", "budget_tokens": 2000}


def test_factory_validation_and_registration(tmp_path):
    assert "claude-cua" in agent_names() and get_agent_preflight("claude-cua") is probe_computer_tool
    raw = tmp_path / "raw.jsonl"
    with pytest.raises(ValueError, match="Messages API only"):
        build_claude_cua_agent(_model(), FakeTask(), AgentRunConfig(1, 100, tmp_path, api="chat_completions"), raw)
    with pytest.raises(ValueError, match="anthropic_messages backend"):
        build_claude_cua_agent(_model("openai_chat", "azure"), FakeTask(), _cfg(tmp_path), raw)
    with pytest.raises(ValueError, match="thinking"):
        build_claude_cua_agent(_model(), FakeTask(), _cfg(tmp_path, thinking="sometimes"), raw)
    with pytest.raises(ValueError, match="tool_version"):
        build_claude_cua_agent(_model(), FakeTask(), _cfg(tmp_path, tool_version="computer_20240101"), raw)
    with pytest.raises(ValueError, match="thinking_budget"):
        build_claude_cua_agent(_model(), FakeTask(), _cfg(tmp_path, thinking="extended", thinking_budget=20000), raw)


def test_probe_reports_whether_the_route_serves_the_computer_tool(monkeypatch):
    import osworld_science.agents.claude_cua.agent as mod
    client = FakeClient([message([tool_use("screenshot", {}, "t1")])])
    monkeypatch.setattr(mod, "make_client", lambda model, options: client)
    ok, why = probe_computer_tool(_model(), AgentConfig(name="claude-cua", api="messages"), log=lambda *_: None)
    assert ok and why.startswith("toolset ok") and client.bodies[0]["tools"] == [{"type": "computer_toolset_20260801"}]
    client = FakeClient([message([BetaTextBlock(type="text", text="I cannot see a screen.")], stop_reason="end_turn")])
    monkeypatch.setattr(mod, "make_client", lambda model, options: client)
    ok, why = probe_computer_tool(_model(), AgentConfig(name="claude-cua", api="messages",
                                                       options={"tool_version": "computer_20250124"}))
    assert not ok and "without a computer call" in why and client.bodies[0]["betas"] == ["computer-use-2025-01-24"]
    assert Path(__file__).name  # keep Path imported for the fixtures above


def test_stray_computer_call_gets_a_text_only_error_and_no_screenshot(tmp_path):
    """The 2026-10-12 400: the model sent a `computer` tool_use in the earlier single-tool shape
    (no toolset_name, a list of actions) while the toolset was declared. The harness answered
    with an is_error result and then appended the after-batch screenshot to it, which the API
    rejects ("all content must be type `text` if `is_error` is true")."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    stray = tool_use("computer", {"actions": '[{"action": "type", "text": "cat > f.txt"}]'}, "t9", toolset=None)
    replies = [message([BetaTextBlock(type="text", text="Writing the file."), stray]),
               message([BetaTextBlock(type="text", text="done")], stop_reason="end_turn")]
    agent, _ = _agent(agent_dir, replies)
    ctl = FakeController(png())
    outcome = run_episode(agent, FakeTask(), ctl, object(), agent_dir, max_steps=5, track_windows=False,
                          log=lambda *_: None)
    assert outcome["status"] == "done"
    result = agent.client.bodies[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t9" and result["is_error"] is True and "toolset_name" not in result
    assert [c["type"] for c in result["content"]] == ["text"]                 # text only: no screenshot
    assert "no single `computer` tool" in result["content"][0]["text"]
    assert not any("pyautogui.write" in c for c in ctl.commands)              # nothing was typed
    assert agent.describe()["cua_invalid_tool_calls"] == 1


def test_zoom_is_pixel_exact_integer_magnification_and_formats_are_selectable(tmp_path):
    import base64
    import random
    random.seed(0)
    screen = Image.new("RGB", (64, 48))
    screen.putdata([tuple(random.randrange(256) for _ in range(3)) for _ in range(64 * 48)])
    buf = io.BytesIO()
    screen.save(buf, "PNG")
    ctl = FakeController(buf.getvalue())

    def zoom(options, region):
        comp = OSWorldComputer((64, 48), tmp_path, options, log=lambda *_: None)
        comp.bind(ctl)
        r = comp.tool_result(tool_use("zoom", {"region": region}))
        src = r["content"][0]["source"]
        return Image.open(io.BytesIO(base64.b64decode(src["data"]))).convert("RGB"), src["media_type"]

    im, media = zoom({}, [8, 4, 24, 16])                      # 16×12 region: fits 4× (64/16, 48/12)
    assert im.size == (64, 48) and media == "image/webp"
    crop = screen.crop((8, 4, 24, 16))
    for x, y in ((0, 0), (5, 3), (15, 11)):                     # every screen pixel is a 4×4 block, unaltered
        assert all(im.getpixel((4 * x + i, 4 * y + j)) == crop.getpixel((x, y)) for i in range(4) for j in range(4))
    im, _ = zoom({}, [0, 0, 40, 30])                          # fits 1.6×: no whole factor > 1, native pixels
    assert im.size == (40, 30) and im.tobytes() == screen.crop((0, 0, 40, 30)).tobytes()
    im, media = zoom({"zoom_resize": "fit", "image_format": "png"}, [0, 0, 40, 30])   # the reference behaviour
    assert im.size == (64, 48) and media == "image/png"
    _, media = zoom({"image_format": "jpeg"}, [8, 4, 24, 16])
    assert media == "image/jpeg"
    with pytest.raises(ValueError):
        OSWorldComputer((64, 48), tmp_path, {"zoom_resize": "bicubic"})
    with pytest.raises(ValueError):
        OSWorldComputer((64, 48), tmp_path, {"image_format": "avif"})
