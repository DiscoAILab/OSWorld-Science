#!/usr/bin/env python3
"""Download benchmark data (and optionally a domain's VM image) from Hugging Face into `data/`.

    uv run python scripts/data_prep/hf_download.py                       # every domain's public + private trees
    uv run python scripts/data_prep/hf_download.py --domain stat         # one domain (repeatable)
    uv run python scripts/data_prep/hf_download.py --domain stat --vm    # + its prepared VM image (23-34 GB)
    uv run python scripts/data_prep/hf_download.py --vm-only --domain radiology

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", action="append", metavar="DOMAIN", help="restrict to these domain(s); default: all")
    ap.add_argument("--vm", action="store_true", help="also download the domain(s)' VM image(s)")
    ap.add_argument("--vm-only", action="store_true", help="download only the VM image(s)")
    ap.add_argument("--revision", default=None)
    a = ap.parse_args()
    from huggingface_hub import HfApi, snapshot_download

    from osworld_science.config import Settings
    s = Settings.load(REPO)
    token = s.env.get("HF_TOKEN") or None
    if not s.hf_data_repo:
        sys.exit("OSCI_HF_DATA_REPO is not set in .env")
    domains = a.domain or sorted({p.split("/")[0] for p in HfApi(token=token).list_repo_files(
        s.hf_data_repo, repo_type="dataset", revision=a.revision) if "/" in p})
    prefixes = [f"{d}/" for d in domains]
    s.data_dir.mkdir(parents=True, exist_ok=True)

    if not a.vm_only:
        patterns = ["README.md", "MANIFEST.json"] + [f"{pre}{sub}/**" for pre in prefixes for sub in ("tasks", "public", "private")]
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
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
