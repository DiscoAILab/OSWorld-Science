"""summary.json: results[model][task] entries, recomputed totals, atomic writes."""
from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path

TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens", "n_llm_calls")
ENTRY_KEYS = ("status", "steps_used", "duration_s", "input_tokens", "output_tokens",
              "total_tokens", "cached_input_tokens", "n_llm_calls", "cost_usd",
              "upstream_cost_usd", "apps_seen")


def _blank_bucket() -> dict:
    return {"runs": 0, "runs_finished": 0, "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "n_llm_calls": 0, "cost_usd": 0.0}


def _accumulate(bucket: dict, entry: dict) -> None:
    tok_in = int(entry.get("input_tokens") or entry.get("prompt_tokens") or 0)
    tok_out = int(entry.get("output_tokens") or entry.get("completion_tokens") or 0)
    bucket["runs"] += 1
    if entry.get("finished"):
        bucket["runs_finished"] += 1
    bucket["input_tokens"] += tok_in
    bucket["output_tokens"] += tok_out
    bucket["total_tokens"] += int(entry.get("total_tokens") or (tok_in + tok_out))
    bucket["n_llm_calls"] += int(entry.get("n_llm_calls") or 0)
    cost = entry.get("cost_usd")
    if isinstance(cost, (int, float)):
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
    elif tok_in or tok_out:
        bucket["cost_usd_incomplete"] = True  # tokens without a price: the total is a lower bound


def recompute_totals(s: dict) -> None:
    """Every run that left a token record counts, not only finished ones:
    failed runs spent real tokens."""
    by_model, by_task, overall = {}, {}, _blank_bucket()
    for model, rows in (s.get("results") or {}).items():
        for tid, entry in (rows or {}).items():
            if not isinstance(entry, dict):
                continue
            _accumulate(by_model.setdefault(model, _blank_bucket()), entry)
            _accumulate(by_task.setdefault(tid, _blank_bucket()), entry)
            _accumulate(overall, entry)
    s["totals"] = {"by_model": by_model, "by_task": dict(sorted(by_task.items())),
                   "overall": overall}


class Summary:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"results": {}}
        self.data.setdefault("results", {})

    def entry(self, model: str, task_id: str) -> dict | None:
        return (self.data["results"].get(model) or {}).get(task_id)

    def set_entry(self, model: str, task_id: str, entry: dict) -> None:
        with self._lock:
            self.data["results"].setdefault(model, {})[task_id] = entry
            self._save_locked()

    def set_preflight(self, model: str, ok: bool, detail: str, describe: dict) -> None:
        with self._lock:
            self.data.setdefault("preflight", {})[model] = {
                "ok": ok, "detail": detail, "backend": describe.get("backend"),
                "model_id": describe.get("model_id"),
                "ts": datetime.datetime.now().isoformat(timespec="seconds")}
            self._save_locked()

    def set_meta(self, **kw) -> None:
        with self._lock:
            self.data.setdefault("run", {}).update(kw)
            self._save_locked()

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        recompute_totals(self.data)
        self.data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, self.path)
