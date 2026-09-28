"""Discover tasks under data/<domain>/tasks/*.json (the domain is the directory name)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .model import Task


class TaskSet:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._tasks: dict[str, Task] = {}
        self._by_domain: dict[str, list[str]] = {}
        domain_dirs = sorted(p for p in self.data_dir.iterdir() if p.is_dir()) if self.data_dir.is_dir() else []
        for domain_dir in domain_dirs:
            if not (domain_dir / "tasks").is_dir():
                continue
            ids = []
            for f in sorted((domain_dir / "tasks").glob("*.json")):
                t = Task.load(f, domain=domain_dir.name)
                if t.id in self._tasks:
                    raise ValueError(f"duplicate task id {t.id!r}: {f} and {self._tasks[t.id].path}")
                self._tasks[t.id] = t
                ids.append(t.id)
            if ids:
                self._by_domain[domain_dir.name] = ids

    def domains(self) -> list[str]:
        return sorted(self._by_domain)

    def ids(self, domain: str | None = None) -> list[str]:
        if domain is None:
            return list(self._tasks)
        if domain not in self._by_domain:
            raise KeyError(f"unknown domain {domain!r}; known: {', '.join(self.domains())}")
        return list(self._by_domain[domain])

    def get(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError:
            known = ", ".join(self._tasks) or "(none — download the data: scripts/data_prep/hf_download.py)"
            raise KeyError(f"no such task: {task_id}\nknown: {known}") from None

    def __contains__(self, task_id: str) -> bool:
        return task_id in self._tasks

    def __iter__(self) -> Iterator[Task]:
        return iter(self._tasks.values())

    def __len__(self) -> int:
        return len(self._tasks)


def select_tasks(tasks: TaskSet, spec: str | None, domain: str | None = None) -> list[Task]:
    """`spec` is 'all', a domain name, or a comma-separated list of ids;
    `domain` restricts 'all'. Unknown ids raise."""
    if domain and spec in (None, "", "all"):
        return [tasks.get(t) for t in tasks.ids(domain)]
    if spec in (None, "", "all"):
        return list(tasks)
    if spec in tasks.domains():
        return [tasks.get(t) for t in tasks.ids(spec)]
    out = []
    for tid in (s.strip() for s in spec.split(",")):
        if tid:
            out.append(tasks.get(tid))
    return out
