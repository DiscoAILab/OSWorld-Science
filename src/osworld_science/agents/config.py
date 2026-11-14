"""configs/agents/*.yaml → per-agent harness settings.

One file per registered agent, named by its `agent:` key (prompt, kimi,
openai-cua). Every file carries `api`, the OpenAI-style API the harness calls
on `openai_chat` backends (chat_completions | responses); the rest is the
agent's own knobs (history window, Kimi budget, CUA tool protocol …), handed to
the factory as `AgentRunConfig.options`. Command-line overrides
(`--agent-option key=value`, `--screenshot-limit`, `--kimi-mode`, …) are merged
on top of the file; an agent without a file runs on its built-in defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..llm.models import API_PATHS

DEFAULT_API = "chat_completions"


@dataclass(frozen=True)
class AgentConfig:
    name: str                               # the registered agent name
    api: str = DEFAULT_API                  # chat_completions | responses
    options: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None                # the file it came from; None = built-in defaults

    def with_overrides(self, overrides: dict[str, Any]) -> AgentConfig:
        """A copy with command-line overrides applied; `api` may be overridden too. A dotted
        key sets one field of a mapping (`screenshot_window.limit=30` keeps keep and prune)."""
        merged = dict(self.options)
        for key, value in overrides.items():
            if key == "api":
                continue
            head, dot, tail = key.partition(".")
            if dot and tail:
                base = merged.get(head)
                merged[head] = {**(base if isinstance(base, dict) else {}), tail: value}
            else:
                merged[key] = value
        api = overrides.get("api", self.api)
        if api not in API_PATHS:
            raise ValueError(f"agent {self.name}: api must be one of {', '.join(API_PATHS)}, not {api!r}")
        return AgentConfig(name=self.name, api=api, options=merged, path=self.path)

    def describe(self) -> dict:
        return {"agent": self.name, "api": self.api, "config_file": str(self.path) if self.path else None,
                "options": dict(self.options)}


def load_agent_config(path: Path) -> AgentConfig:
    path = Path(path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict) or not doc.get("agent"):
        raise ValueError(f"{path}: an agent config is a mapping with an `agent:` key naming the registered agent")
    name = str(doc.pop("agent"))
    api = str(doc.pop("api", DEFAULT_API))
    if api not in API_PATHS:
        raise ValueError(f"{path}: api must be one of {', '.join(API_PATHS)}, not {api!r}")
    return AgentConfig(name=name, api=api, options=dict(doc), path=path)


def load_agent_configs(directory: Path) -> dict[str, AgentConfig]:
    """Every configs/agents/*.yaml keyed by its `agent:` name."""
    directory = Path(directory)
    out: dict[str, AgentConfig] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        cfg = load_agent_config(path)
        if cfg.name in out:
            raise ValueError(f"{path} and {out[cfg.name].path} both configure agent {cfg.name!r}")
        out[cfg.name] = cfg
    return out


def agent_config(configs: dict[str, AgentConfig], name: str, overrides: dict[str, Any] | None = None) -> AgentConfig:
    """The config for `name`, built-in defaults when it has no file, with CLI overrides applied."""
    base = configs.get(name) or AgentConfig(name=name)
    cfg = base.with_overrides(overrides or {})
    if "screenshot_window" in cfg.options or "history_window" in cfg.options:
        from .window import WindowConfig
        try:   # a bad window should stop a sweep before any guest starts, not at its first cell
            WindowConfig.from_options(cfg.options)
        except ValueError as e:
            raise ValueError(f"agent {name}: {e}") from None
    return cfg


def parse_option(text: str) -> tuple[str, Any]:
    """`key=value` from the command line; the value is read as a YAML scalar
    (5 → int, false → bool, quoted text stays text)."""
    if "=" not in text:
        raise ValueError(f"--agent-option expects key=value, got {text!r}")
    key, value = text.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"--agent-option expects key=value, got {text!r}")
    return key, yaml.safe_load(value) if value.strip() else ""
