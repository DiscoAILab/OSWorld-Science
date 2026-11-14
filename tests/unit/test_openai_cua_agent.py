import asyncio
import base64
import io
import json

from agents import Agent, ComputerTool, FunctionTool, RunConfig, Runner
from agents.models.openai_responses import Converter
from agents.testing import ScriptedModel, function_call
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_computer_tool_call import (
    ActionScreenshot,
    ResponseComputerToolCall,
)
from PIL import Image

from osworld_science.agents.base import AgentRunConfig
from osworld_science.agents.openai_cua import PyAutoGUIComputer, build_cua_agent
from osworld_science.agents.registry import agent_names
from osworld_science.llm.models import BackendSpec, ModelSpec, ResolvedModel
from osworld_science.runner.episode import run_episode


def png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buffer, "PNG")
    return buffer.getvalue()


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


class FakeTask:
    def prompt(self) -> str:
        return "Inspect the desktop and finish the task."


def final_message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="message-1",
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(
                type="output_text", text=text, annotations=[], logprobs=[]
            )
        ],
    )


def test_action_invalidates_seed_and_screenshot_returns_post_action_png(tmp_path):
    before, after = png((200, 0, 0)), png((0, 200, 0))
    controller = FakeController(after)
    computer = PyAutoGUIComputer((1920, 1080), tmp_path)
    computer.bind(controller)
    computer.seed_screenshot(before)

    computer.click(12, 34, "left")
    encoded = computer.screenshot()

    assert "pyautogui.click(12, 34, button='left')" in controller.commands[0]
    assert controller.screenshot_calls == 1
    assert base64.b64decode(encoded) == after
    assert (tmp_path / "shots" / "cua_001.png").read_bytes() == after


def test_agents_sdk_returns_screenshot_as_matching_computer_call_output(tmp_path):
    screenshot = png((10, 20, 30))
    controller = FakeController(screenshot)
    computer = PyAutoGUIComputer((1920, 1080), tmp_path)
    computer.bind(controller)
    tool_call = ResponseComputerToolCall(
        id="computer-1",
        call_id="computer-1",
        type="computer_call",
        action=ActionScreenshot(type="screenshot"),
        pending_safety_checks=[],
        status="completed",
    )
    model = ScriptedModel([[tool_call], [final_message("done")]])
    agent = Agent(name="cua-test", model=model, tools=[ComputerTool(computer=computer)])

    result = Runner.run_sync(
        agent,
        "inspect the desktop",
        max_turns=2,
        run_config=RunConfig(tracing_disabled=True),
    )

    assert result.final_output == "done"
    assert len(model.calls) == 2
    second_input = model.calls[1].input
    output = next(item for item in second_input if item.get("type") == "computer_call_output")
    assert output["call_id"] == "computer-1"
    assert output["output"]["type"] == "computer_screenshot"
    data_url = output["output"]["image_url"]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == screenshot


def test_keypress_releases_modifiers_in_reverse_order(tmp_path):
    controller = FakeController(png((0, 0, 0)))
    computer = PyAutoGUIComputer((1920, 1080), tmp_path)
    computer.bind(controller)

    computer.keypress(["CTRL", "SHIFT", "A"])

    assert controller.commands == [
        "pyautogui.keyDown('ctrl')\n"
        "pyautogui.keyDown('shift')\n"
        "pyautogui.keyDown('a')\n"
        "pyautogui.keyUp('a')\n"
        "pyautogui.keyUp('shift')\n"
        "pyautogui.keyUp('ctrl')"
    ]


