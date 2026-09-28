"""The OSWorld PromptAgent (screenshot in, pyautogui code out), ported for
the configuration every sweep used: observation_type="screenshot",
action_space="pyautogui", max_trajectory_length=5, no full-history summary.

Message construction, history windowing, image detail, the reflection
convention ("No valid action" for an empty previous reply) and the
"unparsable → WAIT" rule are unchanged; the LLM call goes through the
injected `ModelCaller`.
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
from .parsing import parse_code_from_string
from .prompts import SYS_PROMPT_IN_SCREENSHOT_OUT_CODE

STEP_PROMPT = "Given the screenshot as below. What's the next step that you will do to help with the task?"


class PromptAgent(AgentBase):
    name = "prompt"
    predict_attempts = 2
    stop_on_empty_response = True

    def __init__(self, caller: ModelCaller, model_id: str, max_tokens: int = 16000,
                 max_trajectory_length: int = 5, image_detail: str = "high",
                 top_p: float = 0.9, temperature: float = 0.5):
        self.caller = caller
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.max_trajectory_length = max_trajectory_length
        self.image_detail = image_detail
        self.top_p, self.temperature = top_p, temperature
        self.system_message = SYS_PROMPT_IN_SCREENSHOT_OUT_CODE
        self.thoughts: list[str] = []
        self.actions: list[list] = []
        self.observations: list[dict] = []

    def reset(self) -> None:
        self.thoughts, self.actions, self.observations = [], [], []

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
        n = self.max_trajectory_length
        if len(self.observations) > n:
            obs_w, act_w, th_w = (self.observations[-n:], self.actions[-n:], self.thoughts[-n:]) \
                if n else ([], [], [])
        else:
            obs_w, act_w, th_w = self.observations, self.actions, self.thoughts
        for prev_obs, _prev_action, prev_thought in zip(obs_w, act_w, th_w, strict=False):
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
        return {"agent": self.name, "history_window": self.max_trajectory_length,
                "image_detail": self.image_detail, "max_tokens": self.max_tokens,
                "temperature_sent": None}


@register_agent("prompt")
def build_prompt_agent(model: ResolvedModel, task: Task, cfg: AgentRunConfig, raw_log: Path,
                       log=print) -> PromptAgent:
    caller = ModelCaller(model, raw_log, reasoning_effort=cfg.reasoning_effort, log=log)
    agent = PromptAgent(caller, model_id=model.name, max_tokens=cfg.max_tokens,
                        max_trajectory_length=int(cfg.options.get("history_window", 5)),
                        image_detail=cfg.image_detail)
    agent.reset()
    return agent
