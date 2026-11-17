# stm: STM-Bench P4 (single-atom positioning) snapshot and grader image

`ubuntu_stm` is the OSWorld Ubuntu 22.04 base plus the desktop console of the STM-Bench instrument, a simulated
scanning tunnelling microscope, for the three P4 tasks (`stm_p4_atom_01..03`, Eigler and Schweizer 1990). The agent works
through the screen; a separate account runs the instrument and holds the hidden world of each task; the host grades the
episode by replaying its exported records in a container. This directory builds both halves.

Status (2026-11-16): written against the runner's public sources. The guest installer and launcher were run, as far as
a rootless container allows, in an `ubuntu:22.04` container; the grader image was built and run with rootless podman.
Nothing here has run in the OSWorld base image, under the runner (`osci`), on KVM, in a GNOME session, or in
the runner's Docker. "What is not tested" below is exact.

## Files

| File | Runs | What |
|---|---|---|
| `pins.sh` | sourced | every pin: STM-Bench commit, Python build and checksum, Qt version, accounts |
| `build_image.sh` | host | `common/build_image.sh ubuntu_stm 5190 provision_guest.sh` (boot base, provision, check, bake) |
| `provision_guest.sh`, `stm_setup.py` | host | upload the pins and the private task list, run `bootstrap_guest.sh` detached, verify as `user`, optional smoke test, scrub |
| `bootstrap_guest.sh` | guest, as `user` | fetch the pinned commit, run its `deploy/osworld/guest/install.sh` |
| `build_grader.sh` | host | build `stmbench-grader:<commit12>` from `deploy/osworld/grader/Dockerfile` of the same commit |
| `../../../configs/snapshots.yaml` | | the `ubuntu_stm` entry (port 5190, prefix `osci_stm`) |

The installer itself (`install.sh`, `stm_launcher.py`, `stm-start`, `stm-bench.service`, the X access entry, `pins.sh`
defaults, `apt-packages.txt`) lives in the STM-Bench repository at the pinned commit under `deploy/osworld/guest/`, so
that the code that runs in the machine and the code that grades it are one commit.

## Build

The guest image is built by the maintainers with `build_image.sh`, from a private `tasks.json` given as `STM_TASKS_JSON`
(an input of the build, not a file of the repository). That file is generated from the three tasks' `gt.json` files
(`data/stm/private/reference_private/<id>/gt.json`: its `scenario` and `seed` per task id), so the image's seeds and the
grader's are the same ones. Users of the dataset need not build anything: they download the published qcow2.

```bash
export STM_COMMIT=<40 hex: the delivered STM-Bench commit>       # or edit pins.sh
export STM_TASKS_JSON=<path to the private tasks.json>            # {"stm_p4_atom_01": {"scenario": ..., "seed": ...}, ...}
STM_SETUP_ARGS="--smoke" scripts/vm_prep/stm/build_image.sh       # ~10 min plus the zero fill; port 5190
scripts/vm_prep/stm/build_grader.sh                                # stmbench-grader:<commit12>
```

`tasks.json` is an input of the build, never a file of a repository; `build_grader.sh` and the task's `gt.json` need
the same seeds and scenarios. `--smoke` starts the console once as `user` (through `stm-start`), takes a screenshot,
lists the console's WM_CLASS and the export, then runs `stm-bench-reset`, because `osci vm bake` must see a guest
that has not run a task. Without `--no-scrub` the free space is overwritten with zeros, so that the deleted private
copy of the task list cannot be recovered from the baked image.

Task `config` (for the dataset): `execute ["bash", "-c", "echo password | sudo -S -p '' /usr/local/sbin/stm-lockdown"]`, then
`execute ["/usr/local/bin/stm-start", "<task_id>"]`, then `sleep 3`. `stm-lockdown` (root only, idempotent) takes the
agent's administrative rights away for that run: a sudoers drop-in `user ALL=(ALL:ALL) !ALL` (checked with `visudo`;
it also binds terminals that are already open), a polkit rule and a `.pkla` denying every action, no more `sudo` and
`admin` group, cached sudo credentials removed. Nothing is revoked in the image, so a shared image keeps its admin
rights for other domains. If the machine's sudoers already fails `visudo -c` (the GitHub runner image does), the lockdown keeps its drop-in and says so; it undoes it only when the drop-in is what broke the configuration; the guest is reverted per task.

## The guest as built

