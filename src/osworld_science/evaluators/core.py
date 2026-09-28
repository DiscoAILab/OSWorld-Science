"""Comparison primitives shared by every evaluator.

Design rule inherited from the OpenFOAM bench: a field is graded with the
tolerance model that matches what produced it, never with one global epsilon.
`floor` exists because a purely relative test on a quantity whose expected
value passes near zero fails on rounding noise alone.
"""
from __future__ import annotations

import math
import re
from typing import Any

NULLISH = {"", ".", "na", "n/a", "nan", "null", "none", "missing", "_"}


def as_number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if isinstance(v, float) and math.isnan(v) else float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s.lower() in NULLISH:
            return None
        s = re.sub(r"^\$", "", s)
        try:
            return float(s)
        except ValueError:
            return None
    return None


def is_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip().lower() in NULLISH:
        return True
    return False


def compare_scalar(spec: dict, got: Any) -> tuple[bool, str]:
    """Grade one field against its spec.

    spec is either {"expect": <value>, "match": "exact_string"} or
    {"expect": <number>, "tol_abs"|"tol_rel": t, "floor": f}.
    """
    exp = spec.get("expect")

    if spec.get("match") == "exact_string":
        # Match the declared literal before applying the numeric/null parser.
        # Some tasks deliberately use a word such as "none" as an exact
        # empty-set representation; NULLISH must not make that string
        # impossible to submit correctly.
        ok = got is not None and str(got).strip() == str(exp).strip()
        return ok, f"got {got!r}, expected {exp!r}"

    if exp is None:
        return is_null(got), f"got {got!r}, expected an empty/missing value"

    if isinstance(exp, str):
        ok = got is not None and str(got).strip() == exp.strip()
        return ok, f"got {got!r}, expected {exp!r}"

    val = as_number(got)
    if val is None:
        return False, f"missing or non-numeric (raw={got!r}), expected {exp:g}"

    d = abs(val - exp)
    has_abs, has_rel = "tol_abs" in spec, "tol_rel" in spec

    # Both present means "either is enough". That combination exists for
    # p-values: a relative band alone fails a solver who read 0.0047 off
    # printed output when the exact value is 0.00470515 — a 1.1e-3 relative
    # miss that is not a wrong answer, it is four decimal places. An absolute
    # band alone would be meaningless across p-values spanning 1e-45 to 0.5.
    if has_abs and has_rel:
        ta, tr = float(spec["tol_abs"]), float(spec["tol_rel"])
        floor = float(spec.get("floor", 0.0))
        rel = d / abs(exp) if exp else float("inf")
        ok = d <= max(ta, floor) or rel <= tr
        return ok, (f"|Δ| = {d:.4g} (abs tol {ta:g}), rel {rel:.3e} "
                    f"(tol {tr:g}); got {val:.10g} expect {exp:.10g}")

    if has_abs:
        tol = float(spec["tol_abs"])
        return d <= tol, f"|{val:.10g} - {exp:.10g}| = {d:.4g} (tol {tol:g})"

    if has_rel:
        tol = float(spec["tol_rel"])
        floor = float(spec.get("floor", 0.0))
        if d <= floor:
            return True, f"|Δ| = {d:.4g} within floor {floor:g}"
        denom = abs(exp)
        if denom == 0:
            return False, f"expected 0, got {val:.10g} (floor {floor:g})"
        rel = d / denom
        return rel <= tol, (f"rel {rel:.3e} (tol {tol:g}); "
                            f"got {val:.10g} expect {exp:.10g}")

    return val == exp, f"exact: got {val!r} expected {exp!r}"


def walk_spec(spec: Any, got: Any, path: str = "") -> list[dict]:
    """Recursively grade a spec tree against a parsed submission.

    A dict carrying an "expect" key is a leaf; anything else is a namespace.
    """
    results: list[dict] = []
    if isinstance(spec, dict) and "expect" in spec:
        ok, detail = compare_scalar(spec, got)
        results.append({"field": path or "<root>", "pass": ok,
                        "detail": detail, "got": got, "expect": spec["expect"]})
        return results
    if isinstance(spec, dict):
        for k, sub in spec.items():
            if k.startswith("_"):
                continue
            child = None
            if isinstance(got, dict):
                child = got.get(k)
            results += walk_spec(sub, child, f"{path}.{k}" if path else k)
        return results
    if isinstance(spec, list):
        for i, sub in enumerate(spec):
            child = got[i] if isinstance(got, list) and i < len(got) else None
            results += walk_spec(sub, child, f"{path}[{i}]")
        return results
    results.append({"field": path, "pass": False,
                    "detail": f"malformed spec node: {type(spec).__name__}",
                    "got": got, "expect": None})
    return results


def summarise(results: list[dict]) -> tuple[float, dict]:
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    fails = [r for r in results if not r["pass"]]
    return (passed / total if total else 0.0,
            {"checked": total, "passed": passed,
             "failures": [{"field": f["field"], "detail": f["detail"]}
                          for f in fails[:40]],
             "n_failures": len(fails)})


def normalise_key(s: str) -> str:
    """Column-name matching that tolerates case and separator drift.

    Solvers deliver ScoreA / score_a / SCOREA depending on the software that
    wrote the file; none of those is a wrong answer to the question asked.
    """
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def index_columns(fieldnames: list[str]) -> dict[str, str]:
    return {normalise_key(f): f for f in fieldnames if f is not None}
