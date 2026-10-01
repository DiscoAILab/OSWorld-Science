# VM preparation

Every guest starts from the official OSWorld Ubuntu image and gets its
domain software installed by the scripts here, then is flattened into a
self-contained qcow2 under `data/<domain>/vm/<snapshot>.qcow2` (git-ignored). The
runtime (`osci vm start|reset`) boots those images; `configs/snapshots.yaml`
maps a task's `snapshot` field to an image and to the provisioner that runs
if the image turns out to lack its toolchain (e.g. you booted the base).

```
scripts/vm_prep/
├── base/download_base.sh          Hugging Face xlangai/ubuntu_osworld → vm/base/Ubuntu.qcow2
├── common/guest_run.py            run a long script inside the guest, detached, poll the .done flag
├── common/build_image.sh          start-from-base → provision → bake (called by each domain)
├── astro/        prepare_episode.py (CIAO/DS9 pre-task state; image is stored under data/physics/vm)
├── stat/         provision_guest.sh (micromamba + statenv.lock), extras_guest.sh (growpart, RStudio,
│                 python packages, ImageMagick), restore_firefox_profile.py, snapshot_firefox_profile.sh,
│                 check_oda_home.py
├── physics/      provision_guest.sh (OpenFOAM + ParaView)
├── geoscience/   QGIS image verification helpers
├── radiology/    provision_guest.sh → weasis_setup.py + slicer_setup.py + slicer_firstrun.sh
├── linguistics/  provision_guest.sh → praat_setup.py
└── eeg/          provision_guest.sh (apt octave + octave-signal/statistics, git clone EEGLAB 2025.1.0 → /opt/eeglab)
```

The 10 EEGLAB tasks are published under `data/eeg`, but an
`ubuntu_eeglab.qcow2` image and reproducible `scripts/vm_prep/eeg/`
provisioner are not yet available. The Ansys recipe travels with the dataset
under `data/physics/vm/build_recipe/` because its Windows image cannot be
redistributed and is not supported by the current runtime backend.

Build an image:

```bash
scripts/vm_prep/base/download_base.sh
scripts/vm_prep/stat/build_image.sh          # ~30-60 min; port 5040 by default
scripts/vm_prep/radiology/build_image.sh     # port 5050
scripts/vm_prep/linguistics/build_image.sh   # port 5080
scripts/vm_prep/eeg/build_image.sh           # port 5150
```

Discipline: **reset, install, bake**. `osci vm bake` refuses a guest that
has served a task since it booted; never bake a guest an agent has used
(a pip-installed pydicom shipped in the radiology v2 image that way).

Known facts about the guest control plane the scripts work around:
`/execute` has a ~120 s server-side timeout (installers are detached via
`guest_run.py`), it runs `/bin/sh` (always `bash -lc`), a request body over
~50 KB fails (chunked upload), and `/setup/upload` moves large files in
seconds. The `user` account is in `sudo` with password `password`.

## The SAS route on the stat image

Nine stat tasks offer "SAS or R". The image ships no SAS (a licensed product that cannot be redistributed);
the route is SAS OnDemand for Academics in the guest's Firefox, and it needs
two things the image cannot carry.

Until 2026-09-26 the `ubuntu_stat.qcow2` that had been published (md5
`8faa893783f6c7dd0d752580d708aad0`, 34.1 GB) was not this scripted build but a
hand-baked predecessor that still carried a site-licensed SAS 9.4 install and
its build host's name. It has been withdrawn and replaced by the image
`stat/build_image.sh` produces (md5 `ab0e7b5e8d32b38e35bdfcf103511e63`,
27.0 GB, no SAS). A copy with the old checksum must not be used or passed on;
delete it and download the current one.

The ODA account is the operator's own and stays under the ODA licence
(personal, academic, noncommercial use; no sharing; no personal data in
uploads); `docs/user_guide/running.md` lists the rules.
`firefox_profile.tgz` (local, git-ignored) holds the saved password and the
pop-up permission for `welcome.oda.sas.com`. The `restore_firefox_profile.py`
hook unpacks it before any task whose `related_apps` contains `sas`; it is
optional, so without the archive the task simply runs R-only. The archive does
*not* carry a session — the guest comes up signed out and the agent signs
itself in, which is what happened in the 2026-09-01 sweep.

The ODA account is cloud-side and no guest reset touches it, so it accumulates
whatever previous runs uploaded. Confirm it is empty before a SAS sweep:

```bash
scripts/vm_prep/stat/check_oda_home.py --port 5040   # screenshots; you click
```

It never drives the page itself. The legacy attempt at that
(`reset_sas_session.py`) deleted one file where it meant to delete a range and
opened tabs where it meant to close them, both silently, because on this page
a click that lands on nothing returns success.

See docs/developer_guide/building-a-vm-snapshot.md for the full procedure and docs/user_guide/requirements.md for what the host needs.