def test_cua_is_registered_and_factory_uses_native_computer_tool(tmp_path):
    backend = BackendSpec(
        name="azure",
        kind="openai_chat",
        base_url_template="",
        key_env="OPENAI_API_KEY",
    )
    model = ResolvedModel(
        spec=ModelSpec(
            name="gpt-test",
            backend=backend,
            model_id="gpt-5.6-sol",
            max_out=128_000,
            reasoning=True,
        ),
        base_url="https://example.test/openai/v1",
        key="secret",
    )
    cfg = AgentRunConfig(max_steps=10, max_tokens=16_000, run_dir=tmp_path, api="responses")
    raw_log = tmp_path / "llm_raw.jsonl"
    raw_log.write_text("")

    openai_cua = build_cua_agent(model, object(), cfg, raw_log)
    try:
        assert "openai-cua" in agent_names()
        assert "cua" not in agent_names()
        assert openai_cua.name == "openai-cua"
        assert len(openai_cua.sdk_agent.tools) == 1
        assert isinstance(openai_cua.sdk_agent.tools[0], ComputerTool)
        assert openai_cua.sdk_agent.tools[0].computer is openai_cua.computer
        assert openai_cua.sdk_agent.model_settings.tool_choice == "required"
        assert openai_cua.sdk_agent.model_settings.parallel_tool_calls is False
        assert openai_cua.sdk_agent.model_settings.truncation == "auto"
        assert openai_cua.tool_protocol == "native_computer"
        converted = Converter.convert_tools(
            openai_cua.sdk_agent.tools,
            [],
            model="gpt-5.6-sol",
            tool_choice="required",
        )
        assert converted.tools == [{"type": "computer"}]
    finally:
        asyncio.run(openai_cua.client.close())


def test_non_native_backend_uses_function_tools_with_image_feedback(tmp_path):
    backend = BackendSpec(
        name="openrouter",
        kind="openai_chat",
        base_url_template="",
        key_env="OPENROUTER_API_KEY",
        supports_provider_order=True,
    )
    model = ResolvedModel(
        spec=ModelSpec(
            name="gpt-test@openrouter",
            backend=backend,
            model_id="openai/gpt-test",
            max_out=128_000,
            provider_order=("openai",),
            reasoning=True,
        ),
        base_url="https://openrouter.ai/api/v1",
        key="secret",
    )
    cfg = AgentRunConfig(max_steps=10, max_tokens=16_000, run_dir=tmp_path, api="responses")
    raw_log = tmp_path / "llm_raw.jsonl"
    raw_log.write_text("")
    openai_cua = build_cua_agent(model, object(), cfg, raw_log)

    try:
        assert openai_cua.tool_protocol == "function_tools"
        assert all(isinstance(tool, FunctionTool) for tool in openai_cua.sdk_agent.tools)
        assert not any(isinstance(tool, ComputerTool) for tool in openai_cua.sdk_agent.tools)
        assert openai_cua.sdk_agent.model_settings.extra_body == {
            "provider": {"order": ["openai"], "allow_fallbacks": True}
        }
        converted = Converter.convert_tools(
            openai_cua.sdk_agent.tools,
            [],
            model="openai/gpt-test",
            tool_choice="required",
        )
        by_name = {tool["name"]: tool for tool in converted.tools}
        assert set(by_name) == {
            "computer_screenshot",
            "computer_click",
            "computer_double_click",
            "computer_scroll",
            "computer_type",
            "computer_keypress",
            "computer_move",
            "computer_drag",
            "computer_wait",
        }
        assert by_name["computer_click"]["type"] == "function"
        assert {"x", "y", "button", "keys"} <= set(
            by_name["computer_click"]["parameters"]["properties"]
        )

        before, after = png((200, 0, 0)), png((0, 200, 0))
        controller = FakeController(after)
        openai_cua.computer.bind(controller)
        openai_cua.computer.seed_screenshot(before)
        scripted = ScriptedModel(
            [
                [
                    function_call(
                        "computer_click",
                        {"x": 12, "y": 34, "button": "left"},
                        call_id="function-1",
                    )
                ],
                [final_message("done")],
            ]
        )
        openai_cua.sdk_agent.model = scripted

        result = Runner.run_sync(
            openai_cua.sdk_agent,
            "operate the desktop",
            max_turns=2,
            run_config=RunConfig(tracing_disabled=True),
        )

        assert result.final_output == "done"
        assert controller.screenshot_calls == 1
        assert "pyautogui.click(12, 34, button='left')" in controller.commands[0]
        second_input = scripted.calls[1].input
        output = next(item for item in second_input if item.get("type") == "function_call_output")
        assert output["call_id"] == "function-1"
        assert isinstance(output["output"], list)
        image = output["output"][0]
        assert image["type"] == "input_image"
        assert image["detail"] == "high"
        assert base64.b64decode(image["image_url"].split(",", 1)[1]) == after
    finally:
        asyncio.run(openai_cua.client.close())


