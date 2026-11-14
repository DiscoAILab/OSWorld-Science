"""The single seam between agents and providers.

`ModelCaller(payload) -> str` takes the OpenAI-style payload the PromptAgent
has always produced (`messages`, `max_tokens`) and speaks either
chat/completions (Azure, OpenRouter, Google, Qwen …) or the native
Anthropic Messages API. The endpoint is the backend's base_url plus the path
of the agent's `api` (configs/agents/<agent>.yaml); this caller implements
`chat_completions` only — the Responses API is driven by the OpenAI Agents
SDK inside the `openai-cua` agent. Retries, truncation doubling, usage
accounting and the raw request log are a verbatim port of the legacy caller
so that runs stay comparable.
"""
from __future__ import annotations

import json
import pathlib
import time

import requests

from .models import ResolvedModel
from .usage import UsageMeter

requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]


def to_anthropic(messages: list, max_tokens: int) -> dict:
    """OpenAI payload → Anthropic Messages body."""
    system_parts, out = [], []
    for msg in messages:
        parts = msg["content"] if isinstance(msg["content"], list) else [
            {"type": "text", "text": str(msg["content"])}]
        if msg["role"] == "system":
            system_parts += [p["text"] for p in parts if p.get("type") == "text"]
            continue
        content = []
        for p in parts:
            if p.get("type") == "image_url":
                url = p["image_url"]["url"]
                data = url.split("base64,", 1)[-1]
                media = url[5:url.index(";")] if url.startswith("data:") and ";" in url else "image/png"
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": media, "data": data}})
            elif p.get("type") == "text":
                content.append({"type": "text", "text": p["text"]})
        out.append({"role": msg["role"], "content": content})
    body = {"max_tokens": max_tokens, "messages": out}
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    return body


def flatten_system(messages: list) -> list:
    """System content as a plain string: some proxies only accept that form."""
    out = []
    for msg in messages:
        if msg["role"] == "system" and isinstance(msg["content"], list):
            txt = "\n".join(p["text"] for p in msg["content"] if p.get("type") == "text")
            out.append({"role": "system", "content": txt})
        else:
            out.append(msg)
    return out


class NonJSONResponse(RuntimeError):
    """The upstream answered with HTTP, but the body is not JSON. The status,
    content-type and body head are kept so the failure can be attributed
    (a tunnel's bare `Internal Server Error` vs. a well-formed refusal)."""

    def __init__(self, r):
        self.scene = {"http": r.status_code,
                      "content_type": r.headers.get("content-type"),
                      "server": r.headers.get("server"),
                      "body_head": r.text[:400]}
        super().__init__(f"HTTP {r.status_code} {r.headers.get('content-type')} {r.text[:120]!r}")


def json_or_raise(r):
    try:
        return r.json()
    except ValueError:
        raise NonJSONResponse(r) from None


