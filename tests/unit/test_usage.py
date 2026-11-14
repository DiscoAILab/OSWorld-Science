"""Token, cache and cost accounting (llm/usage.py), its config (models.yaml `price`, `cost`),
the summary totals and the report's cache.csv. Usage objects are the shapes the providers
returned in the 2026-10-09 endpoint probe."""
import csv
import json
from pathlib import Path

import pytest

from osworld_science.llm.models import ModelRegistry
from osworld_science.llm.usage import UsageMeter, parse_usage, raw_log_records, static_cost
from osworld_science.reporting.tables import write_tables
from osworld_science.runner.summary import recompute_totals

# OpenRouter, openai/gpt-5.6-sol pinned to OpenAI: a cache write, then a cache read
SOL_WRITE = {"prompt_tokens": 7197, "completion_tokens": 5, "total_tokens": 7202, "cost": 0.018041,
             "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 7194},
             "completion_tokens_details": {"reasoning_tokens": 0}}
SOL_READ = {"prompt_tokens": 7197, "completion_tokens": 5, "total_tokens": 7202, "cost": 0.0014948,
            "prompt_tokens_details": {"cached_tokens": 7194, "cache_write_tokens": 0}}
SOL_PRICE = {"in": 2.0, "out": 10.0, "cache_read": 0.2, "cache_write": 2.5}


