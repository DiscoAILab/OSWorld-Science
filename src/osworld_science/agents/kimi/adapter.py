"""Adapter around Moonshot's KimiAgent (tool-calling computer use).

The agent itself is vendored, unmodified, in `.upstream` (from xlang-ai/OSWorld
`mm_agents/kimi/`, Apache-2.0). This module reproduces the legacy sweep's
wrapping exactly:

  * `upstream` budget (default): Moonshot's factory settings — up to 100
    screenshots per request, history 1000, reply cap 65536, temperature 0.0
    and top_p 0.95 sent, forced FAIL on the last step — the configuration the
    recorded stat sweep ran on. It is NOT budget-matched with the other models;
    `matched` aligns history window 5, slide chunk 1, six images per request,
    no forced FAIL and the CLI max_tokens with the PromptAgent arm;
  * temperature/top_p stripped from the payload (matched only);
  * `provider.order` injected for OpenRouter;
  * tool results fed back through `obs["last_tool_result"]`;
  * `RUN_COMMAND` (hybrid mode only) and `WAIT_FOR_STATE` executed by the runner.
"""
from __future__ import annotations

import json
from pathlib import Path

from ...llm.models import ResolvedModel
from ...tasks.model import Task
from ..base import ActionResult, AgentBase, AgentRunConfig, Observation
from ..registry import register_agent
from .upstream import KimiAgent

KIMI_BUDGET_MATCHED = {
    "max_history_length": 5,     # aligned with max_trajectory_length=5
    "history_slide_chunk": 1,    # the default 10 would blank the whole window at 5
    "num_max_images": 6,         # 5 history + current, exactly the PromptAgent's count
    "num_compact_images": 1,
    "fail_on_max_steps": False,  # do not burn the last step on a forced FAIL
}



class _UsageTap:
    """Wrap agent.call_llm only for accounting; the upstream implementation
    (with its own back-off) is kept intact."""

    def __init__(self, agent, model: ResolvedModel, raw_log: Path):
        self._inner = agent.call_llm
        self.model, self.raw = model, raw_log
        self.input_tokens = self.output_tokens = self.total_tokens = 0
        self.cached_input_tokens = self.calls = 0
        self.upstream_cost: float | None = None

    def __call__(self, payload):
        content, info = self._inner(payload)
        usage = info.get("usage") or {}
        if usage:
            self.calls += 1
            self.input_tokens += int(usage.get("prompt_tokens") or 0)
            self.output_tokens += int(usage.get("completion_tokens") or 0)
            self.total_tokens += int(usage.get("total_tokens") or 0)
            details = usage.get("prompt_tokens_details") or {}
            self.cached_input_tokens += int(details.get("cached_tokens") or 0)
            if usage.get("cost") is not None:
                self.upstream_cost = (self.upstream_cost or 0.0) + float(usage["cost"])
        with self.raw.open("a") as f:
            f.write(json.dumps({"model": self.model.name, "finish": info.get("finish_reason"),
                                "tool_calls": [tc.get("function", {}).get("name")
                                               for tc in (info.get("tool_calls") or [])],
                                "content_len": len(content or ""), "usage": usage or None},
                               ensure_ascii=False) + "\n")
        return content, info

    def cost_usd(self):
        price = self.model.spec.price
        if not price or price.get("in") is None or price.get("out") is None:
            return None
        return (self.input_tokens * price["in"] + self.output_tokens * price["out"]) / 1e6

    def token_stats(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens, "cached_input_tokens": self.cached_input_tokens,
                "n_llm_calls": self.calls, "cost_usd": self.cost_usd(),
                "upstream_cost_usd": self.upstream_cost}


class KimiAgentAdapter(AgentBase):
    name = "kimi"
    predict_attempts = 1
    stop_on_empty_response = False

    def __init__(self, inner, tap: _UsageTap, mode: str, budget: str):
        self.inner, self.tap, self.mode, self.budget = inner, tap, mode, budget
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
        return {"agent": "kimi", "agent_mode": self.mode, "kimi_budget": self.budget,
                "images_per_request_cap": self.inner.num_max_images,
                "history_window": self.inner.max_history_length,
                "fail_on_max_steps": self.inner.fail_on_max_steps,
                "temperature_sent": None if self.budget == "matched" else self.inner.temperature,
                "max_tokens": self.inner.max_tokens}

    @property
    def system_prompt(self) -> str:
        return self.inner.system_prompt


@register_agent("kimi")
def build_kimi_agent(model: ResolvedModel, task: Task, cfg: AgentRunConfig, raw_log: Path,
                     log=print) -> KimiAgentAdapter:
    mode = cfg.options.get("kimi_mode", "gui")
    budget = cfg.options.get("kimi_budget", "upstream")
    kwargs = dict(KIMI_BUDGET_MATCHED) if budget == "matched" else {}
    if budget == "matched":
        kwargs["max_tokens"] = cfg.max_tokens
    inner = KimiAgent(model=model.model_id, max_steps=cfg.max_steps, agent_mode=mode,
                      password="password", screen_size=cfg.screen_size,
                      base_url=model.base_url, api_key=model.key,
                      allow_ask_user=False, **kwargs)
    inner.reset()
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
    return KimiAgentAdapter(inner, tap, mode, budget)
