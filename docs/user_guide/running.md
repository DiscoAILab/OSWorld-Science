# Running the benchmark

## One cell by hand

```bash
uv run osci vm reset --snapshot ubuntu_stat --port 5040     # fresh guest from data/stat/vm/ubuntu_stat.qcow2
uv run osci vm check --snapshot ubuntu_stat --port 5040     # what a solver's terminal sees
uv run osci stage stat_qol_sql --port 5040               # run the task's config block
#   … drive the desktop yourself through noVNC on http://localhost:8046, or run an agent …
uv run osci collect stat_qol_sql --port 5040 --out /tmp/sub
uv run osci score stat_qol_sql --submission /tmp/sub
```

## A sweep

```bash
uv run osci run --models claude-sonnet-5,gpt-5.6-terra --tasks stat --workers 2 --run-name stat_sweep
uv run osci run --models kimi-k3 --tasks all --run-name kimi              # models.yaml: default_agent: kimi
uv run osci run --models gpt-5.6-sol --agent openai-cua --tasks stat --run-name openai_cua
uv run osci run --models gpt-5.6-sol --agent openai-cua --agent-option tool_protocol=function_tools --tasks stat
uv run osci run --models claude-opus-5 --agent claude-cua --tasks stat --run-name claude_cua   # Anthropic computer toolset
uv run osci run --models gpt-5.6-terra --agent codex --tasks stat --run-name codex          # Codex CLI + desktop MCP server
uv run osci run --models gpt-5.6-terra --agent codex --agent-option guest_shell=false --tasks stat   # GUI tools only
uv run osci run --models claude-sonnet-5 --tasks biomed --force          # redo biomedical + medical-imaging cells
uv run osci report --run stat_sweep
```

Order of operations per cell: preflight (once per model) → VM reset →
pre-task hooks → stage → agent loop → collect → score → summary update.

### Which agent, which API

Each model in `configs/models.yaml` lists the agents it can run under and a
`default_agent`; `--agent NAME` runs every selected model under that agent and
the sweep stops before preflight when a model's list does not allow it
(`claude-*` run under `prompt` or `claude-cua`, `gemini-3.1-pro` under
`prompt` only, `kimi-k3` only under `kimi`, the GPT models and the OpenRouter
variants under `prompt`, `openai-cua` or `codex`). Each agent's own settings are in
`configs/agents/<agent>.yaml` (`osci agents` prints them): `api` says which
API the harness calls on the backend's base URL (`chat_completions` for
`prompt` and `kimi`, `responses` for `openai-cua` and `codex`, `messages` for
`claude-cua`; Anthropic backends always get the Messages API), the rest are
the agent's knobs (screenshot window, Kimi budget table, CUA tool protocol,
Claude tool version, Codex's config.toml keys …). `--agent-option key=value` overrides one of them for
a run (values are read as YAML; repeatable; a dotted key such as
`screenshot_window.limit=30` sets one field of a mapping), and
`--screenshot-limit`, `--screenshot-keep`, `--screenshot-prune`,
`--kimi-mode`, `--kimi-budget` are shorthands for the matching keys. Preflight
probes the model through the agent's API, plus the agent's own probe when it
has one (`claude-cua` checks that the route serves the computer toolset,
`codex` runs one real `codex exec` turn through the generated provider config), and
`meta.json` records the effective agent config under `agent_config`.

### The `codex` agent

`codex` needs the Codex CLI on the host (`npm install -g @openai/codex`, or
`codex_bin` in `configs/agents/codex.yaml`) and a backend that serves the
Responses API. Each cell runs one `codex exec --json` with its own `CODEX_HOME`
(`<cell>/agent/codex_home/config.toml`, generated: the model's backend as a
`model_providers.osci` entry, approvals off, Codex's host-side shell switched off,
the desktop as `mcp_servers.desktop`). The desktop MCP server offers `screenshot`,
`click`, `double_click`, `move`, `drag`, `scroll`, `type`, `keypress`, `wait` and,
with `guest_shell: true`, `run_command` (bash inside the guest). Every GUI tool
answers with a fresh screenshot (`screenshot_after_action`), each call is one step
of the task's `max_steps` budget (enforced by the server: beyond it every tool fails
and tells the model to summarise), and Codex's own context compaction replaces the
screenshot window (`model_auto_compact_token_limit`; Azure rejects requests with more
than 50 images). Usage is counted per model call from Codex's rollout file when the
run is not `ephemeral`, else from the turn total. `agent/codex_mcp.jsonl` is the
server's own log, `agent/codex_stderr.log` Codex's; `trace.jsonl` gets one
`cua_step` per tool call.

