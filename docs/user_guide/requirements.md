# System requirements

OSWorld-Science hosts each desktop as a QEMU virtual machine inside a
Docker container (`happysixd/osworld-docker`) accelerated by KVM. That is
the only hosting route implemented; the requirements below follow from it.

## Host

| requirement | why | how to check |
|---|---|---|
| Linux, x86-64 | the guest images are x86-64 Ubuntu 22.04 qcow2, run with hardware virtualisation only | `uname -m` → `x86_64` |
| KVM available to your user: CPU flag `vmx`/`svm`, `/dev/kvm` readable and writable | without KVM, QEMU falls back to software emulation, 10–50× slower, and every step budget becomes meaningless | `egrep -c '(vmx\|svm)' /proc/cpuinfo` > 0; `ls -l /dev/kvm` |
| `/dev/net/tun` and `CAP_NET_ADMIN` inside the container | the container's user-mode NAT and port forwards | `ls -l /dev/net/tun` |
| Docker Engine 20+ (rootful or rootless) reachable from your account, able to pass `--device /dev/kvm` | container lifecycle | `docker info`; rootless needs `/dev/kvm` world-accessible or your user in the `kvm` group |
| the `happysixd/osworld-docker` image (260 MB) | the QEMU wrapper | pulled automatically on first `docker run`; needs registry access once |
| `qemu-img` on the host | image sanity check and baking | `qemu-img --version` |
| **8 GB RAM and 4 vCPUs per concurrent guest**, plus headroom for the host | `RAM_SIZE=8G`, `CPU_CORES=4` per container | `free -g`, `nproc`; `--workers` ≤ min(free RAM / 8 GB, cores / 4) |
| disk: 24 GB base image + 24–34 GB per snapshot image; a few GB of overlay per running guest under the Docker root dir; a further copy of each image under `OSCI_VM_FAST_DIR` only when the images live on a network filesystem | see `running.md` and `../developer_guide/building-a-vm-snapshot.md` | `df -h` on `vm/`, the Docker root dir and `OSCI_VM_FAST_DIR` |
| images on a local filesystem, or a local `OSCI_VM_FAST_DIR` with room for a copy | QEMU's random reads over NFS/CIFS are too slow for a usable guest | `findmnt -T data/<domain>/vm` |
| 4 free TCP ports per worker: `PORT`, `PORT+3006` (noVNC), `PORT+3080`, `PORT+4202` | control plane, desktop viewer, guest HTTP and Firefox devtools forwards | `osci run` allocates them and warns about busy ones |
| Python ≥ 3.11 through `uv` | the package | `uv sync` installs the interpreter |
| outbound HTTPS from the host | model endpoints, Hugging Face (data, base image), Docker Hub | `curl -sI https://huggingface.co` |
| `Rscript` (R 4.4.3) on the host | the three R-backed statistics evaluators (`r_syntax`, `r_function_probe`, `shiny_app_check`) | `OSCI_RSCRIPT` in `.env`; `scripts/grading_env/README.md` |
| the `osworld-openfoam-guest:v2512-pv5.11.2` Docker image (4.9 GB) on the host | the `physics` grader, which re-runs OpenFOAM on every submission inside that image | `docker image inspect osworld-openfoam-guest:v2512-pv5.11.2`; build context in `scripts/vm_prep/physics/files/` |
| outbound network **from the guest** only while building images | apt, conda-forge, posit, GitHub, slicer.org during `scripts/vm_prep/*/build_image.sh`; runs do not need it | — |

Not required: a GPU (the guests use software rendering; 3D Slicer runs on
it), a display on the host (noVNC is served by the container), root
(rootless Docker works), VMware or VirtualBox.

`uv run osci doctor [--workers N]` checks all of the above on the machine
it runs on and exits non-zero on a hard failure.

## Not supported

| option / situation | status | why |
|---|---|---|
| VMware Workstation / Fusion, VirtualBox | not supported | only the Docker + KVM lifecycle is implemented; the snapshot images are qcow2 |
| cloud and sandbox providers (AWS, Azure, GCP, Modal, Daytona, …) | not supported | same; the images are not published as cloud snapshots |
| macOS hosts (Intel or Apple silicon) | not supported | no KVM; Apple silicon would also need arm64 guest images |
| Windows hosts (Docker Desktop / WSL2) | not supported, untested | the `/dev/kvm`, `/dev/net/tun` and `CAP_NET_ADMIN` path is untested there |
| hosts without KVM (cloud VMs without nested virtualisation, CI runners) | not supported | software emulation is far too slow for GUI agents; the guest never becomes usable within the boot window |
| arm64 hosts | not supported | x86-64 images only |
| Windows guests | not supported by the current backend | the 14 Ansys Fluent task definitions and graders are published, but their Windows 10 guest cannot be redistributed and the Linux-oriented Docker/KVM lifecycle cannot boot it without changes |
| EEGLAB tasks without a local image | data and offline scoring only | the 10 task definitions, inputs, and graders are published, but `ubuntu_eeglab.qcow2` and its provisioner are not yet available |
| sweeps spread over several hosts | not supported | one host, one port range, one `runs/<name>/summary.json`; run separate sweeps and merge the report CSVs |
| running under a SLURM batch job | not supported | the guests are long-lived Docker containers on a node with `/dev/kvm`; a batch allocation that cannot reach the Docker socket cannot start them |
| accessibility-tree / set-of-marks observations, the `computer_13` action space of upstream OSWorld | not supported | only screenshot + pyautogui is implemented |
| upstream OSWorld task `evaluator` blocks | not compatible | `evaluator.func` names this package's evaluator registry |
| rootful Docker, cgroup v2, Docker Desktop for Linux | expected to work, untested | — |

## Per-snapshot guest size

A snapshot entry may override the default guest size with `ram` and `cpus`
(`configs/snapshots.yaml`). `ubuntu_qupath` runs with 24 GB and 8 vCPUs per
guest because QuPath keeps whole-slide-image tiles in memory; size
`--workers` for that domain accordingly.
