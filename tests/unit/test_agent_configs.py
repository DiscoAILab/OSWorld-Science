"""configs/agents/*.yaml: loading, command-line overrides, the api choice and how a
sweep picks and validates the agent for each model (no network, no VM)."""
from pathlib import Path

import pytest

from osworld_science.agents.base import AgentRunConfig
from osworld_science.agents.config import (
    AgentConfig,
    agent_config,
    load_agent_config,
    load_agent_configs,
    parse_option,
)
from osworld_science.agents.registry import agent_names
from osworld_science.llm.models import BackendSpec, ModelRegistry, ModelSpec, ResolvedModel
from osworld_science.runner.pipeline import agent_for

REPO = Path(__file__).resolve().parents[2]


def test_shipped_agent_configs_cover_every_builtin_agent(settings):
    configs = load_agent_configs(settings.configs_dir / "agents")
    assert set(configs) == set(agent_names()) == {"prompt", "kimi", "openai-cua", "claude-cua", "codex"}
    assert configs["prompt"].api == "chat_completions" and configs["kimi"].api == "chat_completions"
    assert configs["openai-cua"].api == "responses" and configs["claude-cua"].api == "messages"
    assert configs["codex"].api == "responses" and configs["codex"].path.name == "codex.yaml"
    codex = configs["codex"].options
    assert codex["guest_shell"] is True and codex["host_shell"] is False and codex["web_search"] == "disabled"
    assert codex["screenshot_after_action"] is True and codex["ephemeral"] is False and "screenshot_window" not in codex
    window = {"limit": 6, "keep": 5, "prune": "drop_oldest"}
    assert configs["prompt"].options["screenshot_window"] == window
    for name in ("openai-cua", "claude-cua"):            # the computer-use agents ship a wider window (8 / 4)
        assert configs[name].options["screenshot_window"] == {"limit": 8, "keep": 4, "prune": "drop_oldest"}, name
    assert configs["claude-cua"].options["tool_version"] == "toolset" and configs["claude-cua"].path.name == "claude_cua.yaml"
    assert configs["prompt"].options["image_detail"] == "high"
    assert configs["kimi"].options["mode"] == "gui" and configs["kimi"].options["budget"] == "upstream"
    assert configs["kimi"].options["budgets"]["matched"]["screenshot_window"] == window
    assert configs["kimi"].options["budgets"]["upstream"]["screenshot_window"] is None   # Moonshot's own compaction
    cua = configs["openai-cua"].options
    assert cua["tool_protocol"] == "auto" and cua["native_computer_backends"] == ["openai", "azure"]
    assert cua["reasoning_effort"] == "medium" and cua["parallel_tool_calls"] is False
    assert configs["prompt"].path.name == "prompt.yaml" and configs["openai-cua"].path.name == "openai_cua.yaml"


def test_overrides_and_builtin_defaults(settings):
    configs = load_agent_configs(settings.configs_dir / "agents")
    cfg = agent_config(configs, "prompt", {"screenshot_window.limit": 30})      # one field of the mapping
    assert cfg.options["screenshot_window"] == {"limit": 30, "keep": 5, "prune": "drop_oldest"}
    assert cfg.options["image_detail"] == "high" and cfg.api == "chat_completions"
    assert configs["prompt"].options["screenshot_window"]["limit"] == 6     # the loaded config is not mutated
    with pytest.raises(ValueError, match="keep"):
        agent_config(configs, "prompt", {"screenshot_window.limit": 4})     # keep 5 >= limit 4
    with pytest.raises(ValueError, match="replaced by screenshot_window"):
        agent_config(configs, "prompt", {"history_window": 10})
    assert agent_config(configs, "openai-cua", {"api": "chat_completions"}).api == "chat_completions"
    with pytest.raises(ValueError):
        agent_config(configs, "prompt", {"api": "grpc"})
    nothing = agent_config(configs, "mine")                            # an agent without a file
    assert nothing.path is None and nothing.api == "chat_completions" and nothing.options == {}
    assert agent_config(configs, "kimi").describe()["config_file"].endswith("kimi.yaml")


def test_parse_option_reads_yaml_scalars():
    assert parse_option("history_window=7") == ("history_window", 7)
    assert parse_option("parallel_tool_calls=false") == ("parallel_tool_calls", False)
    assert parse_option("tool_protocol=function_tools") == ("tool_protocol", "function_tools")
    assert parse_option("image_detail='high'") == ("image_detail", "high")
    assert parse_option("x=") == ("x", "")
    with pytest.raises(ValueError):
        parse_option("no_equals_sign")


