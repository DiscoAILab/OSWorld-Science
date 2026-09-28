"""Guest lifecycle: QEMU-in-Docker (`happysixd/osworld-docker`).

A port of start_*_env.sh / reset_*_env.sh. Every docker parameter below is
load-bearing and was copied verbatim between the three legacy suites:

  DISK_CACHE=writeback + DISK_IO=threads   without them the guest reads at ~1.7 MB/s
  no DISK_TYPE                             the container's disk.sh drops the second drive
  BOOT=http://example.com/image.iso        placeholder the entrypoint insists on
  image copied to local disk first         qcow2 over a network mount is unusable

Ports: control PORT, noVNC PORT+3006, PORT+3080 → guest 8080, PORT+4202 →
guest 9222 (Firefox remote debugging). Container name <prefix>_<PORT>.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..config import Settings
from ..guest.client import Guest
from .snapshots import Snapshot, VMDefaults

NOVNC_OFFSET, HTTP_OFFSET, DEVTOOLS_OFFSET = 3006, 3080, 4202
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "glusterfs", "lustre", "ceph",
              "fuse.ceph", "9p", "afs", "gpfs", "beegfs", "fuse.s3fs", "fuse.rclone", "fuse.gcsfuse"}


def filesystem_type(path: Path) -> str:
    """Filesystem type of the mount holding `path` (from /proc/mounts; "" if unknown)."""
    try:
        path = Path(path).resolve()
        best, fstype = "", ""
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mnt = parts[1].replace("\\040", " ")
            if (str(path) == mnt or str(path).startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
                best, fstype = mnt, parts[2]
        return fstype
    except OSError:
        return ""


def is_network_fs(path: Path) -> bool:
    return filesystem_type(path) in NETWORK_FS


class VMError(RuntimeError):
    pass


def port_is_free(port: int) -> bool:
    """Can this control port and its three derived ports (noVNC, HTTP, devtools)
    be bound on this host? Docker publishes on 0.0.0.0/[::], so a bind attempt
    on the wildcard address fails while a container maps the port."""
    import socket
    for p in (port, port + NOVNC_OFFSET, port + HTTP_OFFSET, port + DEVTOOLS_OFFSET):
        for family, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    if family == socket.AF_INET6:
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    sock.bind((addr, p))
            except OSError as e:
                if e.errno in (98, 13):        # EADDRINUSE, EACCES
                    return False
                # address family unavailable etc.: not evidence of a conflict
    return True


def containers_publishing(env: dict, port: int) -> list[tuple[str, bool]]:
    """(name, is_ours) for every container (any state) that publishes host `port`.
    Ours = carries the `osci` label (containers created by this tool) or, for
    containers made before labels existed, a name starting with `osci_`."""
    r = subprocess.run(["docker", "ps", "-a", "--filter", f"publish={int(port)}",
                        "--format", '{{.Names}}\t{{.Label "osci"}}'], env=env,
                       capture_output=True, text=True)
    out = []
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        name, _, label = line.partition("\t")
        if name:
            out.append((name, label.strip() == "1" or name.startswith("osci_")))
    return out


def owned_port_containers(settings: Settings, prefixes: list[str]) -> dict[int, str]:
    """control port -> container name, for RUNNING containers this tool created
    (`<prefix>_<port>`). Only a running container of ours binds its port, so
    only those ports are ours to reuse; a created-but-failed or exited one
    holds nothing, and the port's real owner (if any) shows up as busy."""
    env = dict(os.environ)
    dh = default_docker_host(settings)
    if dh:
        env["DOCKER_HOST"] = dh
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], env=env,
                       capture_output=True, text=True)
    owned: dict[int, str] = {}
    if r.returncode != 0:
        return owned
    for name in r.stdout.split():
        for prefix in prefixes:
            if name.startswith(prefix + "_") and name[len(prefix) + 1:].isdigit():
                owned[int(name[len(prefix) + 1:])] = name
    return owned


def default_docker_host(settings: Settings) -> str:
    if settings.docker_host:
        return settings.docker_host
    sock = Path(f"/run/user/{os.getuid()}/docker.sock")
    return f"unix://{sock}" if sock.exists() else ""


