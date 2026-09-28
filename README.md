# OSWorld-Science: A Playground of Computer Use Agents for Learning and Using Scientific Software

OSWorld-Science is a benchmark for evaluating computer-use agents on
scientific workflows. It contains 122 tasks across eight domains, each run
inside an Ubuntu virtual machine equipped with domain-specific desktop
software.

Each task follows the OSWorld format: an agent receives a natural-language
instruction, interacts with the desktop, and leaves behind a set of
deliverables. Executable, task-specific graders then evaluate those files
offline against reference results, including structured fields, numerical
tolerances, and application-native artefacts. Task definitions live in
`data/<domain>/tasks/*.json` and specify the VM snapshot, staging steps,
deliverables, and evaluator pipeline.

## Requirements

- A **Linux x86-64 host with KVM** (`egrep -c '(vmx|svm)' /proc/cpuinfo` > 0,
  `/dev/kvm` accessible to your user) and **Docker** (rootless is fine) that can
  pass `--device /dev/kvm`. The guests are QEMU virtual machines run by the
  `happysixd/osworld-docker` image; without KVM they are unusably slow.
- **Around 8 GB RAM and 4 vCPUs per concurrent guest** (`--workers`), plus
  disk for the prepared domain snapshots (about 196 GB for all eight) and
  `qemu-img` on the host. Building snapshots yourself additionally requires
  the 24 GB base image. A snapshot may request more resources (`ram`/`cpus`
  in `configs/snapshots.yaml`).
- Python 3.11+ through `uv` (installed by
  `curl -LsSf https://astral.sh/uv/install.sh | sh`) and `Rscript` (R 4.4.3)
  for the R-backed statistics evaluators.
- Not supported: macOS or Windows hosts, hosts without KVM, arm64, the
  VMware/VirtualBox/cloud providers of upstream OSWorld, Windows guests.

Run `uv run osci doctor` to check the host. See the
[`docs/user_guide/`](docs/user_guide/) for detailed requirements, benchmark
usage, and task authoring, or the
[`docs/developer_guide/`](docs/developer_guide/) for adding domains and agents
or building VM snapshots.

## Quick start

```bash
uv sync --extra hf       # Python 3.11+, managed by uv (add --extra grader for grader tests)
cp .env.example .env     # add credentials for the model backends you use

# Prepare benchmark data and prebuilt VM images.
uv run python scripts/data_prep/hf_download.py --domain stat --vm  # one domain; repeat --domain, or omit --vm to skip its image
uv run python scripts/data_prep/hf_download.py --vm                # all domains and VM images (~196 GB)
```

Use `uv run osci models` to list the model names configured in
`configs/models.yaml`. `--tasks` accepts `all`, a domain name, or a
comma-separated list of task IDs. The run command performs its own model
preflight checks.

```bash
uv run osci run --models MODEL[,MODEL...] --tasks {all|DOMAIN|TASK_ID[,TASK_ID...]} [--run-name NAME]
uv run osci run --models claude-sonnet-5 --tasks stat_qol_sql --run-name smoke                 # one task
uv run osci run --models claude-sonnet-5 --tasks stat --workers 3 --run-name stat-sweep        # one domain
uv run osci run --models claude-sonnet-5,gpt-5.6-terra --tasks all --workers 3 --run-name full  # all tasks
uv run osci report --run full  # PASS/FAIL/VOID counts, scores, steps, and cost CSVs
```

Reusing a run name resumes that run and skips completed model–task cells unless `--force` is supplied.

## Tasks

| Domain | Tasks | Desktop software | Primary deliverables |
|---|---:|---|---|
| `astro` | 3 | SAOImage DS9, CIAO, LibreOffice Calc | Chandra measurements and DS9 session evidence |
| `biomed` | 31 | QuPath, PyMOL, browser, Mnova | Pathology annotations and readouts; structural-biology and NMR outputs |
| `chem` | 43 | PDF viewer, XDrawChem, browser, terminal | Paper extraction and retrosynthesis plans |
| `geoscience` | 6 | QGIS | GeoPackage layers, NDVI statistics, and georeferencing points |
| `linguistics` | 2 | Praat | TextGrids with VOT annotations |
| `physics` | 14 | OpenFOAM, ParaView, terminal | Converged field matrices |
| `radiology` | 3 | Weasis, 3D Slicer | Findings tables, landmarks, and notes |
| `stat` | 20 | R, RStudio, Python, sqlite3 (SAS only as SAS OnDemand for Academics in Firefox; nothing SAS is installed) | Analyses, functions, SQL, plots, and findings |

## How it fits together

```
data/<domain>/tasks/<id>.json ─►  osci run ──►  reset VM (configs/snapshots.yaml)
                                          ──►  stage: task.config (execute / upload / launch / sleep)
                                          ──►  agent loop: screenshot → LLM → pyautogui code (agents registry)
                                          ──►  collect: evaluator.result[] → runs/<run>/<task>/<model>/submission/
                                          ──►  score: evaluator.func[] × weights, gates → score.json
```

* **Agents** are registered factories (`osworld_science.agents`). Built in:
  `prompt` (the OSWorld PromptAgent: screenshot in, pyautogui code out,
  five-step history) and `kimi` (Moonshot's tool-calling agent, vendored
  from xlang-ai/OSWorld under Apache-2.0). Add your own with `@register_agent`.
* **Models** are rows in `configs/models.yaml` (backend, upstream id, price,
  output cap). Backends speak OpenAI chat/completions or the native
  Anthropic Messages API; keys come from `.env`.
* **Evaluators** are registered functions (`osworld_science.evaluators`);
  the shipped ones cover JSON fields, CSV tables, byte-identity of inputs,
  artefact checks, R function probes, SQL replay, Slicer markups and Praat
  TextGrids. Five more (`module_metric`, `package_metric`,
  `package_text_score`, `text_include_exclude`, `file_min_bytes`) call an
  evaluator that a task author shipped with the ground truth, unchanged.
* **Snapshots** are rows in `configs/snapshots.yaml`: which image, which
  port, how to detect and (re)install the toolchain, which pre-task hooks.

## Data

One directory per domain: `data/<domain>/tasks/` holds the task
definitions, `data/<domain>/public/` is what the guest receives,
`data/<domain>/private/` is what only the grader reads (ground truth and the
grader test suites), `data/<domain>/vm/` holds the domain's prepared VM image
with its md5. "private" names what
the solver never sees, not an access control: **the answer keys are
published**, so a score from a model that may have trained on the dataset is
not a held-out measurement.
The dataset is released under CC BY-NC 4.0: its sources are open data
usable for academic research.
The prepared VM images are 23–34 GB each; `hf_download.py --domain <d> --vm`
fetches one, or `scripts/vm_prep/<d>/build_image.sh` rebuilds it from the
official OSWorld base image.

## Repository map

```
data/             (git-ignored, from Hugging Face) <domain>/{tasks,public,private,vm}
configs/          models.yaml, snapshots.yaml
src/osworld_science/   the package: tasks, guest, vm, harness, evaluators, llm, agents, runner, reporting, cli
scripts/          data_prep (HF sync), vm_prep (image builds)
tests/            unit tests (no data) and grader parity tests (need data/)
docs/             user_guide/ (requirements, running, adding a task), developer_guide/ (domains, agents, VM snapshots)
```
