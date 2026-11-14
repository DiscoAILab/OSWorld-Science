"""Flatten summary.json into CSVs: one row per model, one column per task
(a port of the legacy make_result_tables.py).

Verdicts are three-valued. PASS / FAIL come from the grader; VOID means
"this cell did not produce a complete run, as opposed to the model getting
it wrong":
  1. finished == false — the pipeline broke (reset / stage / collect).
  2. the harness cut the run short (empty_response, predict_error:*,
     no_screenshot, interrupted) AND the verdict is not PASS. A PASS
     survives because an interruption can only remove deliverables, never
     invent correct ones.
  3. a null run: every step spent without the agent ever signalling DONE or
     FAIL, and not one deliverable of its own to show for it. A max_steps
     run that did deliver something is the model's own FAIL (or PASS).
  4. no verdict at all — the cell was never graded.

Rule 3 counts deliverables with n_delivered, because the two readier
measures both lie. collect.json's `collected` includes the `inputs/` files
the harness stages in and pulls back to check they were not tampered with
(73 of mri_spine_levels' 75 results are inputs), so it is positive even for
an agent that never moved. And "scored zero" never happens either: every
task gates on the inputs coming back byte-identical, worth 0.1, which a
null run passes for free — such cells score 0.05 to 0.2, never 0.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ..llm.usage import UsageMeter, raw_log_records

ABORTED = ("empty_response", "predict_error", "no_screenshot", "interrupted")

CACHE_COLUMNS = ["model", "task", "cache_source", "n_llm_calls", "input_tokens", "cache_read_tokens",
                 "cache_write_tokens", "uncached_input_tokens", "cache_hit_rate", "cost_usd", "cost_source",
                 "cost_usd_static", "upstream_cost_usd"]


def is_aborted(status: str | None) -> bool:
    s = str(status or "")
    return any(s.startswith(a) for a in ABORTED)


def is_null_run(entry: dict) -> bool:
    """Rule 3. `delivered` is counted off disk by backfill_delivered; a cell
    whose directory is gone counts as having delivered nothing, which is
    what the rule used to say about every max_steps cell anyway."""
    return entry.get("status") == "max_steps" and not entry.get("delivered")


def verdict_of(entry: dict) -> str:
    if not entry.get("finished"):
        return "VOID"
    v = entry.get("verdict")
    if is_aborted(entry.get("status")) and v != "PASS":
        return "VOID"
    if is_null_run(entry):
        return "VOID"
    return v or "VOID"


def n_delivered(submission: Path) -> int:
    """How many files of the model's own are in a submission. `inputs/` does
    not count: those are staged in by the harness and pulled back to check
    they were not tampered with, so they return whether or not the agent did
    anything. collect.json is the collector's own bookkeeping."""
    if not submission.is_dir():
        return 0
    return sum(1 for p in submission.rglob("*")
               if p.is_file() and p.name != "collect.json"
               and "inputs" not in p.relative_to(submission).parts[:-1])


def backfill_delivered(results: dict, summary_path: Path) -> None:
    """summary.json has no deliverable count; rule 3 needs one, so count the
    files in each cell's submission."""
    for model, rows in results.items():
        for tid, entry in (rows or {}).items():
            if not isinstance(entry, dict):
                continue
            cell = Path(entry.get("run_dir") or summary_path.parent / tid / model)
            entry["delivered"] = n_delivered(cell / "submission")


def fmt(value, spec: str | None = None) -> str:
    if value is None:
        return ""
    if spec:
        try:
            return format(value, spec)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _meter_for(model: str, registry) -> UsageMeter:
    """A meter priced like the model in configs/models.yaml, or an unpriced one for a model
    the registry no longer has (renamed or retired arms of old runs)."""
    if registry is not None:
        try:
            return UsageMeter.for_model(registry.get(model))
        except KeyError:
            pass
    return UsageMeter(None, "static")


