"""The shared screenshot window (agents/window.py): limit / keep / prune semantics, the
algorithm registry, validation, the command-line mapping, and its application in the
PromptAgent and (through step-tagged images) the vendored Kimi agent."""
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from osworld_science.agents.base import AgentRunConfig, Observation
from osworld_science.agents.window import (
    Screenshot,
    ScreenshotWindow,
    WindowConfig,
    get_prune,
    prune_names,
    register_prune,
)


def request_sizes(config: WindowConfig, n: int) -> list[int]:
    """Screenshots carried by each of n requests, one new screenshot per request."""
    w, sizes = ScreenshotWindow(config), []
    for k in range(n):
        ids = list(range(k + 1))
        sizes.append(len([i for i in ids if i not in w.apply(ids)]))
    return sizes


def test_limit_and_keep_semantics():
    # limit 6 / keep 5 = the recorded sweeps' window: 5 earlier + the current, one leaving per step
    assert request_sizes(WindowConfig(limit=6, keep=5), 10) == [1, 2, 3, 4, 5, 6, 6, 6, 6, 6]
    # a gap between them: when the window holds `limit`, the next screenshot first cuts it to `keep`
    assert request_sizes(WindowConfig(limit=4, keep=1), 10) == [1, 2, 3, 4, 2, 3, 4, 2, 3, 4]
    # keep 5 / limit 30: the 6..30 cycle of the reference loop's interval pruning (interval 25)
    sizes = request_sizes(WindowConfig(limit=30, keep=5), 80)
    assert max(sizes) == 30 and sizes[30:55] == list(range(6, 31)) and sizes[55] == 6
    assert request_sizes(WindowConfig(limit=1, keep=0), 4) == [1, 1, 1, 1]
    assert request_sizes(WindowConfig(), 50)[-1] == 50                     # null limit never prunes
    w = ScreenshotWindow(WindowConfig(limit=4, keep=1))
    for k in range(10):
        w.apply(list(range(k + 1)))
    # screenshot 4 prunes 0-2 (keeps 3), screenshot 7 prunes 3-5 (keeps 6)
    assert w.pruned == {0, 1, 2, 3, 4, 5} and w.n_prunes == 2 and w.n_pruned == 6
    assert w.stats() == {"screenshot_window": {"limit": 4, "keep": 1, "prune": "drop_oldest"},
                         "screenshot_prunes": 2, "screenshots_pruned": 6}


def test_pruned_screenshots_never_return_and_the_newest_is_kept():
    w = ScreenshotWindow(WindowConfig(limit=2, keep=0))
    assert w.apply(["a", "b"]) == set()
    assert w.apply(["a", "b", "c"]) == {"a", "b"}
    assert w.apply(["b", "c", "d"]) == {"a", "b"}             # 'b' listed again stays out; c, d fit the limit
    assert w.apply(["c", "d", "e"]) == {"a", "b", "c", "d"}
    assert w.force(["e", "f", "g", "h"], keep=1) == {"a", "b", "c", "d", "e", "f"}   # a size cap prunes at once


def test_registered_and_imported_algorithms():
    @register_prune("keep_first_test")
    def keep_first(earlier, keep):
        """Always keep the run's very first screenshot, then the newest keep - 1."""
        first = [s for s in earlier if s.ordinal == 0]
        rest = [s for s in earlier if s.ordinal != 0]
        return (first + rest[len(rest) - (keep - len(first)):]) if keep > len(first) else first[:keep]
    assert "keep_first_test" in prune_names() and "drop_oldest" in prune_names()
    w = ScreenshotWindow(WindowConfig(limit=3, keep=2, prune="keep_first_test"))
    for k in range(6):
        omit = w.apply(list(range(k + 1)))
    assert [i for i in range(6) if i not in omit] == [0, 4, 5]
    assert get_prune("osworld_science.agents.window:drop_oldest") is get_prune("drop_oldest")
    with pytest.raises(ValueError, match="unknown"):
        WindowConfig.from_value({"limit": 3, "prune": "nope"})
    with pytest.raises(ValueError, match="cannot be imported"):
        get_prune("no_such_module_xyz:fn")

    @register_prune("greedy_test")
    def greedy(earlier, keep):
        return earlier                                                    # returns more than keep
    w = ScreenshotWindow(WindowConfig(limit=2, keep=1, prune="greedy_test"))
    with pytest.raises(ValueError, match="at most 1"):
        w.apply([1, 2, 3])
    assert isinstance(Screenshot(id=1, ordinal=0), Screenshot)


def test_config_validation_and_migration():
    assert WindowConfig.from_value(None) == WindowConfig()
    assert WindowConfig.from_value({"limit": 6}) == WindowConfig(limit=6, keep=5)          # keep defaults to limit - 1
    assert WindowConfig.from_value({"limit": None, "keep": 3}) == WindowConfig()
    assert WindowConfig.sliding(5) == WindowConfig(limit=6, keep=5) and WindowConfig.sliding(None) == WindowConfig()
    for bad in ({"limit": 0}, {"limit": 3, "keep": 3}, {"limit": 3, "keep": -1}, {"limit": "6"}, {"limt": 6}, [6, 5]):
        with pytest.raises(ValueError):
            WindowConfig.from_value(bad)
    with pytest.raises(ValueError, match="limit: N \\+ 1, keep: N"):
        WindowConfig.from_options({"history_window": 5})
    assert WindowConfig.from_options({}, default={"limit": 6, "keep": 5}) == WindowConfig(limit=6, keep=5)
    assert WindowConfig(limit=6, keep=5).describe() == {"limit": 6, "keep": 5, "prune": "drop_oldest"}
    assert WindowConfig().describe() is None


