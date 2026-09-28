"""The vendored Kimi agent must stay byte-identical to the copy the sweeps
ran (== xlang-ai/OSWorld@b138d348 mm_agents/kimi), and the adapter must wrap
it exactly as the legacy run_agent_kimi did (upstream budget by default,
matched on request; provider lock; feedback plumbing). No network is touched."""
import hashlib
import json
from pathlib import Path

from osworld_science.agents.base import ActionResult, AgentRunConfig, Observation
from osworld_science.agents.kimi import KIMI_BUDGET_MATCHED, build_kimi_agent
from osworld_science.agents.registry import agent_names
from osworld_science.llm.models import BackendSpec, ModelSpec, ResolvedModel
from osworld_science.tasks.model import Task

UPSTREAM = Path(__file__).resolve().parents[2] / "src" / "osworld_science" / "agents" / "kimi" / "upstream"
PINNED = {
    "kimi_agent.py": "10d2aba2cdc424728072431c9ef49b4f1fd3c31f75189c5d1d987475776bda1b",
    "__init__.py": "b1a29452a6014403692dcf0c4fcf232eb3fa1073ece0934d15abf5b6bb63cc68",   # the one deliberate deviation; re-pin after editing its docstring
    "utils.py": "6d84f2df3504c6b90780834d61b2a12629df58fbad3f03633152d57e281ee70b",
}


def test_vendored_files_are_unmodified():
    for name, want in PINNED.items():
        got = hashlib.sha256((UPSTREAM / name).read_bytes()).hexdigest()
        assert got == want, f"{name} was modified; adapt in agents/kimi/adapter.py instead"
    assert (UPSTREAM / "LICENSE").read_text().lstrip().startswith("Apache License")


def _model(provider_order=("moonshotai",)) -> ResolvedModel:
    backend = BackendSpec(name="openrouter", kind="openai_chat",
                          url_template="https://openrouter.ai/api/v1/chat/completions",
                          key_env="OPENROUTER_API_KEY", supports_provider_order=True)
    spec = ModelSpec(name="kimi-k3", backend=backend, model_id="moonshotai/kimi-k3",
                     price={"in": 3.0, "out": 15.0}, max_out=943718,
                     provider_order=tuple(provider_order), agent="kimi")
    return ResolvedModel(spec=spec, url="https://openrouter.ai/api/v1/chat/completions", key="k")


def _task(tmp_path: Path) -> Task:
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"id": "t", "snapshot": "s", "instruction": "Do it.", "config": [],
                             "evaluator": {"func": []}, "budget": {"max_steps": 7}}))
    return Task.load(p, domain="d")