### The screenshot window

Every agent bounds the screenshots it sends with the same setting,
`screenshot_window` in its config (`osworld_science.agents.window`):

```yaml
screenshot_window:
  limit: 6            # the most screenshots one request carries, the newest included
  keep: 5             # what a prune leaves of the earlier screenshots
  prune: drop_oldest  # the algorithm that picks them
```

Screenshots accumulate until the window holds `limit`. When the next one
arrives, the prune algorithm first cuts the earlier screenshots down to
`keep`, then the new one joins, so once the window has filled a request
carries between `keep + 1` and `limit` screenshots. The newest is never
pruned and a pruned screenshot never comes back. `drop_oldest`, the default,
drops the oldest `limit - keep`. Other algorithms are registered with
`osworld_science.agents.register_prune("name")` or named as
`package.module:function`; one receives the earlier screenshots (an id and
the screenshot's position in the run, oldest first) and `keep`, and returns
the ones to keep. `limit: null` never prunes.

`limit 6 / keep 5` is what every agent ships with and what the recorded sweeps
used: five earlier screenshots plus the current one, one leaving per step. A
wider gap prunes only every `limit - keep` steps and keeps the request
prefix stable in between, which is what prompt caching needs (`claude-cua`
with `keep 5 / limit 30` re-writes its cache once every 25 turns instead of
every turn). What a pruned screenshot becomes depends on the agent: the
PromptAgent drops the whole earlier step (screenshot and reply),
`openai-cua` keeps every call and its reasoning and replaces the image of the
tool result with a line of text (Azure rejects more than 50 images in one
request),
`claude-cua` writes `[Image Omitted]` and counts the seed screenshot next to
the instruction as well, and Kimi writes Moonshot's own
`[Screenshot at Step n]`. `claude-cua` also prunes at once to two earlier
screenshots when the serialized request exceeds `max_request_mb`. Kimi's
`upstream` budget keeps Moonshot's own image compaction (100 images, then 10)
because the recorded stat sweep ran on it; its `matched` budget uses the
shared window. `meta.json` records the window and how many screenshots it
pruned (`screenshot_window`, `screenshots_pruned`).

A rerun of a cell (`--force`, or a cell left unfinished) moves the earlier
attempt to `<task>/<model>.prev-<timestamp>/` next to it before starting, so
two attempts' screenshots and traces never mix.

## What lands in `runs/<run>/`

```
summary.json                         results[model][task] + totals + preflight (atomic, updated per cell)
<task>/<model>/agent/
   prompt_sent.txt                   the instruction as sent (with the Deliverables list)
   system_prompt.txt                 (kimi only)
   shots/step_NNN.png                the screen at the start of step N (a model turn for openai-cua/claude-cua)
   shots/cua_NNN.png                 every screenshot a computer-use agent returned to the model, numbered per
                                     screenshot, in the format sent (claude-cua: .webp, lossless, by default;
                                     zoom_NNN.* for its zooms; codex: shots/seed.png is the one attached to the task)
   codex_home/                       (codex only) the run's CODEX_HOME: config.toml as generated, Codex's rollout
                                     (sessions/…/rollout-*.jsonl, the full transcript) and state; codex_mcp.jsonl,
                                     codex_stderr.log, codex_command.txt and codex_last_message.txt next to it
   trace.jsonl                       windows / response / action / exec_result / special, per step
   llm_raw.jsonl                     http status, finish reason, usage, resolved model, per request
   meta.json                         model, agent, status, steps, tokens, cost, apps_seen
<task>/<model>/submission/           collected deliverables, inputs/, collect.json
<task>/<model>/score.json            per-check scores, gates, verdict
csv/                                 osci report output
```

### Tokens, cache and cost

Every agent and the judge count usage the same way (`osworld_science.llm.usage`).
Per request, `llm_raw.jsonl` keeps the provider's own `usage` object and a
normalised `counted` record: prompt tokens (cached ones included), cache reads,
cache writes, output and reasoning tokens, and the call's cost with its source.
Per cell, `meta.json` and `summary.json` carry `input_tokens`,
`cached_input_tokens` (cache reads), `cache_write_tokens`, `reasoning_tokens`,
`cache_hit_rate` (reads ÷ prompt tokens; empty when the API reported no cache
at all, which is not the same as 0 %), `cost_usd`, `cost_source`,
`cost_usd_static` and `upstream_cost_usd`. The cell's log line and the run's
totals print the hit rate.

How `cost_usd` is computed is the model's `cost:` in `configs/models.yaml`:

* `cost: api` (OpenRouter models): the cost the API returned for each call
  (`usage.cost`; on bring-your-own-key routes plus
  `cost_details.upstream_inference_cost`). A call that came back without one
  is priced statically and counted in `n_calls_cost_estimated`, and
  `cost_source` becomes `api+static`.
* `cost: static` (Azure, Anthropic, Google, Qwen, Moonshot): the price table,
  each token class at its own rate: uncached input × `in` + cache reads ×
  `cache_read` + cache writes × `cache_write` (Anthropic 1-hour writes ×
  `cache_write_1h`) + output × `out`. A missing cache rate is billed at `in`.

`cost_usd_static` is recorded on every cell, so on OpenRouter models the
estimate can be checked against the charge; with the shipped prices it matches
OpenRouter to the cent wherever the in/out prices match the pinned provider.

`osci report` writes `cache.csv`: one row per cell and an `ALL` row per model
with prompt tokens, cache reads and writes, uncached tokens, the hit rate and
the three cost figures, and adds `cache_hit_rate` to `summary_by_model.csv`.
Cells from runs made before cache accounting existed are recomputed from their
`llm_raw.jsonl` (`cache_source: llm_raw`; their `cost_usd` is the old flat
estimate, `cost_usd_static` the cache-aware one); computer-use cells of that
era logged no cache detail, so their hit rate stays empty.

`meta.json.status` is `done` / `fail` (the agent's own signal), `max_steps`,
or one of the harness outcomes `empty_response`, `predict_error:*`,
`no_screenshot`, `interrupted`. The report treats the harness outcomes as
`VOID` unless the cell still passed (an interruption cannot invent a
correct deliverable). `max_steps` is `VOID` only for a null run — the agent
spent every step without ever signalling `DONE` or `FAIL` and left nothing
of its own in `submission/`; a `max_steps` cell that did deliver something
is a real `FAIL`. The `inputs/` copies don't count as a delivery: the
harness stages them in and pulls them back to check they weren't tampered
with, so they return no matter what the agent did.

## Ports and parallelism

Workers take the first free control ports from `PORT_BASE` upwards (a port
already bound on the host — for example by a legacy container — or locked
by another sweep is skipped with a warning; a port held only by a container
of a previous `osci` run — of any snapshot — is reused: that container is removed
and the slot recreated with the snapshot the cell needs); the container is
`<prefix>_<port>` and noVNC is at `port + 3006`. A lock file per port
(`vm/locks/`) refuses a second sweep on a VM another sweep is using. A
worker whose VM cannot be reset hands its cell back to the queue and
retires after two consecutive failures, so one broken slot cannot burn
through the job list.

One Ctrl-C stops the sweep: running cells end at their next step (a boot
wait is abandoned at once, an LLM request runs to its timeout), the
summary is saved, and unfinished cells rerun on the next invocation. A
second Ctrl-C exits immediately.
`OSCI_MAX_WORKERS` caps the pool (8 GB RAM per guest).

### Where the image bytes come from

Every container mounts the snapshot image read-only and writes to its own
copy-on-write overlay (a few GB, inside the container; discarded on reset),
so N workers share one image and never interfere. Whether that image is
mounted where it is or first copied to a local disk is `OSCI_VM_COPY_IMAGES`:
`auto` (default) copies only when the image sits on a network filesystem,
`always` copies (what the legacy scripts did), `never` mounts in place. A
copy goes to `OSCI_VM_FAST_DIR`, is made once even when workers start
together (a lock file serialises it; the others wait), needs 24–34 GB per
image, and is kept as a cache for later runs — `osci vm-cache list` shows
the copies and which containers mount them, `osci vm-cache clean` removes
the unused ones. A full disk fails the cell with a message naming these
three knobs.

## Options

| flag | default | notes |
|---|---|---|
| `--max-tokens` | 16000 (`OSCI_MAX_TOKENS`) | doubled automatically on a truncated reply, up to the model's `max_out` |
| `--screenshot-limit` / `--screenshot-keep` / `--screenshot-prune` | the agent's `screenshot_window` (6 / 5 / `drop_oldest`) | see "The screenshot window"; `--screenshot-limit none` never prunes, `--screenshot-keep` defaults to limit − 1. `--history-window N` is shorthand for limit N+1, keep N |
| `--agent-option KEY=VALUE` | — | override one key of the agent's config for this run (repeatable) |
| `--max-actions-per-step` | 10 (`OSCI_MAX_ACTIONS_PER_STEP`) | a reply that parses into more actions executes only the first N; the step is logged and counted in `meta.json` (`n_truncated_steps`), the model is told nothing. 0 = unlimited |
| `--agent` | the model's `default_agent` | must be in the model's `agents:` list. `kimi` = vendored Moonshot tool-calling agent; `openai-cua` = OpenAI Agents SDK computer use over the Responses API (native computer tool on OpenAI/Azure, custom GUI function tools on other backends that serve `/responses`); `claude-cua` = Anthropic's computer toolset over the Messages API (`tool_version: computer_20251124` for routes that only serve the earlier tool); `codex` = the installed Codex CLI over the Responses API with the desktop as an MCP server (`configs/agents/codex.yaml`: `guest_shell`, `screenshot_after_action`, `model_auto_compact_token_limit`, …) |
| `--kimi-mode` / `--kimi-budget` | `configs/agents/kimi.yaml` (`gui` / `upstream`) | `hybrid` adds a shell tool. `upstream` is Moonshot's factory budget (up to 100 screenshots per request, history 1000, reply cap 65536, temperature 0.0 sent, forced FAIL on the last step), the setting the recorded stat sweep used; `matched` aligns the window, cap and sampling with the PromptAgent arm for same-table comparison (the table is `budgets.matched` in the file) |
| `--no-window-snapshot` | off | skip the per-step `wmctrl` tally |
| `--no-reset` | off | debug only: stage onto the guest as it is |
| `OSCI_REASONING_EFFORT` | unset | sent to models flagged `reasoning: true` |
| `OSCI_IMAGE_DETAIL` | the agent config's `image_detail` (`high`) | `original` is only understood by Azure |

## Screenshots and the guest's X server

The guest server's `/screenshot` endpoint leaks one X client per call (it
opens a display to read the cursor image and never closes it), and Xorg
accepts 256 clients, so a run that takes about 220 screenshots through it
ends with every GUI action failing with "Maximum number of clients reached"
and the endpoint itself dead. The harness therefore takes screenshots through
`/execute` in a short-lived process that grabs and pastes the cursor the same
way (`OSCI_SCREENSHOT_VIA=execute`, the default; the picture is pixel-identical
and takes the same 0.4 s). `OSCI_SCREENSHOT_VIA=endpoint` restores the old
path. Collecting results no longer needs the
display, so the deliverables of a run whose X server did wedge still come
back. The images themselves still carry the leaking server; a rebuilt image
should fix `pyxcursor.py` (close the display, or create one `Xcursor` per
process).

## Guest lifecycle commands

```bash
uv run osci vm start  --snapshot ubuntu_radiology --port 5050    # idempotent: leaves a live guest alone
uv run osci vm reset  --snapshot ubuntu_radiology --port 5050    # destroy + recreate + toolchain probe
uv run osci vm status --snapshot ubuntu_radiology --port 5050
uv run osci vm check  --snapshot ubuntu_radiology --port 5050    # what a solver's terminal sees
uv run osci vm stop   --snapshot ubuntu_radiology --port 5050
uv run osci vm-cache  list|clean                                  # local image copies (only when copying is on)
```

The image a snapshot boots is `data/<domain>/vm/<snapshot>.qcow2`
(downloaded with `hf_download.py --domain <d> --vm`); a local
`vm/images/<snapshot>.qcow2` is honoured as a fallback, and with neither
present the pristine base boots and the snapshot's provision script installs
the toolchain. Reset between tasks, always: the previous task's files,
windows, shell history and pip installs otherwise survive in the container
overlay — `osci run` does this for every cell.

## Tasks that wait for a review (linguistics)

`hoosier_phonotactics_lab1` (R in RKWard, a written report) is graded by its
authors' scorer, which issues no score until an independent reviewer has read
the report and the trajectory. After `osci run` such a cell shows PENDING and
counts as VOID; `<cell>/hoosier_review/` holds the evidence packet, the
trajectory and an unfilled review. Save the completed review as
`<cell>/review.json` and run `osci score-run --run <name>`. Grading needs
`uv sync --extra linguistics` and the R judge image
(`scripts/vm_prep/linguistics/build_r_judge.sh`); the workflow is in
`data/linguistics/private/reference_private/hoosier_phonotactics_lab1/README.md`.
The task allows 1500 steps.

`vowel_duration_fixed_lab2` (sixteen recordings segmented in Praat, a table
and a scatterplot made in RKWard, a report written in Writer) works the same
way: its authors' scorer waits for a reviewer who judges every boundary
against the waveform and spectrogram of the original recording. The evidence
is under `<cell>/vowel_review/`: the packet, the trajectory, an unfilled
review and `collection/review-evidence-*/index.html` with one acoustic card
per recording, the report pages, the plot and the R source. Grading needs
`uv sync --extra linguistics` only (nothing is executed, so no judge image);
the workflow is in
`data/linguistics/private/reference_private/vowel_duration_fixed_lab2/README.md`.
The task allows 2500 steps and runs on the `ubuntu_rkward` snapshot, which
must carry Praat: an image baked before this task is provisioned on every
`osci vm reset` (apt installs Praat, which needs network from the guest) until
it is rebuilt with `scripts/vm_prep/linguistics/build_rkward_image.sh`.

## LLM as a judge

`osci judge` lets a model write that review. Judges are their own section of
`configs/models.yaml` (`judges:`, default `deepseek-v4.1-flash` through
OpenRouter, with `gpt-5.6-luna` on the Azure route as a fallback), separate
from the solver models, so a judge never appears as a run column and a solver
is never asked to review its own run (the command refuses when the judge and
the solver are the same model). A judge entry may set `vision: true` (it is
shown page images and screenshots) and `json_mode: true` (the request asks
for a JSON object; replies are parsed leniently either way).

```bash
uv run --extra linguistics osci judge --run hoosier_test              # every pending cell, default judge
uv run --extra linguistics osci judge --run hoosier_test --judge NAME --models claude-fable-5-1
uv run --extra linguistics osci run --models claude-fable-5-1 --tasks hoosier_phonotactics_lab1 --judge
```

`osci run` does this on its own for tasks that declare a judge: the judge is
resolved and probed in preflight (one text and, for a `vision` judge, one
image request) before any cell starts, and the run stops there if it is not
reachable; a cell that ends pending is then reviewed right after scoring and
rescored. `--judge NAME` picks another judge, `--no-judge` leaves pending
cells pending, and `osci judge` probes its judge the same way before touching
a run (`osci preflight --judges` probes every judge). What the judge is shown and what it must answer is
task-specific: the task JSON names a module shipped with the task's data
(`judge: {module, func}`), and that module builds the review from the cell,
asks the judge through a client that insists on JSON and keeps a transcript,
validates the result with the grader's own rules and writes `<cell>/review.json`.
Everything the judge produced is kept under `<cell>/judge/` (transcript,
request log, the review candidate and a `judge.json` verdict with the cost);
`summary.json` records the judge's usage under the cell's `judge` key, apart
from the solver's own tokens. A review the grader rejects leaves the cell
pending with the reason in `judge.json`.

A judge request is not limited to the answer size its module names. A
reasoning model counts its hidden reasoning against `max_tokens`, so every
judge request carries the judge's whole output allowance, the `max_out` of its
entry in `configs/models.yaml`, from the first attempt; only generated tokens
are billed. A reply that still runs into the allowance is a model going round
in circles rather than one short of tokens, and it is asked once more (on
OpenRouter with a reasoning budget, which ends the loop). If that is cut off
too, the stage fails, the cell stays pending and `judge.json` says so; run
`osci judge` again, or raise `max_out` (every OpenRouter host of
`deepseek/deepseek-v4.1-flash` accepts at least 131072).

For `hoosier_phonotactics_lab1` the module makes four passes: it reads the two
tables as printed in the report (text and page images), scores the Part III
criteria and the method explanations with page-anchored quotes checked against
the dictionary facts, reads the whole of `Analysis.R` against the function
contract, and reads every step of the trajectory with its screenshot for
prohibited routes. A judge without `vision: true` cannot confirm the
screenshots, so its GUI verdict cannot be "compliant" and the cell stays
pending.

For `vowel_duration_fixed_lab2` the module makes six passes: the sixteen
acoustic cards the grader rendered (one recording per request: right target
region, each boundary on its landmark, the cue seen), the sixteen values as
printed in the report, the plot against the submitted table and its presence
in the report, the whole of `Analysis.R` for prohibited operations, every step
of the trajectory with its screenshot, and the five rubric criteria with
page-anchored quotes. It needs a `vision: true` judge and writes no review
without one. A model reading a spectrogram image is weaker evidence than a
phonetician with Praat open; the acoustic pass is told to withhold credit
when the card does not resolve a landmark, and `<cell>/judge/stages.json`
keeps every finding for a human to audit.

## SAS tasks (stat)

Nine stat tasks say "SAS or R". Nothing SAS is installed in `ubuntu_stat`
(SAS is licensed software and cannot be redistributed); the SAS route is SAS
OnDemand for Academics in the guest's Firefox, which needs an account of your
own. The optional pre-task hook `scripts/vm_prep/stat/restore_firefox_profile.py`
unpacks a local, git-ignored Firefox profile archive with your saved sign-in
before each such task; without the archive the task simply runs with R. See
`scripts/vm_prep/README.md` for how to capture the profile and check the
cloud-side home directory before a sweep.

The SAS OnDemand for Academics licence you accept at registration
(`https://support.sas.com/ondemand/pdf/click_license.pdf`) sets the rules
for that route, and they are yours to keep, not the benchmark's:

* The account is personal: the service "may be used and accessed solely by
  you". Use your own account, and do not share one across people or
  harnesses.
* Use is limited to coursework, instruction, learning, and *noncommercial*
  research as SAS defines it (degree or tenure work, federally funded
  academic research, university programmes). An evaluation run by or for a
  company does not qualify: use the R route, which every SAS task accepts.
* Letting an agent drive the browser inside your own session is not
  addressed by the licence; you remain responsible for what it does there.
* Only data files (CSV, TXT, DAT, SAS data sets) may be uploaded, and
  nothing with unobfuscated personal information. The shipped SAS-task
  inputs are CSV/DAT with no personal data; keep it that way if you add tasks.
* Screenshots and logs you publish from SAS runs show the signed-in account
  (e-mail, name, user id, session tickets in the URL). Redact them before
  sharing trajectories.
