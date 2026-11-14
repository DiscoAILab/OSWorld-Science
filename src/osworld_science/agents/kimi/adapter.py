"""Adapter around Moonshot's KimiAgent (tool-calling computer use).

The agent itself is vendored, unmodified, in `.upstream` (from xlang-ai/OSWorld
`mm_agents/kimi/`, Apache-2.0). This module reproduces the legacy sweep's
wrapping exactly:

  * `upstream` budget (default): Moonshot's factory settings — up to 100
    screenshots per request, compacted to 10 by the vendored agent itself,
    history 1000, reply cap 65536, temperature 0.0 and top_p 0.95 sent,
    forced FAIL on the last step — the configuration the recorded stat sweep
    ran on. It is NOT budget-matched with the other models; `matched` aligns
    the screenshot window (limit 6, keep 5), a 5-step history with slide
    chunk 1, no forced FAIL and the CLI max_tokens with the PromptAgent arm;
  * the shared screenshot window (agents/window.py) is applied on top of the
    vendored message builder whenever the budget (or the command line) sets
    one: every image is tagged with its step on the way in, pruned images
    become Moonshot's own "[Screenshot at Step n]" text, and the vendored
    image compaction is switched off so only one mechanism decides;
  * temperature/top_p stripped from the payload (matched only);
  * `provider.order` injected for OpenRouter;
  * tool results fed back through `obs["last_tool_result"]`;
  * `RUN_COMMAND` (hybrid mode only) and `WAIT_FOR_STATE` executed by the runner.
"""
from __future__ import annotations

import json
from pathlib import Path

from ...llm.models import ResolvedModel
from ...llm.usage import UsageMeter
from ...tasks.model import Task
from ..base import ActionResult, AgentBase, AgentRunConfig, Observation
from ..registry import register_agent
from ..window import ScreenshotWindow, WindowConfig
from .upstream import KimiAgent

KIMI_BUDGET_MATCHED = {
    "max_history_length": 5,     # 5 earlier steps of text, the PromptAgent's history
    "history_slide_chunk": 1,    # the default 10 would blank the whole window at 5
    "fail_on_max_steps": False,  # do not burn the last step on a forced FAIL
}
KIMI_MATCHED_WINDOW = {"limit": 6, "keep": 5, "prune": "drop_oldest"}   # 5 earlier + current
NO_COMPACTION = 10 ** 6          # num_max_images that the vendored compaction never reaches



class _UsageTap:
    """Wrap agent.call_llm only for accounting; the upstream implementation
    (with its own back-off) is kept intact."""

    def __init__(self, agent, model: ResolvedModel, raw_log: Path):
        self._inner = agent.call_llm
        self.model, self.raw = model, raw_log
        self.meter = UsageMeter.for_model(model)     # tokens, cache, cost (llm/usage.py)

    def __call__(self, payload):
        content, info = self._inner(payload)
        usage = info.get("usage") or {}
        counted = self.meter.add(usage) if usage else None
        with self.raw.open("a") as f:
            f.write(json.dumps({"model": self.model.name, "finish": info.get("finish_reason"),
                                "tool_calls": [tc.get("function", {}).get("name")
                                               for tc in (info.get("tool_calls") or [])],
                                "content_len": len(content or ""), "usage": usage or None,
                                "counted": counted},
                               ensure_ascii=False) + "\n")
        return content, info

    def token_stats(self) -> dict:
        return self.meter.stats()


class KimiAgentAdapter(AgentBase):
    name = "kimi"
    predict_attempts = 1
    stop_on_empty_response = False

    def __init__(self, inner, tap: _UsageTap, mode: str, budget: str, window: ScreenshotWindow | None = None):
        self.inner, self.tap, self.mode, self.budget = inner, tap, mode, budget
        self.window = window
        self._pending_result = None

    def reset(self) -> None:
        self._pending_result = None

    def predict(self, instruction: str, obs: Observation) -> tuple[str, list]:
        o = {"screenshot": obs.screenshot}
        if self._pending_result is not None:
            o["last_tool_result"] = self._pending_result
            self._pending_result = None
        response, actions = self.inner.predict(instruction, o)
        return response, actions

    def observe_result(self, result: ActionResult) -> None:
        if result.kind == "wait":
            self._pending_result = {"success": True}
        elif result.kind == "wait_for_state":
            self._pending_result = {"success": True, **result.extra}
        elif result.kind == "code":
            self._pending_result = {"success": result.success, "returncode": result.returncode,
                                    "stderr": result.stderr[:2000]}
        elif result.kind == "run_command":
            self._pending_result = {"success": result.success, "returncode": result.returncode,
                                    "stdout": result.stdout[:8000], "stderr": result.stderr[:4000]}
        else:
            self._pending_result = {"success": False,
                                    "error": "This tool is not available in this benchmark; "
                                             "continue using the desktop tools."}

    def token_stats(self) -> dict:
        return self.tap.token_stats()

    def describe(self) -> dict:
        if self.window is not None:
            images = self.window.stats()
        else:      # the vendored agent compacts on its own (the `upstream` budget)
            images = {"screenshot_window": None, "kimi_image_compaction": {
                "num_max_images": self.inner.num_max_images, "num_compact_images": self.inner.num_compact_images}}
        return {"agent": "kimi", "api": "chat_completions", "agent_mode": self.mode, "kimi_budget": self.budget,
                **images,
                "max_history_steps": self.inner.max_history_length,
                "history_slide_chunk": self.inner.history_slide_chunk,
                "fail_on_max_steps": self.inner.fail_on_max_steps,
                "temperature_sent": None if self.budget == "matched" else self.inner.temperature,
                "max_tokens": self.inner.max_tokens}

    @property
    def system_prompt(self) -> str:
        return self.inner.system_prompt


