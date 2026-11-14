"""configs/models.yaml → model registry, and its resolution against the environment.

Two sections share the backends: `models:` are the solvers a run drives, `judges:`
are the models `osci judge` uses to review free-text deliverables. They are kept
apart so a judge never appears as a column in a run and a solver is never picked
as its own reviewer by accident.

A backend carries the API root (`base_url`); which path is called on it is the
agent harness's choice (`api` in configs/agents/<agent>.yaml): chat/completions
for the PromptAgent and the Kimi agent, /responses for the OpenAI Agents SDK.
Anthropic backends always speak /v1/messages. Each model lists the agents it can
run under and the one `osci run` picks when --agent is not given."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import expand_template
from .usage import COST_MODES, PRICE_KEYS

API_PATHS = {"chat_completions": "/chat/completions", "responses": "/responses", "messages": "/v1/messages"}
ANTHROPIC_MESSAGES_PATH = API_PATHS["messages"]
DEFAULT_AGENT = "prompt"


class MissingCredential(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendSpec:
    name: str
    kind: str                       # openai_chat | anthropic_messages
    base_url_template: str          # the API root; the agent's `api` adds the path
    key_env: str
    max_tokens_field: str = "max_tokens"
    reasoning_style: str = ""       # "" | openai | openrouter
    usage_include: bool = False
    supports_provider_order: bool = False


@dataclass(frozen=True)
class ModelSpec:
    name: str
    backend: BackendSpec
    model_id: str
    price: dict | None = None
    max_out: int | None = None
    provider_order: tuple[str, ...] = ()
    agents: tuple[str, ...] = (DEFAULT_AGENT,)   # harnesses this model can run under
    default_agent: str = DEFAULT_AGENT
    reasoning: bool = False
    cost: str = "static"            # api = the cost the API returns per call | static = price table × tokens
    extra: dict = field(default_factory=dict)

    def supports_agent(self, agent: str) -> bool:
        return agent in self.agents


@dataclass(frozen=True)
class ResolvedModel:
    """A ModelSpec plus the API root and key taken from the environment."""
    spec: ModelSpec
    base_url: str
    key: str

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def model_id(self) -> str:
        return self.spec.model_id

    @property
    def backend(self) -> BackendSpec:
        return self.spec.backend

    def endpoint(self, api: str = "chat_completions") -> str:
        """The URL an agent harness posts to: base_url plus the path of `api`
        (chat_completions | responses | messages). Anthropic backends always speak
        /v1/messages whatever `api` says; an OpenAI-style backend has no Messages API."""
        if api not in API_PATHS:
            raise ValueError(f"unknown api {api!r}; one of {', '.join(API_PATHS)}")
        if self.backend.kind == "anthropic_messages":
            return self.base_url + ANTHROPIC_MESSAGES_PATH
        if api == "messages":
            raise ValueError(f"model {self.name} is on {self.backend.name} ({self.backend.kind}), which has no "
                             f"Anthropic Messages API")
        return self.base_url + API_PATHS[api]

    def openai_client_kwargs(self, timeout: float = 300.0) -> dict:
        """Constructor arguments for the official `openai` client against this backend
        (Azure's v1 route wants the key in an `api-key` header as well)."""
        kwargs = {"api_key": self.key, "base_url": self.base_url, "timeout": timeout}
        if self.backend.name == "azure":
            kwargs["default_headers"] = {"api-key": self.key}
        return kwargs

    def describe(self) -> dict:
        return {"model": self.spec.name, "model_id": self.spec.model_id,
                "backend": self.spec.backend.name, "base_url": self.base_url,
                "provider_order": list(self.spec.provider_order) or None,
                "price_usd_per_1m": self.spec.price, "cost_mode": self.spec.cost,
                "agents": list(self.spec.agents), "default_agent": self.spec.default_agent}


def normalise_base_url(url: str) -> str:
    """Strip a trailing slash and a trailing endpoint path (an old .env that still says
    .../chat/completions keeps working)."""
    url = url.strip().rstrip("/")
    for path in (API_PATHS["chat_completions"], API_PATHS["responses"]):
        if url.endswith(path):
            url = url[: -len(path)]
    return url.rstrip("/")


class ModelRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        doc = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.backends: dict[str, BackendSpec] = {}
        for name, b in (doc.get("backends") or {}).items():
            if "base_url" not in b:
                raise ValueError(f"backend {name} in {self.path}: needs `base_url` (the API root without "
                                 f"/chat/completions; the agent config picks the path)")
            kind = str(b["kind"])
            if kind not in ("openai_chat", "anthropic_messages"):
                raise ValueError(f"backend {name}: unknown kind {kind!r} (openai_chat | anthropic_messages)")
            self.backends[name] = BackendSpec(
                name=name, kind=kind, base_url_template=str(b["base_url"]),
                key_env=str(b["key_env"]), max_tokens_field=str(b.get("max_tokens_field", "max_tokens")),
                reasoning_style=str(b.get("reasoning_style", "") or ""),
                usage_include=bool(b.get("usage_include", False)),
                supports_provider_order=bool(b.get("supports_provider_order", False)))
        self.models: dict[str, ModelSpec] = {}
        for name, m in (doc.get("models") or {}).items():
            self.models[name] = self._spec(name, m, "model")
        judges = dict(doc.get("judges") or {})
        self.default_judge: str | None = judges.pop("default", None)
        self.judges: dict[str, ModelSpec] = {name: self._spec(name, m, "judge") for name, m in judges.items()}
        if self.default_judge is not None and self.default_judge not in self.judges:
            raise ValueError(f"judges.default names {self.default_judge!r}, which is not a judge in {self.path}")

    _KNOWN = frozenset({"backend", "model_id", "price", "max_out", "provider_order", "agents", "default_agent",
                        "reasoning", "cost"})

    def _spec(self, name: str, m: dict, kind: str) -> ModelSpec:
        if "/" in name:
            raise ValueError(f"{kind} name {name!r} must not contain '/': it is used as a directory name")
        backend = self.backends.get(m["backend"])
        if backend is None:
            raise ValueError(f"{kind} {name}: unknown backend {m['backend']!r}")
        if "agent" in m:
            raise ValueError(f"{kind} {name}: `agent:` is no longer read; write `agents: [...]` (supported) "
                             f"and `default_agent:` instead")
        agents = tuple(str(a) for a in (m.get("agents") or ()))
        if kind == "judge":
            if agents or "default_agent" in m:
                raise ValueError(f"judge {name}: judges take no `agents`/`default_agent` (they are never a harness)")
            agents = ()
            default_agent = ""
        else:
            agents = agents or (DEFAULT_AGENT,)
            default_agent = str(m.get("default_agent") or agents[0])
            if default_agent not in agents:
                raise ValueError(f"model {name}: default_agent {default_agent!r} is not in agents {list(agents)}")
        price = m.get("price")
        if price is not None:
            if not isinstance(price, dict):
                raise ValueError(f"{kind} {name}: price must be a mapping {{in, out, cache_read, cache_write}}")
            unknown = sorted(set(price) - set(PRICE_KEYS))
            if unknown:
                raise ValueError(f"{kind} {name}: unknown price key(s) {unknown}; allowed: {', '.join(PRICE_KEYS)}")
        # `api` needs a backend that returns the cost of each call (usage_include: OpenRouter)
        cost = str(m.get("cost") or ("api" if backend.usage_include else "static"))
        if cost not in COST_MODES:
            raise ValueError(f"{kind} {name}: cost must be one of {', '.join(COST_MODES)}, not {cost!r}")
        if cost == "api" and not backend.usage_include:
            raise ValueError(f"{kind} {name}: cost: api, but backend {backend.name} returns no per-call cost "
                             f"(only backends with usage_include: true do); use cost: static")
        return ModelSpec(
            name=name, backend=backend, model_id=str(m["model_id"]),
            price=price, max_out=m.get("max_out"), cost=cost,
            provider_order=tuple(m.get("provider_order") or ()),
            agents=agents, default_agent=default_agent, reasoning=bool(m.get("reasoning", False)),
            extra={k: v for k, v in m.items() if k not in self._KNOWN})

    def names(self) -> list[str]:
        return list(self.models)

    def get(self, name: str) -> ModelSpec:
        try:
            return self.models[name]
        except KeyError:
            raise KeyError(f"unknown model {name!r}; known: {', '.join(self.models)} "
                           f"(add a line to {self.path})") from None

    def judge_names(self) -> list[str]:
        return list(self.judges)

    def get_judge(self, name: str | None) -> ModelSpec:
        """`None` or "default" is the `judges.default` entry."""
        if name in (None, "", "default"):
            if not self.default_judge:
                raise KeyError(f"no judges.default in {self.path}")
            name = self.default_judge
        try:
            return self.judges[name]
        except KeyError:
            raise KeyError(f"unknown judge {name!r}; known: {', '.join(self.judges) or '(none)'} "
                           f"(add it under judges: in {self.path})") from None

    def resolve_judge(self, name: str | None, env: dict[str, str]) -> ResolvedModel:
        return self._resolve(self.get_judge(name), env)

    def resolve(self, name: str, env: dict[str, str]) -> ResolvedModel:
        return self._resolve(self.get(name), env)

    def _resolve(self, spec: ModelSpec, env: dict[str, str]) -> ResolvedModel:
        name = spec.name
        key = env.get(spec.backend.key_env) or ""
        if not key:
            raise MissingCredential(f"{name} needs {spec.backend.key_env} in .env, which is empty")
        try:
            base = expand_template(spec.backend.base_url_template, env)
        except KeyError as e:
            raise MissingCredential(f"{name} needs {e.args[0]} in .env, which is empty") from None
        return ResolvedModel(spec=spec, base_url=normalise_base_url(base), key=key)
