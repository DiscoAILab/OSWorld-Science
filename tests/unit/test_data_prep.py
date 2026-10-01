from __future__ import annotations

import json
import runpy
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST_TOOL = runpy.run_path(REPO / "scripts" / "data_prep" / "build_manifest.py")
DOWNLOAD_TOOL = runpy.run_path(REPO / "scripts" / "data_prep" / "hf_download.py")
build = MANIFEST_TOOL["build"]
prune_local = DOWNLOAD_TOOL["prune_local"]
published_domains = DOWNLOAD_TOOL["published_domains"]


def test_published_domains_ignores_auxiliary_roots():
    files = [
        "README.md",
        "ablation/zh/qp001_t1_1.json",
        "biomed/tasks/qp001_t1_1.json",
        "eeg/tasks/eeglab-cli-001-event-erp.json",
        "physics/vm/ubuntu_openfoam.qcow2",
    ]
    assert published_domains(files) == ["biomed", "eeg"]


def test_prune_moves_stale_files_but_preserves_cache(tmp_path):
    data = tmp_path / "data"
    keep = data / "eeg" / "tasks" / "keep.json"
    stale = data / "astro" / "tasks" / "old.json"
    cache = data / ".cache" / "huggingface" / "old.metadata"
    for path in (keep, stale, cache):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    prune_local(data, {"eeg/tasks/keep.json"}, ["eeg"], all_domains=True,
                include_data=True, include_vm=False)

    assert keep.is_file() and cache.is_file() and not stale.exists()
    backups = list(tmp_path.glob("data.remote-extra-*"))
    assert len(backups) == 1
    assert (backups[0] / "astro" / "tasks" / "old.json").is_file()


def test_manifest_excludes_hub_metadata_and_bytecode(tmp_path):
    data = tmp_path / "data"
    payload = data / "eeg" / "tasks" / "task.json"
    bytecode = data / "eeg" / "private" / "__pycache__" / "grader.pyc"
    metadata = data / ".cache" / "huggingface" / "download.metadata"
    for path in (payload, bytecode, metadata, data / ".gitattributes"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (data / "MANIFEST.json").write_text(json.dumps({}), encoding="utf-8")

    manifest, reused = build(data, {}, full_hash=False)

    assert list(manifest) == ["eeg/tasks/task.json"]
    assert reused == 0
