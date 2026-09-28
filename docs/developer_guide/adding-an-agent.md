# Adding an agent

The runner drives any object that implements the protocol in
`osworld_science.agents.base`:

```python
from pathlib import Path
from osworld_science.agents import AgentBase, AgentRunConfig, Observation, ActionResult, register_agent
from osworld_science.llm.models import ResolvedModel
from osworld_science.tasks.model import Task

class MyAgent(AgentBase):
    name = "mine"
    predict_attempts = 1            # how often the runner retries predict() after an exception
    stop_on_empty_response = False  # end the run when predict() returns an empty reply

    def reset(self): ...
    def predict(self, instruction: str, obs: Observation) -> tuple[str, list]:
        # obs.screenshot: PNG bytes. Return (raw reply text, actions).
        # actions: "WAIT" | "DONE" | "FAIL" | python code for the guest (pyautogui)
        #          | {"action_type": "RUN_COMMAND", "command": ..., "cwd": ..., "timeout": ...}
        #          | {"action_type": "WAIT_FOR_STATE", "seconds": n}
        ...
    def rollback_failed_prediction(self): ...      # called after predict() raised
    def observe_result(self, result: ActionResult): ...   # feedback after each executed action
    def token_stats(self) -> dict: ...             # goes into meta.json and summary.json
    def describe(self) -> dict: ...                # goes into meta.json

@register_agent("mine")
def build(model: ResolvedModel, task: Task, cfg: AgentRunConfig, raw_log: Path, log=print) -> MyAgent:
    ...
```

`model` carries the endpoint URL and key resolved from `configs/models.yaml`
and `.env`; `osworld_science.llm.ModelCaller(model, raw_log)` gives you
retries, truncation handling, usage accounting and the per-request log for
free if your agent produces OpenAI-style `messages`. `cfg.run_dir` is the
cell's `agent/` directory, `cfg.options` carries CLI extras.

Select it with `osci run --agent mine …` or pin it per model in
`configs/models.yaml` (`agent: mine`). Packages can register through the
`osworld_science.agents` entry-point group.

The runner owns execution and timing (1 s after each action, 2 s after
`WAIT`), screenshots, the per-step window tally and the trace files, so
two agents are compared under identical conditions.