def budget_settings(budget: str, options: dict) -> tuple[dict, WindowConfig]:
    """(KimiAgent constructor overrides, screenshot window) for a named budget: the
    `budgets:` table of configs/agents/kimi.yaml when present, else the built-in ones
    (`upstream` = Moonshot's factory settings, no window; `matched` = KIMI_BUDGET_MATCHED +
    limit 6 / keep 5). A top-level `screenshot_window` (the command line) is merged over
    the budget's."""
    if "history_window" in options:
        WindowConfig.from_options(options)          # raises with the migration hint
    table = options.get("budgets") or {}
    if budget in table:
        kwargs = dict(table[budget] or {})
        window = kwargs.pop("screenshot_window", None)
    elif budget == "matched":
        kwargs, window = dict(KIMI_BUDGET_MATCHED), dict(KIMI_MATCHED_WINDOW)
    elif budget == "upstream":
        kwargs, window = {}, None
    else:
        raise ValueError(f"kimi budget {budget!r} is neither matched/upstream nor a key of `budgets:` "
                         f"in configs/agents/kimi.yaml")
    if "screenshot_window" in options:
        override = options["screenshot_window"]
        window = {**window, **override} if isinstance(window, dict) and isinstance(override, dict) else override
    return kwargs, WindowConfig.from_value(window, where=f"kimi budget {budget}: screenshot_window")


def install_window(inner, config: WindowConfig) -> ScreenshotWindow:
    """Apply the shared screenshot window to the vendored agent's requests without touching its
    code. Every image the message builder emits comes from `_image_message_from_obs(observation)`;
    the observation objects are the ones stored in `inner.history[step]` (the live one is the
    current step), so the wrapper tags each image with its step. After `prepare_messages` the
    window decides which steps' images stay; the others become "[Screenshot at Step n]", the
    vendored agent's own wording for a compacted image."""
    window = ScreenshotWindow(config)
    steps_by_obs: dict[int, int] = {}
    original_image = inner._image_message_from_obs
    original_prepare = inner.prepare_messages

    def image_with_step(obs):
        part = original_image(obs)
        return {**part, "_osci_step": steps_by_obs.get(id(obs), inner.cur_step)}

    def prepare_messages(instruction, obs):
        steps_by_obs.clear()
        steps_by_obs.update({id(h.get("observation")): step for step, h in inner.history.items()
                             if isinstance(h, dict) and h.get("observation") is not None})
        messages = original_prepare(instruction, obs)
        slots = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                slots += [(part["_osci_step"], content, i) for i, part in enumerate(content)
                          if isinstance(part, dict) and "_osci_step" in part]
        omit = window.apply(sorted({step for step, _, _ in slots}))      # steps are chronological
        for step, content, i in slots:
            if step in omit:
                content[i] = {"type": "text", "text": f"[Screenshot at Step {step}]"}
            else:
                content[i] = {k: v for k, v in content[i].items() if k != "_osci_step"}
        return messages

    inner._image_message_from_obs = image_with_step
    inner.prepare_messages = prepare_messages
    return window


@register_agent("kimi")
def build_kimi_agent(model: ResolvedModel, task: Task, cfg: AgentRunConfig, raw_log: Path,
                     log=print) -> KimiAgentAdapter:
    """Knobs come from configs/agents/kimi.yaml (`mode`, `budget`, `budgets` with their
    `screenshot_window`, `request_timeout`) with the command line on top; the vendored agent
    posts to chat/completions itself."""
    if cfg.api != "chat_completions":
        raise ValueError(f"the kimi agent speaks chat/completions only (configs/agents/kimi.yaml says "
                         f"api: {cfg.api})")
    mode = str(cfg.options.get("mode", "gui"))
    budget = str(cfg.options.get("budget", "upstream"))
    kwargs, window_config = budget_settings(budget, cfg.options)
    if window_config.limit is not None:      # one mechanism decides: switch the vendored compaction off
        kwargs["num_max_images"] = kwargs["num_compact_images"] = NO_COMPACTION
    if budget == "matched":
        kwargs.setdefault("max_tokens", cfg.max_tokens)
    if cfg.options.get("request_timeout") is not None:
        kwargs["request_timeout"] = float(cfg.options["request_timeout"])
    inner = KimiAgent(model=model.model_id, max_steps=cfg.max_steps, agent_mode=mode,
                      password="password", screen_size=cfg.screen_size,
                      base_url=model.base_url, api_key=model.key,
                      allow_ask_user=False, **kwargs)
    inner.reset()
    window = install_window(inner, window_config) if window_config.limit is not None else None
    if budget == "matched":
        original = inner._payload

        def no_sampling(messages, temperature=None):
            body = original(messages, temperature=temperature)
            body.pop("temperature", None)
            body.pop("top_p", None)
            return body
        inner._payload = no_sampling
    tap = _UsageTap(inner, model, raw_log)
    inner.call_llm = tap
    if model.spec.provider_order:
        prev = inner._payload

        def with_provider(messages, temperature=None):
            body = prev(messages, temperature=temperature)
            body["provider"] = {"order": list(model.spec.provider_order), "allow_fallbacks": True}
            return body
        inner._payload = with_provider
    (cfg.run_dir / "system_prompt.txt").write_text(inner.system_prompt, encoding="utf-8")
    return KimiAgentAdapter(inner, tap, mode, budget, window)
