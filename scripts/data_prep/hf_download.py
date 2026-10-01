#!/usr/bin/env python3
"""Download benchmark data (and optionally a domain's VM image) from Hugging Face into `data/`.

    uv run python scripts/data_prep/hf_download.py                       # every domain's public + private trees
    uv run python scripts/data_prep/hf_download.py --domain stat         # one domain (repeatable)
    uv run python scripts/data_prep/hf_download.py --domain stat --vm    # + its prepared VM image (23-34 GB)
    uv run python scripts/data_prep/hf_download.py --vm-only --domain biomed
    uv run python scripts/data_prep/hf_download.py --vm --prune          # exact, recoverable mirror

The dataset (OSCI_HF_DATA_REPO in .env) has one directory per domain:
<domain>/tasks (task definitions), <domain>/public (guest assets),
<domain>/private (ground truth, grader tests), <domain>/vm (<snapshot>.qcow2 + .md5). `data/` mirrors it, so the harness finds
everything where it expects. No token is needed for public repos.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_images(vm_dir: Path) -> None:
    for z in sorted(vm_dir.glob("*.qcow2.zip")):
        img = z.with_suffix("")
        if not img.exists():
            print(f"  unzipping {z.name}")
            with zipfile.ZipFile(z) as zf:
                zf.extractall(vm_dir)
            z.unlink()
    for img in sorted(vm_dir.glob("*.qcow2")):
        md5 = img.with_suffix(img.suffix + ".md5")
        if md5.exists():
            want = md5.read_text().split()[0]
            print(f"  md5 {'ok' if md5_of(img) == want else 'MISMATCH'}: {img.name} ({img.stat().st_size / 1e9:.1f} GB)")


def published_domains(repo_files: list[str]) -> list[str]:
    """Return roots that actually contain task definitions, not auxiliary trees."""
    out = set()
    for rel in repo_files:
        parts = rel.split("/")
        if len(parts) == 3 and parts[1] == "tasks" and parts[2].endswith(".json"):
            out.add(parts[0])
    return sorted(out)


def _managed_by_download(rel: Path, domains: set[str], *, all_domains: bool,
                         include_data: bool, include_vm: bool) -> bool:
    parts = rel.parts
    if include_data and len(parts) == 1 and parts[0] in {".gitattributes", "README.md", "MANIFEST.json"}:
        return True
    if include_data and all_domains and parts and parts[0] == "ablation":
        return True
    if len(parts) < 2:
        return False
    domain, subtree = parts[0], parts[1]
    selected = all_domains or domain in domains
    return selected and ((include_data and subtree in {"tasks", "public", "private"})
                         or (include_vm and subtree == "vm"))


def prune_local(data_dir: Path, repo_files: set[str], domains: list[str], *,
                all_domains: bool, include_data: bool, include_vm: bool) -> None:
    """Move files absent from the Hub to a timestamped sibling backup.

    This deliberately never deletes bytes. The local Hugging Face metadata is
    outside the managed scope and remains available for incremental downloads.
    """
    extras: list[tuple[Path, Path]] = []
    for path in data_dir.rglob("*"):
        if not (path.is_file() or path.is_symlink()):
            continue
        rel = path.relative_to(data_dir)
        if ".cache" in rel.parts:
            continue
        if (_managed_by_download(rel, set(domains), all_domains=all_domains,
                                 include_data=include_data, include_vm=include_vm)
                and rel.as_posix() not in repo_files):
            extras.append((path, rel))

    if not extras:
        print("prune: already aligned; no local-only files")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = data_dir.parent / f"{data_dir.name}.remote-extra-{stamp}"
    for source, rel in extras:
        destination = backup / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
    for directory in sorted((p for p in data_dir.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        if ".cache" in directory.relative_to(data_dir).parts:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass
    print(f"prune: moved {len(extras)} local-only file(s) to {backup}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", action="append", metavar="DOMAIN", help="restrict to these domain(s); default: all")
    ap.add_argument("--vm", action="store_true", help="also download the domain(s)' VM image(s)")
    ap.add_argument("--vm-only", action="store_true", help="download only the VM image(s)")
    ap.add_argument("--prune", action="store_true",
                    help="after a successful download, move local-only files in the selected scope to a timestamped sibling backup")
    ap.add_argument("--revision", default=None)
    a = ap.parse_args()
    from huggingface_hub import HfApi, snapshot_download

    from osworld_science.config import Settings
    s = Settings.load(REPO)
    token = s.env.get("HF_TOKEN") or None
    if not s.hf_data_repo:
        sys.exit("OSCI_HF_DATA_REPO is not set in .env")
    repo_files = HfApi(token=token).list_repo_files(
        s.hf_data_repo, repo_type="dataset", revision=a.revision)
    known_domains = published_domains(repo_files)
    if a.domain:
        unknown = sorted(set(a.domain) - set(known_domains))
        if unknown:
            ap.error(f"unknown domain(s): {', '.join(unknown)}; known: {', '.join(known_domains)}")
        domains = list(dict.fromkeys(a.domain))
    else:
        domains = known_domains
    prefixes = [f"{d}/" for d in domains]
    s.data_dir.mkdir(parents=True, exist_ok=True)

    if not a.vm_only:
        patterns = ["README.md", "MANIFEST.json"] + [f"{pre}{sub}/**" for pre in prefixes for sub in ("tasks", "public", "private")]
        if a.domain is None:
            patterns += [".gitattributes", "ablation/**"]
        print(f"data for {', '.join(domains)}: {s.hf_data_repo} -> {s.data_dir}")
        snapshot_download(repo_id=s.hf_data_repo, repo_type="dataset", revision=a.revision,
                          allow_patterns=patterns, local_dir=s.data_dir, token=token)
    if a.vm or a.vm_only:
        print(f"VM image(s) for {', '.join(domains)}: {s.hf_data_repo} -> {s.data_dir}/<domain>/vm/")
        snapshot_download(repo_id=s.hf_data_repo, repo_type="dataset", revision=a.revision,
                          allow_patterns=[f"{pre}vm/**" for pre in prefixes], local_dir=s.data_dir, token=token)
        for d in domains:
            if (s.data_dir / d / "vm").is_dir():
                verify_images(s.data_dir / d / "vm")
    if a.prune:
        prune_local(s.data_dir, set(repo_files), domains, all_domains=a.domain is None,
                    include_data=not a.vm_only, include_vm=a.vm or a.vm_only)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