def cache_cell(model: str, entry: dict, cell: Path, registry=None) -> dict:
    """Cache figures of one cell. Runs made since cache accounting existed carry them in
    summary.json (`cache_hit_rate` present); for older cells they are recomputed from the
    cell's request log, which kept every provider usage object, priced statically with the
    current price table (the summary's `cost_usd` there is the old flat estimate)."""
    row = {"model": model}
    if "cache_hit_rate" in entry:
        inp = int(entry.get("input_tokens") or 0)
        read, write = int(entry.get("cached_input_tokens") or 0), int(entry.get("cache_write_tokens") or 0)
        row.update(cache_source="summary", n_llm_calls=entry.get("n_llm_calls"), input_tokens=inp,
                   cache_read_tokens=read, cache_write_tokens=write,
                   uncached_input_tokens=max(0, inp - read - write), cache_hit_rate=entry.get("cache_hit_rate"),
                   cost_usd=entry.get("cost_usd"), cost_source=entry.get("cost_source"),
                   cost_usd_static=entry.get("cost_usd_static"), upstream_cost_usd=entry.get("upstream_cost_usd"))
        row["_reported"] = entry.get("cache_hit_rate") is not None
        return row
    records = raw_log_records(cell / "agent" / "llm_raw.jsonl")
    if not records:
        row.update(cache_source="none", cost_usd=entry.get("cost_usd"), _reported=False)
        return row
    meter = _meter_for(model, registry)
    for usage in records:
        meter.add(usage)
    st = meter.stats()
    row.update(cache_source="llm_raw", n_llm_calls=st["n_llm_calls"], input_tokens=st["input_tokens"],
               cache_read_tokens=st["cached_input_tokens"], cache_write_tokens=st["cache_write_tokens"],
               uncached_input_tokens=max(0, st["input_tokens"] - st["cached_input_tokens"] - st["cache_write_tokens"]),
               cache_hit_rate=st["cache_hit_rate"], cost_usd=entry.get("cost_usd"), cost_source="recorded (pre-cache)",
               cost_usd_static=st["cost_usd_static"], upstream_cost_usd=st["upstream_cost_usd"])
    row["_reported"] = st["cache_hit_rate"] is not None
    return row


