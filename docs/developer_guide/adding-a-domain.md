# Adding a domain

A domain is a folder of tasks that share a desktop (one VM snapshot, or a
few) and, usually, a few domain-specific evaluators. The shipped domains are
the template: `stat` (R/RStudio/Python desktop, 20 tasks), `radiology`
(Weasis + 3D Slicer, 3 tasks), `linguistics` (Praat, 2 tasks), `biomed`
(QuPath, and PyMOL + browser; 26 tasks) and `chem` (browser + XDrawChem,
1 task). A domain may declare more than one snapshot; each task names the
one it runs on.

Everything a domain consists of, and where it goes:

| piece | where | required? |
|---|---|---|
| task definitions | `data/<domain>/tasks/<task_id>.json` | yes |
| guest assets (what the VM receives) | `data/<domain>/public/assets/…` | yes if any task uploads files |
| ground truth | `data/<domain>/private/reference_private/<task_id>/gt.json` | yes (scoring); runs without it collect but do not score |
| VM snapshot | `configs/snapshots.yaml` entry + `data/<domain>/vm/<snapshot>.qcow2` | yes |
| image build scripts | `scripts/vm_prep/<domain>/` | yes if the snapshot needs software beyond the base image |
| domain evaluators | `src/osworld_science/evaluators/<domain>.py` | only if the shipped evaluators do not cover the task |
| grader test suite | `data/<domain>/private/grader_tests/run_tests.py` | strongly recommended |
| pre-task hooks, window classes, bake cleanup | snapshot entry | optional |
| a custom agent | `src/osworld_science/agents/…` | no — agents are domain-independent |

The steps below are in the order that lets you test each one before the next.

## 1. Decide the desktop: the snapshot

Add an entry to `configs/snapshots.yaml`:

```yaml
  ubuntu_<domain>:
    description: what is installed
    image: ubuntu_<domain>.qcow2        # data/<domain>/vm/, built in step 2 or downloaded
    default_port: 5090                  # pick an unused control port; noVNC is port+3006
    container_prefix: osci_<domain>
    tools_probe: "command -v mytool"    # non-empty output + exit 0 ⇒ toolchain present
    shell_init: "source ~/miniconda3/etc/profile.d/conda.sh && conda activate mytool"  # optional: run before tools_probe / check_commands
    provision: scripts/vm_prep/<domain>/provision_guest.sh   # optional: run when the probe fails
    pre_task_hooks: []                  # optional, see § 6
    check_commands: [mytool, python3, firefox]               # what `osci vm check` lists
    window_classes: {mytool: mytool, gnome-terminal: terminal}  # optional: WM_CLASS substrings tallied per step
    bake_cleanup: "rm -rf /home/user/.mytool"                # optional: extra cleanup before baking
```

Required keys: `image`, `default_port`, `container_prefix`. `tools_probe`
is what makes `osci vm reset` self-healing: if the booted image lacks the
toolchain (for example you booted the pristine base), the `provision`
script is run. Without a probe, reset trusts the image. The probe and
`check_commands` run in a non-interactive login shell (`bash -lc`); if the
toolchain is only put on PATH by `~/.bashrc` for interactive shells (a conda
env, for example), give `shell_init` the same activation line the desktop
terminal runs so the probes see what the solver sees.

## 2. Build the image

Create `scripts/vm_prep/<domain>/`:

* `provision_guest.sh --port P` — installs the software into a running
  guest. Long steps must go through `scripts/vm_prep/common/guest_run.py`
  (the guest control plane's `/execute` times out after ~120 s). The guest
  user is `user`, in `sudo`, password `password`. Copy
  `scripts/vm_prep/linguistics/praat_setup.py` for an apt package,
  `radiology/weasis_setup.py` for a `.deb`, `radiology/slicer_setup.py` for
  a tarball plus desktop entry, `stat/provision_guest.sh` for a conda lock.
* `build_image.sh` — three lines calling `scripts/vm_prep/common/build_image.sh
  ubuntu_<domain> <port> <provisioners…>`; it boots the base, provisions,
  runs `osci vm check`, and bakes to `data/<domain>/vm/ubuntu_<domain>.qcow2`.
  The full procedure is `building-a-vm-snapshot.md` in this guide.

Rules that cost the legacy suites real time: pin versions; handle the apt
lock held by unattended-upgrades right after boot; put GUI programs in the
applications menu and on `PATH` (agents find software both ways); clear
first-run dialogs and update checks before baking (agents burn steps on
them); never bake a guest an agent has used.

Check: `uv run osci vm reset --snapshot ubuntu_<domain>` then
`uv run osci vm check --snapshot ubuntu_<domain>` shows the tools.

## 3. Write the tasks

One JSON per task under `data/<domain>/tasks/` — see `docs/user_guide/adding-a-task.md`
for the fields. The parts that tie a task to its domain:

* `"snapshot": "ubuntu_<domain>"`;
* `config`: the staging steps. `execute` runs a command (a non-zero exit
  aborts staging), `upload` sends a file from `data/<domain>/public/…` to a
  guest path (sha256 verified), `launch` starts a GUI program detached,
  `sleep` waits. Stage into a clean directory and make the inputs
  read-only, as the shipped tasks do;
* `evaluator.result`: which guest files to pull back and where they land
  in the submission directory (`inputs/…` for protected inputs);
* `evaluator.func/params/weights/labels/stages`: the checks.

Ids are stable names without version suffixes; an incompatible change is a
new id. `related_apps` and `bench` are documentation for readers and
audits, not read by the runner — but keep the `bench` audit fields
(language requirement, reward-hacking audit, false-negative audit): they
are what reviewers of the benchmark will ask for.

Check: `uv run osci tasks validate` (schema, wiring, weights sum to 1,
registered evaluators, gates, deliverables covered) and `uv run osci stage
<task_id>` against the reset guest, then look at the desktop through noVNC.


## 4. Ship the data

Public (what the guest receives, plus what evaluators re-read):

```
data/<domain>/public/assets/<task_id>.tgz      bundles uploaded by `upload` steps (optional: upload files directly)
data/<domain>/public/assets/data/<family>/…    the same files unpacked, if an evaluator needs them
```

Private (never enters the guest):

```
data/<domain>/private/reference_private/<task_id>/gt.json
data/<domain>/private/reference_private/…            reference files an evaluator reads (name them in gt.json with reference_private/… paths)
data/<domain>/private/grader_tests/run_tests.py      see § 7
```

Paths inside task JSONs and `gt.json` are relative to `data/<domain>/public/`,
except those starting with `reference_private/`, which resolve under
`data/<domain>/private/` (`osworld_science.data.DataLayout`). Nothing else
belongs in the trees: no generators, oracles, raw sources, notes or
credentials — those stay with the authors. Ground truth must be produced by
a script that never reads a solver's output, and no ground-truth value may
appear in an instruction.

Both trees are mirrors of one public Hugging Face dataset; after adding a
domain run `scripts/data_prep/hf_upload.py` (`--dry-run` first).

## 5. Evaluators

Use the shipped ones when they fit: `json_fields` (spec trees with
per-field tolerances), `csv_table` (row-by-row with a key column),
`protected_inputs_hash` (inputs unchanged; usually the gate),
`artifact` (a file exists and is what it claims), `r_syntax`,
`r_function_probe` (call the solver's R function with held-out arguments),
`sql_query_replay`, `shiny_app_check`, `markups_points` (Slicer landmarks),
`praat_vot_textgrid`.

Otherwise add `src/osworld_science/evaluators/<domain>.py`:

```python
from pathlib import Path
from osworld_science.evaluators import register, EvalContext
from osworld_science.evaluators.core import compare_scalar, summarise

@register("my_check")
def my_check(submission: Path, params: dict, gt: dict, ctx: EvalContext) -> tuple[float, dict]:
    p = submission / params["file"]
    if not p.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    results = [{"field": "x", "pass": ok, "detail": why} for ok, why in ...]
    return summarise(results)          # (passed fraction, {checked, passed, failures, n_failures})
```

and list the module in `_BUILTIN` in `evaluators/registry.py` (or expose it
through the `osworld_science.evaluators` entry-point group from another
package). Conventions worth keeping: return `{"error": …}` with score 0 for
malformed submissions instead of raising; use `compare_scalar` so
tolerances live in `gt.json`; read host paths through `ctx.resolve()`;
never touch the guest. If a check runs solver-written code (as
`r_function_probe` does), say so in the label and bound its runtime.

## 6. Optional: hooks and per-step tallies

* `pre_task_hooks` run host scripts before staging, optionally only for
  tasks whose `related_apps` contains a keyword (`when_related_app`);
  `required: false` logs a failure and continues. The stat domain uses one
  to restore a browser profile; the astro domain uses a required one to
  bring up DS9 in the state its tasks assume (`scripts/vm_prep/astro/`).
  Hooks receive `--port`; `.py` hooks run under the project's interpreter.
* `window_classes` maps WM_CLASS substrings to tags; the runner records
  `wmctrl -lx` every step and tallies `apps_seen` in `meta.json`, which is
  how a GUI route is told from a scripted one after the fact.
* `bake_cleanup` is appended to the generic cleanup before baking.

## 7. Grader tests

Write `data/<domain>/private/grader_tests/run_tests.py` following
`linguistics/grader_tests/run_tests.py`: build the correct submission from
`gt.json` (and, if needed, `make_fixtures.py` + `fixtures/`), then mutate
copies of it. Every shortcut must FAIL (answers copied between data sets,
a tampered input, a missing deliverable, a memorised constant, a value
just outside tolerance); every in-tolerance variant must PASS. The script
calls `<suite root>/score.py <task_id> --submission <dir> --json`;
`tests/grader/test_grader_suites.py` supplies that command and runs the
suite under pytest when the data is present. A grader with a bug that
fails correct answers looks exactly like a hard benchmark; only these
tests tell the two apart.

## 8. Run it

```bash
uv run osci preflight --models <model>
uv run osci run --models <model> --tasks <domain> --run-name <domain>_smoke
uv run osci report --run <domain>_smoke
```

Then read one trajectory end to end (`runs/<run>/<task>/<model>/agent/
shots/`, `trace.jsonl`, `meta.json`) before trusting any number.

## Checklist

- [ ] snapshot entry + image build script; `osci vm reset` and `osci vm check` pass
- [ ] `data/<domain>/tasks/*.json`; `osci tasks validate` passes; staging looks right in noVNC
- [ ] `data/<domain>/public/assets` (+ `assets/data`) and `data/<domain>/private/reference_private/<id>/gt.json`
- [ ] evaluators registered; `osci score` on a hand-made submission gives the expected verdict
- [ ] `grader_tests/run_tests.py`: correct passes, attacks fail, variants pass
- [ ] one real agent run per task, trajectories read
- [ ] optional: hooks, window classes, bake cleanup


## Reusing an evaluator the task author ships

When a task package comes with its own grader (one `evaluator.py` per task,
or a `verify_submission.py` carrying the original metric), do not port it:
ship it unchanged under `data/<domain>/private/reference_private/<task_id>/`
next to the private ground truth, and wire it with one of the delegating
evaluators in `src/osworld_science/evaluators/external.py`:

| evaluator | calls | params |
|---|---|---|
| `module_metric` | `fn(result_path, {"type": "rule", "rules": {"oracle_dir": …}})` → `(score, note)` or `{"score": …}` | `module`, `func`, `file`, `oracle_dir`, optional `expected` |
| `package_metric` | `fn(result_state, **options)` → `1.0 / 0.0`, options = the task's `gt.json` | `module`, `func`, `files` (ordered), `arg` (`list` or `path`), `fixture_dir` |
| `package_text_score` | `score_submission_text(agent_text, reference_text)` → `{"score": …}` | `module`, `file`, `reference` |
| `text_include_exclude` | a collected text must contain / not contain strings | `file`, `include`, `exclude` |
| `file_min_bytes` | a collected file exists and is large enough | `file`, `min_bytes` |

Paths in `params` are private-tree relative (`reference_private/…`); the
harness resolves them. Sibling imports inside a shipped module resolve
against its own directory and are not shared between tasks. The host-side
imports those modules need are optional extras (`uv sync --extra biomed
--extra chem --extra geoscience`). The `biomed`, `chem` and `geoscience` task
definitions are worked examples.

A gate that hashes a file too large to pull (a whole-slide image) is a
`vm_command_line` result: the harness runs the command in the guest and
keeps its output as a text file for `text_include_exclude` — see
`docs/user_guide/adding-a-task.md`.