def test_cua_sdk_turns_are_reported_as_episode_steps(tmp_path):
    backend = BackendSpec(
        name="azure",
        kind="openai_chat",
        base_url_template="",
        key_env="OPENAI_API_KEY",
    )
    model = ResolvedModel(
        spec=ModelSpec(
            name="gpt-test",
            backend=backend,
            model_id="gpt-test",
            max_out=16_000,
            reasoning=True,
        ),
        base_url="https://example.test/openai/v1",
        key="secret",
    )
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    raw_log = agent_dir / "llm_raw.jsonl"
    raw_log.write_text("")
    cfg = AgentRunConfig(max_steps=10, max_tokens=16_000, run_dir=agent_dir, api="responses")
    messages: list[str] = []
    openai_cua = build_cua_agent(model, FakeTask(), cfg, raw_log, log=messages.append)
    openai_cua.sdk_agent.model = ScriptedModel(
        [
            [
                ResponseComputerToolCall(
                    id="computer-1",
                    call_id="computer-1",
                    type="computer_call",
                    action=ActionScreenshot(type="screenshot"),
                    pending_safety_checks=[],
                    status="completed",
                )
            ],
            [
                ResponseComputerToolCall(
                    id="computer-2",
                    call_id="computer-2",
                    type="computer_call",
                    action=ActionScreenshot(type="screenshot"),
                    pending_safety_checks=[],
                    status="completed",
                )
            ],
            [final_message("done")],
        ]
    )

    outcome = run_episode(
        openai_cua,
        FakeTask(),
        FakeController(png((20, 40, 60))),
        object(),
        agent_dir,
        max_steps=10,
        track_windows=False,
        log=messages.append,
    )

    assert outcome["status"] == "done"
    assert outcome["steps_used"] == 3
    assert outcome["n_failed_actions"] == 0
    assert openai_cua.describe()["step_accounting"] == "agents_sdk_model_turns"
    assert [message[:4] for message in messages] == ["[01]", "[02]", "[03]"]
    shot_names = {path.name for path in (agent_dir / "shots").iterdir()}
    assert {"step_001.png", "step_002.png", "step_003.png"} <= shot_names
    raw_records = [json.loads(line) for line in raw_log.read_text().splitlines()]
    assert [record["index"] for record in raw_records if record["kind"] == "model_response"] == [
        1,
        2,
        3,
    ]
    trace_records = [
        json.loads(line) for line in (agent_dir / "trace.jsonl").read_text().splitlines()
    ]
    assert sum(record["kind"] == "cua_step" for record in trace_records) == 3


