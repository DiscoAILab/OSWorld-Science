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
    def bind_runtime(self, controller, guest, **kwargs): ...  # optional: SDK-owned tool loops
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

`model` carries the backend's API root (`model.base_url`) and key resolved
from `configs/models.yaml` and `.env`; `model.endpoint(api)` adds the path
(`chat_completions` → `/chat/completions`, `responses` → `/responses`;
Anthropic backends always get `/v1/messages`). `osworld_science.llm.ModelCaller(model,
raw_log, api=cfg.api)` gives you retries, truncation handling, usage accounting
and the per-request log for free if your agent produces OpenAI-style
`messages` (it speaks chat/completions only). `cfg.run_dir` is the cell's
`agent/` directory.

An agent's own knobs live in `configs/agents/<name>.yaml`:

```yaml
agent: mine                  # the registered name
api: chat_completions        # or responses: which API the harness calls on openai_chat backends
screenshot_window: {limit: 6, keep: 5, prune: drop_oldest}   # the shared window (see below)
image_detail: high           # anything else is yours, handed over as cfg.options
```

`cfg.api` is that file's `api`; `cfg.options` is the rest of the file with
`--agent-option key=value` overrides merged on top (values are read as YAML).
An agent without a file runs with `api: chat_completions` and empty options.
`osci agents` lists what each agent will receive.

A model runs under your agent only if its `configs/models.yaml` entry lists it
(`agents: [prompt, mine]`); `default_agent: mine` makes it the choice when
`osci run` is given no `--agent`. Packages can register through the
`osworld_science.agents` entry-point group.

Bound the screenshots your agent sends with the shared window rather than a
knob of its own, so every agent reads the same `screenshot_window` setting and
the command-line flags work for it too:

```python
from osworld_science.agents import ScreenshotWindow, WindowConfig

window = ScreenshotWindow(WindowConfig.from_options(cfg.options, default={"limit": 6, "keep": 5}))
...
omit = window.apply(ids)   # every screenshot of the request being built, oldest first, newest last
# leave out (or replace with text) the screenshots whose id is in `omit`; record window.stats() in describe()
```

`ids` are whatever identifies a screenshot in your transcript (a step number, a
call id, a message position) and must stay the same from one request to the
next. A new prune algorithm is a function `(earlier: list[Screenshot], keep)
-> list[Screenshot]` registered with `@register_prune("name")`.

An agent that needs a capability
the generic text+image probe cannot see can register its own preflight check
with `@register_agent_preflight("mine")` (`fn(model, agent_config, log) ->
(ok, why)`); it runs after the generic probe, in `osci preflight` and at the
start of every sweep.

The runner owns execution and timing (1 s after each action, 2 s after
`WAIT`), screenshots, the per-step window tally and the trace files, so
two agents are compared under identical conditions.

The built-in `openai-cua` agent is the exception to the returned-action pattern.
It drives the Responses API through the OpenAI Agents SDK (`api: responses` in
`configs/agents/openai_cua.yaml`; image tool results do not travel over
chat/completions, so that is the only value it accepts) and uses the native
`computer` protocol on the backends listed under `native_computer_backends`
(OpenAI/Azure) and equivalent custom function tools elsewhere
(`tool_protocol: auto | native_computer | function_tools`). The SDK executes
each action and captures its post-action screenshot before issuing the next
request. It uses `bind_runtime()` to attach the live controller, then returns
`DONE` only after the SDK's complete tool loop finishes. Its `cua_NNN.png` files
are the exact screenshots encoded into either matching `computer_call_output`
items or `function_call_output` image content. A model may set
`native_computer_tool: true|false` in `configs/models.yaml` to override the
automatic selection. Only models whose backend serves `/responses` can list
`openai-cua` under `agents:`; the Anthropic Messages route and Moonshot's own API
do not. Its screenshot window is applied with the SDK's
`RunConfig.call_model_input_filter` hook, so the SDK's transcript stays complete
while each request carries only the window's screenshots. Its model is a subclass
of the SDK's `OpenAIResponsesModel` that retries every failed model call on the
same request, as `ModelCaller` does (`request_attempts`, 4 tries 10/20/30 s
apart, the OpenAI client's own retries off); a response that ends incomplete,
such as Azure's `content_filter`, counts as a failure, and each failed try is an
`error` record in `llm_raw.jsonl`.

The built-in `claude-cua` agent does the same for Claude with the Anthropic
Messages API (`api: messages`): its driver subclasses the SDK's
`BetaAbstractComputerToolset20260801` (`anthropic.tools.computer`), so the SDK
validates every member call, renders the acknowledgement and stamps
`toolset_name` on each result, and the loop follows Anthropic's reference
implementations (batch actions run in order and halt at the first failure,
a screenshot is appended to a batch that ends without one, prompt-cache
breakpoints trail the last turns, older screenshots are pruned). Both agents
set `owns_episode_loop = True`, report their turns through `episode_stats()`
(including a `status` of `max_steps` or `interrupted`) and log one `cua_step`
per model turn in `trace.jsonl`, which the trajectory-reading graders group
into steps.

The built-in `codex` agent (`agents/codex`) takes the third route: it owns no
model loop at all. It runs the installed Codex CLI once per cell (`codex exec
--json -`, the task on stdin, the first screenshot attached with `--image`) with a
per-cell `CODEX_HOME` whose `config.toml` it generates (`codex_config.py`: the
model's backend as a `model_providers` entry with `wire_api = "responses"`, the
key through the backend's `key_env`, `approval_policy = "never"`, Codex's host
shell off through `[features]`, `project_doc_max_bytes = 0`). The guest desktop is
given to Codex as a stdio MCP server (`desktop_mcp.py`, started by Codex itself
from `[mcp_servers.desktop]`), which reuses `PyAutoGUIComputer` from `openai-cua`
for the GUI tools, adds `run_command` for a shell inside the guest, returns
screenshots as MCP image blocks (`_meta.codex/imageDetail`) and enforces the step
budget by failing every call beyond `max_steps`. The agent reads Codex's JSONL
event stream (`thread.started`, `item.started/completed`, `turn.completed` with
the usage) to write `cua_step` records, an image-free `llm_raw.jsonl` and the
usage meter; per-model-call usage comes from the `token_count` events of Codex's
rollout file under `codex_home/sessions/` when the run is not ephemeral. Its
preflight runs one real `codex exec` turn. The shared screenshot window does not
apply: Codex compacts its own history (`model_auto_compact_token_limit`).
