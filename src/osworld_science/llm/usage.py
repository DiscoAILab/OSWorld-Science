"""Token, cache and cost accounting shared by every agent and the judge.

Each provider spells its `usage` object differently (measured 2026-10-09, see
docs/internal_notes/endpoint-usage-and-cost.md):

  chat/completions   prompt_tokens (cache included), completion_tokens,
                     prompt_tokens_details.{cached_tokens, cache_write_tokens},
                     completion_tokens_details.reasoning_tokens; Moonshot has
                     also used a top-level `cached_tokens`, DashScope's qwen-cua a
                     top-level `reasoning_tokens`; OpenRouter adds `cost`,
                     `cost_details` and `is_byok`
  responses          input_tokens (cache included), output_tokens,
                     input_tokens_details.{cached_tokens, cache_write_tokens},
                     output_tokens_details.reasoning_tokens (+ `cost` on OpenRouter)
  Anthropic messages input_tokens (cache NOT included), cache_read_input_tokens,
                     cache_creation_input_tokens (+ cache_creation.ephemeral_1h_input_tokens),
                     output_tokens (+ output_tokens_details.thinking_tokens)

`parse_usage` brings all of them to one `CallUsage` in which `input_tokens`
counts every prompt token, cached or not. `UsageMeter` sums calls for one model
and prices them the way the model's `cost:` in configs/models.yaml says:

  static  price table × tokens, every token class at its own rate:
          uncached input × in + cache reads × cache_read + cache writes ×
          cache_write (Anthropic 1-hour writes × cache_write_1h) + output × out.
          A missing cache rate falls back to `in` (no discount assumed).
  api     the cost the API returned for the call (OpenRouter `usage.cost`; with a
          bring-your-own-key route, `cost` + `cost_details.upstream_inference_cost`).
          A call that came back without one is priced statically and counted in
          `n_calls_cost_estimated`.

The static estimate is always kept as well (`cost_usd_static`), so the two can be
compared on the routes that return a cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRICE_KEYS = ("in", "out", "cache_read", "cache_write", "cache_write_1h")
COST_MODES = ("api", "static")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


@dataclass
class CallUsage:
    input_tokens: int = 0            # every prompt token: uncached + cache reads + cache writes
    output_tokens: int = 0           # reasoning tokens included
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0      # all cache writes (Anthropic: 5-minute + 1-hour)
    cache_write_1h_tokens: int = 0   # the 1-hour part of the writes (Anthropic only)
    reasoning_tokens: int = 0
    cache_reported: bool = False     # the usage object carried a cache field at all
    api_cost: float | None = None    # what the API said the call cost, in USD

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cache_read_tokens - self.cache_write_tokens)


def to_dict(usage: Any) -> dict | None:
    """A usage object (dict, pydantic model, or an SDK dataclass) as a plain dict."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    out = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "requests"):
        if hasattr(usage, key):
            out[key] = getattr(usage, key)
    for key in ("input_tokens_details", "output_tokens_details"):
        if hasattr(usage, key):
            out[key] = _dict(getattr(usage, key))
    return out or None


def parse_usage(usage: Any, anthropic: bool = False) -> CallUsage | None:
    """One provider usage object as a CallUsage; None when there is nothing to count.
    `anthropic` selects the Messages API convention (input_tokens excludes the cache)."""
    u = to_dict(usage)
    if not u:
        return None
    call = CallUsage()
    if anthropic or "cache_read_input_tokens" in u or "cache_creation_input_tokens" in u:
        read = _int(u.get("cache_read_input_tokens"))
        write = _int(u.get("cache_creation_input_tokens"))
        call.input_tokens = _int(u.get("input_tokens")) + read + write
        call.output_tokens = _int(u.get("output_tokens"))
        call.cache_read_tokens, call.cache_write_tokens = read, write
        call.cache_write_1h_tokens = _int(_dict(u.get("cache_creation")).get("ephemeral_1h_input_tokens"))
        call.reasoning_tokens = _int(_dict(u.get("output_tokens_details")).get("thinking_tokens"))
        call.cache_reported = "cache_read_input_tokens" in u or "cache_creation_input_tokens" in u
    else:
        chat = "prompt_tokens" in u
        call.input_tokens = _int(u.get("prompt_tokens") if chat else u.get("input_tokens"))
        call.output_tokens = _int(u.get("completion_tokens") if chat else u.get("output_tokens"))
        details = _dict(u.get("prompt_tokens_details") if chat else u.get("input_tokens_details"))
        out_details = _dict(u.get("completion_tokens_details") if chat else u.get("output_tokens_details"))
        call.cache_read_tokens = _int(details.get("cached_tokens")) or _int(u.get("cached_tokens"))
        call.cache_write_tokens = _int(details.get("cache_write_tokens"))
        call.reasoning_tokens = _int(out_details.get("reasoning_tokens")) or _int(u.get("reasoning_tokens"))
        call.cache_reported = "cached_tokens" in details or "cache_write_tokens" in details or "cached_tokens" in u
    cost = u.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        api = float(cost)
        if u.get("is_byok"):    # the provider bills the key holder directly; OpenRouter's fee is `cost`
            upstream = _dict(u.get("cost_details")).get("upstream_inference_cost")
            if isinstance(upstream, (int, float)):
                api += float(upstream)
        call.api_cost = api
    if not (call.input_tokens or call.output_tokens or call.api_cost):
        return None
    return call