| Path | Owner, mode | Content |
|---|---|---|
| `/opt/stm-bench/{python,venv,src,guest,COMMIT,versions.txt}` | root, 0755 dirs, world-readable | standalone Python 3.13, venv, the commit's tree, the launcher, `pip freeze` |
| `/var/lib/stm-bench/private/` | `stm`, 0700 | `tasks.json` (0600); during an episode `run/` (the full ledger, seed included) and `console.log` |
| `/var/lib/stm-bench/export/` | `stm`, 0755; files 0644 | the agent-visible export: `session.json`, `actions.jsonl`, `digests.jsonl`, `report.json` |
| `/run/stm-bench/` | `stm`, 0755 (systemd `RuntimeDirectory`) | `start` (FIFO, 0622), `ready`, `task`, `error`, `qt/` (0700) |
| `/usr/local/bin/stm-start` | root, 0755 | what the agent's account runs |
| `/usr/local/sbin/stm-bench-reset` | root, 0750 | back to the pristine state (build and test only) |
| `/usr/local/sbin/stm-lockdown` | root, 0750 | per-task removal of the agent's admin rights (task `config`) |
| `/etc/systemd/system/stm-bench.service` | root | `User=stm`, enabled, starts at boot |
| `/etc/xdg/autostart/stm-bench-xhost.desktop` | root | `xhost +SI:localuser:stm` at login (`stm-start` also runs it) |

Start protocol: the service creates the FIFO (right after boot it may not exist yet: `stm-start` polls for it every 0.2 s inside
its overall wait, and exits 1 only if it never appears); `stm-start <id>` validates the id, writes it into the FIFO and waits up
to 90 s (`STM_START_WAIT_S`) for `/run/stm-bench/ready`: exit 0 up, 1 refused (reason printed), 2 usage, 3 timeout. The
launcher looks the id up in `tasks.json` and starts `python -m stmbench.cli gui --sealed --lang en --private-dir
.../private/run --export-dir .../export --ready-file /run/stm-bench/ready` with the seed as one JSON line on stdin
(never an argument or environment variable). The console opens full screen on `:0`, and writes the ready file once
the window is exposed. One console per boot: the same id again is a no-op, another id is refused; a console that dies
before its window is shown is cleaned up and may be retried; after that nothing of the export is ever touched.

## Pins and why

- Python 3.13.18, python-build-standalone release 20261110, `install_only` x86-64 glibc build, sha256
  `50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc` (the release's `SHA256SUMS`, and the same value
  as a download checked on 2026-11-16). STM-Bench needs 3.13; Ubuntu 22.04 ships 3.10. 3.13.18 is the patch release
  the grader image's `python:3.13-slim` carried on 2026-11-16 (base digest `sha256:48d884813cf4...`), so interpreter and
  image agree; fix the image's base with `PYTHON_IMAGE=...@sha256:` as well.
- numpy 2.5.4, scipy 1.18.1, PyYAML 6.0.3: `deploy/osworld/grader/requirements.txt`, the file the grader image
  installs too. A replay is bit-identical only with the same numerics.
- PySide6_Essentials 6.12.0 and shiboken6 6.12.0: the Qt the console was developed and its layout tuned with. Its
  wheels need glibc 2.34, which Ubuntu 22.04 has (2.35). Essentials, not the full PySide6 (no WebEngine, 3D, ...).
  A 6.9.3 environment (glibc 2.28 cluster) fails five of the console's layout and pixel tests (with Linux fonts too), so it is not used.
- The STM-Bench commit: one variable (`STM_COMMIT`), used by the guest and the grader image.
- apt packages are not version-pinned (`apt-packages.txt`: Qt's xcb libraries, fonts, `xhost`); they do not enter the
  numerics (numpy and scipy bring their own BLAS), only libc and libm do (see the cross-environment result below).

## Grader image and replay checks (2026-11-16, rootless podman 3.3.1 on a cluster node, commit f6d18a47bfd5)

`build_grader.sh` built `stmbench-grader:f6d18a47bfd5` (base `public.ecr.aws/docker/library/python:3.13-slim`,
digest `sha256:48d884813cf4...`; Python 3.13.18, numpy 2.5.4, scipy 1.18.1, PyYAML 6.0.3; user `grader`, uid 10001).
Run with `--network none`, the export read-only (as `--user 0`, see below), scenario `P4_atom_positioning_cu111`:

- trial 7 export (version 2): score 0.0, 0 of 2 claims verified, replay consistent (hashes differ on 52 arrays by
  rounding, within 1e-8, accepted by the fingerprints).
- trial 6 export (version 1, no fingerprints): strict grading, as the adapter runs it: replay inconsistent (44 digest
  mismatches, 0 result mismatches), score 0.0; with `--no-strict`: 0.5 (1 of 2 claims). The export predates the
  fingerprints, so a last-bits difference between machines cannot be told from a different measurement. Exports made by
  the delivered console are version 2.
- the adapter itself (`deploy/osworld/stm_metric.py`) with a `docker` shim that calls podman: the same two results.
- an export made in the container test with the guest's venv (Ubuntu glibc 2.35, standalone Python 3.13.18; 6 actions: scans
  of 64, 128 and 256 px, a spectrum, a move, a scan) graded in the image on another node (same CPU model, AMD EPYC
  9654): replay consistent, every hash equal. One CPU model only; other CPUs are not covered.

