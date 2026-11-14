"""The config.toml one Codex run gets (agents/codex).

Each cell runs Codex with its own ``CODEX_HOME`` (``<cell>/agent/codex_home``) holding a
config.toml generated from configs/models.yaml and configs/agents/codex.yaml, so the user's
~/.codex is never read and the file stays with the run as a record of exactly what Codex was
told. The model's backend becomes a ``model_providers.osci`` entry (Responses wire API, key from
the backend's ``key_env``, Azure's extra ``api-key`` header), the desktop becomes
``mcp_servers.desktop`` (agents/codex/desktop_mcp.py), approvals are off (``codex exec`` never
prompts), and Codex's own host-side tools are switched off by feature flag unless asked for.

``config_overrides`` in the agent config is merged last, so any key Codex understands can be
set without touching this module. The TOML writer covers what a config.toml needs: scalars,
arrays of scalars and nested tables.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...llm.models import ResolvedModel

PROVIDER_ID = "osci"                 # the generated model_providers entry
DESKTOP_SERVER = "desktop"           # the generated mcp_servers entry
AUTH_MODES = ("env_key", "chatgpt")

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k, ensure_ascii=False)


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError(f"TOML cannot carry {v!r}")
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, Path):
        return json.dumps(str(v), ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        if any(isinstance(x, dict) for x in v):
            raise ValueError("arrays of tables are not supported by this writer")
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def toml_dumps(doc: dict[str, Any]) -> str:
    """A dict as TOML: scalar keys first, then one `[a.b]` table per nested mapping (recursively).
    `None` values are left out."""
    lines: list[str] = []

    def emit(table: dict[str, Any], path: tuple[str, ...]) -> None:
        scalars = {k: v for k, v in table.items() if v is not None and not isinstance(v, dict)}
        tables = {k: v for k, v in table.items() if isinstance(v, dict)}
        if path and (scalars or not tables):      # a header only where it carries keys (or an empty table)
            if lines:
                lines.append("")
            lines.append("[" + ".".join(_key(p) for p in path) + "]")
        for k, v in scalars.items():
            lines.append(f"{_key(k)} = {_scalar(v)}")
        for k, v in tables.items():
            emit(v, (*path, k))

    emit(doc, ())
    return "\n".join(lines) + "\n"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def provider_table(model: ResolvedModel, options: dict[str, Any]) -> dict[str, Any]:
    """The `model_providers.osci` entry for this model's backend: its API root, the environment
    variable holding its key (the run exports it), the Responses wire API (the only one Codex
    speaks), and Codex's own retry knobs. Azure's v1 route wants the key in an `api-key` header
    as well (what ResolvedModel.openai_client_kwargs does for the openai client)."""
    be = model.backend
    if be.kind == "anthropic_messages":
        raise ValueError(f"model {model.name} is on {be.name} (Anthropic Messages); Codex speaks the Responses API only")
    table: dict[str, Any] = {
        "name": f"{be.name} (configs/models.yaml)",
        "base_url": model.base_url,
        "env_key": be.key_env,
        "wire_api": "responses",
        "request_max_retries": int(options.get("request_max_retries", 4)),
        "stream_max_retries": int(options.get("stream_max_retries", 5)),
        "stream_idle_timeout_ms": int(options.get("stream_idle_timeout_ms", 300_000)),
    }
    if be.name == "azure":
        table["env_http_headers"] = {"api-key": be.key_env}
    compaction = options.get("remote_compaction")          # `capabilities` is unknown to Codex <= 0.156: opt-in
    if compaction:
        table["capabilities"] = {"remote_compaction": str(compaction)}
    return deep_merge(table, options.get("provider_overrides") or {})


def desktop_server_table(args: list[str], *, python: str, startup_timeout_s: int = 60, tool_timeout_s: int = 600,
                         env: dict[str, str] | None = None) -> dict[str, Any]:
    """The `mcp_servers.desktop` entry: the stdio server Codex starts for the guest desktop."""
    table: dict[str, Any] = {"command": python, "args": list(args), "startup_timeout_sec": int(startup_timeout_s),
                             "tool_timeout_sec": int(tool_timeout_s), "required": True,
                             # `codex exec` runs with approval_policy never, and in the default `auto` mode a tool
                             # without a destructiveHint=false annotation asks for approval, i.e. is refused
                             "default_tools_approval_mode": "approve"}
    if env:
        table["env"] = dict(env)
    return table


def codex_config(model: ResolvedModel, options: dict[str, Any], *, reasoning_effort: str,
                 developer_instructions: str | None, mcp_server: dict[str, Any] | None) -> dict[str, Any]:
    """The whole config.toml as a dict. Keys with None values are dropped by the writer."""
    auth = str(options.get("auth", "env_key"))
    if auth not in AUTH_MODES:
        raise ValueError(f"codex auth must be one of {', '.join(AUTH_MODES)}, not {auth!r}")
    host_shell = bool(options.get("host_shell", False))
    features: dict[str, Any] = {"shell_tool": host_shell, "unified_exec": host_shell}
    features.update(options.get("features") or {})
    doc: dict[str, Any] = {
        "model": model.model_id,
        "model_provider": PROVIDER_ID if auth == "env_key" else "openai",
        "model_reasoning_effort": reasoning_effort or None,
        "model_reasoning_summary": options.get("reasoning_summary", "auto"),
        "model_verbosity": options.get("verbosity"),
        "service_tier": options.get("service_tier"),
        "model_context_window": options.get("model_context_window"),
        "model_auto_compact_token_limit": options.get("model_auto_compact_token_limit"),
        "tool_output_token_limit": options.get("tool_output_token_limit"),
        "approval_policy": "never",
        "sandbox_mode": str(options.get("sandbox_mode", "read-only")),
        "web_search": str(options.get("web_search", "disabled")),
        "project_doc_max_bytes": 0,                    # no AGENTS.md from the host's directories
        "check_for_update_on_startup": False,
        "include_environment_context": bool(options.get("include_environment_context", False)),
        "hide_agent_reasoning": False,                 # reasoning summaries show up in the event stream
        "developer_instructions": developer_instructions or None,
        "model_providers": {PROVIDER_ID: provider_table(model, options)} if auth == "env_key" else None,
        "features": features,
        "history": {"persistence": "none"},
        "analytics": {"enabled": False},
        "feedback": {"enabled": False},
        "mcp_servers": {DESKTOP_SERVER: mcp_server} if mcp_server else None,
    }
    doc = deep_merge(doc, options.get("config_overrides") or {})
    return {k: v for k, v in doc.items() if v is not None}
