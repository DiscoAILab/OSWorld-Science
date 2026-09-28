"""configs/models.yaml → model registry, and its resolution against the environment."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import expand_template


class MissingCredential(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendSpec:
    name: str
    kind: str                       # openai_chat | anthropic_messages
    url_template: str
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
    agent: str | None = None
    reasoning: bool = False
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedModel:
    """A ModelSpec plus the endpoint URL and key taken from the environment."""
    spec: ModelSpec
    url: str
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

    @property
    def base_url(self) -> str:
        """The OpenAI-style base (what clients that append /chat/completions want)."""
        return self.url.rsplit("/chat/completions", 1)[0]

    def describe(self) -> dict:
        return {"model": self.spec.name, "model_id": self.spec.model_id,
                "backend": self.spec.backend.name, "endpoint": self.url,
                "provider_order": list(self.spec.provider_order) or None,
                "price_usd_per_1m": self.spec.price, "agent": self.spec.agent or "prompt"}


class ModelRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        doc = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.backends: dict[str, BackendSpec] = {}
        for name, b in (doc.get("backends") or {}).items():
            self.backends[name] = BackendSpec(
                name=name, kind=str(b["kind"]), url_template=str(b["url"]),
                key_env=str(b["key_env"]), max_tokens_field=str(b.get("max_tokens_field", "max_tokens")),
                reasoning_style=str(b.get("reasoning_style", "") or ""),
                usage_include=bool(b.get("usage_include", False)),
                supports_provider_order=bool(b.get("supports_provider_order", False)))
        self.models: dict[str, ModelSpec] = {}
        known = {"backend", "model_id", "price", "max_out", "provider_order", "agent", "reasoning"}
        for name, m in (doc.get("models") or {}).items():
            if "/" in name:
                raise ValueError(f"model name {name!r} must not contain '/': it is used as a directory name")
            backend = self.backends.get(m["backend"])
            if backend is None:
                raise ValueError(f"model {name}: unknown backend {m['backend']!r}")
            self.models[name] = ModelSpec(
                name=name, backend=backend, model_id=str(m["model_id"]),
                price=m.get("price"), max_out=m.get("max_out"),
                provider_order=tuple(m.get("provider_order") or ()),
                agent=m.get("agent"), reasoning=bool(m.get("reasoning", False)),
                extra={k: v for k, v in m.items() if k not in known})

    def names(self) -> list[str]:
        return list(self.models)

    def get(self, name: str) -> ModelSpec:
        try:
            return self.models[name]
        except KeyError:
            raise KeyError(f"unknown model {name!r}; known: {', '.join(self.models)} "
                           f"(add a line to {self.path})") from None

    def resolve(self, name: str, env: dict[str, str]) -> ResolvedModel:
        spec = self.get(name)
        key = env.get(spec.backend.key_env) or ""
        if not key:
            raise MissingCredential(f"{name} needs {spec.backend.key_env} in .env, which is empty")
        try:
            url = expand_template(spec.backend.url_template, env)
        except KeyError as e:
            raise MissingCredential(f"{name} needs {e.args[0]} in .env, which is empty") from None
        return ResolvedModel(spec=spec, url=url, key=key)
