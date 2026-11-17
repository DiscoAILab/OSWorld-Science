#!/usr/bin/env python3
"""Upload a versioned installer and run installation detached in a fresh guest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent / "common"))
from guest_run import run  # noqa: E402

from osworld_science.guest.client import Guest  # noqa: E402

VERSION = "5.7.2"
URL = "https://www.profex-xrd.org/wp-content/uploads/2026/08/Profex-5.7.2-linux-x86_64-bin.tar.gz"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=5180)
    ap.add_argument("--tarball", type=Path)
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"No OSWorld guest on :{a.port}; reset a fresh VM first.")
    cache = REPO / ".cache/profex"
    cache.mkdir(parents=True, exist_ok=True)
    tarball = a.tarball or Path(os.environ.get("PROFEX_TARBALL") or
                               cache / f"Profex-{VERSION}-linux-x86_64-bin.tar.gz")
    if not tarball.is_file():
        partial = tarball.with_suffix(tarball.suffix + ".partial")
        subprocess.run(["curl", "--fail", "--location", "--retry", "2", "--connect-timeout",
                        "20", "--max-time", "900", "--output", str(partial), URL], check=True)
        partial.replace(tarball)
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    lock = cache / "installer-lock.json"
    if lock.exists() and json.loads(lock.read_text())["sha256"] != digest:
        sys.exit("Installer differs from the locally recorded download; inspect it before proceeding.")
    # This is a local reproducibility lock, not a vendor-authenticated checksum.
    lock.write_text(json.dumps({"version": VERSION, "url": URL, "sha256": digest,
                                "verification": "local first-download checksum"}, indent=2) + "\n")
    g.upload(tarball, "/home/user/profex-install/profex.tar.gz")
    if g.sha256_of("/home/user/profex-install/profex.tar.gz") != digest:
        sys.exit("Uploaded Profex installer checksum mismatch")
    subprocess.run(["bash", str(HERE / "prepare_runtime.sh")], check=True)
    runtime = cache / "profex-ubuntu24-runtime.tar"
    g.upload(runtime, "/home/user/profex-install/runtime.tar")
    if g.sha256_of("/home/user/profex-install/runtime.tar") != hashlib.sha256(runtime.read_bytes()).hexdigest():
        sys.exit("Uploaded compatibility runtime checksum mismatch")
    return run(g, "profex_install", HERE / "install_guest.sh", minutes=40)


if __name__ == "__main__":
    sys.exit(main())
