"""Evaluator registry.

An evaluator is `fn(submission_dir, params, gt, ctx) -> (score, detail)` with
`score` in [0, 1]. Register with `@register("name")`; the name is what a task
JSON writes in `evaluator.func`. Third-party packages can expose evaluators
through the `osworld_science.evaluators` entry-point group (each entry point
resolves to a module that registers on import).
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..data.layout import DataLayout

Evaluator = Callable[[Path, dict, dict, "EvalContext"], tuple[float, dict]]

REGISTRY: dict[str, Evaluator] = {}
_BUILTIN = ("osworld_science.evaluators.common", "osworld_science.evaluators.stat",
            "osworld_science.evaluators.radiology", "osworld_science.evaluators.linguistics",
            "osworld_science.evaluators.external")
_loaded = False


@dataclass
class EvalContext:
    """What the legacy evaluators pulled from `Path(__file__)` and env vars."""
    domain: str
    layout: DataLayout
    rscript: str = "Rscript"
    task: Any = None

    def resolve(self, rel: str | Path) -> Path:
        return self.layout.resolve(self.domain, rel)


def register(name: str) -> Callable[[Evaluator], Evaluator]:
    def deco(fn: Evaluator) -> Evaluator:
        if name in REGISTRY and REGISTRY[name] is not fn:
            raise ValueError(f"evaluator {name!r} registered twice")
        REGISTRY[name] = fn
        return fn
    return deco


def load_builtin() -> None:
    global _loaded
    if _loaded:
        return
    for mod in _BUILTIN:
        importlib.import_module(mod)
    try:  # third-party evaluators
        from importlib.metadata import entry_points
        for ep in entry_points(group="osworld_science.evaluators"):
            importlib.import_module(ep.value)
    except Exception:  # noqa: BLE001 — never let a broken plugin stop grading
        pass
    _loaded = True


def get(name: str) -> Evaluator | None:
    load_builtin()
    return REGISTRY.get(name)


def names() -> set[str]:
    load_builtin()
    return set(REGISTRY)
