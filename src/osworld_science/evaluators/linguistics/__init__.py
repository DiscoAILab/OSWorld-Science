"""Linguistics-domain evaluators.

The `*_verifier.py` modules are task authors' graders, vendored byte-identical
(`praat_vot_verifier.py` == praat_vot_task/verifier.py for Plosive1;
`praat_vot_neg_verifier.py` == praat_vot_neg_dir1_1/verifier.py for the
negative-VOT token). Each hard-codes its own word list, tolerances and VOT
sign, so a task names the module it is graded by in `params.verifier`
(default `praat_vot_verifier`). This module only adapts them to the registry.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from ..core import summarise
from ..registry import EvalContext, register

VERIFIERS = ("praat_vot_verifier", "praat_vot_neg_verifier")


def _verifier(name: str):
    if name not in VERIFIERS:
        raise ValueError(f"unknown Praat verifier {name!r}; known: {', '.join(VERIFIERS)}")
    return importlib.import_module(f"{__name__}.{name}")


@register("praat_vot_textgrid")
def praat_vot_textgrid(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Grade a Praat TextGrid of word-initial VOT intervals against the
    private reference with the author's verifier named by `params.verifier`.
    Ground truth names the initial TextGrid (public assets) and the reference
    (private), both host-side. Score is the verifier's passed_tokens / total_tokens."""
    v = _verifier(params.get("verifier", "praat_vot_verifier"))
    p = sub / params["file"]
    if not p.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    try:
        rep = v.evaluate_submission(p, ctx.resolve(gt["reference_textgrid"]),
                                    ctx.resolve(gt["initial_textgrid"]))
    except v.ConfigurationError as e:
        return 0.0, {"error": f"grader configuration invalid (not the solver's fault): {e}"}
    results = []
    for tk in rep["tokens"]:
        errs = tk.get("errors_ms")
        detail = ("; ".join(tk["reasons"]) if tk["reasons"] else
                  f"start {errs['start']:.3f} / end {errs['end']:.3f} / dur {errs['duration']:.3f} ms "
                  f"(tol {tk['tolerance_ms']:.0f} ms)" if errs else "ok")
        results.append({"field": tk["word"], "pass": bool(tk["passed"]), "detail": detail})
    score, detail = summarise(results)
    detail["global_errors"] = rep["global_errors"]
    detail["valid_submission"] = rep["valid_submission"]
    detail["passed_words"] = rep["passed_tokens"]
    detail["verifier"] = v.__name__.rsplit(".", 1)[-1]
    # summarise() gives the passed fraction, which for equally weighted words is
    # the verifier's own score; assert the two agree rather than trust it.
    assert abs(score - rep["score"]) < 1e-9, (score, rep["score"])
    return rep["score"], detail
