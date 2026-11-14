"""The OSWorld PromptAgent (screenshot in, pyautogui code out), ported for
the configuration every sweep used: observation_type="screenshot",
action_space="pyautogui", max_trajectory_length=5, no full-history summary.

Message construction, image detail, the reflection convention ("No valid
action" for an empty previous reply) and the "unparsable → WAIT" rule are
unchanged; the LLM call goes through the injected `ModelCaller`. The history
is bounded by the shared screenshot window (agents/window.py): a pruned
screenshot takes its whole turn (the screenshot and the reply to it) out of
the request; `limit: 6, keep: 5` is the recorded sweeps' window of five.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from ...llm.caller import ModelCaller
from ...llm.models import ResolvedModel
from ...tasks.model import Task
from ..base import ActionResult, AgentBase, AgentRunConfig, Observation
from ..registry import register_agent
from ..window import ScreenshotWindow, WindowConfig
from .parsing import parse_code_from_string
from .prompts import SYS_PROMPT_IN_SCREENSHOT_OUT_CODE

STEP_PROMPT = "Given the screenshot as below. What's the next step that you will do to help with the task?"


class PromptAgent(AgentBase):
    name = "prompt"
    predict_attempts = 2
    stop_on_empty_response = True

    def __init__(self, caller: ModelCaller, model_id: str, max_tokens: int = 16000,
                 max_trajectory_length: int | None = 5, image_detail: str = "high",
                 top_p: float = 0.9, temperature: float = 0.5, window: WindowConfig | None = None):
        """`window` is the screenshot window; without one, `max_trajectory_length` N earlier
        steps slide (limit N + 1, keep N), the upstream PromptAgent's parameter."""
        self.caller = caller
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.window = ScreenshotWindow(window or WindowConfig.sliding(max_trajectory_length))
        self.image_detail = image_detail
        self.top_p, self.temperature = top_p, temperature
        self.system_message = SYS_PROMPT_IN_SCREENSHOT_OUT_CODE
        self.thoughts: list[str] = []
        self.actions: list[list] = []
        self.observations: list[dict] = []

    def reset(self) -> None:
        self.thoughts, self.actions, self.observations = [], [], []
        self.window.reset()

    @staticmethod
    def _screen_size(png: bytes) -> tuple[int, int]:
        try:
            from PIL import Image
            return Image.open(io.BytesIO(png)).size
        except Exception:  # noqa: BLE001
            return 1280, 800

    def build_messages(self, instruction: str, png: bytes) -> list[dict]:
        sx, sy = self._screen_size(png)
        system_message = (self.system_message.replace("{SCREENSHOT_X}", str(sx))
                          .replace("{SCREENSHOT_Y}", str(sy))
                          + f"\nYou are asked to complete the following task: {instruction}")
        messages = [{"role": "system", "content": [{"type": "text", "text": system_message}]}]

        assert len(self.observations) == len(self.actions) == len(self.thoughts), \
            "The number of observations and actions should be the same."
        # screenshot i is step i's; the current one (index len) is never pruned
        omit = self.window.apply(list(range(len(self.observations) + 1)))
        turns = [i for i in range(len(self.observations)) if i not in omit]
        for i in turns:
            prev_obs, prev_thought = self.observations[i], self.thoughts[i]
            messages.append({"role": "user", "content": [
                {"type": "text", "text": STEP_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{prev_obs['screenshot']}",
                    "detail": self.image_detail}}]})
            messages.append({"role": "assistant", "content": [
                {"type": "text", "text": prev_thought.strip() if prev_thought else "No valid action"}]})

        b64 = base64.b64encode(png).decode("utf-8")
        self.observations.append({"screenshot": b64, "accessibility_tree": None})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": STEP_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}",
                                                "detail": self.image_detail}}]})
        return messages

    def predict(self, instruction: str, obs: Observation) -> tuple[str, list]:
        messages = self.build_messages(instruction, obs.screenshot)
        try:
            response = self.caller({"model": self.model_id, "messages": messages,
                                    "max_tokens": self.max_tokens,
                                    "top_p": self.top_p, "temperature": self.temperature})
        except Exception:  # noqa: BLE001 — the caller already retried; treat as empty
            response = ""
        actions = self.parse_actions(response)
        self.thoughts.append(response)
        return response, actions

    def parse_actions(self, response: str) -> list:
        try:
            actions = parse_code_from_string(response)
        except ValueError:
            actions = ["WAIT"]  # unparsable reply → placeholder action (upstream rule)
        self.actions.append(actions)
        return actions

    def rollback_failed_prediction(self) -> None:
        while len(self.observations) > len(self.actions):
            self.observations.pop()

    def observe_result(self, result: ActionResult) -> None:
        pass  # the PromptAgent only ever sees screenshots

    def token_stats(self) -> dict:
        return self.caller.token_stats()

    def describe(self) -> dict:
        return {"agent": self.name, "api": getattr(self.caller, "api", "chat_completions"),
                **self.window.stats(),
                "image_detail": self.image_detail, "max_tokens": self.max_tokens,
                "temperature_sent": None}


@register_agent("prompt")
def build_prompt_agent(model: ResolvedModel, task: Task, cfg: AgentRunConfig, raw_log: Path,
                       log=print) -> PromptAgent:
    """Knobs come from configs/agents/prompt.yaml (`screenshot_window`, `image_detail`) with the
    command line on top; `api` must be chat_completions, the only one ModelCaller speaks."""
    if cfg.api != "chat_completions":
        raise ValueError(f"the prompt agent speaks chat/completions only (configs/agents/prompt.yaml says "
                         f"api: {cfg.api})")
    caller = ModelCaller(model, raw_log, reasoning_effort=cfg.reasoning_effort, log=log, api=cfg.api)
    agent = PromptAgent(caller, model_id=model.name, max_tokens=cfg.max_tokens,
                        window=WindowConfig.from_options(cfg.options, default={"limit": 6, "keep": 5}),
                        image_detail=cfg.image_detail)
    agent.reset()
    return agent