def test_load_agent_config_rejects_bad_files(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("history_window: 5\n")
    with pytest.raises(ValueError):
        load_agent_config(bad)                                         # no `agent:` key
    bad.write_text("agent: prompt\napi: websocket\n")
    with pytest.raises(ValueError):
        load_agent_config(bad)
    (tmp_path / "a.yaml").write_text("agent: prompt\n")
    (tmp_path / "b.yaml").write_text("agent: prompt\n")
    with pytest.raises(ValueError):
        load_agent_configs(tmp_path)                                   # two files for one agent
    assert load_agent_configs(tmp_path / "missing") == {}


def test_agent_for_honours_the_models_agent_list(settings):
    reg = ModelRegistry(settings.configs_dir / "models.yaml")
    sol = reg.get("gpt-5.6-sol")
    assert agent_for(sol) == sol.default_agent and sol.default_agent in sol.agents
    assert agent_for(sol, "openai-cua") == "openai-cua" and agent_for(sol, "prompt") == "prompt"
    assert agent_for(reg.get("kimi-k3")) == "kimi"
    with pytest.raises(ValueError, match="does not run under agent 'openai-cua'"):
        agent_for(reg.get("claude-opus-5"), "openai-cua")
    with pytest.raises(ValueError, match="allows kimi"):
        agent_for(reg.get("kimi-k3"), "prompt")


def test_registry_rejects_legacy_agent_key_and_bad_defaults(tmp_path):
    base = ("backends:\n  b:\n    kind: openai_chat\n    base_url: https://x\n    key_env: K\n"
            "models:\n  m:\n    backend: b\n    model_id: m\n")
    (tmp_path / "ok.yaml").write_text(base)
    spec = ModelRegistry(tmp_path / "ok.yaml").get("m")
    assert spec.agents == ("prompt",) and spec.default_agent == "prompt"    # omitted = prompt only
    (tmp_path / "legacy.yaml").write_text(base + "    agent: kimi\n")
    with pytest.raises(ValueError, match="agents"):
        ModelRegistry(tmp_path / "legacy.yaml")
    (tmp_path / "bad.yaml").write_text(base + "    agents: [prompt]\n    default_agent: kimi\n")
    with pytest.raises(ValueError, match="default_agent"):
        ModelRegistry(tmp_path / "bad.yaml")
    (tmp_path / "old_url.yaml").write_text(base.replace("base_url", "url"))
    with pytest.raises(ValueError, match="base_url"):
        ModelRegistry(tmp_path / "old_url.yaml")


def _model(backend_name="azure", kind="openai_chat") -> ResolvedModel:
    backend = BackendSpec(name=backend_name, kind=kind, base_url_template="", key_env="K")
    return ResolvedModel(spec=ModelSpec(name="m", backend=backend, model_id="m", max_out=1000),
                         base_url="https://x.example/v1", key="k")


def test_agents_refuse_the_wrong_api(tmp_path):
    from osworld_science.agents.kimi import build_kimi_agent
    from osworld_science.agents.openai_cua import build_cua_agent
    from osworld_science.agents.prompt_agent.agent import build_prompt_agent
    raw = tmp_path / "raw.jsonl"
    with pytest.raises(ValueError, match="chat/completions only"):
        build_prompt_agent(_model(), object(), AgentRunConfig(1, 100, tmp_path, api="responses"), raw)
    with pytest.raises(ValueError, match="chat/completions only"):
        build_kimi_agent(_model(), object(), AgentRunConfig(1, 100, tmp_path, api="responses"), raw)
    with pytest.raises(ValueError, match="Responses API only"):
        build_cua_agent(_model(), object(), AgentRunConfig(1, 100, tmp_path, api="chat_completions"), raw)
    with pytest.raises(ValueError, match="no Responses API"):
        build_cua_agent(_model("anthropic", "anthropic_messages"), object(),
                        AgentRunConfig(1, 100, tmp_path, api="responses"), raw)


def test_cua_tool_protocol_from_config(tmp_path):
    import asyncio

    from agents import ComputerTool

    from osworld_science.agents.openai_cua import build_cua_agent
    raw = tmp_path / "raw.jsonl"
    raw.write_text("")
    base = {"api": "responses", "run_dir": tmp_path}
    forced = build_cua_agent(_model("openrouter"), object(),
                             AgentRunConfig(1, 100, options={"tool_protocol": "native_computer"}, **base), raw)
    auto_other = build_cua_agent(_model("openrouter"), object(),
                                 AgentRunConfig(1, 100, options={"tool_protocol": "auto",
                                                                 "native_computer_backends": ["azure"]}, **base), raw)
    listed = build_cua_agent(_model("openrouter"), object(),
                             AgentRunConfig(1, 100, options={"native_computer_backends": ["openrouter"]}, **base), raw)
    try:
        assert isinstance(forced.sdk_agent.tools[0], ComputerTool) and forced.tool_protocol == "native_computer"
        assert auto_other.tool_protocol == "function_tools"
        assert listed.tool_protocol == "native_computer"
        assert forced.describe()["api"] == "responses" and forced.reasoning_effort == "medium"
        assert forced.sdk_agent.model_settings.tool_choice == "required"
        tuned = build_cua_agent(_model(), object(),
                                AgentRunConfig(1, 100, options={"reasoning_effort": "low", "tool_choice": "auto",
                                                                "parallel_tool_calls": True, "truncation": "disabled"},
                                               **base), raw)
        assert tuned.reasoning_effort == "low" and tuned.sdk_agent.model_settings.tool_choice == "auto"
        assert tuned.sdk_agent.model_settings.parallel_tool_calls is True
        assert tuned.sdk_agent.model_settings.truncation == "disabled"
        env_wins = build_cua_agent(_model(), object(),
                                   AgentRunConfig(1, 100, reasoning_effort="high",
                                                  options={"reasoning_effort": "low"}, **base), raw)
        assert env_wins.reasoning_effort == "high"           # OSCI_REASONING_EFFORT beats the agent file
        asyncio.run(tuned.client.close())
        asyncio.run(env_wins.client.close())
        with pytest.raises(ValueError, match="tool_protocol"):
            build_cua_agent(_model(), object(), AgentRunConfig(1, 100, options={"tool_protocol": "magic"}, **base), raw)
    finally:
        for a in (forced, auto_other, listed):
            asyncio.run(a.client.close())


def test_kimi_budget_table_from_config(tmp_path):
    from osworld_science.agents.kimi import (
        KIMI_BUDGET_MATCHED,
        KIMI_MATCHED_WINDOW,
        build_kimi_agent,
    )
    from osworld_science.agents.kimi.adapter import budget_settings
    from osworld_science.agents.window import WindowConfig
    kwargs, window = budget_settings("matched", {})
    assert kwargs == KIMI_BUDGET_MATCHED and window == WindowConfig.from_value(KIMI_MATCHED_WINDOW)
    assert budget_settings("upstream", {}) == ({}, WindowConfig())          # no window: vendored compaction
    table = {"budgets": {"matched": {"num_max_images": 3}, "tiny": {"max_history_length": 1,
                                                                   "screenshot_window": {"limit": 4, "keep": 1}}}}
    assert budget_settings("matched", table) == ({"num_max_images": 3}, WindowConfig())   # the file wins
    assert budget_settings("tiny", table) == ({"max_history_length": 1}, WindowConfig(limit=4, keep=1))
    # a top-level window (the command line) is merged over the budget's
    assert budget_settings("tiny", {**table, "screenshot_window": {"limit": 9}})[1] == WindowConfig(limit=9, keep=1)
    with pytest.raises(ValueError):
        budget_settings("nope", table)
    (tmp_path / "agent").mkdir()
    raw = tmp_path / "agent" / "raw.jsonl"
    cfg = AgentRunConfig(3, 777, tmp_path / "agent", options={"budget": "tiny", **table, "request_timeout": 12})
    agent = build_kimi_agent(_model("moonshot"), object(), cfg, raw)
    assert agent.inner.max_history_length == 1 and agent.inner.max_tokens == 65536   # not matched: factory cap
    assert agent.inner.request_timeout == 12.0 and agent.describe()["kimi_budget"] == "tiny"
    matched = build_kimi_agent(_model("moonshot"), object(),
                               AgentRunConfig(3, 777, tmp_path / "agent", options={"budget": "matched"}), raw)
    assert matched.inner.max_tokens == 777 and matched.window.config == WindowConfig(limit=6, keep=5)
    assert matched.inner.num_max_images == 10 ** 6          # the vendored compaction is off while a window is set


def test_run_config_carries_the_api():
    assert AgentRunConfig(1, 1, Path(".")).api == "chat_completions"
    assert AgentConfig(name="x").with_overrides({}).api == "chat_completions"
