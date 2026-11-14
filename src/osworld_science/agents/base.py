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
    api: str = "chat_completions"      # which OpenAI-style API the harness calls (configs/agents/<agent>.yaml)
    options: dict[str, Any] = field(default_factory=dict)   # the agent config's knobs plus CLI overrides


class AgentBase:
    """Default implementations; subclasses override what they need."""
    name: str = "base"
    predict_attempts: int = 1
    stop_on_empty_response: bool = False
    owns_episode_loop: bool = False

    def reset(self) -> None:
        pass

    def bind_runtime(self, controller: Any, guest: Any, **kwargs: Any) -> None:
        """Attach the live desktop runtime before an episode starts.

        Most agents return actions for the outer episode loop to execute and do
        not need this hook.  Agents whose own SDK owns the tool loop (for
        example, the OpenAI Agents SDK ``ComputerTool``) use it to execute
        actions and capture the required post-action screenshot synchronously.
        """
        pass

    def predict(self, instruction: str, obs: Observation) -> tuple[str, list[Action]]:
        raise NotImplementedError

    def rollback_failed_prediction(self) -> None:
        pass

    def observe_result(self, result: ActionResult) -> None:
        pass

    def episode_stats(self) -> dict[str, Any]:
        """Return step-loop counters owned internally by an agent SDK.

        Ordinary agents leave this empty because :func:`run_episode` owns their
        model/action loop.  An agent that sets ``owns_episode_loop`` may report
        the model turns and failed actions performed inside one ``predict`` call.
        """
        return {}

    def token_stats(self) -> dict:
        return {}

    def describe(self) -> dict:
        return {"agent": self.name}
