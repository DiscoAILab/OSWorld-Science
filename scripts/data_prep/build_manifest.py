#!/usr/bin/env python3
"""Build data/MANIFEST.json from the release payload on disk.

The manifest excludes itself, Hugging Face repository metadata, and Python
bytecode caches. Unchanged entries reuse their previous digest when byte size
and integer mtime match; pass --full-hash for an independent full rehash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def included(path: Path, data_dir: Path) -> bool:
    rel = path.relative_to(data_dir)
    if not path.is_file() or ".cache" in rel.parts or "__pycache__" in rel.parts:
        return False
    if rel.as_posix() in {".gitattributes", "MANIFEST.json"}:
        return False
    return path.suffix != ".pyc" and path.name != ".DS_Store"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 24), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(data_dir: Path, previous: dict[str, dict], *, full_hash: bool) -> tuple[dict, int]:
    manifest = {}
    reused = 0
    paths = sorted((p for p in data_dir.rglob("*") if included(p, data_dir)),
                   key=lambda p: p.relative_to(data_dir).as_posix())
    for path in paths:
        rel = path.relative_to(data_dir).as_posix()
        stat = path.stat()
        size, mtime = stat.st_size, int(stat.st_mtime)
        old = previous.get(rel) or {}
        if (not full_hash and old.get("bytes") == size and old.get("mtime") == mtime
                and isinstance(old.get("sha256"), str) and len(old["sha256"]) == 64):
            digest = old["sha256"]
            reused += 1
        else:
            digest = sha256_of(path)
        manifest[rel] = {"bytes": size, "sha256": digest, "mtime": mtime}
    return manifest, reused


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO / "data")
    parser.add_argument("--full-hash", action="store_true",
                        help="rehash every file instead of reusing unchanged records")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero instead of writing when the manifest is stale")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    output = data_dir / "MANIFEST.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    manifest, reused = build(data_dir, previous, full_hash=args.full_hash)
    encoded = json.dumps(manifest, indent=2) + "\n"
    current = output.read_text(encoding="utf-8") if output.is_file() else ""
    if args.check:
        if encoded != current:
            print(f"stale: {output}", file=sys.stderr)
            return 1
        print(f"ok: {len(manifest)} entries ({reused} digests reused)")
        return 0

    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(output)
    total = sum(item["bytes"] for item in manifest.values())
    print(f"wrote {output}: {len(manifest)} entries, {total} bytes, {reused} digests reused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