def test_kimi_registered_and_matched_budget(tmp_path, monkeypatch):
    assert "kimi" in agent_names()
    # negative control: the vendored constructor falls back to these env vars; the
    # resolved model's endpoint must win over them
    monkeypatch.setenv("KIMI_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    run_dir = tmp_path / "agent"
    run_dir.mkdir()
    cfg = AgentRunConfig(max_steps=7, max_tokens=16000, run_dir=run_dir,
                         options={"kimi_mode": "gui", "kimi_budget": "matched"})
    agent = build_kimi_agent(_model(), _task(tmp_path), cfg, run_dir / "llm_raw.jsonl")
    inner = agent.inner
    matched = {"max_history_length": 5, "history_slide_chunk": 1, "num_max_images": 6,
               "num_compact_images": 1, "fail_on_max_steps": False}
    assert KIMI_BUDGET_MATCHED == matched
    for k, v in matched.items():
        assert getattr(inner, k) == v, k
    # with chunk=1 the window really is the last five steps (chunk=10 would blank it)
    inner.cur_step = 7
    inner.history = {i: {} for i in range(7)}
    assert inner._history_steps() == [2, 3, 4, 5, 6]
    inner.reset()
    assert inner.agent_mode == "gui" and inner.allow_ask_user is False and inner.screen_size == (1920, 1080)
    names = [t["function"]["name"] for t in inner.tool_declares]
    assert set(names) == {"take_screenshot", "computer", "execute_pyautogui_code", "wait_for_state", "finish_task"}
    assert inner.max_tokens == 16000 and inner.max_steps == 7
    assert inner.base_url == "https://openrouter.ai/api/v1" and inner.api_key == "k"
    assert (run_dir / "system_prompt.txt").read_text() == inner.system_prompt
    assert inner.password == "password"
    assert 'The computer password is "password".' in inner.system_prompt
    assert "osworld-public-evaluation" not in inner.system_prompt
    # the payload the agent would send: no temperature/top_p, provider locked
    body = inner._payload([{"role": "user", "content": "hi"}])
    assert "temperature" not in body and "top_p" not in body
    assert body["provider"] == {"order": ["moonshotai"], "allow_fallbacks": True}
    assert body["model"] == "moonshotai/kimi-k3" and body["max_tokens"] == 16000
    d = agent.describe()
    assert d["agent"] == "kimi" and d["kimi_budget"] == "matched" and d["temperature_sent"] is None
    assert d["images_per_request_cap"] == 6 and d["history_window"] == 5


def test_kimi_upstream_budget_keeps_factory_settings(tmp_path):
    run_dir = tmp_path / "agent"
    run_dir.mkdir()
    cfg = AgentRunConfig(max_steps=7, max_tokens=16000, run_dir=run_dir,
                         options={"kimi_mode": "hybrid", "kimi_budget": "upstream"})
    agent = build_kimi_agent(_model(), _task(tmp_path), cfg, run_dir / "llm_raw.jsonl")
    inner = agent.inner
    assert inner.num_max_images == 100 and inner.max_history_length == 1000 and inner.fail_on_max_steps is True
    assert inner.max_tokens == 65536 and agent.describe()["max_tokens"] == 65536
    assert inner.agent_mode == "hybrid"
    assert "run_command" in [t["function"]["name"] for t in inner.tool_declares]
    body = inner._payload([{"role": "user", "content": "hi"}])
    # factory sampling settings kept, provider lock still applied (it is independent of the budget)
    assert body["temperature"] == 0.0 and body["top_p"] == 0.95
    assert body["provider"] == {"order": ["moonshotai"], "allow_fallbacks": True}
    assert agent.describe()["temperature_sent"] == 0.0 and agent.describe()["agent_mode"] == "hybrid"
    no_provider = build_kimi_agent(_model(provider_order=()), _task(tmp_path), cfg, run_dir / "llm_raw2.jsonl")
    assert "provider" not in no_provider.inner._payload([{"role": "user", "content": "hi"}])


def test_default_budget_is_upstream(tmp_path):
    """The recorded stat sweep ran on Moonshot's factory budget; that is the default."""
    run_dir = tmp_path / "agent"
    run_dir.mkdir()
    cfg = AgentRunConfig(max_steps=3, max_tokens=16000, run_dir=run_dir, options={})
    agent = build_kimi_agent(_model(), _task(tmp_path), cfg, run_dir / "llm_raw.jsonl")
    inner = agent.inner
    assert agent.budget == "upstream" and agent.describe()["kimi_budget"] == "upstream"
    assert inner.num_max_images == 100 and inner.max_history_length == 1000
    assert inner.max_tokens == 65536 and inner.fail_on_max_steps is True
    body = inner._payload([{"role": "user", "content": "hi"}])
    assert body["temperature"] == 0.0 and body["top_p"] == 0.95 and body["max_tokens"] == 65536
    assert body["provider"] == {"order": ["moonshotai"], "allow_fallbacks": True}
    assert agent.describe()["temperature_sent"] == 0.0 and agent.describe()["max_tokens"] == 65536


def test_feedback_plumbing(tmp_path):
    run_dir = tmp_path / "agent"
    run_dir.mkdir()
    cfg = AgentRunConfig(max_steps=3, max_tokens=100, run_dir=run_dir, options={})
    agent = build_kimi_agent(_model(), _task(tmp_path), cfg, run_dir / "llm_raw.jsonl")
    agent.observe_result(ActionResult("x()", "code", False, 1, "", "boom"))
    assert agent._pending_result == {"success": False, "returncode": 1, "stderr": "boom"}
    agent.observe_result(ActionResult({"action_type": "RUN_COMMAND"}, "run_command", True, 0, "out", ""))
    assert agent._pending_result["stdout"] == "out"
    agent.observe_result(ActionResult("WAIT", "wait", True))
    assert agent._pending_result == {"success": True}
    agent.observe_result(ActionResult({"action_type": "ASK_USER"}, "unsupported", False))
    assert agent._pending_result["success"] is False and "not available" in agent._pending_result["error"]
    agent.observe_result(ActionResult({"action_type": "WAIT_FOR_STATE"}, "wait_for_state", True,
                                      extra={"waited_seconds": 3}))
    assert agent._pending_result == {"success": True, "waited_seconds": 3}
    # legacy truncation limits: stdout 8000 / stderr 4000 for shell results, stderr 2000 for code
    agent.observe_result(ActionResult({"action_type": "RUN_COMMAND"}, "run_command", True, 0, "o" * 9000, "e" * 5000))
    assert len(agent._pending_result["stdout"]) == 8000 and len(agent._pending_result["stderr"]) == 4000
    agent.observe_result(ActionResult("x()", "code", False, 1, "", "e" * 3000))
    assert len(agent._pending_result["stderr"]) == 2000
    # the pending result is handed to the inner agent exactly once, via obs["last_tool_result"]
    calls = []

    def fake_predict(instruction, obs):
        calls.append(dict(obs))
        return "r", ["DONE"]
    agent.inner.predict = fake_predict
    agent.predict("t", Observation(b"png"))
    assert calls[0]["last_tool_result"]["stderr"] == "e" * 2000 and agent._pending_result is None
    agent.predict("t", Observation(b"png"))
    assert "last_tool_result" not in calls[1] and calls[1]["screenshot"] == b"png"


def test_real_request_path_and_accounting(tmp_path, monkeypatch):
    """Drive inner.predict through the vendored request code with a fake HTTP
    layer: the wire payload, the tool-call parse, the tool-result feedback and
    the usage accounting are all exercised end to end."""
    import io
    import json as _json

    from PIL import Image

    from osworld_science.agents.kimi.upstream import kimi_agent as km

    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(buf, "PNG")  # screen-sized, like a real capture
    png = buf.getvalue()
    captured = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "execute_pyautogui_code",
                    "arguments": _json.dumps({"code": "pyautogui.click(100, 200)"})}}]}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                              "cost": 0.001, "prompt_tokens_details": {"cached_tokens": 40}}}

    def fake_post(url, headers=None, data=None, timeout=None, verify=None, **kw):
        captured.append({"url": url, "headers": headers, "body": _json.loads(data)})
        return Resp()
    monkeypatch.setattr(km.requests, "post", fake_post)

    run_dir = tmp_path / "agent"
    run_dir.mkdir()
    cfg = AgentRunConfig(max_steps=5, max_tokens=16000, run_dir=run_dir, options={"kimi_budget": "matched"})
    agent = build_kimi_agent(_model(), _task(tmp_path), cfg, run_dir / "llm_raw.jsonl")
    response, actions = agent.predict("Do it.", Observation(png))
    # (coordinates <= 1 would be read as normalised and rescaled to the screen)
    assert any(isinstance(a, str) and "pyautogui.click(100, 200)" in a for a in actions), actions
    assert response == "" or isinstance(response, str)
    body = captured[0]["body"]
    assert captured[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer k"
    assert "temperature" not in body and "top_p" not in body
    assert body["provider"] == {"order": ["moonshotai"], "allow_fallbacks": True}
    assert body["model"] == "moonshotai/kimi-k3" and body["max_tokens"] == 16000
    assert {t["function"]["name"] for t in body["tools"]} >= {"execute_pyautogui_code", "finish_task"}

    agent.observe_result(ActionResult(actions[0], "code", False, 1, "", "boom-stderr"))
    agent.predict("Do it.", Observation(png))
    assert "boom-stderr" in _json.dumps(captured[1]["body"])  # the tool result reached the model
    assert len(captured[1]["body"]["messages"]) > len(body["messages"])

    stats = agent.token_stats()
    assert stats == {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220,
                     "cached_input_tokens": 80, "n_llm_calls": 2,
                     "cost_usd": (200 * 3.0 + 20 * 15.0) / 1e6, "upstream_cost_usd": 0.002}
    lines = [_json.loads(x) for x in (run_dir / "llm_raw.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["tool_calls"] == ["execute_pyautogui_code"]
    assert lines[0]["usage"]["prompt_tokens"] == 100 and lines[0]["finish"] == "tool_calls"