def test_command_line_shorthands():
    from osworld_science.cli import _agent_options

    def args(**kw):
        base = {"agent_option": None, "history_window": None, "screenshot_limit": None, "screenshot_keep": None,
                "screenshot_prune": None, "kimi_mode": None, "kimi_budget": None}
        return SimpleNamespace(**{**base, **kw})
    assert _agent_options(args()) == {}                  # no flag must not touch the configured window
    assert _agent_options(args(screenshot_limit="none")) == {"screenshot_window.limit": None}
    assert _agent_options(args(history_window=10)) == {"screenshot_window.limit": 11, "screenshot_window.keep": 10}
    assert _agent_options(args(screenshot_limit=30, screenshot_keep=5, screenshot_prune="x:y")) == {
        "screenshot_window.limit": 30, "screenshot_window.keep": 5, "screenshot_window.prune": "x:y"}
    assert _agent_options(args(history_window=3, screenshot_limit=9)) == {      # the explicit flag wins
        "screenshot_window.limit": 9, "screenshot_window.keep": 3}


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 16), (0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


class _Caller:
    def __init__(self):
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        return "```python\npyautogui.click(1, 1)\n```"

    def token_stats(self):
        return {}


def test_prompt_agent_drops_whole_turns_with_hysteresis():
    from osworld_science.agents.prompt_agent.agent import PromptAgent
    caller = _Caller()
    agent = PromptAgent(caller, "m", window=WindowConfig(limit=4, keep=1))
    for _ in range(7):
        agent.predict("t", Observation(_png()))
    images = [sum(c["type"] == "image_url" for m in p["messages"] for c in m["content"]) for p in caller.payloads]
    assert images == [1, 2, 3, 4, 2, 3, 4]
    turns = [sum(m["role"] == "assistant" for m in p["messages"]) for p in caller.payloads]
    assert turns == [i - 1 for i in images]                      # each earlier screenshot comes with its reply
    assert agent.describe()["screenshot_window"] == {"limit": 4, "keep": 1, "prune": "drop_oldest"}
    agent.reset()
    assert agent.window.n_pruned == 0
    # the upstream PromptAgent's parameter still means N earlier steps sliding
    assert PromptAgent(_Caller(), "m", max_trajectory_length=5).window.config == WindowConfig(limit=6, keep=5)


def test_kimi_window_tags_images_by_step(tmp_path, monkeypatch):
    """The vendored agent's own message builder, its images tagged with their steps and pruned
    by the shared window: pruned steps become Moonshot's '[Screenshot at Step n]' text."""
    from osworld_science.agents.kimi import build_kimi_agent
    from osworld_science.agents.kimi.upstream import kimi_agent as km
    from osworld_science.llm.models import BackendSpec, ModelSpec, ResolvedModel

    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(buf, "PNG")
    png = buf.getvalue()
    bodies = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "execute_pyautogui_code", "arguments": json.dumps({"code": "pyautogui.click(100, 200)"})}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}

    def fake_post(url, headers=None, data=None, timeout=None, verify=None, **kw):
        bodies.append(json.loads(data))
        return Resp()
    monkeypatch.setattr(km.requests, "post", fake_post)
    spec = ModelSpec(name="kimi-k3", backend=BackendSpec(name="moonshot", kind="openai_chat",
                                                          base_url_template="", key_env="K"),
                     model_id="kimi-k3", agents=("kimi",), default_agent="kimi")
    model = ResolvedModel(spec=spec, base_url="https://unused.invalid/v1", key="k")
    run_dir = tmp_path / "agent"
    run_dir.mkdir()
    options = {"budget": "wide", "budgets": {"wide": {"max_history_length": 100, "history_slide_chunk": 1,
                                                       "screenshot_window": {"limit": 3, "keep": 1}}}}
    agent = build_kimi_agent(model, object(), AgentRunConfig(10, 1000, run_dir, options=options),
                             run_dir / "llm_raw.jsonl")
    for _ in range(6):
        agent.predict("Do it.", Observation(png))

    def parts(body, kind):
        return [p for m in body["messages"] if isinstance(m.get("content"), list) for p in m["content"]
                if p.get("type") == kind]
    assert [len(parts(b, "image_url")) for b in bodies] == [1, 2, 3, 2, 3, 2]
    placeholders = [p["text"] for p in parts(bodies[-1], "text") if p["text"].startswith("[Screenshot at Step")]
    assert placeholders == [f"[Screenshot at Step {s}]" for s in (0, 1, 2, 3)]
    assert all("_osci_step" not in p for b in bodies for p in parts(b, "image_url"))   # the tag never leaves
    assert agent.inner.num_max_images == 10 ** 6
    assert agent.describe()["screenshots_pruned"] == 4
    base64.b64decode(parts(bodies[-1], "image_url")[0]["image_url"]["url"].split(",", 1)[1])   # still a real image