class VM:
    def __init__(self, snapshot: Snapshot, defaults: VMDefaults, settings: Settings,
                 port: int | None = None, image: Path | None = None, name: str | None = None,
                 log=print):
        self.snapshot = snapshot
        self.defaults = defaults
        self.settings = settings
        self.port = int(port or snapshot.default_port)
        self.name = name or f"{snapshot.container_prefix}_{self.port}"
        self.log = log
        self._image_override = Path(image) if image else None
        self.guest = Guest(self.port)
        self.stop_event = None          # set by the sweep so long waits abort on Ctrl-C
        self._env = dict(os.environ)
        dh = default_docker_host(settings)
        if dh:
            self._env["DOCKER_HOST"] = dh

    # ── paths ──────────────────────────────────────────────────────────────
    @property
    def images_dir(self) -> Path:
        """Where this snapshot's prepared image is expected: data/<domain>/vm/."""
        return self.settings.data_dir / self.snapshot.domain / "vm"

    @property
    def base_image(self) -> Path:
        return self.settings.vm_dir / "base" / self.defaults.base_image

    def image_candidates(self) -> list[Path]:
        """In order: the domain's data/<domain>/vm/<image>, a local vm/images/<image>."""
        return [self.images_dir / self.snapshot.image,
                self.settings.vm_dir / "images" / self.snapshot.image]

    @property
    def image(self) -> Path:
        """Prepared image for this snapshot (data/<domain>/vm first, then the
        local vm/images fallback), else the pristine base — in which case the
        snapshot's provision script runs after boot when the tools probe fails."""
        if self._image_override:
            return self._image_override
        for cand in self.image_candidates():
            if cand.exists():
                return cand
        return self.base_image

    @property
    def default_bake_target(self) -> Path:
        return self.images_dir / self.snapshot.image

    @property
    def volume(self) -> str:
        return f"{self.name}_storage"

    @property
    def novnc_port(self) -> int:
        return self.port + NOVNC_OFFSET

    # ── docker helpers ─────────────────────────────────────────────────────
    def docker(self, *args: str, check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], env=self._env, check=check,
                              capture_output=capture, text=True)

    def exists(self) -> bool:
        return self.docker("inspect", self.name).returncode == 0

    def container_status(self) -> str | None:
        r = self.docker("inspect", "-f", "{{.State.Status}}", self.name)
        return r.stdout.strip() if r.returncode == 0 else None

    def backing_image(self) -> Path | None:
        r = self.docker("inspect", "-f",
                        '{{range .Mounts}}{{if eq .Destination "/System.qcow2"}}{{.Source}}{{end}}{{end}}',
                        self.name)
        return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None

    def alive(self) -> bool:
        return self.guest.alive()

    # ── image handling ─────────────────────────────────────────────────────
    def _refuse_backing_file(self, image: Path) -> None:
        if shutil.which("qemu-img"):
            r = subprocess.run(["qemu-img", "info", str(image)], capture_output=True, text=True)
            if "backing file" in r.stdout.lower():
                raise VMError(f"{image} has a backing file — not self-contained, refusing")

    def should_copy(self, durable: Path) -> bool:
        """OSCI_VM_COPY_IMAGES: `always` (legacy behaviour), `never` (mount the
        image where it is), `auto` (copy only when the image sits on a network
        filesystem, where QEMU's random reads are too slow for a usable guest)."""
        policy = self.settings.vm_copy_images
        if policy == "always":
            return True
        if policy == "never":
            return False
        return is_network_fs(durable)

    def ensure_local_copy(self) -> Path:
        """Return the image file the container should mount read-only.

        With copying enabled, the image is copied to `OSCI_VM_FAST_DIR` once,
        even when several workers start at the same moment: the copy is
        serialised by a lock file next to the target — the first worker
        copies to a private temp name and renames it into place, the others
        block on the lock and then find the finished copy. (Without this,
        parallel workers wrote into one shared `.part` file, two failed on
        the rename, and the third booted a guest from a file still being
        written.) Copies are kept as a cache for later runs; `osci vm cache
        clean` removes the ones no container mounts."""
        durable = self.image.resolve()
        if not durable.is_file() or durable.stat().st_size == 0:
            raise VMError(f"no base image at {durable}\n"
                          f"(download: scripts/vm_prep/base/download_base.sh; build: scripts/vm_prep/<domain>/build_image.sh)")
        self._refuse_backing_file(durable)
        if not self.should_copy(durable):
            self.log(f"mounting the image in place ({filesystem_type(durable) or 'local'} filesystem, "
                     f"OSCI_VM_COPY_IMAGES={self.settings.vm_copy_images}): {durable}")
            return durable
        fast = self.settings.vm_fast_dir
        fast.mkdir(parents=True, exist_ok=True)
        local = fast / durable.name
        size = durable.stat().st_size

        def present() -> bool:
            return local.is_file() and local.stat().st_size == size

        if present():
            self.log(f"local working copy already present: {local}")
            return local
        lock_path = fast / (durable.name + ".lock")
        with open(lock_path, "a+") as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.log("another worker is copying the base image to local disk — waiting for it")
                fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                if present():
                    self.log(f"local working copy already present: {local}")
                    return local
                need_gb = size // (1024 ** 3) + 2
                avail_gb = shutil.disk_usage(fast).free // (1024 ** 3)
                if avail_gb < need_gb:
                    raise VMError(
                        f"{fast} has {avail_gb}G free, needs {need_gb}G for a local copy of {durable.name}.\n"
                        f"      Options: `osci vm cache list` / `osci vm cache clean` to drop copies no container uses;\n"
                        f"      OSCI_VM_FAST_DIR=<dir on a roomy local disk>; or OSCI_VM_COPY_IMAGES=never to mount\n"
                        f"      {durable} in place (it is on a {filesystem_type(durable) or 'local'} filesystem).")
                self.log(f"copying base image to local disk ({need_gb}G)…")
                part = fast / f"{durable.name}.part.{os.getpid()}.{threading.get_ident()}"
                try:
                    shutil.copyfile(durable, part)
                    if part.stat().st_size != size:
                        raise VMError(f"short copy of {durable.name}: {part.stat().st_size} != {size} bytes")
                    os.replace(part, local)
                finally:
                    part.unlink(missing_ok=True)
                self.log("copied")
                return local
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    # ── lifecycle ──────────────────────────────────────────────────────────
    def wait_alive(self, timeout_s: int | None = None, interval: int = 8) -> bool:
        deadline = time.time() + (timeout_s or self.defaults.boot_timeout_s)
        probe = 0
        while time.time() < deadline:
            probe += 1
            if self.alive():
                self.log(f"guest ready (probe {probe})")
                return True
            if self.stop_event is not None and self.stop_event.wait(interval):
                raise VMError("interrupted while waiting for the guest to boot")
            if self.stop_event is None:
                time.sleep(interval)
        return False

    def start(self, rebuild: bool = False) -> None:
        local = self.ensure_local_copy()
        if not rebuild and self.exists():
            if self.alive():
                self.log(f"{self.name} is already up and answering on :{self.port} — leaving it alone")
                return
            self.log(f"{self.name} exists but is not answering; starting it")
            self.docker("start", self.name)
            if self.wait_alive(timeout_s=300, interval=5):
                return
            self.log("it did not come up; recreating")

        self.docker("rm", "-f", self.name)
        self.docker("volume", "rm", self.volume)
        self._free_port_holders()
        self.docker("volume", "create", self.volume)
        d = self.defaults
        r = self.docker(
            "run", "-d", "--name", self.name,
            "--label", "osci=1", "--label", f"osci.snapshot={self.snapshot.name}",
            "--label", f"osci.port={self.port}",
            "--device", "/dev/kvm", "--device", "/dev/net/tun", "--cap-add", "CAP_NET_ADMIN",
            "-e", f"RAM_SIZE={self.snapshot.ram or d.ram}", "-e", f"CPU_CORES={self.snapshot.cpus or d.cpus}",
            "-e", f"DISK_SIZE={d.disk}",
            "-e", "NETWORK=user", "-e", "BOOT=http://example.com/image.iso",
            "-e", "USER_PORTS=5000,9222,8080",
            "-e", "DISK_CACHE=writeback", "-e", "DISK_IO=threads",
            "-p", f"{self.port}:5000", "-p", f"{self.port + NOVNC_OFFSET}:8006",
            "-p", f"{self.port + HTTP_OFFSET}:8080", "-p", f"{self.port + DEVTOOLS_OFFSET}:9222",
            "-v", f"{local}:/System.qcow2:ro", "-v", f"{self.volume}:/storage",
            d.docker_image)
        if r.returncode != 0:
            raise VMError(f"docker run failed: {r.stderr.strip()[-400:]}")
        self.log("container up; waiting for the guest to boot (1.7-20 min, high variance)")
        if not self.wait_alive():
            tail = self.docker("logs", "--tail", "20", self.name).stdout
            raise VMError(f"guest never came up on :{self.port}\n{tail}")
        self.log(f"noVNC on http://localhost:{self.novnc_port}   control plane on :{self.port}")

    def _free_port_holders(self) -> None:
        """A control port is a slot, not a snapshot: a container of ours from an
        earlier run (possibly of another snapshot, e.g. osci_ling_5042 when we
        now need osci_stat_5042) may still hold it. Remove it — reset would
        have destroyed it anyway — and refuse if the holder is not ours."""
        for name, ours in containers_publishing(self._env, self.port):
            if name == self.name:
                continue
            if not ours:
                raise VMError(f"port {self.port} is published by container {name}, which this tool did "
                              f"not create — stop it or choose another port (OSCI_PORT_BASE)")
            self.log(f"removing our container {name}, which still holds port {self.port}")
            self.docker("rm", "-f", name)
            self.docker("volume", "rm", f"{name}_storage")

    def probe_tools(self) -> tuple[bool, str]:
        """True when the snapshot's toolchain is present (baked image)."""
        if not self.snapshot.tools_probe:
            return True, ""
        try:
            r = self.guest.execute(["bash", "-lc", self._with_init(self.snapshot.tools_probe)], 60)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        out = ((r.get("output") or "") + (r.get("error") or "")).strip()
        return r.get("returncode") in (0, None) and bool(out), out

    def run_provision(self) -> None:
        if not self.snapshot.provision:
            raise VMError(f"snapshot {self.snapshot.name} has no provision script and the "
                          f"tools probe failed on {self.image}")
        script = self.settings.repo_root / self.snapshot.provision
        if not script.exists():
            raise VMError(f"provision script missing: {script}")
        self.log(f"toolchain not in this image — provisioning with {script.name}")
        cmd = [str(script), "--port", str(self.port)]
        if script.suffix == ".py":
            cmd = ["python3", *cmd]
        r = subprocess.run(cmd, cwd=self.settings.repo_root, env=self._env)
        if r.returncode != 0:
            raise VMError(f"provisioning failed (exit {r.returncode})")

    def reset(self) -> None:
        """Destroy and recreate from the image, then make sure the toolchain is there."""
        self.start(rebuild=True)
        ok, out = self.probe_tools()
        if ok:
            self.log(f"toolchain present ({out.splitlines()[0] if out else 'probe ok'}) — skipping provision")
        else:
            self.run_provision()
        self.log(f"guest reset on :{self.port}")

    def stop(self, timeout: int = 90) -> None:
        self.docker("stop", "-t", str(timeout), self.name)

    def remove(self) -> None:
        self.docker("rm", "-f", self.name)
        self.docker("volume", "rm", self.volume)

    def status(self) -> dict:
        return {"name": self.name, "port": self.port, "novnc": self.novnc_port,
                "container": self.container_status(), "alive": self.alive(),
                "image": str(self.image), "backing": str(self.backing_image() or "")}

    def _with_init(self, script: str) -> str:
        """Prepend the snapshot's `shell_init` (what the desktop terminal runs at start-up,
        e.g. a conda activate that ~/.bashrc only does for interactive shells)."""
        init = self.snapshot.shell_init.strip()
        return f"{init}\n{script}" if init else script

    def check(self) -> str:
        """What a solver's terminal sees (login shell, so ~/.profile applies)."""
        cmds = " ".join(self.snapshot.check_commands) or "python3 firefox"
        probe = (f"for c in {cmds}; do "
                 "printf '%-10s %s\\n' \"$c\" \"$(command -v $c || echo MISSING)\"; done")
        return self.guest.output(["bash", "-lc", self._with_init(probe)], 60)