class ModelCaller:
    """Multi-backend caller with retries, usage accounting and per-request logging."""

    MAX_ATTEMPTS = 4

    def __init__(self, model: ResolvedModel, raw_log: pathlib.Path,
                 reasoning_effort: str = "", request_timeout: int = 300, log=print,
                 api: str = "chat_completions"):
        if model.backend.kind != "anthropic_messages" and api != "chat_completions":
            raise ValueError(f"ModelCaller speaks chat/completions only on {model.backend.name}; api {api!r} needs "
                             f"an agent that drives it itself (openai-cua: Responses API through the Agents SDK; "
                             f"claude-cua: the Messages API with the computer toolset)")
        self.model, self.raw = model, pathlib.Path(raw_log)
        self.name, self.cfg = model.name, model
        self.api = api
        self.url = model.endpoint(api)
        self.reasoning_effort = reasoning_effort
        self.request_timeout = request_timeout
        self.log = log
        # tokens, cache reads/writes and cost, priced per the model's `cost:` (llm/usage.py)
        self.meter = UsageMeter.for_model(model)
        # how the most recent request ended: {"finish", "truncated", "max_tokens", "output_tokens",
        # "reasoning_tokens"}. An empty reply does not say why it is empty; the judge reads this
        # to tell an allowance spent entirely on reasoning from a transport failure.
        self.last: dict = {}

    # ── accounting ─────────────────────────────────────────────────────────
    def _record(self, rec: dict) -> None:
        with self.raw.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @property
    def input_tokens(self) -> int:
        return self.meter.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.meter.output_tokens

    @property
    def total_tokens(self) -> int:
        return self.meter.input_tokens + self.meter.output_tokens

    @property
    def cached_input_tokens(self) -> int:
        return self.meter.cache_read_tokens

    @property
    def calls(self) -> int:
        return self.meter.calls

    def token_stats(self) -> dict:
        return self.meter.stats()

    # ── the call ───────────────────────────────────────────────────────────
    def __call__(self, payload: dict, _attempt: int = 0, _max_tokens: int | None = None) -> str:
        m = self.model
        b = m.backend
        messages = payload["messages"]
        cap = m.spec.max_out or 0
        max_tokens = _max_tokens or int(payload.get("max_tokens") or 16000)
        if cap:
            max_tokens = min(max_tokens, cap)
        t0 = time.time()
        try:
            if b.kind == "anthropic_messages":
                body = {"model": m.model_id, **to_anthropic(messages, max_tokens)}
                r = requests.post(self.url, json=body, timeout=self.request_timeout, verify=False,
                                  headers={"x-api-key": m.key, "anthropic-version": "2023-06-01",
                                           "content-type": "application/json"})
                j = json_or_raise(r)
                content = "".join(blk.get("text", "") for blk in j.get("content", [])
                                  if isinstance(blk, dict)) or None
                finish = j.get("stop_reason")
                truncated = finish == "max_tokens"
                resolved, provider = j.get("model"), None
            else:
                body = {"model": m.model_id, "messages": flatten_system(messages),
                        b.max_tokens_field: max_tokens}
                if m.spec.provider_order:
                    body["provider"] = {"order": list(m.spec.provider_order), "allow_fallbacks": True}
                if b.usage_include:
                    body["usage"] = {"include": True}
                if payload.get("response_format"):       # judges ask for JSON objects; agents never set this
                    body["response_format"] = payload["response_format"]
                # temperature/top_p are never sent: reasoning models reject them and
                # every model then runs under the same (provider-default) condition.
                if m.spec.reasoning and self.reasoning_effort:
                    if b.reasoning_style == "openrouter":
                        body["reasoning"] = {"effort": self.reasoning_effort}
                    else:
                        body["reasoning_effort"] = self.reasoning_effort
                # a judge bounding its reasoning on a second try (judge/client.py); agents never set this,
                # and only OpenRouter's unified `reasoning` object has a token budget
                if payload.get("reasoning") and b.reasoning_style == "openrouter":
                    body["reasoning"] = payload["reasoning"]
                r = requests.post(self.url, json=body, timeout=self.request_timeout, verify=False,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {m.key}", "api-key": m.key})
                j = json_or_raise(r)
                ch = (j.get("choices") or [{}])[0]
                content = (ch.get("message") or {}).get("content")
                finish = ch.get("finish_reason")
                truncated = finish == "length"
                resolved, provider = j.get("model"), j.get("provider")

            usage = j.get("usage")
            counted = self.meter.add(usage) if isinstance(usage, dict) else None
            details = (usage or {}).get("completion_tokens_details") if isinstance(usage, dict) else None
            self.last = {"finish": finish, "truncated": bool(truncated), "max_tokens": max_tokens,
                         "output_tokens": (usage or {}).get("completion_tokens", (usage or {}).get("output_tokens"))
                         if isinstance(usage, dict) else None,
                         "reasoning_tokens": (details or {}).get("reasoning_tokens") if isinstance(details, dict) else None}
            self._record({"model": self.name, "model_id": m.model_id, "http": r.status_code,
                          "elapsed_s": round(time.time() - t0, 1), "finish": finish,
                          "content_len": len(content or ""), "usage": usage,
                          # normalised: cache reads/writes, reasoning tokens, the call's cost and its source
                          "counted": counted,
                          "max_tokens": max_tokens, "resolved_model": resolved,
                          "provider": provider, "n_messages": len(messages),
                          "attempt": _attempt,
                          "error": (j.get("error") if r.status_code != 200 else None)})

            if r.status_code == 200 and content:
                return content
            if truncated:
                nxt = min(max_tokens * 2, cap) if cap else max_tokens * 2
                if nxt > max_tokens and _attempt < self.MAX_ATTEMPTS - 1:
                    self.log(f"     .. truncated, doubling max_tokens to {nxt} and retrying")
                    return self(payload, _attempt + 1, nxt)
                # two different stops: the model's declared `max_out` was reached, or the doubling ran
                # out of attempts below it (16000 -> 128000 at most); only the first is the model's cap
                self.log(f"     !! truncated at the output cap {max_tokens}; giving up this step" if cap and max_tokens >= cap
                         else f"     !! still truncated at max_tokens {max_tokens} after {_attempt + 1} attempts"
                              + (f" (the model's cap is {cap})" if cap else "") + "; giving up this step")
                return ""
            self.log(f"     !! unexpected response http={r.status_code} finish={finish} "
                     f"body={json.dumps(j, ensure_ascii=False)[:160]}")
        except Exception as e:  # noqa: BLE001
            scene = getattr(e, "scene", {})
            self._record({"model": self.name, "exception": f"{type(e).__name__}: {e}",
                          "elapsed_s": round(time.time() - t0, 1), "n_messages": len(messages),
                          "attempt": _attempt, **scene})
            self.last = {"finish": None, "truncated": False, "max_tokens": max_tokens,
                         "error": f"{type(e).__name__}: {e}"[:200]}
            self.log(f"     .. request failed {type(e).__name__}"
                     + (f" http={scene['http']} body={scene['body_head'][:80]!r}" if scene else ""))
        if _attempt < self.MAX_ATTEMPTS - 1:
            time.sleep(10 * (_attempt + 1))
            return self(payload, _attempt + 1, _max_tokens)
        return ""  # empty string, never None: the parser must not crash
