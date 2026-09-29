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
uv run osci run --models kimi-k3 --tasks all --run-name kimi              # models.yaml pins agent: kimi
uv run osci run --models claude-sonnet-5 --tasks biomed --force          # redo biomedical + medical-imaging cells
uv run osci report --run stat_sweep
```

Order of operations per cell: preflight (once per model) → VM reset →
pre-task hooks → stage → agent loop → collect → score → summary update.

## What lands in `runs/<run>/`

```
summary.json                         results[model][task] + totals + preflight (atomic, updated per cell)
<task>/<model>/agent/
   prompt_sent.txt                   the instruction as sent (with the Deliverables list)
   system_prompt.txt                 (kimi only)
   shots/step_NNN.png                one screenshot per step
   trace.jsonl                       windows / response / action / exec_result / special, per step
   llm_raw.jsonl                     http status, finish reason, usage, resolved model, per request
   meta.json                         model, agent, status, steps, tokens, cost, apps_seen
<task>/<model>/submission/           collected deliverables, inputs/, collect.json
<task>/<model>/score.json            per-check scores, gates, verdict
csv/                                 osci report output
```

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
| `--history-window` | 5 | screenshots kept in the PromptAgent's context |
| `--max-actions-per-step` | 10 (`OSCI_MAX_ACTIONS_PER_STEP`) | a reply that parses into more actions executes only the first N; the step is logged and counted in `meta.json` (`n_truncated_steps`), the model is told nothing. 0 = unlimited |
| `--agent` | `prompt`, or the model's `agent:` | `kimi` = the vendored Moonshot tool-calling agent |
| `--kimi-mode` / `--kimi-budget` | `gui` / `upstream` | `hybrid` adds a shell tool. `upstream` is Moonshot's factory budget (up to 100 screenshots per request, history 1000, reply cap 65536, temperature 0.0 sent, forced FAIL on the last step), the setting the recorded stat sweep used; `matched` aligns the window, cap and sampling with the PromptAgent arm for same-table comparison |
| `--no-window-snapshot` | off | skip the per-step `wmctrl` tally |
| `--no-reset` | off | debug only: stage onto the guest as it is |
| `OSCI_REASONING_EFFORT` | unset | sent to models flagged `reasoning: true` |
| `OSCI_IMAGE_DETAIL` | `high` | `original` is only understood by Azure |

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
