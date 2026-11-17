"""Host-only phase identification grading; no task answers are embedded here."""
from __future__ import annotations

import json
import math
import re
from functools import reduce
from pathlib import Path

from .registry import register

_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_TOKEN = re.compile(r"([A-Z][a-z]?)([1-9][0-9]*)?")


def formula_key(value: str) -> tuple:
    """Compare elemental ratios, preserving element case (Co is not CO)."""
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("phase must be a chemical formula string")
    text = re.sub(r"\s+", "", value.translate(_SUBSCRIPTS))
    def group(start, depth=0):
        if depth > 8:
            raise ValueError("formula nesting is too deep")
        counts: dict[str, int] = {}
        pos = start
        while pos < len(text) and text[pos] != ")":
            if text[pos] == "(":
                inner, pos = group(pos + 1, depth + 1)
                if pos >= len(text) or text[pos] != ")" or not inner:
                    raise ValueError("unbalanced or empty formula group")
                pos += 1
                multiplier = re.match(r"[1-9][0-9]*", text[pos:])
                factor = int(multiplier[0]) if multiplier else 1
                if multiplier:
                    pos += len(multiplier[0])
                for el, number in inner.items():
                    counts[el] = counts.get(el, 0) + number * factor
            else:
                match = _TOKEN.match(text, pos)
                if not match:
                    raise ValueError("use element symbols, integer counts and balanced parentheses")
                counts[match[1]] = counts.get(match[1], 0) + int(match[2] or 1)
                pos = match.end()
        return counts, pos

    counts, end = group(0)
    if not counts or end != len(text):
        raise ValueError("empty or unbalanced formula")
    factor = reduce(math.gcd, counts.values())
    return tuple(sorted((el, n // factor) for el, n in counts.items()))


@register("xrd_phase_set")
def phase_set(sub: Path, params: dict, gt: dict, ctx):
    """1.0 only for the exact phase set; precision and recall are reported for analysis.

    Formulas are compared by identity, so polymorphs are not distinguished.
    """
    path = sub / params["file"]
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("submission must be a regular JSON file of at most 64 KiB")
        def unique_keys(pairs):
            out = {}
            for k, v in pairs:
                if k in out:
                    raise ValueError("duplicate JSON key")
                out[k] = v
            return out
        doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        raw = doc.get("phases") if isinstance(doc, dict) else None
        if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
            raise ValueError('expected {"phases": ["chemical formula", ...]}')
        got, invalid = [], []
        for index, value in enumerate(raw):
            try:
                got.append(formula_key(value))
            except ValueError as exc:
                invalid.append({"index": index, "error": str(exc)})
        if len(set(got)) != len(got):
            raise ValueError("duplicate equivalent phase formulas")
        expected = {formula_key(v) for v in gt["phases"]}
    except (OSError, ValueError, TypeError) as exc:
        return 0.0, {"error": str(exc)}
    got = set(got)
    tp = len(got & expected)
    predicted_count = len(got) + len(invalid)
    exact = not invalid and got == expected
    return float(exact), {"true_positives": tp, "false_positives": len(got - expected) + len(invalid),
                   "false_negatives": len(expected - got), "precision": tp / predicted_count,
                   "recall": tp / len(expected), "invalid_entries": invalid}
