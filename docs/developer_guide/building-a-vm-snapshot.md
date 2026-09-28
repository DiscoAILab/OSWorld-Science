# Building a VM snapshot from the base image

Every domain's desktop is one **snapshot**: the official OSWorld Ubuntu
22.04 image with the domain's software installed, flattened into a
self-contained qcow2 and shipped as `data/<domain>/vm/<snapshot>.qcow2`.
Users download it; this page is how it is made.

## How a snapshot is used

Guests run as QEMU-in-Docker (`happysixd/osworld-docker`) with KVM. The
container mounts the snapshot image read-only and writes to its own
copy-on-write overlay, so any number of guests share one image and a reset
is `docker rm` + `docker run`. `configs/snapshots.yaml` maps a snapshot
name to its domain, image file, default port, tool probe, provision script
and optional hooks; a task selects it with its `snapshot` field.

## 0. Prerequisites

* A host that passes `uv run osci doctor` (KVM, Docker, `qemu-img`, 8 GB RAM
  per guest, disk for the base image and the new snapshot).
* Outbound network **from the guest** during the build (apt, conda-forge,
  GitHub releases, vendor downloads); runs do not need it.
* The base image: `scripts/vm_prep/base/download_base.sh` fetches
  `Ubuntu.qcow2.zip` from `xlangai/ubuntu_osworld` on Hugging Face (~24 GB
  unpacked) into `vm/base/`, checks it has no backing file, and records its
  md5. The base is never modified; it is mounted read-only.

What the base already contains: Ubuntu 22.04.3 with GNOME at 1920×1080,
user `user` (in `sudo`, password `password`), Firefox (snap), gedit, VS Code,
GNOME Terminal, a bare `/usr/bin/python3`, and the OSWorld control-plane
server on guest port 5000 (`/screenshot`, `/execute`, `/file`,
`/setup/upload`) that the harness and every provisioner talk to. Two
defects to know about: the root partition is 29.5 GB of a 50 GB disk (grow
it with `growpart` + `resize2fs` if the toolchain is large, as the stat
image does), and a recursive grep over the VS Code tree can flip the root
filesystem read-only.

## 1. Register the snapshot

Add an entry to `configs/snapshots.yaml`:

```yaml
  ubuntu_<domain>:
    domain: <domain>
    description: what is installed
    image: ubuntu_<domain>.qcow2                 # data/<domain>/vm/
    default_port: 5090                           # an unused control port; noVNC is port+3006
    container_prefix: osci_<domain>
    tools_probe: "command -v mytool"             # exit 0 + output ⇒ toolchain present
    shell_init: "conda activate mytool"          # optional: prepended to the probe and to `osci vm check`
    provision: scripts/vm_prep/<domain>/provision_guest.sh
    check_commands: [mytool, python3, firefox]   # listed by `osci vm check`
    window_classes: {mytool: mytool, gnome-terminal: terminal}   # optional per-step tally
    bake_cleanup: "rm -rf /home/user/.mytool"    # optional extra cleanup before baking
```

`tools_probe` is what makes `osci vm reset` self-healing: when the booted
image lacks the toolchain (for example the pristine base), `provision`
runs. Both probes use a non-interactive login shell, so a toolchain that
`~/.bashrc` activates only for interactive shells needs `shell_init`.

## 2. Write the provisioner

`scripts/vm_prep/<domain>/provision_guest.sh --port P` installs everything
into a running guest through the control plane. Constraints of that
control plane, each learned the expensive way:

* `/execute` has a ~120 s server-side timeout the client cannot raise, so
  any long step (apt, conda, tarball extraction, downloads) is written into
  the guest, started detached, and polled through
  `scripts/vm_prep/common/guest_run.py` (upload a script, run it with
  `setsid nohup`, poll a `.done` flag; an exhausted poll is a timeout, never
  a success).
* `/execute` runs `/bin/sh`; use `bash -lc` so `~/.profile` applies, exactly
  as a desktop terminal does.
* Right after boot `unattended-upgrades` may hold the apt lock: retry
  `apt-get update` and pass `-o DPkg::Lock::Timeout=180`.
* A single `/execute` body over ~50 KB fails; large files go through
  `Guest.upload()` (multipart `/setup/upload`, byte-checked).

Patterns to copy: `linguistics/praat_setup.py` (an apt package pinned to a
version), `radiology/weasis_setup.py` (a vendor `.deb` plus PATH symlink and
MIME association), `radiology/slicer_setup.py` + `slicer_firstrun.sh` (a
tarball, a desktop entry, first-run settings written through the
application's own settings API), `stat/provision_guest.sh` (a conda lock
file installed with micromamba in user space, PATH lines appended to
`~/.profile` and `~/.bashrc`).

Rules that decide whether agents can use the software: pin versions (one
stat task is graded on R's random stream to 1e-9); put GUI programs in the
applications menu **and** on `PATH` (agents find them both ways); clear
first-run dialogs, disclaimers and update checks before baking (they cost
agents steps); do not install shortcuts the task means to forbid (no
Python DICOM or speech libraries in the radiology and linguistics images).

## 3. Build and bake

`scripts/vm_prep/<domain>/build_image.sh` is three lines around the shared
driver:

```bash
scripts/vm_prep/common/build_image.sh ubuntu_<domain> <port> scripts/vm_prep/<domain>/provision_guest.sh [more provisioners…]
```

which does, in order:

1. `osci vm start --snapshot ubuntu_<domain> --image vm/base/Ubuntu.qcow2 --rebuild`
   — a fresh container on the pristine base;
2. each provisioner against that port;
3. `osci vm check` — prints what a solver's terminal will see;
4. `osci vm bake --snapshot ubuntu_<domain> --out data/<domain>/vm/ubuntu_<domain>.qcow2`
   — cleans run state (shell history, caches, task directories, the
   snapshot's `bake_cleanup`), shuts the guest down cleanly, copies the
   container's overlay out, rebases it onto the base and flattens it with
   `qemu-img convert`, refuses the result if a backing file remains, and
   writes `<image>.md5` and `<image>.manifest.json` next to it.

Discipline: **reset, install, bake.** `osci vm bake` refuses a guest that
has served a task since it booted (`--force` overrides; do not). A guest an
agent has used carries its residue into the image — a pip-installed DICOM
library shipped that way once and let a model bypass the viewer.

## 4. Verify

```bash
uv run osci vm reset --snapshot ubuntu_<domain>      # cold start from the new image, no provisioning expected
uv run osci vm check --snapshot ubuntu_<domain>
uv run osci stage <task_id>                          # stage one task, look at the desktop through noVNC
uv run osci run --models <model> --tasks <task_id> --run-name smoke
```

Read the trajectory (`shots/`, `trace.jsonl`, `meta.json`) before trusting
the image: first-run dialogs, missing menu entries and wrong resolutions
show up there, not in the build log.

## 5. Publish

The image lives with the domain's data: `data/<domain>/vm/<snapshot>.qcow2`
plus its `.md5`. `scripts/data_prep/hf_upload.py --vm-only --domain <domain>`
uploads it (23–34 GB, resumable); users fetch it with
`scripts/data_prep/hf_download.py --domain <domain> --vm`, which verifies the
md5. Rebuilding is always possible from the scripts and the base image,
which is why the provisioners, not the image, are the source of truth.
