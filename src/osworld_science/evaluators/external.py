"""Evaluators that delegate to code shipped with the data.

Some task authors deliver their own grader next to the ground truth: the
QuPath packages ship one `evaluator.py` per task, the structural-biology and
chemistry packages ship a `verify_submission.py` with the original OSWorld-style
metric. Those files live under data/<domain>/private/reference_private/<task>/
and are loaded from there, byte for byte, with the calling convention they
were written for. Nothing is copied into this package, so the grading logic
stays the author's and can be checked against the delivered hashes.

`module_metric`       fn(result_path, expected) -> (score, note) | {"score": ..}
`package_metric`      fn(result_state, **options) -> float        (options = gt.json)
`package_text_score`  score_submission_text(agent_text, reference_text) -> {"score": ..}
`text_include_exclude`, `file_min_bytes`   the two generic checks those task
                      packages wire around their metric (a sha256 gate on a
                      protected input, a size check on a deliverable)
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .registry import EvalContext, register

_MODULES: dict[Path, Any] = {}


def load_module(path: Path) -> Any:
    """Import a shipped evaluator file by path. Sibling modules it imports
    (`qupath_eval_common`) resolve against its own directory and are not left
    in sys.modules, so two tasks shipping different files of the same name
    never see each other's copy."""
    path = Path(path).resolve()
    if path in _MODULES:
        return _MODULES[path]
    if not path.is_file():
        raise FileNotFoundError(f"shipped evaluator not found: {path} (download the data)")
    name = "_osci_ext_" + re.sub(r"[^A-Za-z0-9_]", "_", str(path))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    parent = str(path.parent)
    before = set(sys.modules)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(parent)
        for n in set(sys.modules) - before - {name}:
            f = getattr(sys.modules.get(n), "__file__", None) or ""
            if f.startswith(parent):
                del sys.modules[n]
    _MODULES[path] = mod
    return mod


def _clamp(x: Any) -> float:
    return max(0.0, min(1.0, float(x)))


def resolve_metric(mod: Any, name: str) -> Any:
    """Find a check by the name the task JSON uses: an entry of the module's
    METRICS map, a module-level function of that name, or `metric_<name>`
    (packages that inline their checks and export them with that prefix)."""
    table = getattr(mod, "METRICS", None)
    if isinstance(table, dict) and callable(table.get(name)):
        return table[name]
    for attr in (name, f"metric_{name}"):
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn
    raise AttributeError(f"{getattr(mod, '__file__', mod)} has no check {name!r} "
                         f"(looked in METRICS, {name}, metric_{name})")


def _outcome(ret: Any) -> tuple[float, dict]:
    """Normalise what a shipped function returns into (score, detail)."""
    if isinstance(ret, tuple) and len(ret) == 2:
        return _clamp(ret[0]), {"note": str(ret[1])}
    if isinstance(ret, dict):
        detail = {k: v for k, v in ret.items() if k != "score"}
        return _clamp(ret.get("score", 0.0)), detail
    if isinstance(ret, (int, float)) and not isinstance(ret, bool):
        return _clamp(ret), {}
    raise TypeError(f"shipped evaluator returned {type(ret).__name__}, expected (score, note), dict or float")


@contextmanager
def _capture_info_logs() -> Iterator[list[str]]:
    """The original metrics explain their criteria through logging.info; keep
    those lines as the check's detail instead of losing them."""
    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    root = logging.getLogger()
    h = _H(level=logging.INFO)
    old = root.level
    root.addHandler(h)
    if old > logging.INFO or old == logging.NOTSET:
        root.setLevel(logging.INFO)
    try:
        yield lines
    finally:
        root.removeHandler(h)
        root.setLevel(old)


