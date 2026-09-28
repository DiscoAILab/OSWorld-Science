"""`osci doctor`: does this host meet the requirements in docs/user_guide/requirements.md?

Each check returns (name, status, detail) with status "ok", "warn" or "fail".
Only "fail" items make the command exit non-zero.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Settings
from .vm.docker import default_docker_host, filesystem_type, is_network_fs, port_is_free
from .vm.snapshots import SnapshotRegistry

GB = 1024 ** 3


def _run(cmd: list[str], env: dict | None = None, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _mem_available_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 ** 2)
    except OSError:
        pass
    return None


def checks(settings: Settings, workers: int = 1) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def add(name, status, detail):
        out.append((name, status, detail))

    # platform
    arch, system = platform.machine(), platform.system()
    add("host", "ok" if (system == "Linux" and arch == "x86_64") else "fail",
        f"{system} {arch} ({platform.release()})")
    try:
        flags = Path("/proc/cpuinfo").read_text()
        n_virt = sum(1 for ln in flags.splitlines() if ln.startswith("flags") and (" vmx" in ln or " svm" in ln))
    except OSError:
        n_virt = 0
    add("cpu virtualisation", "ok" if n_virt else "fail", f"{n_virt} CPU(s) with vmx/svm")
    kvm = Path("/dev/kvm")
    add("/dev/kvm", "ok" if kvm.exists() and os.access(kvm, os.R_OK | os.W_OK) else "fail",
        "readable and writable" if kvm.exists() and os.access(kvm, os.R_OK | os.W_OK)
        else ("present but not accessible to this user" if kvm.exists() else "missing"))
    tun = Path("/dev/net/tun")
    add("/dev/net/tun", "ok" if tun.exists() else "fail", "present" if tun.exists() else "missing")

    # docker
    env = dict(os.environ)
    dh = default_docker_host(settings)
    if dh:
        env["DOCKER_HOST"] = dh
    rc, info = _run(["docker", "info", "-f", "{{.ServerVersion}} {{.SecurityOptions}} cgroup={{.CgroupVersion}}"], env)
    add("docker", "ok" if rc == 0 else "fail", info.splitlines()[0][:120] if info else "not reachable")
    rc, img = _run(["docker", "image", "inspect", "-f", "{{.Size}}", "happysixd/osworld-docker"], env)
    add("happysixd/osworld-docker", "ok" if rc == 0 else "warn",
        f"pulled ({int(img) / 1e6:.0f} MB)" if rc == 0 and img.isdigit() else "not pulled yet (docker run pulls it)")
    add("qemu-img", "ok" if shutil.which("qemu-img") else "fail",
        shutil.which("qemu-img") or "missing (needed for the backing-file check and baking)")

    # capacity
    cores = os.cpu_count() or 0
    avail = _mem_available_gb()
    need_ram = 8 * workers + 4
    add("cpu capacity", "ok" if cores >= 4 * workers else "warn",
        f"{cores} cores; {workers} worker(s) × 4 vCPUs")
    add("memory", "ok" if (avail is not None and avail >= need_ram) else "warn",
        f"{avail:.0f} GB available; {workers} worker(s) × 8 GB + 4 GB headroom = {need_ram} GB"
        if avail is not None else "unknown")

    # images and disk
    reg = SnapshotRegistry(settings.configs_dir / "snapshots.yaml")
    base = settings.vm_dir / "base" / reg.defaults.base_image
    add("base image", "ok" if base.exists() else "warn",
        str(base) if base.exists() else f"missing: {base} (scripts/vm_prep/base/download_base.sh)")
    from .vm.docker import VM
    for name in reg.names():
        vm = VM(reg[name], reg.defaults, settings, log=lambda *_: None)
        img = next((c for c in vm.image_candidates() if c.exists()), None)
        add(f"snapshot {name}", "ok" if img else "warn",
            f"{img} ({img.stat().st_size / GB:.1f} GB, {filesystem_type(img) or '?'} fs"
            + (", network fs → copied to OSCI_VM_FAST_DIR" if is_network_fs(img) else ", mounted in place")
            + ")" if img else f"missing: {vm.image_candidates()[0]} "
            f"(hf_download.py --domain {reg[name].domain} --vm, or scripts/vm_prep/{reg[name].domain}/build_image.sh)")
    for label, path in (("vm dir", settings.vm_dir), ("fast dir", settings.vm_fast_dir)):
        try:
            free = shutil.disk_usage(path if path.exists() else path.parent).free / GB
            add(f"disk: {label}", "ok" if free > 40 else "warn", f"{free:.0f} GB free at {path}")
        except OSError as e:
            add(f"disk: {label}", "warn", str(e))
    rc, root = _run(["docker", "info", "-f", "{{.DockerRootDir}}"], env)
    if rc == 0 and root:
        try:
            free = shutil.disk_usage(root).free / GB
            add("disk: docker root", "ok" if free > 20 else "warn", f"{free:.0f} GB free at {root}")
        except OSError:
            pass

    # ports
    busy = [p for p in range(settings.port_base, settings.port_base + workers) if not port_is_free(p)]
    add("ports", "ok" if not busy else "warn",
        f"{settings.port_base}..{settings.port_base + workers - 1} free" if not busy
        else f"in use: {busy} (osci run skips them and takes the next free ones)")

    # toolchain / keys
    add("python", "ok" if sys.version_info >= (3, 11) else "fail", platform.python_version())
    rs = shutil.which(settings.rscript) or (settings.rscript if Path(settings.rscript).exists() else None)
    add("Rscript", "ok" if rs else "warn",
        rs or f"{settings.rscript!r} not found (R-backed evaluators will score 0; set OSCI_RSCRIPT)")
    present = sorted(k for k in settings.env if k.endswith("_API_KEY") and settings.env[k])
    add("model keys", "ok" if present else "warn",
        ", ".join(present) if present else "no *_API_KEY set in .env")
    return out


def main_doctor(settings: Settings, workers: int = 1, log=print) -> int:
    rows = checks(settings, workers)
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        mark = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
        log(f" {mark} {name:<{width}}  {detail}")
    fails = [r for r in rows if r[1] == "fail"]
    warns = [r for r in rows if r[1] == "warn"]
    log(f"\n{len(fails)} failure(s), {len(warns)} warning(s) — see docs/user_guide/requirements.md")
    return 1 if fails else 0