The build needs outbound network (a base image, PyPI); the cluster's compute nodes have none, so it was built on the
login node and run on a compute node.

## Switches for OSWorld's answers

| Question | Switch | Effect |
|---|---|---|
| May `user` lose sudo? | the task's lockdown step (track C's build flag) | `stm-lockdown` per task instead of a revoked image: without the step `user` can read the private directory, so the seed is not sealed. No answer needed to build; whether the step is allowed is theirs. |
| Can a private value reach the guest without the public task JSON? | default: `tasks.json` is baked into the image (0600, owner `stm`); `--no-tasks` (`STM_NO_TASKS=1`) bakes none | Baking is the default: the runner's README says `private/` is not an access-control boundary and the published dataset includes answer keys, so seeds in the image expose nothing the dataset does not. What is defended is the agent during a run: the 0700/0600 modes plus the per-task `stm-lockdown`. With `--no-tasks` the list has to be placed in `/var/lib/stm-bench/private/tasks.json` (owner `stm`, 0600) at task start; the launcher re-reads it on every request. Because nothing is revoked at build time, `osci vm bake` can still power the guest off with `sudo shutdown`. |
| May the grader run as a Docker image on the host? | `build_grader.sh`; if not, `STMBENCH_GRADER_CMD` (`deploy/osworld/README.md`) | a Python 3.13 environment with the same pins instead of the image |
| Will they build the qcow2? | this directory | `build_image.sh` is the whole recipe |
| Which domain? | `domain: stm` in the snapshot entry, `data/stm/...` | one variable |

## On a shared (unified) image

`install.sh` and `stm_setup.py` assume no fresh base: the account, directories, venv and files are created or replaced in
place, the service is restarted, nothing is removed that was not put there by this installer. It adds `/opt/stm-bench`,
`/var/lib/stm-bench`, `/run/stm-bench`, `stm-start`, `stm-bench-reset`, `stm-lockdown`, one enabled service and one
autostart entry (`xhost +SI:localuser:stm`: the service account may draw on the session's display, which no other
software needs). `apt-get install` adds missing libraries (no upgrades of installed packages are requested). Not
tested on a unified image: none was available.

## Does anything after the config steps use sudo in the guest? (runner sources read 2026-11-16)

Not in `src/osworld_science/harness/` (stage, collect), `guest/` (client, controller, screenshot) or `runner/`: result
files are pulled through the control plane's `/file` and screenshots through `/execute`, both as `user` without sudo.
`sudo` appears in `vm/bake.py` (`sudo -S shutdown`, build time only), in provisioners and in two pre-task hooks of
other domains (`astro/prepare_episode.py`, `biomed/install_mnova_license.py`, which run before the task's config on
their own snapshots), and in the agents' prompts (`prompt_agent` and `claude_cua` tell the model that the password is
`password` for sudo; after the lockdown that password no longer works, which the model may try and fail). Whether the
control plane server itself runs as root or as `user` could not be read; the code above does not call sudo.

## Tests that ran, and what stays untested

See the report that came with this directory; in short, run: the launcher and `stm-start` unit tests
(`tests/test_osworld_guest.py` in the repository, about 60 tests, including the real console offscreen, `stm-lockdown` with a fake `visudo` and the FIFO wait); `install.sh` in a
rootless `ubuntu:22.04` container (offline, one uid mapped); the console under Xvfb 1920x1080 with openbox, started
through the launcher by `stm-start`, screenshot and export checked; the grader image built and run.
Not run: the OSWorld base image, GNOME (mutter) and its autostart, systemd and `User=stm`, real file ownership and
the denial of the private directory to `user`, the control plane's `/execute` and screenshot path, `common/guest_run.py`,
`osci vm bake`, Docker (the image was run with podman and `--user 0`, because a one-uid user namespace cannot start a
container as uid 10001), `stm_setup.py` end to end. The repository's workflow `.github/workflows/osworld-guest.yml`
(manual trigger; it ran through on `ubuntu-22.04`: install, lockdown, console start, permissions, grade) covers the systemd, ownership and permission parts on an `ubuntu-22.04` runner.