@register("module_metric")
def module_metric(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """One function of a task-author evaluator module (QuPath packages).

    params: module     private path of the module, e.g. reference_private/<task>/evaluator.py
            func       function name; called as fn(result_path, expected)
            file       collected file (submission-relative) handed over as result_path
            oracle_dir private path passed as expected.rules.oracle_dir (the author's private_gt)
            expected   extra rules merged into expected.rules (optional)
    """
    mod = load_module(ctx.resolve(params["module"]))
    fn = resolve_metric(mod, params["func"])
    target = sub / params["file"]
    if not target.exists():
        return 0.0, {"error": f"{params['file']} was not collected from the guest"}
    rules = dict(params.get("expected") or {})
    if params.get("oracle_dir"):
        rules["oracle_dir"] = str(ctx.resolve(params["oracle_dir"]))
    score, detail = _outcome(fn(str(target), {"type": "rule", "rules": rules}))
    detail.setdefault("func", params["func"])
    return score, detail


@register("package_metric")
def package_metric(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """The original OSWorld-style metric shipped in a task package's
    verify_submission.py: fn(result_state, **options) -> 1.0 | 0.0.

    params: module      private path of verify_submission.py
            func        metric name (check_rcsb_faceted, check_protein_alignment, ...)
            files       collected files, in the order the metric documents
            arg         "list" (default): result_state is the list of paths;
                        "path": the single path string
            fixture_dir private dir the metric reads reference structures from
                        (sets the module's _FIXTURE_DIR when it has one)
    options are the task's gt.json (the package's reference.json) minus '_'-keys.
    """
    mod = load_module(ctx.resolve(params["module"]))
    fn = getattr(mod, params["func"], None)
    if not callable(fn):
        raise AttributeError(f"{params['module']} has no callable {params['func']!r}")
    files = [str(sub / f) for f in params["files"]]
    if params.get("fixture_dir") and hasattr(mod, "_FIXTURE_DIR"):
        mod._FIXTURE_DIR = str(ctx.resolve(params["fixture_dir"]))
    options = {k: v for k, v in gt.items() if not k.startswith("_")}
    arg: Any = files[0] if params.get("arg") == "path" else files
    with _capture_info_logs() as lines:
        score = fn(arg, **options)
    missing = [f for f in params["files"] if not (sub / f).exists()]
    return _clamp(score), {"func": params["func"], "criteria": lines, "missing": missing}


@register("package_text_score")
def package_text_score(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """A package scorer that compares the submitted text with a reference text:
    score_submission_text(agent_text, reference_text, **kwargs) -> {"score": .., ...}.

    params: module, file (collected), reference (private path), func (default
            score_submission_text), kwargs (optional)
    """
    mod = load_module(ctx.resolve(params["module"]))
    fn = getattr(mod, params.get("func", "score_submission_text"), None)
    if not callable(fn):
        raise AttributeError(f"{params['module']} has no callable {params.get('func', 'score_submission_text')!r}")
    target = sub / params["file"]
    if not target.exists():
        return 0.0, {"error": f"{params['file']} was not collected from the guest"}
    reference = ctx.resolve(params["reference"]).read_text(encoding="utf-8-sig")
    agent = target.read_text(encoding="utf-8-sig", errors="replace")
    return _outcome(fn(agent, reference, **dict(params.get("kwargs") or {})))


@register("text_include_exclude")
def text_include_exclude(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """A collected text (usually a command line's output) must contain every
    `include` string and none of the `exclude` strings."""
    p = sub / params["file"]
    if not p.exists():
        return 0.0, {"error": f"{params['file']} was not collected from the guest"}
    text = p.read_text(encoding="utf-8", errors="replace")
    missing = [k for k in params.get("include", []) if k not in text]
    forbidden = [k for k in params.get("exclude", []) if k in text]
    ok = not missing and not forbidden
    return (1.0 if ok else 0.0), {"missing": missing, "forbidden_present": forbidden,
                                  "text": text[:300]}


@register("file_min_bytes")
def file_min_bytes(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """A collected file exists and is at least `min_bytes` long."""
    p = sub / params["file"]
    n = p.stat().st_size if p.is_file() else 0
    ok = p.is_file() and n >= int(params.get("min_bytes", 1))
    return (1.0 if ok else 0.0), {"bytes": n}