def test_screenshot_window_bounds_each_request(tmp_path):
    """limit 2 / keep 1: every request carries at most the latest screenshot plus one earlier
    one; dropped native pairs take their reasoning item with them; usage is summed per turn."""
    from agents.items import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import ResponseReasoningItem

    from osworld_science.agents.openai_cua.agent import ScreenshotWindowFilter
    from osworld_science.agents.window import WindowConfig

    backend = BackendSpec(name="azure", kind="openai_chat", base_url_template="", key_env="OPENAI_API_KEY")
    model = ResolvedModel(spec=ModelSpec(name="gpt-test", backend=backend, model_id="gpt-test", max_out=16_000,
                                         reasoning=True), base_url="https://example.test/openai/v1", key="secret")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    raw_log = agent_dir / "llm_raw.jsonl"
    raw_log.write_text("")
    cfg = AgentRunConfig(max_steps=10, max_tokens=16_000, run_dir=agent_dir, api="responses",
                         options={"screenshot_window": {"limit": 2, "keep": 1}})
    openai_cua = build_cua_agent(model, FakeTask(), cfg, raw_log, log=lambda *_: None)

    def turn(i):
        return [
            ResponseReasoningItem(id=f"rs_{i}", type="reasoning", summary=[]),
            ResponseComputerToolCall(id=f"computer-{i}", call_id=f"computer-{i}", type="computer_call",
                                     action=ActionScreenshot(type="screenshot"), pending_safety_checks=[],
                                     status="completed"),
        ]

    class CountingModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            return ModelResponse(output=response.output, response_id=response.response_id,
                                 usage=Usage(requests=1, input_tokens=100, output_tokens=10, total_tokens=110))

    scripted = CountingModel([turn(1), turn(2), turn(3), turn(4), [final_message("done")]])
    openai_cua.sdk_agent.model = scripted
    outcome = run_episode(openai_cua, FakeTask(), FakeController(png((1, 2, 3))), object(), agent_dir,
                          max_steps=10, track_windows=False, log=lambda *_: None)
    assert outcome["status"] == "done" and outcome["steps_used"] == 5
    for n, call in enumerate(scripted.calls):
        items = call.input
        outputs = [it for it in items if it.get("type") == "computer_call_output"]
        images = [o for o in outputs if o["output"].get("type") == "computer_screenshot"]
        texts = [o for o in outputs if o["output"].get("type") == "input_text"]
        assert len(images) <= 2 and len(images) + len(texts) == len(outputs) == n
        assert all(o["output"]["text"] == ScreenshotWindowFilter.PLACEHOLDER for o in texts)
        calls = [it["call_id"] for it in items if it.get("type") == "computer_call"]
        assert [o["call_id"] for o in outputs] == calls                # every call keeps its output
        reasoning = [i for i, it in enumerate(items) if it.get("type") == "reasoning"]
        assert len(reasoning) == n                                     # no reasoning item is dropped
        assert all(items[i + 1].get("type") == "computer_call" for i in reasoning)
    final_outputs = [it["output"] for it in scripted.calls[-1].input if it.get("type") == "computer_call_output"]
    assert [o["type"] for o in final_outputs] == ["input_text", "input_text", "computer_screenshot", "computer_screenshot"]
    stats = openai_cua.token_stats()
    assert stats["n_llm_calls"] == 5 and stats["input_tokens"] == 500 and stats["output_tokens"] == 50
    assert openai_cua.describe()["screenshot_window"] == {"limit": 2, "keep": 1, "prune": "drop_oldest"}
    assert openai_cua.describe()["screenshots_pruned"] == 2
    # the filter on function-tool outputs keeps the call and replaces the image with text
    window = ScreenshotWindowFilter(WindowConfig(limit=1, keep=0))
    items = [{"role": "user", "content": "go"}]
    for i in range(3):
        items += [{"type": "function_call", "call_id": f"c{i}", "name": "computer_click", "arguments": "{}"},
                  {"type": "function_call_output", "call_id": f"c{i}",
                   "output": [{"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "high"}]}]
    data = type("D", (), {"model_data": type("M", (), {"input": items, "instructions": "sys"})()})()
    filtered = window(data).input
    assert len(filtered) == 7 and [it.get("type") for it in filtered].count("function_call") == 3
    outputs = [it["output"] for it in filtered if it.get("type") == "function_call_output"]
    assert isinstance(outputs[0], str) and "omitted" in outputs[0] and isinstance(outputs[-1], list)


def _retry_agent(tmp_path, **options):
    from osworld_science.agents.openai_cua.agent import RetryingResponsesModel
    backend = BackendSpec(name="azure", kind="openai_chat", base_url_template="", key_env="OPENAI_API_KEY")
    model = ResolvedModel(spec=ModelSpec(name="gpt-test", backend=backend, model_id="gpt-test", max_out=16_000,
                                         reasoning=True), base_url="https://example.test/openai/v1", key="secret")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    raw_log = agent_dir / "llm_raw.jsonl"
    raw_log.write_text("")
    cfg = AgentRunConfig(max_steps=10, max_tokens=16_000, run_dir=agent_dir, api="responses", options=options)
    agent = build_cua_agent(model, FakeTask(), cfg, raw_log, log=lambda *_: None)
    assert isinstance(agent.sdk_agent.model, RetryingResponsesModel)
    return agent, raw_log


def test_model_calls_retry_the_same_request(tmp_path, monkeypatch):
    """Each model call gets up to 4 tries with the same arguments, 10/20/30 s apart, and the
    OpenAI client adds none of its own; failed tries land in llm_raw.jsonl as `error` records."""
    from agents.models.openai_responses import OpenAIResponsesModel

    from osworld_science.llm.usage import raw_log_records
    agent, raw_log = _retry_agent(tmp_path)
    model = agent.sdk_agent.model
    assert model.attempts == 4 and agent.client.max_retries == 0
    model.backoff_s = 0.001
    calls, outcomes = [], [RuntimeError("Responses stream ended with terminal event `response.incomplete`. "
                                        "incomplete_details=IncompleteDetails(reason='content_filter')"),
                           ConnectionError("reset"), "ok"]

    async def fake_get_response(self, *args, **kwargs):
        calls.append((args, kwargs))
        result = outcomes[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(OpenAIResponsesModel, "get_response", fake_get_response)
    assert asyncio.run(model.get_response("sys", [{"role": "user", "content": "go"}], previous_response_id=None)) == "ok"
    assert len(calls) == 3 and all(c == calls[0] for c in calls)          # the same request every time
    errors = [json.loads(line) for line in raw_log.read_text().splitlines()]
    assert [(e["kind"], e["attempt"], e["retry_in_s"]) for e in errors] == [("error", 1, 0.001), ("error", 2, 0.002)]
    assert "content_filter" in errors[0]["exception"] and raw_log_records(raw_log) == []
    assert model.retries == 2

    # four failures: the fourth is raised, nothing more is sent
    calls.clear()
    outcomes[:] = [RuntimeError(f"boom {i}") for i in range(6)]
    try:
        asyncio.run(model.get_response("sys", [], previous_response_id=None))
        raise AssertionError("expected the fourth failure to be raised")
    except RuntimeError as e:
        assert str(e) == "boom 3"
    assert len(calls) == 4
    last = json.loads(raw_log.read_text().splitlines()[-1])
    assert last["attempt"] == 4 and last["retry_in_s"] is None


def test_model_call_retry_honours_attempts_option_and_interruption(tmp_path, monkeypatch):
    import threading

    from agents.models.openai_responses import OpenAIResponsesModel
    agent, _ = _retry_agent(tmp_path, request_attempts=2)
    model = agent.sdk_agent.model
    assert model.attempts == 2
    model.backoff_s = 30.0                                     # an interruption must not wait this out
    stop = threading.Event()
    agent.computer.bind(FakeController(png((0, 0, 0))), stop_event=stop)
    calls = []

    async def failing(self, *args, **kwargs):
        calls.append(1)
        stop.set()
        raise RuntimeError("HTTP 500")
    monkeypatch.setattr(OpenAIResponsesModel, "get_response", failing)
    try:
        asyncio.run(model.get_response("sys", [], previous_response_id=None))
        raise AssertionError("expected the run to stop")
    except RuntimeError as e:
        assert "interrupted" in str(e)
    assert len(calls) == 1
