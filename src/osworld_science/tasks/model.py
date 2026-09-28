"""A typed view over a task JSON. The JSON shape is the contract; this class
only names the fields the harness reads and never rewrites the document."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConfigStep:
    type: str                 # execute | upload | launch | sleep
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ResultSpec:
    type: str                 # vm_file | vm_command_line
    path: str                 # vm_file: guest absolute path ("" for a command line)
    dest: str                 # submission-relative path (a command line's stdout+stderr land here)
    command: tuple[str, ...] = ()   # vm_command_line: argv run inside the guest
    timeout: int = 120              # vm_command_line: seconds


@dataclass(frozen=True)
class Task:
    id: str
    domain: str
    path: Path
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: Path, domain: str | None = None) -> Task:
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        tid = raw.get("id") or path.stem
        return cls(id=tid, domain=domain or path.parent.name, path=path, raw=raw)

    # ── fields the harness reads ───────────────────────────────────────────
    @property
    def snapshot(self) -> str:
        return str(self.raw.get("snapshot", ""))

    @property
    def instruction(self) -> str:
        return str(self.raw["instruction"])

    @property
    def deliverables(self) -> list[str]:
        return list(self.raw.get("deliverables") or [])

    @property
    def related_apps(self) -> list[str]:
        return [str(a) for a in (self.raw.get("related_apps") or [])]

    @property
    def max_steps(self) -> int:
        return int((self.raw.get("budget") or {}).get("max_steps", 50))

    @property
    def config(self) -> list[ConfigStep]:
        return [ConfigStep(type=str(s["type"]), parameters=dict(s.get("parameters") or {}))
                for s in (self.raw.get("config") or [])]

    @property
    def evaluator(self) -> dict[str, Any]:
        return self.raw["evaluator"]

    @property
    def results(self) -> list[ResultSpec]:
        return [ResultSpec(type=str(r.get("type", "")), path=str(r.get("path") or ""),
                           dest=str(r.get("dest") or ""),
                           command=tuple(str(c) for c in (r.get("command") or [])),
                           timeout=int(r.get("timeout", 120)))
                for r in (self.evaluator.get("result") or [])]

    @property
    def bench(self) -> dict[str, Any]:
        return dict(self.raw.get("bench") or {})

    def prompt(self) -> str:
        """The instruction as the legacy sweeps sent it: instruction plus a
        Deliverables list when the task declares one."""
        text = self.instruction
        if self.deliverables:
            text += "\n\nDeliverables:\n" + "\n".join(f"  - {d}" for d in self.deliverables)
        return text