def test_parse_usage_handles_every_provider_shape():
    c = parse_usage(SOL_WRITE)
    assert (c.input_tokens, c.cache_read_tokens, c.cache_write_tokens, c.uncached_input_tokens) == (7197, 0, 7194, 3)
    assert c.cache_reported and c.api_cost == 0.018041
    # Responses API (Azure): cache inside input_tokens, reasoning under output_tokens_details
    r = parse_usage({"input_tokens": 7197, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 7194,
                     "cache_write_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 3}})
    assert (r.input_tokens, r.cache_read_tokens, r.reasoning_tokens, r.api_cost) == (7197, 7194, 3, None)
    # Anthropic: input_tokens excludes the cache; the 1-hour writes are a subset of the writes
    a = parse_usage({"input_tokens": 100, "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 900,
                     "cache_creation": {"ephemeral_5m_input_tokens": 600, "ephemeral_1h_input_tokens": 300},
                     "output_tokens": 50, "output_tokens_details": {"thinking_tokens": 20}})
    assert (a.input_tokens, a.cache_read_tokens, a.cache_write_tokens, a.cache_write_1h_tokens) == (5000, 4000, 900, 300)
    assert a.uncached_input_tokens == 100 and a.reasoning_tokens == 20
    # Moonshot's top-level cached_tokens; qwen-cua's top-level reasoning_tokens and null details
    assert parse_usage({"prompt_tokens": 10, "completion_tokens": 1, "cached_tokens": 8}).cache_read_tokens == 8
    q = parse_usage({"prompt_tokens": 10, "completion_tokens": 3, "prompt_tokens_details": None, "reasoning_tokens": 2})
    assert q.reasoning_tokens == 2 and not q.cache_reported
    # bring-your-own-key: OpenRouter's fee plus what the provider billed directly
    b = parse_usage({"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.0, "is_byok": True,
                     "cost_details": {"upstream_inference_cost": 0.0105}})
    assert b.api_cost == pytest.approx(0.0105)
    assert parse_usage({}) is None and parse_usage(None) is None


def test_static_cost_bills_each_token_class_and_matches_openrouter():
    # the probe: OpenRouter's charge reproduced exactly from the price table
    assert static_cost(parse_usage(SOL_WRITE), SOL_PRICE) == pytest.approx(0.018041)
    assert static_cost(parse_usage(SOL_READ), SOL_PRICE) == pytest.approx(0.0014948)
    # a missing cache price falls back to `in`; no in/out = unpriced
    assert static_cost(parse_usage(SOL_READ), {"in": 2.0, "out": 10.0}) == pytest.approx((7197 * 2 + 5 * 10) / 1e6)
    assert static_cost(parse_usage(SOL_READ), {"in": None, "out": 10.0}) is None
    # Anthropic 1-hour writes at their own rate
    a = parse_usage({"input_tokens": 100, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 1000,
                     "cache_creation": {"ephemeral_1h_input_tokens": 400}, "output_tokens": 0})
    price = {"in": 5.0, "out": 25.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0}
    assert static_cost(a, price) == pytest.approx((100 * 5 + 600 * 6.25 + 400 * 10) / 1e6)


def test_meter_modes_hit_rate_and_fallback():
    static = UsageMeter(SOL_PRICE, "static")
    rec = static.add(SOL_WRITE)
    static.add(SOL_READ)
    assert rec["cache_write_tokens"] == 7194 and rec["cost_source"] == "static"
    st = static.stats()
    assert st["cache_hit_rate"] == pytest.approx(7194 / 14394, abs=1e-4) and st["cached_input_tokens"] == 7194
    assert st["cost_usd"] == pytest.approx(0.018041 + 0.0014948) and st["cost_source"] == "static"
    assert st["upstream_cost_usd"] == pytest.approx(0.0195358)
    # api mode: the API's cost; a call without one is estimated and counted
    api = UsageMeter(SOL_PRICE, "api")
    api.add(SOL_WRITE)
    api.add({k: v for k, v in SOL_READ.items() if k != "cost"})
    st = api.stats()
    assert st["cost_usd"] == pytest.approx(0.018041 + 0.0014948)
    assert st["cost_source"] == "api+static" and st["n_calls_cost_estimated"] == 1
    # no cache field at all: the hit rate is unknown, not 0
    blind = UsageMeter({"in": 1, "out": 1}, "static")
    blind.add({"prompt_tokens": 10, "completion_tokens": 1})
    assert blind.stats()["cache_hit_rate"] is None
    with pytest.raises(ValueError):
        UsageMeter(SOL_PRICE, "guess")


def _registry(tmp_path, model_yaml: str) -> ModelRegistry:
    (tmp_path / "m.yaml").write_text(
        "backends:\n"
        "  az:\n    kind: openai_chat\n    base_url: https://a\n    key_env: K\n"
        "  orr:\n    kind: openai_chat\n    base_url: https://o\n    key_env: K\n    usage_include: true\n"
        "models:\n" + model_yaml)
    return ModelRegistry(tmp_path / "m.yaml")


def test_registry_cost_modes_and_price_keys(tmp_path):
    reg = _registry(tmp_path, "  a:\n    backend: az\n    model_id: a\n"
                              "  o:\n    backend: orr\n    model_id: o\n    price: {in: 1, out: 2, cache_read: 0.1}\n")
    assert reg.get("a").cost == "static" and reg.get("o").cost == "api"          # defaults follow the backend
    with pytest.raises(ValueError, match="returns no per-call cost"):
        _registry(tmp_path, "  a:\n    backend: az\n    model_id: a\n    cost: api\n")
    with pytest.raises(ValueError, match="unknown price key"):
        _registry(tmp_path, "  a:\n    backend: az\n    model_id: a\n    price: {in: 1, out: 2, cached_in: 0.1}\n")
    with pytest.raises(ValueError, match="cost must be"):
        _registry(tmp_path, "  a:\n    backend: az\n    model_id: a\n    cost: dynamic\n")


def test_shipped_models_declare_cost_and_cache_prices(settings):
    reg = ModelRegistry(settings.configs_dir / "models.yaml")
    for spec in list(reg.models.values()) + list(reg.judges.values()):
        assert spec.cost in ("api", "static"), spec.name
        assert spec.cost == ("api" if spec.backend.usage_include else "static"), spec.name
        assert "cache_read" in (spec.price or {}), spec.name                  # a price, or an explicit null
    assert reg.get("qwen-cua").price["cache_read"] is None                     # the one route with no cache price
    assert reg.get("claude-opus-5").price["cache_write_1h"] == 10.0


def test_summary_totals_count_cache_only_where_reported():
    s = {"results": {"m": {
        "t1": {"finished": True, "input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 600,
               "cache_write_tokens": 100, "cache_hit_rate": 0.6, "cost_usd": 0.01, "cost_usd_static": 0.012},
        "t2": {"finished": True, "input_tokens": 3000, "output_tokens": 10, "cached_input_tokens": 0,
               "cost_usd": 0.03},                                              # an old cell: no cache info
    }}}
    recompute_totals(s)
    b = s["totals"]["by_model"]["m"]
    assert b["cached_input_tokens"] == 600 and b["cache_write_tokens"] == 100
    assert b["cache_hit_rate"] == 0.6 and b["input_tokens"] == 4000 and b["cost_usd_static"] == 0.012


def test_cache_csv_uses_summary_fields_and_backfills_old_cells_from_the_request_log(tmp_path, settings):
    run = tmp_path / "run"
    old_cell = run / "t_old" / "gpt-5.6-sol@openrouter"
    (old_cell / "agent").mkdir(parents=True)
    with (old_cell / "agent" / "llm_raw.jsonl").open("w") as f:
        for u in (SOL_WRITE, SOL_READ):
            f.write(json.dumps({"model": "x", "usage": u}) + "\n")
        f.write(json.dumps({"kind": "run", "usage": {"input_tokens": 999999}}) + "\n")   # a run summary: skipped
    summary = {"results": {"gpt-5.6-sol@openrouter": {
        "t_new": {"finished": True, "verdict": "PASS", "input_tokens": 1000, "output_tokens": 5,
                  "cached_input_tokens": 900, "cache_write_tokens": 0, "cache_hit_rate": 0.9, "n_llm_calls": 3,
                  "cost_usd": 0.001, "cost_source": "api", "cost_usd_static": 0.0011, "upstream_cost_usd": 0.001},
        "t_old": {"finished": True, "verdict": "FAIL", "input_tokens": 14394, "output_tokens": 10,
                  "cost_usd": 0.0289, "run_dir": str(old_cell)},
    }}}
    (run / "summary.json").write_text(json.dumps(summary))
    reg = ModelRegistry(settings.configs_dir / "models.yaml")
    per_model = write_tables(run / "summary.json", run / "csv", registry=reg, task_order=["t_new", "t_old"])
    rows = list(csv.DictReader((run / "csv" / "cache.csv").open()))
    new, old, total = rows
    assert new["cache_source"] == "summary" and new["cache_hit_rate"] == "0.9000" and new["cost_source"] == "api"
    assert old["cache_source"] == "llm_raw" and old["n_llm_calls"] == "2"
    assert old["cache_read_tokens"] == "7194" and old["cache_write_tokens"] == "7194"
    assert float(old["cost_usd_static"]) == pytest.approx(0.018041 + 0.0014948)
    assert float(old["upstream_cost_usd"]) == pytest.approx(0.0195358)
    assert total["task"] == "ALL" and float(total["cache_hit_rate"]) == pytest.approx((900 + 7194) / (1000 + 14394), abs=1e-4)
    assert per_model["gpt-5.6-sol@openrouter"]["cache_hit_rate"] == pytest.approx((900 + 7194) / (1000 + 14394))
    head = (run / "csv" / "summary_by_model.csv").read_text().splitlines()[0]
    assert head.endswith("cache_hit_rate")
    assert len(raw_log_records(old_cell / "agent" / "llm_raw.jsonl")) == 2
    assert raw_log_records(Path(tmp_path / "missing.jsonl")) == []
