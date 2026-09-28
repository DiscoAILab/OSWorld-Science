"""The agent protocol the runner drives (see docs/developer_guide/adding-an-agent.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Action = Any  # str (special token or python code) | dict(action_type=...)


@dataclass
class Observation:
    screenshot: bytes
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    action: Action
    kind: str                 # code | run_command | wait | wait_for_state | unsupported
    success: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunConfig:
    """Everything an agent factory may need besides the model."""
    max_steps: int
    max_tokens: int
    run_dir: Path
    image_detail: str = "high"
    reasoning_effort: str = ""
    screen_size: tuple[int, int] = (1920, 1080)
    options: dict[str, Any] = field(default_factory=dict)   # agent-specific (kimi_mode, kimi_budget …)


class AgentBase:
    """Default implementations; subclasses override what they need."""
    name: str = "base"
    predict_attempts: int = 1
    stop_on_empty_response: bool = False

    def reset(self) -> None:
        pass

    def predict(self, instruction: str, obs: Observation) -> tuple[str, list[Action]]:
        raise NotImplementedError

    def rollback_failed_prediction(self) -> None:
        pass

    def observe_result(self, result: ActionResult) -> None:
        pass

    def token_stats(self) -> dict:
        return {}

    def describe(self) -> dict:
        return {"agent": self.name}
