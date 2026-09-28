"""Flatten a container's overlay into a standalone qcow2 (a port of the
bake_*_image.sh scripts). Refuses to bake a guest that has served a task."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from .docker import VM, VMError

TASK_MARKER = "/tmp/.osci_task_ran"
GENERIC_CLEANUP = ("rm -rf /home/user/work /home/user/assets /home/user/.bash_history "
                   "/home/user/.lesshst /home/user/.sudo_as_admin_successful /home/user/*.log "
                   f"/home/user/*.done /home/user/*_setup.sh {TASK_MARKER}")


def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bake(vm: VM, out: Path, force: bool = False, manifest_extra: dict | None = None) -> Path:
    out = Path(out)
    fast = vm.settings.vm_fast_dir
    fast.mkdir(parents=True, exist_ok=True)
    backing = vm.backing_image()
    if not backing or not backing.exists():
        raise VMError("cannot determine the container's backing image")
    vm.log(f"backing image: {backing}")

    if vm.alive():
        if vm.guest.file_exists(TASK_MARKER) and not force:
            raise VMError("this guest has served a task since it booted; reset, install, then bake "
                          "(use --force to override)")
        cleanup = GENERIC_CLEANUP + ("; " + vm.snapshot.bake_cleanup if vm.snapshot.bake_cleanup else "")
        vm.log("cleaning run state and asking the guest to power down")
        vm.guest.execute(["bash", "-lc",
                          f"{cleanup}; sync; (sleep 2; echo password | sudo -S shutdown -h now) "
                          f">/dev/null 2>&1 & echo powering-down"], 30)
        for i in range(60):
            if vm.container_status() == "exited":
                vm.log(f"container exited (probe {i + 1})")
                break
            time.sleep(5)
    if vm.container_status() != "exited":
        vm.log("still running after 5 min; docker stop -t 90")
        vm.stop(90)

    overlay = fast / f"{vm.name}_overlay.qcow2"
    overlay.unlink(missing_ok=True)
    vm.log("extracting the overlay")
    r = vm.docker("cp", f"{vm.name}:/boot.qcow2", str(overlay))
    if r.returncode != 0:
        raise VMError(f"docker cp failed: {r.stderr[-300:]}")
    vm.log(f"re-pointing the backing reference at {backing}")
    subprocess.run(["qemu-img", "rebase", "-u", "-F", "qcow2", "-b", str(backing), str(overlay)], check=True)
    tmp = out.with_name(out.name + ".new")
    tmp.unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    vm.log("flattening (base + overlay -> standalone image)")
    subprocess.run(["qemu-img", "convert", "-O", "qcow2", "-m", "8", str(overlay), str(tmp)], check=True)
    info = subprocess.run(["qemu-img", "info", str(tmp)], capture_output=True, text=True).stdout
    if "backing" in info.lower():
        tmp.unlink(missing_ok=True)
        raise VMError("flattened image still has a backing file — refusing")
    os.replace(tmp, out)
    overlay.unlink(missing_ok=True)
    digest = md5_of(out)
    (out.with_suffix(out.suffix + ".md5")).write_text(f"{digest}  {out.name}\n")
    manifest = {"snapshot": vm.snapshot.name, "image": out.name, "md5": digest,
                "bytes": out.stat().st_size, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "base": str(backing), **(manifest_extra or {})}
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # a stale working copy with a different size would otherwise be reused
    stale = fast / out.name
    if stale.exists() and stale.stat().st_size != out.stat().st_size:
        stale.unlink()
    vm.log(f"done: {out}  md5 {digest}")
    return out
