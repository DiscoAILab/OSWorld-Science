"""Agent registry: name → factory(model, task, cfg, raw_log) -> Agent.
Third parties can add agents through the `osworld_science.agents`
entry-point group (module that registers on import)."""
from __future__ import annotations

import importlib
from collections.abc import Callable

_REGISTRY: dict[str, Callable] = {}
_BUILTIN = ("osworld_science.agents.prompt_agent", "osworld_science.agents.kimi")
_loaded = False


def register_agent(name: str) -> Callable[[Callable], Callable]:
    def deco(factory: Callable) -> Callable:
        _REGISTRY[name] = factory
        return factory
    return deco


def load_builtin() -> None:
    global _loaded
    if _loaded:
        return
    for mod in _BUILTIN:
        importlib.import_module(mod)
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="osworld_science.agents"):
            importlib.import_module(ep.value)
    except Exception:  # noqa: BLE001
        pass
    _loaded = True


def get_agent_factory(name: str) -> Callable:
    load_builtin()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown agent {name!r}; known: {', '.join(_REGISTRY)}") from None


def agent_names() -> list[str]:
    load_builtin()
    return list(_REGISTRY)
