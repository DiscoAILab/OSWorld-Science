"""Radiology-domain evaluators (ported from radiology_osworld/evaluators/checks.py)."""
from __future__ import annotations

import re
from pathlib import Path

from ._io import read_json
from .core import summarise
from .registry import EvalContext, register


def _norm_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


@register("markups_points")
def markups_points(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Grade a 3D Slicer markups file (.mrk.json) of point control points.

    Ground truth is a list of {label, lps:[L,P,S], tol_mm}. Positions are read
    in the file's declared coordinateSystem (LPS default; RAS converted).
    Each expected label must be present exactly once within tol_mm (3D
    Euclidean) of the expected position.
    """
    doc, err = read_json(sub / params["file"])
    if err:
        return 0.0, {"error": err}
    expected = gt
    for step in params.get("gt_path", []):
        expected = expected[step]

    markups = doc.get("markups") if isinstance(doc, dict) else None
    if not markups:
        return 0.0, {"error": "file has no 'markups' array — not a Slicer markups file"}
    system = str(doc.get("coordinateSystem", markups[0].get("coordinateSystem", "LPS"))).upper()
    if system not in ("LPS", "RAS"):
        return 0.0, {"error": f"unknown coordinateSystem {system!r}"}

    got = {}
    n_points = 0
    for m in markups:
        for cp in m.get("controlPoints", []):
            pos = cp.get("position")
            if not (isinstance(pos, list) and len(pos) == 3):
                continue
            n_points += 1
            p = [float(v) for v in pos]
            if system == "RAS":  # RAS -> LPS
                p = [-p[0], -p[1], p[2]]
            got.setdefault(_norm_label(cp.get("label", "")), []).append(p)

    results = [{"field": "<point count>", "pass": n_points == len(expected),
                "detail": f"{n_points} control points, expected {len(expected)}"}]
    for e in expected:
        key = _norm_label(e["label"])
        cands = got.get(key, [])
        if len(cands) != 1:
            results.append({"field": e["label"], "pass": False,
                            "detail": ("label absent" if not cands
                                       else f"label appears {len(cands)} times")})
            continue
        d = sum((a - b) ** 2 for a, b in zip(cands[0], e["lps"], strict=False)) ** 0.5
        results.append({"field": e["label"], "pass": d <= float(e["tol_mm"]),
                        "detail": f"{d:.1f} mm from reference (tol {e['tol_mm']} mm)"})
    return summarise(results)