def mounted_images(settings: Settings) -> dict[str, list[str]]:
    """Image file -> names of containers (any state) that mount it as /System.qcow2."""
    env = dict(os.environ)
    dh = default_docker_host(settings)
    if dh:
        env["DOCKER_HOST"] = dh
    out: dict[str, list[str]] = {}
    r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return out
    for name in r.stdout.split():
        m = subprocess.run(["docker", "inspect", "-f",
                            '{{range .Mounts}}{{if eq .Destination "/System.qcow2"}}{{.Source}}{{end}}{{end}}',
                            name], env=env, capture_output=True, text=True)
        src = m.stdout.strip()
        if src:
            out.setdefault(src, []).append(name)
    return out


def cache_entries(settings: Settings) -> list[dict]:
    """Local image copies under OSCI_VM_FAST_DIR with size and the containers using them."""
    fast = settings.vm_fast_dir
    if not fast.is_dir():
        return []
    users = mounted_images(settings)
    entries = []
    for p in sorted(fast.iterdir()):
        if p.is_file() and p.suffix in (".qcow2", ".part") or ".part." in p.name:
            entries.append({"path": str(p), "bytes": p.stat().st_size,
                            "used_by": users.get(str(p), [])})
    return entries


def clean_cache(settings: Settings, all_copies: bool = False, log=print) -> list[str]:
    """Remove local copies no container mounts (`all_copies`: also the ones in use —
    only after stopping those containers)."""
    removed = []
    for e in cache_entries(settings):
        if e["used_by"] and not all_copies:
            log(f"keep   {e['path']}  ({e['bytes'] / 1e9:.1f} GB, mounted by {', '.join(e['used_by'])})")
            continue
        Path(e["path"]).unlink(missing_ok=True)
        lock = Path(e["path"] + ".lock")
        lock.unlink(missing_ok=True)
        removed.append(e["path"])
        log(f"removed {e['path']}  ({e['bytes'] / 1e9:.1f} GB)")
    return removed