def static_cost(call: CallUsage, price: dict | None) -> float | None:
    """USD for one call from the price table (USD per 1M tokens); None without in/out prices."""
    price = price or {}
    p_in, p_out = price.get("in"), price.get("out")
    if p_in is None or p_out is None:
        return None
    p_read = price.get("cache_read") if price.get("cache_read") is not None else p_in
    p_write = price.get("cache_write") if price.get("cache_write") is not None else p_in
    p_write_1h = price.get("cache_write_1h") if price.get("cache_write_1h") is not None else p_write
    write_5m = max(0, call.cache_write_tokens - call.cache_write_1h_tokens)
    return (call.uncached_input_tokens * p_in + call.cache_read_tokens * p_read + write_5m * p_write
            + call.cache_write_1h_tokens * p_write_1h + call.output_tokens * p_out) / 1e6


class UsageMeter:
    """Sums the calls of one model and prices them per its `cost:` mode."""

    def __init__(self, price: dict | None, cost_mode: str = "static", anthropic: bool = False):
        if cost_mode not in COST_MODES:
            raise ValueError(f"cost mode must be one of {', '.join(COST_MODES)}, not {cost_mode!r}")
        self.price, self.cost_mode, self.anthropic = price or {}, cost_mode, anthropic
        self.calls = 0
        self.input_tokens = self.output_tokens = 0
        self.cache_read_tokens = self.cache_write_tokens = self.reasoning_tokens = 0
        self.cache_reported = False
        self.cost = 0.0
        self.cost_static = 0.0
        self.upstream_cost: float | None = None
        self.n_cost_estimated = 0       # api mode: calls without an API cost, priced statically
        self.n_unpriced = 0             # calls that could not be priced at all

    @classmethod
    def for_model(cls, model) -> UsageMeter:
        """A meter for a ResolvedModel or ModelSpec."""
        spec = getattr(model, "spec", model)
        return cls(spec.price, getattr(spec, "cost", "static") or "static",
                   anthropic=spec.backend.kind == "anthropic_messages")

    def add(self, usage: Any) -> dict | None:
        """Count one call. Returns the per-call record (also what the raw request log
        stores), or None when the usage object held nothing countable."""
        call = parse_usage(usage, anthropic=self.anthropic)
        if call is None:
            return None
        self.calls += 1
        self.input_tokens += call.input_tokens
        self.output_tokens += call.output_tokens
        self.cache_read_tokens += call.cache_read_tokens
        self.cache_write_tokens += call.cache_write_tokens
        self.reasoning_tokens += call.reasoning_tokens
        self.cache_reported = self.cache_reported or call.cache_reported
        est = static_cost(call, self.price)
        if est is not None:
            self.cost_static += est
        if call.api_cost is not None:
            self.upstream_cost = (self.upstream_cost or 0.0) + call.api_cost
        if self.cost_mode == "api" and call.api_cost is not None:
            cost, source = call.api_cost, "api"
        else:
            cost, source = est, "static"
            if self.cost_mode == "api":
                self.n_cost_estimated += 1
        if cost is None:
            self.n_unpriced += 1
        else:
            self.cost += cost
        return {"input_tokens": call.input_tokens, "cache_read_tokens": call.cache_read_tokens,
                "cache_write_tokens": call.cache_write_tokens, "output_tokens": call.output_tokens,
                "reasoning_tokens": call.reasoning_tokens,
                "cost_usd": None if cost is None else round(cost, 8), "cost_source": source}

    @property
    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens served from the cache; None when the API never said."""
        if not self.cache_reported or not self.input_tokens:
            return None
        return round(self.cache_read_tokens / self.input_tokens, 4)

    @property
    def cost_source(self) -> str:
        if self.cost_mode == "static":
            return "static"
        if self.n_cost_estimated == 0:
            return "api"
        return "static" if self.n_cost_estimated == self.calls else "api+static"

    def stats(self) -> dict:
        """The token_stats() every agent reports (meta.json, summary.json)."""
        priced = self.calls - self.n_unpriced
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
                "cached_input_tokens": self.cache_read_tokens,     # the cache reads (name kept for old tables)
                "cache_write_tokens": self.cache_write_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "cache_hit_rate": self.cache_hit_rate,
                "n_llm_calls": self.calls,
                "cost_usd": round(self.cost, 8) if priced else None,
                "cost_source": self.cost_source,
                "n_calls_cost_estimated": self.n_cost_estimated,
                "cost_usd_static": round(self.cost_static, 8) if (self.price.get("in") is not None
                                                                  and self.price.get("out") is not None) else None,
                "upstream_cost_usd": None if self.upstream_cost is None else round(self.upstream_cost, 8)}


def raw_log_records(path) -> list[dict]:
    """The usage objects of a cell's `agent/llm_raw.jsonl`, one per model call: the plain
    request records of ModelCaller and the Kimi tap, and the `model_response` records of
    the computer-use agents (their per-run `run` summary would count everything twice)."""
    import json
    from pathlib import Path
    out = []
    p = Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") not in (None, "model_response"):
            continue
        usage = rec.get("raw_usage") or rec.get("usage")
        if isinstance(usage, dict) and usage:
            out.append(usage)
    return out
