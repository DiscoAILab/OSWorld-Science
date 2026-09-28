"""Where task bytes and images live on the host.

`data/` mirrors the Hugging Face dataset repository, one directory per domain:

    data/<domain>/tasks/<task_id>.json                    task definitions
    data/<domain>/public/assets/...                       guest assets + shipped data (the guest receives these)
    data/<domain>/private/reference_private/<id>/gt.json  ground truth (+ reference files, grader tests)
    data/<domain>/vm/<snapshot>.qcow2 (+ .md5)            the domain's prepared VM image

`private` means "never enters the guest", not access-controlled. Paths written
inside task JSONs (`upload.local`, `params.data_dir`) and inside gt.json files
are relative to the domain's `public/` tree, except those starting with
`reference_private/`, which resolve under `private/`.
"""
from __future__ import annotations

import json
from pathlib import Path

PRIVATE_PREFIXES = ("reference_private/", "grader_tests/")
SUBTREES = ("tasks", "public", "private", "vm")


class DataLayout:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def domain_root(self, domain: str) -> Path:
        return self.data_dir / domain

    def tasks_root(self, domain: str) -> Path:
        return self.data_dir / domain / "tasks"

    def public_root(self, domain: str) -> Path:
        return self.data_dir / domain / "public"

    def private_root(self, domain: str) -> Path:
        return self.data_dir / domain / "private"

    def vm_root(self, domain: str) -> Path:
        return self.data_dir / domain / "vm"

    def resolve(self, domain: str, rel: str | Path) -> Path:
        rel = Path(rel)
        if rel.is_absolute():
            return rel
        s = rel.as_posix()
        if s.startswith(PRIVATE_PREFIXES):
            return self.private_root(domain) / rel
        return self.public_root(domain) / rel

    def gt_path(self, domain: str, task_id: str) -> Path:
        return self.private_root(domain) / "reference_private" / task_id / "gt.json"

    def has_gt(self, domain: str, task_id: str) -> bool:
        return self.gt_path(domain, task_id).is_file()

    def load_gt(self, domain: str, task_id: str) -> dict:
        p = self.gt_path(domain, task_id)
        if not p.is_file():
            raise FileNotFoundError(
                f"no ground truth for {task_id}: expected {p}\n"
                f"(download the data: scripts/data_prep/hf_download.py --domain {domain})")
        return json.loads(p.read_text(encoding="utf-8"))

    def grader_tests_dir(self, domain: str) -> Path:
        return self.private_root(domain) / "grader_tests"

    def available_domains(self) -> list[str]:
        if not self.data_dir.is_dir():
            return []
        return sorted(p.name for p in self.data_dir.iterdir()
                      if p.is_dir() and any((p / t).is_dir() for t in SUBTREES))

    def has_data(self, domain: str) -> bool:
        return self.public_root(domain).is_dir() and self.private_root(domain).is_dir()
