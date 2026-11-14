"""Probe every model in configs/models.yaml for what its endpoint reports: the `usage`
object (cache hits, reasoning tokens), any cost field, and, for OpenAI-style backends,
whether /chat/completions and /responses are both served.

Two identical requests are sent per API with a ~4k-token system prefix, so a cache hit
can show up on the second one (OpenAI caches prefixes >= 1024 tokens automatically,
Anthropic needs `cache_control` and >= 1024 tokens, Gemini's implicit cache wants more).
Keys never reach the output; backends are named as in models.yaml.

    uv run python scripts/dev/probe_endpoint_usage.py docs/internal_notes/endpoint_probe.json [model ...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

from osworld_science.config import Settings
from osworld_science.llm.models import ModelRegistry, ResolvedModel

requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

TIMEOUT = 180
# ~4.2k tokens of deterministic filler: long enough for every provider's cache minimum
FILLER = "You are a careful assistant. " + " ".join(
    f"Fact {i}: the reference value of item {i} is {i * 7 % 1000} units and it was recorded on day {i % 28 + 1}."
    for i in range(1, 300))
QUESTION = "Reply with exactly: OK"


def headers(m: ResolvedModel) -> dict:
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {m.key}"}
    if m.backend.name == "azure":
        h["api-key"] = m.key
    return h


def post(url: str, body: dict, hdrs: dict) -> dict:
    t0 = time.time()
    try:
        r = requests.post(url, json=body, headers=hdrs, timeout=TIMEOUT, verify=False)
    except Exception as e:  # noqa: BLE001
        return {"http": None, "error": f"{type(e).__name__}: {str(e)[:200]}", "elapsed_s": round(time.time() - t0, 1)}
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text[:300]}
    interesting = {k: v for k, v in r.headers.items()
                   if k.lower().startswith(("x-ratelimit", "anthropic-ratelimit", "openai-", "x-request-id",
                                            "request-id", "apim-request-id", "x-ms-", "x-openrouter"))}
    return {"http": r.status_code, "elapsed_s": round(time.time() - t0, 1), "json": j, "headers": interesting}


def summarise(resp: dict, kind: str) -> dict:
    j = resp.get("json") or {}
    out = {"http": resp.get("http"), "elapsed_s": resp.get("elapsed_s"), "error": resp.get("error") or j.get("error")}
    if not isinstance(j, dict):
        return out
    out["usage"] = j.get("usage")
    out["id"] = j.get("id")
    out["model"] = j.get("model")
    out["provider"] = j.get("provider")
    if kind == "chat":
        ch = (j.get("choices") or [{}])[0]
        out["finish"] = ch.get("finish_reason")
        out["content"] = ((ch.get("message") or {}).get("content") or "")[:40]
    elif kind == "responses":
        out["status"] = j.get("status")
        out["content"] = (j.get("output_text") or "".join(
            c.get("text", "") for it in j.get("output", []) if isinstance(it, dict)
            for c in it.get("content", []) if isinstance(c, dict)))[:40]
    elif kind == "messages":
        out["stop_reason"] = j.get("stop_reason")
        out["content"] = "".join(b.get("text", "") for b in j.get("content", []) if isinstance(b, dict))[:40]
        out["service_tier"] = j.get("service_tier")
    out["other_top_level_keys"] = sorted(k for k in j if k not in ("choices", "output", "content", "usage", "id",
                                                                      "model", "object", "created", "provider"))
    out["headers"] = resp.get("headers")
    return out


def probe(name: str, m: ResolvedModel) -> dict:
    b = m.backend
    res = {"model": name, "backend": b.name, "kind": b.kind, "model_id": m.model_id,
           "provider_order": list(m.spec.provider_order) or None}
    hdrs = headers(m)
    if b.kind == "anthropic_messages":
        body = {"model": m.model_id, "max_tokens": 32,
                "system": [{"type": "text", "text": FILLER, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": QUESTION}]}
        url = m.endpoint("messages")
        res["messages"] = [summarise(post(url, body, hdrs), "messages") for _ in range(2)]
        return res
    # chat/completions
    body = {"model": m.model_id, "messages": [{"role": "system", "content": FILLER}, {"role": "user", "content": QUESTION}],
            b.max_tokens_field: 256}
    if m.spec.provider_order:
        body["provider"] = {"order": list(m.spec.provider_order), "allow_fallbacks": True}
    if b.usage_include:
        body["usage"] = {"include": True}
    url = m.endpoint("chat_completions")
    res["chat_completions"] = [summarise(post(url, body, hdrs), "chat") for _ in range(2)]
    # responses
    body = {"model": m.model_id, "instructions": FILLER, "input": QUESTION, "max_output_tokens": 256}
    if m.spec.provider_order:
        body["provider"] = {"order": list(m.spec.provider_order), "allow_fallbacks": True}
    if b.usage_include:
        body["usage"] = {"include": True}
    url = m.endpoint("responses")
    res["responses"] = [summarise(post(url, body, hdrs), "responses") for _ in range(2)]
    # OpenRouter's per-generation cost record
    if b.name == "openrouter":
        gid = (res["chat_completions"][0] or {}).get("id")
        if gid:
            time.sleep(3)
            try:
                r = requests.get(f"{m.base_url}/generation", params={"id": gid}, headers=hdrs, timeout=60)
                res["generation_endpoint"] = {"http": r.status_code, "json": r.json() if r.ok else r.text[:200]}
            except Exception as e:  # noqa: BLE001
                res["generation_endpoint"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return res


def main() -> None:
    out = Path(sys.argv[1])
    only = set(sys.argv[2:])
    s = Settings.load()
    reg = ModelRegistry(s.configs_dir / "models.yaml")
    targets = [(n, "model") for n in reg.names()] + [(n, "judge") for n in reg.judge_names()]
    results = []
    for name, role in targets:
        label = name if role == "model" else f"judge:{name}"
        if only and name not in only and label not in only:
            continue
        try:
            m = reg.resolve(name, s.env) if role == "model" else reg.resolve_judge(name, s.env)
        except Exception as e:  # noqa: BLE001
            results.append({"model": label, "error": str(e)})
            print(f"{label:26s} unresolved: {e}", flush=True)
            continue
        print(f"{label:26s} probing {m.backend.name} ...", flush=True)
        r = probe(label, m)
        results.append(r)
        for api in ("chat_completions", "responses", "messages"):
            for i, x in enumerate(r.get(api) or []):
                print(f"   {api:16s} #{i + 1} http={x.get('http')} {x.get('elapsed_s')}s usage={json.dumps(x.get('usage'))[:160]}"
                      + (f" err={str(x.get('error'))[:100]}" if x.get("error") else ""), flush=True)
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print("done", flush=True)


if __name__ == "__main__":
    main()
