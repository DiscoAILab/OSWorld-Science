# astro: the `ubuntu_astro` image (CIAO + SAOImage DS9)

Three Chandra tasks run on `ubuntu_astro`: background-subtracted net counts of
a quasar, a source light curve, and a readout-streak measurement, each done in
DS9 with its DAX analysis menu (CIAO tools) and written into a spreadsheet.

## What the image contains

* The OSWorld Ubuntu 22.04 base at 1920×1080.
* Miniconda3 with the build-locked env `ciao-4.18` (CIAO 4.18.0, CALDB 4.12.4,
  ciao-contrib) and SAOImage DS9 8.7b2 with DAX. `~/.bashrc` activates the env
  for interactive shells only, which is why the snapshot entry carries a
  `shell_init` for the non-interactive probes.
* `/home/user/CIAO_001/13858/`: the ObsID 13858 products, the baseline image
  that DS9 opens at the start of every episode (the netcounts task's input;
  its staging re-uploads the same file, sha256-checked).
* `/home/user/start_ds9.sh` and two desktop launchers ("CIAO Terminal",
  "SAOImageDS9 (CIAO)"). The baked start script is replaced per episode, see
  below.
* `fstrim.timer` masked on purpose. Never `apt purge`/`autoremove` in this
  image (the base's boot chain breaks).

The image was built by the task authors; no build script is available here.
Rebake with `osci vm bake` after changes; `bake_cleanup` in
`configs/snapshots.yaml` keeps `CIAO_001/13858` and scrubs everything else.

## Per-episode initial state: `prepare_episode.py`

The tasks assume DS9 is already open in a defined state, and the desktop has
some run-time traps. None of this survives a container reset, so the snapshot
declares `prepare_episode.py` as a required pre-task hook; `osci run` runs it
between the reset and the staging steps. When staging by hand:

```bash
uv run osci vm reset --snapshot ubuntu_astro --port 5090
uv run python scripts/vm_prep/astro/prepare_episode.py --port 5090
uv run osci stage chandra-ds9-lightcurve-4479-01 --port 5090
```

The hook trusts the desktop launchers, silences the Snap Store update pop-up,
scrubs the CIAO parameter directory (`~/cxcds_param4`, which keeps the last
command line of every tool, i.e. an answer if the harness ever ran one) and
the DS9 auto-backup, installs `start_ds9.sh` over the baked copy and runs it,
then injects a Tcl guard so that DS9's + and - keys no longer raise a modal
"dcube" error on 2-D images (a run-time deviation from stock DS9; say so when
writing up runs). It exits non-zero unless DS9 is showing the baseline image.

`start_ds9.sh` differs from the baked script in two ways that matter for
grading: it restores whatever DS9 was showing instead of always reopening the
task-1 image (an agent mis-clicking the desktop icon during another task would
otherwise get the wrong image and see task 1's file name), and it empties
`/tmp/ds9_dax.user` instead of deleting it (dax never recreates the directory;
deleting it disables dax for the whole session). Each task's last staging step
records the file DS9 shows in `~/.ds9_current_file` for the same reason.

## Leak channels

`~/cxcds_param4/*.par` (tool parameters incl. region strings), `~/ds9.auto*`
(the previous session's regions, offered for restore after a kill), `~/.ds9`,
`~/.ds9_current_file`, `/tmp/ds9_dax.user/*` (DAX outputs). The hook scrubs
them per episode and `bake_cleanup` before baking.
