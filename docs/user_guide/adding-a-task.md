# Adding a task

A task is one JSON file under `data/<domain>/tasks/`, guest assets under
`data/<domain>/public/assets/`, and ground truth under
`data/<domain>/private/reference_private/<id>/gt.json`. The file name is
the task id.

## The JSON

```jsonc
{
  "id": "stat_example",
  "snapshot": "ubuntu_stat",                 // key in configs/snapshots.yaml
  "instruction": "…the only text the solver sees…",
  "source": "where it came from (no URLs, no course ids)",
  "budget": {"max_steps": 100},
  "config": [                                // staging, run in order against a fresh guest
    {"type": "execute", "parameters": {"command": ["bash", "-c", "rm -rf … && mkdir -p …"]}},
    {"type": "upload",  "parameters": {"local": "assets/stat_example.tgz",
                                       "guest": "/home/user/assets/stat_example.tgz",
                                       "sha256": "…"}},
    {"type": "execute", "parameters": {"command": ["bash", "-c", "tar xzf … && chmod -R a-w …"]}},
    {"type": "launch",  "parameters": {"command": ["praat", "--open", "…"]}},   // detached GUI program
    {"type": "sleep",   "parameters": {"seconds": 4}}
  ],
  "related_apps": ["r", "terminal"],
  "deliverables": ["/home/user/work/submission/result.json"],
  "evaluator": {
    "conj": "weighted",
    "weights": [0.1, 0.9],                   // sum to 1
    "pass_score": 1.0,
    "stages": [{"gate": 0, "blocks_from": 1}],   // check 0 < 1 ⇒ checks 1.. score 0
    "func":   ["protected_inputs_hash", "json_fields"],
    "labels": ["[gate] inputs unmodified", "[stat] 19 quantities"],
    "params": [{"dir": "inputs"}, {"file": "result.json", "gt_path": ["datasets", "a"]}],
    "result": [                              // what `collect` pulls, and where it lands
      {"type": "vm_file", "path": "/home/user/work/submission/result.json", "dest": "result.json"},
      {"type": "vm_file", "path": "/home/user/work/data/input_a.csv",       "dest": "inputs/input_a.csv"}
    ]
  },
  "bench": { "…audit fields, see any data/stat/tasks/*.json for the full set…" }
}
```

`upload.local` and `params.data_dir` are relative to
`data/<domain>/public/`; anything under `reference_private/` resolves in
`data/<domain>/private/`. `uv run osci tasks validate` checks the wiring
(arrays aligned, weights, registered evaluators, gates, deliverables
covered by `result`).

## Ground truth

`gt.json` is whatever your evaluators need. The shipped evaluators read
spec trees of `{"expect": v, "tol_abs"|"tol_rel": t, "floor": f}` leaves
(`json_fields`), row lists plus a `table_spec` (`csv_table`),
`protected_inputs: {rel_path: sha256}` (`protected_inputs_hash`), and
`held_out_calls` with `_args` (`r_function_probe`). Generate it with a
script that never reads a solver's output, keep the script next to it in
`reference_private/`, and never let a ground-truth value appear in the
instruction. The stat tasks show the discipline worth copying: two data
sets per task, hash-gated inputs, executable graders, negative tests written
before the task is believed. Task ids carry no version suffix; if a task's
definition changes incompatibly, give it a new id.

## Evaluators

Register a new one:

```python
from osworld_science.evaluators import register, EvalContext

@register("my_check")
def my_check(submission_dir, params, gt, ctx: EvalContext):
    ...
    return score_in_0_1, {"failures": [...], "n_failures": n}
```

`ctx.resolve(rel)` maps suite-relative paths to `data/`; `ctx.rscript` is
the host Rscript. Third-party packages can expose the
`osworld_science.evaluators` entry-point group.

## Tests

Build a correct submission from `gt.json`, then mutate copies of it: every
shortcut you can think of must FAIL, every in-tolerance variant must PASS.
Those suites embed answer values, so they live with the ground truth
(`data/<domain>/private/grader_tests/run_tests.py`) rather than in git, and
are run by `tests/grader/`.


## Results that are not files

Besides `vm_file`, a result can be a command run inside the guest whose
stdout and stderr are kept as a text file in the submission directory:

```json
{"type": "vm_command_line",
 "command": ["bash", "-c", "sha256sum /home/user/work/inputs/slide.ome.tif | cut -d' ' -f1"],
 "dest": "protected_input_sha256.txt", "timeout": 900}
```

Use it for evidence that is impractical to pull (the hash of a 600 MB
input) or that is a state rather than a file (the head of a project file).
Grade the text with `text_include_exclude` (`{"file": "protected_input_sha256.txt",
"include": ["<sha256>"]}`), usually as a zero-weight gate. A deliverable
counts as covered when a `vm_file` pulls it or a `vm_command_line`
inspects it.

## Reusing an evaluator shipped with the data

If the task author delivered their own grader, ship it unchanged under
`data/<domain>/private/reference_private/<task_id>/` and wire it with
`module_metric`, `package_metric` or `package_text_score` — see the table in
`docs/developer_guide/adding-a-domain.md` and any `data/biomed/tasks/*.json`.