def write_cache_table(results: dict, summary_path: Path, out_dir: Path, models: list[str], order: list[str],
                      registry=None) -> dict:
    """cache.csv: one row per cell and one `ALL` row per model. The hit rate is cache reads
    over all prompt tokens, counted only over cells whose API reported a cache (a blank
    rate means the API never said, not 0 %)."""
    totals = {}
    with (out_dir / "cache.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CACHE_COLUMNS)
        for m in models:
            agg = {k: 0 for k in ("n_llm_calls", "input_tokens", "cache_read_tokens", "cache_write_tokens",
                                  "uncached_input_tokens")}
            agg.update(cost_usd=None, cost_usd_static=None, upstream_cost_usd=None, reported_input=0, reported_read=0)
            sources = set()
            for t in order:
                entry = results[m].get(t)
                if not entry:
                    continue
                cell = Path(entry.get("run_dir") or summary_path.parent / t / m)
                row = cache_cell(m, entry, cell, registry)
                row["task"] = t
                sources.add(row.get("cost_source") or "")
                for k in ("n_llm_calls", "input_tokens", "cache_read_tokens", "cache_write_tokens",
                          "uncached_input_tokens"):
                    agg[k] += int(row.get(k) or 0)
                for k in ("cost_usd", "cost_usd_static", "upstream_cost_usd"):
                    if isinstance(row.get(k), (int, float)):     # blank stays blank: no cell had that figure
                        agg[k] = (agg[k] or 0.0) + row[k]
                if row.get("_reported"):
                    agg["reported_input"] += int(row.get("input_tokens") or 0)
                    agg["reported_read"] += int(row.get("cache_read_tokens") or 0)
                w.writerow([fmt(row.get(c), ".4f" if c == "cache_hit_rate" else
                                (".8f" if c.startswith(("cost_usd", "upstream")) else None)) for c in CACHE_COLUMNS])
            hit = agg["reported_read"] / agg["reported_input"] if agg["reported_input"] else None
            total = {"model": m, "task": "ALL", "cache_source": "", "cache_hit_rate": hit,
                     "cost_source": "+".join(sorted(s for s in sources if s)), **agg}
            totals[m] = total
            w.writerow([fmt(total.get(c), ".4f" if c == "cache_hit_rate" else
                            (".8f" if c.startswith(("cost_usd", "upstream")) else None)) for c in CACHE_COLUMNS])
    return totals


def write_tables(summary_path: Path, out_dir: Path, exclude: list[str] | None = None,
                 task_order: list[str] | None = None, registry=None) -> dict:
    summary_path = Path(summary_path)
    s = json.loads(summary_path.read_text())
    results: dict[str, dict] = s.get("results") or {}
    backfill_delivered(results, summary_path)
    exclude = set(exclude or [])
    models = [m for m in results if m not in exclude]
    seen = {t for m in models for t in results[m]}
    order = [t for t in (task_order or []) if t in seen] + sorted(seen - set(task_order or []))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def table(name: str, getter, spec: str | None = None):
        with (out_dir / f"{name}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["model", *order])
            for m in models:
                w.writerow([m, *[fmt(getter(results[m].get(t) or {}), spec) for t in order]])

    table("verdict", lambda e: verdict_of(e) if e else "")
    table("status", lambda e: str(e.get("status") or "")[:40])  # legacy truncation
    table("score", lambda e: e.get("score"), ".6f")
    table("steps", lambda e: e.get("steps_used"))
    table("duration_s", lambda e: e.get("duration_s"), ".1f")
    table("cost_usd", lambda e: e.get("cost_usd"), ".4f")
    table("tokens_total", lambda e: e.get("total_tokens"))
    table("tokens_input", lambda e: e.get("input_tokens"))
    table("tokens_output", lambda e: e.get("output_tokens"))
    cache_totals = write_cache_table(results, summary_path, out_dir, models, order, registry)

    per_model = {}
    with (out_dir / "summary_by_model.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "cells", "PASS", "FAIL", "VOID", "pass_rate_all", "pass_rate_valid",
                    "mean_score_valid", "mean_score_all_void_as_0", "cost_usd", "cache_hit_rate"])
        for m in models:
            entries = [results[m].get(t) or {} for t in order]
            verdicts = [verdict_of(e) if e else "VOID" for e in entries]
            n = len(order)
            n_pass = verdicts.count("PASS")
            n_fail = verdicts.count("FAIL")
            n_void = verdicts.count("VOID")
            valid = [e for e, v in zip(entries, verdicts, strict=False) if v != "VOID"]
            scores_valid = [e.get("score") for e in valid if isinstance(e.get("score"), (int, float))]
            scores_all = [e.get("score") if (isinstance(e.get("score"), (int, float)) and v != "VOID") else 0.0
                          for e, v in zip(entries, verdicts, strict=False)]
            cost = sum(e.get("cost_usd") or 0 for e in entries)
            row = {"cells": n, "PASS": n_pass, "FAIL": n_fail, "VOID": n_void,
                   "pass_rate_all": n_pass / n if n else None,
                   "pass_rate_valid": n_pass / len(valid) if valid else None,
                   "mean_score_valid": sum(scores_valid) / len(scores_valid) if scores_valid else None,
                   "mean_score_all_void_as_0": sum(scores_all) / n if n else None,
                   "cost_usd": cost, "cache_hit_rate": (cache_totals.get(m) or {}).get("cache_hit_rate")}
            per_model[m] = row
            w.writerow([m, n, n_pass, n_fail, n_void, fmt(row["pass_rate_all"], ".3f"),
                        fmt(row["pass_rate_valid"], ".3f"), fmt(row["mean_score_valid"], ".4f"),
                        fmt(row["mean_score_all_void_as_0"], ".4f"), fmt(cost, ".4f"),
                        fmt(row["cache_hit_rate"], ".4f")])
    return per_model
