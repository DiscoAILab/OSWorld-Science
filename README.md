<div align="center">

# 🧪 OSWorld-Science

### A Benchmark of Computer Use Agents for Learning and Using Scientific Software

*Can computer-use agents turn scientific intent into verifiable results inside real research software?*

[![arXiv](https://img.shields.io/badge/arXiv-2609.39903-B31B1B?style=for-the-badge&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2609.39903)
[![Project Page](https://img.shields.io/badge/Project-Page-2563EB?style=for-the-badge&logo=googlechrome&logoColor=white)](https://discoailab.github.io/osworld-science-page/)
[![Hugging Face](https://img.shields.io/badge/Hugging%20Face-Dataset-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black)](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data)
[![Tasks](https://img.shields.io/badge/Benchmark-164%20Tasks-8B5CF6?style=for-the-badge)](#-benchmark-at-a-glance)
[![License](https://img.shields.io/badge/License-CC%20BY%204.0-EF9421?style=for-the-badge&logo=creativecommons&logoColor=white)](LICENSE)

[**Paper**](https://arxiv.org/pdf/2609.39903) ·
[**Project page**](https://huggingface.co/spaces/SciAILab/osworld-science-page) ·
[**Dataset & VM images**](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data) ·
[**Documentation**](docs/README.md)

</div>

---

OSWorld-Science is a benchmark and evaluation environment for studying how
computer-use agents solve scientifically meaningful tasks with professional
software. Agents must interpret specialized interfaces, manipulate scientific
objects, combine GUI and command-line actions, and leave behind results that can
be checked for scientific correctness.

The benchmark connects **expert-defined scientific goals** to **verifiable
software outcomes**. Its task-specific evaluators inspect application states and
artifacts such as molecular structures, segmentation masks, plots, tables, and
numerical results, awarding partial credit when a workflow is only partly
complete.

<table align="center">
  <tr>
    <td align="center" width="25%"><b>164 tasks</b><br/><sub>Scientifically meaningful workflows</sub></td>
    <td align="center" width="25%"><b>8 domains</b><br/><sub>From chemistry to linguistics</sub></td>
    <td align="center" width="25%"><b>20+ tools</b><br/><sub>Real scientific software</sub></td>
    <td align="center" width="25%"><b>12 VLMs</b><br/><sub>Open and proprietary models</sub></td>
  </tr>
</table>

## 📊 Benchmark at a glance

The current release contains **164 tasks in eight runner domains**. A runner
domain is a directory under `data/` and follows the VM layout, so the names are
not identical to the seven scientific-domain labels used in the paper.

| Runner domain | Tasks | Main software and workflows |
|---|---:|---|
| `biomed` | 34 | QuPath, PyMOL, Mnova, Weasis, and 3D Slicer; pathology, structural biology, NMR, and medical imaging |
| `chem` | 53 | ChemDraw-compatible molecular drawing, ASKCOS, PDF extraction, retrosynthesis, and 3D model building in Avogadro 2 |
| `econ` | 6 | gretl and LibreOffice Calc; unit-root and cointegration tests, VECM restrictions, monetary VARs, and difference-in-differences |
| `eeg` | 10 | GNU Octave and EEGLAB; event-related potentials, spectral and time-frequency analysis, and decoding |
| `geoscience` | 6 | QGIS; digitization, NDVI, and georeferencing |
| `linguistics` | 4 | Praat, RKWard, and LibreOffice Writer; voice-onset-time annotation, and two lab assignments (R analysis, Praat segmentation, written report) graded after an independent review |
| `physics` | 31 | OpenFOAM + ParaView, Ansys Fluent, and CIAO + SAOImage DS9; computational fluid dynamics and Chandra X-ray measurements |
| `stat` | 20 | R, RStudio, Python, SQLite, and optional SAS OnDemand; statistical computing, SQL, plots, and reporting |
| **Total** | **164** | |

The paper reports the first release, 146 tasks across seven scientific
domains:

| Scientific domain | Tasks | Share | Example workflows |
|---|---:|---:|---|
| Chemistry | 43 | 29.5% | Molecular drawing, paper extraction, retrosynthesis |
| Physics | 31 | 21.2% | CFD and engineering simulation |
| Medicine | 26 | 17.8% | Pathology and medical-image analysis |
| Statistics | 20 | 13.7% | Statistical computing, SQL, plots, and reporting |
| Biology | 18 | 12.3% | Structural biology, NMR, microscopy, and EEG analysis |
| Geographic information | 6 | 4.1% | GIS digitization, georeferencing, and spatial analysis |
| Linguistics | 2 | 1.4% | Acoustic analysis and annotation |

Added since the paper: six applied-econometrics tasks (`econ`), ten atropisomer
model-building tasks in Avogadro 2 (`chem`), and two linguistics lab
assignments. In the paper's taxonomy, `biomed` holds the 26 medicine tasks and
eight of the 18 biology tasks; the other ten biology tasks are the EEG
workflows under `eeg`.

The paper's release spans software configurations including QuPath, ChemDraw,
ASKCOS, SAS/R, OpenFOAM, ANSYS, EEGLAB, PyMOL/Mnova, QGIS, CIAO + DS9,
Weasis, Praat, 3D Slicer, and PDF viewers; the later tasks add gretl,
Avogadro 2, RKWard, and LibreOffice. All 146 tasks of that release require
visual observation; 83.6% also support CLI access, while the remaining 16.4%
are GUI-only under the recorded configurations.

## 🧭 Quick start

### 1. Check the host

OSWorld-Science currently supports Linux x86-64 hosts with KVM and Docker.
Plan for approximately **8 GB RAM and 4 vCPUs per concurrent guest**, plus disk
space for the selected VM images. The ten published per-domain images are
7–38 GB each, approximately 214 GB together.

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo  # must be greater than 0
ls -l /dev/kvm                      # must be accessible to your user
```

Python 3.11+ is managed with [`uv`](https://docs.astral.sh/uv/). Some graders
need more on the host: the R-backed statistics evaluators require `Rscript`
(R 4.4.3), the physics grader re-runs OpenFOAM inside a Docker image, and the
two linguistics lab tasks need the `linguistics` extra (one of them also a
small R sandbox image).

See the [full system requirements](docs/user_guide/requirements.md), or run
the built-in environment check after installation:

```bash
uv run osci doctor
```

### 2. Install

```bash
git clone https://github.com/DiscoAILab/OSWorld-Science.git
cd OSWorld-Science

uv sync --extra hf       # core package + dev tools + Hugging Face downloader
cp .env.example .env     # add credentials only for the model backends you use
```

`uv sync` installs the core package and the default development tools
(`pytest` and `ruff`). Optional extras are opt-in: `hf` downloads the dataset,
`grader` supports grader tests, and `biomed`, `chem`, `eeg`, `geoscience`, and
`linguistics` provide domain-specific evaluator dependencies. Repeat `--extra` to combine
them, or run `uv sync --all-extras` to install every extra.

### 3. Download tasks and VM images

```bash
# One domain and its prepared VM image
uv run python scripts/data_prep/hf_download.py --domain stat --vm

# All tasks and every published per-domain VM image (~220 GB).
# --prune moves local-only files from older releases into a timestamped backup.
uv run python scripts/data_prep/hf_download.py --vm --prune
```

The files are downloaded from the
[OSWorld-Science dataset](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data).
Task data and graders are published for every task. Two snapshots have no
per-domain image to download: the EEGLAB toolchain is published only inside
the unified Linux image (`vm/ubuntu_unified.qcow2`, about 20 GB, which the
script above does not fetch), and the Windows/Ansys Fluent image cannot be
redistributed. The `ubuntu_rkward` snapshot of the two linguistics lab tasks
can be built locally with `scripts/vm_prep/linguistics/build_rkward_image.sh`.
See the dataset card for current image availability and for running tasks on
the unified image.

After downloading, list the complete task registry and validate every task
definition:

```bash
uv run osci tasks list       # every task by domain with per-domain counts, then the total
uv run osci tasks validate   # check every task's schema, evaluator wiring, and snapshot
```

A complete download ends the list with `total: 164 tasks in 8 domains` and
validates as `164/164 task definitions valid`.

### 4. Run an evaluation

Use `uv run osci models` to list the model names configured in
[`configs/models.yaml`](configs/models.yaml) with the agents each can run
under, and `uv run osci agents` to list the agents. `--tasks` accepts `all`, a
domain name, or a comma-separated list of task IDs; `--agent` runs every model
under one agent instead of its default.

```bash
# One smoke-test task
uv run osci run \
  --models claude-sonnet-5 \
  --tasks stat_qol_sql \
  --run-name smoke

# One domain with three concurrent guests
uv run osci run \
  --models claude-sonnet-5 \
  --tasks stat \
  --workers 3 \
  --run-name stat-sweep

# Full model × task sweep
uv run osci run \
  --models claude-sonnet-5,gpt-5.6-terra \
  --tasks all \
  --workers 3 \
  --run-name full

# Export PASS/FAIL/VOID counts, scores, steps, and costs
uv run osci report --run full
```

Reusing a run name resumes that run and skips completed model–task cells unless
`--force` is supplied.

Tasks that declare a judge (currently `hoosier_phonotactics_lab1` and
`vowel_duration_fixed_lab2`) receive a score only after an independent review
of the submission. `osci run` asks the judge model configured under `judges:`
in `configs/models.yaml` for that review right after scoring (`--judge NAME`
picks another judge, `--no-judge` skips the review). A cell whose review did
not complete stays `PENDING` and can be reviewed later:

```bash
# Review and rescore every pending cell of a run
uv run --extra linguistics osci judge --run full
```

See the [running guide](docs/user_guide/running.md) for manual VM control,
parallelism, run artifacts, the LLM judge, and all command-line options.

## 🔍 How it works

```text
task definition
      │
      ├── reset a domain-specific VM
      ├── stage inputs and launch scientific software
      ├── run the agent: screenshot → VLM → GUI/CLI action
      ├── collect the requested deliverables
      ├── score artifacts with task-specific executable evaluators
      └── if the task declares a judge: LLM review of the evidence, then rescore
                              │
                              └── score.json + trajectories + reports
```

Every task follows the OSWorld format: an agent receives a natural-language
instruction, interacts with a desktop, and leaves behind a set of deliverables.
Task definitions live in `data/<domain>/tasks/*.json` and specify the VM snapshot,
staging steps, deliverables, and evaluator pipeline.

- **Agents.** Registered factories live under `osworld_science.agents`. The
  built-in `prompt` agent uses the OSWorld PromptAgent interaction pattern; the
  built-in `kimi` agent is Moonshot's tool-calling agent, vendored from
  xlang-ai/OSWorld under Apache-2.0; `openai-cua` uses the OpenAI Agents SDK
  with a PyAutoGUI driver—native `ComputerTool` on OpenAI/Azure backends and
  portable GUI function tools on other Responses-compatible backends; and
  `claude-cua` drives Anthropic's computer toolset (`computer_toolset_20260801`)
  over the Messages API with the same PyAutoGUI driver; and `codex` runs the
  installed [Codex CLI](https://github.com/openai/codex) (`codex exec`) against
  the model's backend, handing it the desktop as an MCP server (GUI tools on the
  same PyAutoGUI driver, plus a shell inside the guest) while Codex keeps its own
  loop, prompts and context compaction.
- **Models.** [`configs/models.yaml`](configs/models.yaml) records backend
  (API root and key), upstream model ID, pricing, output limits, the agents a
  model can run under and its default agent. Each agent's own settings, including
  which OpenAI-style API it calls (chat/completions or Responses), live in
  [`configs/agents/`](configs/agents/). A separate `judges:` section of
  `configs/models.yaml` names the models that review pending cells.
- **Evaluators.** The registry includes JSON, CSV, artifact, R, SQL, Slicer, and
  Praat evaluators, along with task-author-provided scientific checks. A task
  whose score depends on a reviewer's reading (a written report, segment
  boundaries checked against the spectrogram) ships a judge module: an LLM
  judge fills in the review, and the task's grader scores the reviewed result.
- **Snapshots.** [`configs/snapshots.yaml`](configs/snapshots.yaml) maps each
  task to a reproducible image, resource requirements, toolchain probes, and
  pre-task hooks.

## 📂 Data layout

```text
data/<domain>/
├── tasks/     # task definitions
├── public/    # inputs staged into the guest
├── private/   # grader references and test suites
└── vm/        # prepared VM image and checksum
```

The name `private/` means that its contents are hidden from the solver during a
run; it is not an access-control boundary. The published dataset includes answer
keys, so results from models that may have trained on the dataset should not be
interpreted as held-out measurements.

Prepared VM images are approximately 7–38 GB each. Download an image with
`hf_download.py --domain <domain> --vm`, or rebuild it from the official OSWorld
base image with the corresponding script under `scripts/vm_prep/`.

## 📦 Repository map

```text
configs/               model, agent and VM snapshot registries
data/                  downloaded tasks, references, inputs, and VM images
docs/                  user and developer guides
scripts/data_prep/     Hugging Face dataset synchronization
scripts/grading_env/   host-side R environment for the statistics graders
scripts/vm_prep/       reproducible domain-image build scripts
src/osworld_science/   agents, harness, VM control, evaluators, and CLI
tests/                 unit tests and grader-parity tests
```

## 📚 Documentation

- [System requirements](docs/user_guide/requirements.md)
- [Running the benchmark](docs/user_guide/running.md)
- [Adding a task](docs/user_guide/adding-a-task.md)
- [Adding a domain](docs/developer_guide/adding-a-domain.md)
- [Adding an agent](docs/developer_guide/adding-an-agent.md)
- [Building a VM snapshot](docs/developer_guide/building-a-vm-snapshot.md)

## 🍻 Acknowledgements

OSWorld-Science builds on and is inspired by the following open-source projects:

- **[OSWorld](https://github.com/xlang-ai/OSWorld)**, for establishing a
  foundational benchmark, environment, and interaction framework for evaluating
  multimodal agents on real computer tasks.
- **[Orion](https://github.com/Genentech/Orion)**, for pioneering computer-use
  agents for laboratory automation and inspiring scientific-software workflows
  in this benchmark.
- **[OpenAI Agents SDK](https://github.com/openai/openai-agents-python)**, whose
  `ComputerTool` and run loop power the built-in `openai-cua` agent.
- **[Claude Quickstarts](https://github.com/anthropics/claude-quickstarts)**,
  whose computer-use reference implementations (the agent loop, batch-action
  contract, screenshot sizing, and history pruning) the built-in `claude-cua`
  agent follows.
- **[Codex CLI](https://github.com/openai/codex)**, OpenAI's open-source coding
  agent, which the built-in `codex` agent runs unmodified as a harness through
  its non-interactive mode and MCP server support.

We thank the authors and contributors of these projects for making their work
publicly available to the research community.

## 📝 Citation

If OSWorld-Science is useful in your work, please cite the paper:

```bibtex
@misc{dai2026osworldscience,
  title  = {{OSWorld-Science}: A Benchmark of Computer Use Agents for Learning and Using Scientific Software},
  author = {Dai, Dingyuan and Qi, Heli and Liu, Lei and Li, Yinxi and Chen, Baiding and Dou, Zijun and Zeng, Qingcheng and Kang, Qi and Sun, Oliver and Wang, Eric and Zhou, Bo and Wang, Haixin and Du, Yufan and Bo, Shi and Lin, Ruihan and Yuan, Mengqi and Lu, Dunjie and Dillmann, Steven and Shi, Yiming and Su, Tina and Xin, Xin and Liu, Minghao and Wang, Xi and Huang, Xu and Zhang, Ge and Nie, Pengyu and Yang, Zhen and Tang, Jie and Li, Juanzi and Xuan, Weihao and Liu, Tianyu},
  year={2026},
  eprint={2609.39903},
  archivePrefix={arXiv},
  primaryClass={cs.AI},
  url={https://arxiv.org/abs/2609.39903}, 
}
```

## 📄 License

This repository is released under the [Creative Commons Attribution 4.0
International License](LICENSE). Individual third-party software, datasets, and
vendored components remain subject to their respective licenses.

<div align="center">

**[Read the paper](https://arxiv.org/pdf/2609.39903)**
· **[Explore the project](https://huggingface.co/spaces/SciAILab/osworld-science-page)**
· **[Download the data](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data)**

</div>
