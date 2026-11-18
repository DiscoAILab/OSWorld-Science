# econ_gui — `ubuntu_econ_gui`: the GUI-only economics desktop

The econ research-software tasks ECON-040 … ECON-059 measure how an agent operates scientific software through its
graphical interface: seasonal adjustment in **JDemetra+ 3.9.0**, spatial statistics in **GeoDa 1.22.1**, investment
performance in **Portfolio Performance 0.89.0**, agent-based simulation in **NetLogo 7.0.4**, and **LibreOffice Calc**
for the answer sheets. Deliverables are files the programs save themselves (JDemetra+ workspaces, GeoDa weights and
tables, a Portfolio Performance file, a BehaviorSpace table); the graders re-open or re-estimate them with the same
programs' engines.

The tasks were developed and run in a Docker desktop container on Ubuntu 24.04 + Xfce (Xvfb, 1280x720), built from
the files in `files/standin/`. `ubuntu_econ_gui.qcow2` is that container as a QEMU/KVM virtual machine, built from the
Ubuntu 24.04 cloud image the same way `physics/xfce/` builds `ubuntu_openfoam`:

```
build_image.sh            cloud image → seed → KVM boot → install → reboot → verify → power off → flatten
install_econ_gui.sh       what runs inside the VM (STANDIN=1 runs the package steps in a plain ubuntu:24.04 container)
guest/30_verify.sh        PASS/FAIL checks through `bash -lc` (programs, lock-down, grader side, desktop, guest server)
files/grader/PPRef*.java  Portfolio Performance reference/grader classes (compiled in the image against PP's jars)
files/registrymodifications.xcu   LibreOffice profile: no first-run/tip/keep-format/recovery dialogs, macros off
files/standin/            the stand-in container the tasks were run on (Dockerfile.base → .gui → .pp → .pp2/.pp3)
```

`files/standin/` is kept for provenance, as the contributor used it (local package mirrors, installers next to the
Dockerfiles). The stand-in's NetLogo layer (ECON-045) did what section 6 of `install_econ_gui.sh` does; its Dockerfile
is not included.

`build_image.sh` also takes the guest server and the no-browser URL handler from `physics/files/`.

## Installers (pinned, sha256-checked)

| Program | File | sha256 |
|---|---|---|
| JDemetra+ 3.9.0 desktop | `jdplus-main-desktop-bin-3.9.0.zip` (Maven Central `eu.europa.ec.joinup.sat`) | `11670b65…a42b123a` |
| jwsacruncher 3.9.0 | `jdplus-main-cli-bin-3.9.0.zip` (Maven Central) | `4c23ccbc…99fcda` |
| GeoDa 1.22.1 | `geoda_1.22.1-1noble1_amd64.deb` (GitHub release) | `5ee442fd…6cf989` |
| Portfolio Performance 0.89.0 | `PortfolioPerformance-0.89.0-linux.gtk.x86_64.tar.gz` (GitHub release) | `01ee66de…750b322c` |
| NetLogo 7.0.4 | `NetLogo-7.0.4-64.tgz` (GitHub release) | `75636b84…702dfbc` |

## GUI only

A solver with a shell can skip the GUI entirely. In the tasks' development a model given a terminal compiled a Java
program against JDemetra+'s jars and wrote the workspace without opening JDemetra+. So the image leaves no route from
the desktop to a command line:

| Layer | What the image does |
|---|---|
| Desktop | no terminal emulator, no run dialog (xfce4-appfinder), no file manager (Thunar has custom actions), no `exo-open` / preferred-applications chooser (it accepts a custom command), no settings manager or keyboard-shortcut editor (a custom shortcut runs a command), no keyboard shortcut bound to a command, Xfce kiosk mode for the panel; URLs go to a no-op handler (no browser in the image) |
| Programs | JDemetra+ and Portfolio Performance run on `/opt/jre-jd`, a jlink'd Java 21 without `jdk.compiler` (javac), `jdk.jshell` or `jdk.attach` (`java.compiler`, the `javax.tools` API that `java.se` requires, stays; without `jdk.compiler` it has no compiler to hand out); no JDK on the PATH; LibreOffice macros disabled (security level 3, execution off); NetLogo without the `py` and `sr` extensions (they start Python and R) and without its headless/console launchers and BehaviorSearch |
| Grader side | jwsacruncher 3.9.0 and the Portfolio Performance reference classes live in `/opt/grader` (root:root 0700). The tasks' `vm_command_line` results reach them with `sudo -n`, so `user` keeps passwordless sudo as in every OSWorld image; no desktop program can open a shell to use it |
| Action channel | **runner side, not in this image**: the tasks declare `"gui_only": true`; the agent's actions must be restricted to mouse and keyboard (pyautogui mouse/keyboard calls and `time.sleep`; no other imports or builtins, no `RUN_COMMAND`). The task results were obtained with such a filter in the contributor's harness; see the pull request that adds this snapshot |

## Parity with the stand-in

| Item | Stand-in container (where the results were obtained) | `ubuntu_econ_gui` VM |
|---|---|---|
| OS / desktop | Ubuntu 24.04, Xfce on Xvfb, 1280x720 | Ubuntu 24.04 (cloud image), Xfce on Xorg + LightDM autologin, 1920x1080 like the other snapshots |
| Programs | the five installers above (GeoDa noble build), LibreOffice 24.2 from the noble archive | same files, same checksums |
| Java | jlink'd `/opt/jre-jd` (Java 21) without compiler/jshell | same module list |
| Grader engines | `/opt/grader`, root-only; the harness ran them as root | `/opt/grader`, root-only; reached with `sudo -n` |
| `user` sudo | removed (the harness used `docker exec -u root`) | passwordless, needed by the evaluator commands |
| Collection | `docker cp` of files and folders | the runner pulls single files, so the JDemetra+ tasks pack the workspace folder in the guest (`output/<task>.workspace.tgz`) |

Checked so far: `install_econ_gui.sh` with `STANDIN=1` runs through in a plain `ubuntu:24.04` amd64 container (every
package, the five sha256-checked installers, jlink, javac of the reference classes, the lock-down and its assertions),
and `guest/30_verify.sh` run there as `user` passes 23 of its 28 checks; the other five need the booted VM (Xfce
session on :0, 1920x1080 screen, masked fstrim.timer, hostname, `xdg-open` with a display — with `DISPLAY` set it
reaches the no-op handler). In that container the evaluator commands of ECON-053 and ECON-055 (jwsacruncher, both
policies) and ECON-042 (Portfolio Performance's engine), run as `user` on the reference submissions, grade PASS 1.0
through the runner's scorer. Not run: the KVM build itself (no KVM on the contributor's machine); its display, LightDM
and systemd steps are the ones `physics/xfce/install_xfce_openfoam.sh` uses.

## Rebuild / verify

```
scripts/vm_prep/econ_gui/build_image.sh            # needs KVM, qemu-img, genisoimage, outbound network
```

Leak channels between tasks (`bake_cleanup` in `configs/snapshots.yaml`): `~/work`, `~/assets`, JDemetra+'s
`~/.nbdemetra` and `~/.cache/nbdemetra`, `~/.PortfolioPerformance`, Java user preferences (NetLogo),
LibreOffice's recovery backup, the recent-files list, thumbnails.
