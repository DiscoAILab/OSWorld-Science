<div align="center">

# 🧪 OSWorld-Science

### A Benchmark of Computer Use Agents for Learning and Using Scientific Software

*Can computer-use agents turn scientific intent into verifiable results inside real research software?*

[![Paper](https://img.shields.io/badge/Paper-PDF-B31B1B?style=for-the-badge&logo=adobeacrobatreader&logoColor=white)](https://sciailab-osworld-science-page.static.hf.space/static/paper/OSWorld-Science.pdf)
[![Project Page](https://img.shields.io/badge/Project-Page-2563EB?style=for-the-badge&logo=googlechrome&logoColor=white)](https://huggingface.co/spaces/SciAILab/osworld-science-page)
[![Hugging Face](https://img.shields.io/badge/Hugging%20Face-Dataset-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black)](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data)
[![Tasks](https://img.shields.io/badge/Benchmark-146%20Tasks-8B5CF6?style=for-the-badge)](https://sciailab-osworld-science-page.static.hf.space/static/paper/OSWorld-Science.pdf)
[![License](https://img.shields.io/badge/License-CC%20BY%204.0-EF9421?style=for-the-badge&logo=creativecommons&logoColor=white)](LICENSE)

[**Paper**](https://sciailab-osworld-science-page.static.hf.space/static/paper/OSWorld-Science.pdf) ·
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
    <td align="center" width="25%"><b>146 tasks</b><br/><sub>Scientifically meaningful workflows</sub></td>
    <td align="center" width="25%"><b>7 domains</b><br/><sub>From chemistry to linguistics</sub></td>
    <td align="center" width="25%"><b>17 tools</b><br/><sub>Real scientific software</sub></td>
    <td align="center" width="25%"><b>12 VLMs</b><br/><sub>Open and proprietary models</sub></td>
  </tr>
</table>

## 📊 Benchmark at a glance

The full benchmark reported in the paper contains 146 tasks across seven
scientific domains:

| Scientific domain | Tasks | Share | Example workflows |
|---|---:|---:|---|
| Chemistry | 43 | 29.5% | Molecular drawing, paper extraction, retrosynthesis |
| Physics | 31 | 21.2% | CFD and engineering simulation |
| Medicine | 26 | 17.8% | Pathology and medical-image analysis |
| Statistics | 20 | 13.7% | Statistical computing, SQL, plots, and reporting |
| Biology | 18 | 12.3% | Structural biology, NMR, and microscopy |
| Geographic information | 6 | 4.1% | GIS digitization, georeferencing, and spatial analysis |
| Linguistics | 2 | 1.4% | Acoustic analysis and annotation |

The benchmark spans software configurations including QuPath, ChemDraw,
ASKCOS, SAS/R, OpenFOAM, ANSYS, EEGLAB, PyMOL/Mnova, QGIS, CIAO + DS9,
Weasis, Praat, 3D Slicer, and PDF viewers. All tasks require visual observation;
83.6% also support CLI access, while the remaining 16.4% are GUI-only under the
recorded configurations.

## 🧭 Quick start

### 1. Check the host

OSWorld-Science currently supports Linux x86-64 hosts with KVM and Docker.
Plan for approximately **8 GB RAM and 4 vCPUs per concurrent guest**, plus disk
space for the selected VM images. A full download of the eight public runner
images is approximately 196 GB.

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo  # must be greater than 0
ls -l /dev/kvm                      # must be accessible to your user
```

Python 3.11+ is managed with [`uv`](https://docs.astral.sh/uv/). The
R-backed statistics evaluators additionally require `Rscript` (R 4.4.3).

See the [full system requirements](docs/user_guide/requirements.md), or run
the built-in environment check after installation:

```bash
uv run osci doctor
```

### 2. Install

```bash
git clone https://github.com/DiscoAILab/OSWorld-Science.git
cd OSWorld-Science

uv sync --extra hf       # add --extra grader when running grader tests
cp .env.example .env     # add credentials only for the model backends you use
```

### 3. Download tasks and VM images

```bash
# One domain and its prepared VM image
uv run python scripts/data_prep/hf_download.py --domain stat --vm

# All public domains and VM images (~196 GB)
uv run python scripts/data_prep/hf_download.py --vm
```

The files are downloaded from the
[OSWorld-Science dataset](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data).

After downloading, list the complete task registry and validate every task
definition:

```bash
uv run osci tasks list       # list all available tasks, grouped by domain
uv run osci tasks validate   # check every task's schema, evaluator wiring, and snapshot
```

### 4. Run an evaluation

Use `uv run osci models` to list the model names configured in
[`configs/models.yaml`](configs/models.yaml). `--tasks` accepts `all`, a domain
name, or a comma-separated list of task IDs.

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
`--force` is supplied. See the [running guide](docs/user_guide/running.md) for
manual VM control, parallelism, run artifacts, and all command-line options.

## 🔍 How it works

```text
task definition
      │
      ├── reset a domain-specific VM
      ├── stage inputs and launch scientific software
      ├── run the agent: screenshot → VLM → GUI/CLI action
      ├── collect the requested deliverables
      └── score artifacts with task-specific executable evaluators
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
  xlang-ai/OSWorld under Apache-2.0.
- **Models.** [`configs/models.yaml`](configs/models.yaml) records backend,
  upstream model ID, pricing, output limits, and optional agent selection.
- **Evaluators.** The registry includes JSON, CSV, artifact, R, SQL, Slicer, and
  Praat evaluators, along with task-author-provided scientific checks.
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

Prepared VM images are approximately 23–34 GB each. Download an image with
`hf_download.py --domain <domain> --vm`, or rebuild it from the official OSWorld
base image with the corresponding script under `scripts/vm_prep/`.

## 📦 Repository map

```text
configs/               model and VM snapshot registries
data/                  downloaded tasks, references, inputs, and VM images
docs/                  user and developer guides
scripts/data_prep/     Hugging Face dataset synchronization
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

We thank the authors and contributors of both projects for making their work
publicly available to the research community.

## 📝 Citation

If OSWorld-Science is useful in your work, please cite the paper:

```bibtex
@misc{dai2026osworldscience,
  title  = {{OSWorld-Science}: A Benchmark of Computer Use Agents for Learning and Using Scientific Software},
  author = {Dai, Dingyuan and Qi, Heli and Liu, Lei and Li, Yinxi and Chen, Baiding and Dou, Zijun and Zeng, Qingcheng and Kang, Qi and Sun, Oliver and Wang, Eric and Zhou, Bo and Wang, Haixin and Du, Yufan and Bo, Shi and Lin, Ruihan and Yuan, Mengqi and Lu, Dunjie and Dillmann, Steven and Shi, Yiming and Su, Tina and Xin, Xin and Liu, Minghao and Wang, Xi and Huang, Xu and Zhang, Ge and Nie, Pengyu and Yang, Zhen and Tang, Jie and Li, Juanzi and Xuan, Weihao and Liu, Tianyu},
  year   = {2026},
  note   = {Preprint},
  url    = {https://sciailab-osworld-science-page.static.hf.space/static/paper/OSWorld-Science.pdf}
}
```

## 📄 License

This repository is released under the [Creative Commons Attribution 4.0
International License](LICENSE). Individual third-party software, datasets, and
vendored components remain subject to their respective licenses.

<div align="center">

**[Read the paper](https://sciailab-osworld-science-page.static.hf.space/static/paper/OSWorld-Science.pdf)**
· **[Explore the project](https://huggingface.co/spaces/SciAILab/osworld-science-page)**
· **[Download the data](https://huggingface.co/datasets/SciAILab/OSWorld-Science-data)**

</div>
