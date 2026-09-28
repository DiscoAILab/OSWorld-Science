# physics — `ubuntu_openfoam`: the r3 OpenFOAM container rebuilt as a KVM virtual machine

The OSWorld-Science OpenFOAM tasks (OF-001 … OF-018) were run, on the original host without KVM, in a
**native Docker desktop container** (`osworld-openfoam-guest:v2512-pv5.11.2`, revision r3: Ubuntu 24.04 +
Xfce on Xvfb + OpenFOAM ESI v2512 + ParaView 5.11.2 + the OSWorld guest server). `files/` is that
container's complete build context (Dockerfile, README, helper scripts, desktop files, the ParaView
tarball), pulled from the `daipath/openfoam` archive (`vm/build_ctx/`).

`ubuntu_openfoam.qcow2` is that container as a **QEMU/KVM virtual machine**, built from the Ubuntu 24.04
cloud image by `xfce/install_xfce_openfoam.sh` — a line-by-line port of the Dockerfile — so that `osci`
(`happysixd/osworld-docker` + read-only qcow2) can run it like every other domain.

```
xfce/build_xfce_image.sh          cloud image → seed → KVM boot → install → reboot → verify → power off → flatten
xfce/install_xfce_openfoam.sh     what runs inside the VM (the Dockerfile, step by step)
guest/30_verify_xfce.sh           36 PASS/FAIL checks through `bash -lc` (binary identity, desktop, tools, leak-free state)
verify_image.sh --port 5100       acceptance from a FRESH osci container + volume (never the build VM)
provision_guest.sh + guest/1*.sh  the earlier GNOME/22.04 attempt (`ubuntu_openfoam_gnome22`, kept for reference)
```

## Parity with the r3 container (verified, not assumed)

| Item | r3 container | `ubuntu_openfoam` VM | Check |
|---|---|---|---|
| OS | Ubuntu 24.04.3 | Ubuntu 24.04.5 (cloud image) | `30_verify_xfce.sh` |
| OpenFOAM | `openfoam2512 2512.0-1` (noble deb inside `opencfd/openfoam-default:2512`), gcc 13.3.0 | the same noble deb, **pinned and held at 2512.0-1** | `simpleFoam` md5 `d66bb581…` and `libfiniteVolume.so` md5 `5715ef87…` identical |
| ParaView | 5.11.2 official tarball → `/opt/paraview` | same tarball (md5 `36d2cb9a…`), same paths | md5 + `pvpython` render |
| Desktop | Xfce 4.18 on Xvfb, 1920×1080, elementary-xfce + Greybird | Xfce 4.18 on **Xorg + LightDM autologin**, 1920×1080 (`Virtual-1`, virtio GPU) | `xdpyinfo`, `wmctrl -m` |
| Panel / menu / desktop | ParaView launcher instead of Web Browser; web-browser, mail, Log Out, x11vnc entries hidden; Terminal + ParaView on the desktop | same files (`/etc/xdg/xfce4/panel/default.xml`, `helpers.rc`, `NoDisplay=true`, `~/Desktop/*.desktop`) | grep + screenshot |
| No browser / no mailer | `osworld-no-browser` scheme handler + `xfce4-mime-helper` wrapper that exits 0 | same | `xdg-open`/`exo-open` return at once, no modal |
| MIME | `.foam`/VTK/empty file → ParaView; images → Ristretto; PDF → Atril | same | `xdg-mime query` |
| Python | system python3 with numpy/scipy/matplotlib/pandas/tk, pyautogui 0.9.54, `python` → python3 | same (flask/lxml/requests/pillow/xlib from the noble archive instead of pip) | import checks |
| Guest server | vendored OSWorld server, `/execute` with `stdin=/dev/null`, `MPLBACKEND=Agg`, `PAGER=cat` | same code, as `osworld.service` (systemd, after LightDM) | `cat` returns at once, `plt.show()` returns at once |
| Account | `user`/`password`, passwordless sudo, `~/work`, `.bashrc` with the history instrumentation | same | |
| Hygiene | no network at run time | no unattended-upgrades, no snapd, no cloud-init after first boot, `fstrim.timer` masked; network is a run-time property of the container | |

What is inherently different: a real kernel (6.8) and real 8 GB of RAM instead of the host's; `x11vnc`/noVNC
are not inside the guest (the `happysixd/osworld-docker` container serves the console on port+3006); the
desktop is driven by Xorg on a virtual GPU instead of Xvfb.

## Leak channels to reset between tasks (bake_cleanup in configs/snapshots.yaml)

`~/work` (the whole previous case), `~/.bash_history` (deliberately complete: audit instrumentation),
ParaView's `ParaView-UserSettings.json` and the recent-files list in `ParaView5.11.2.ini`, thumbnails, Xfce session cache.
Inspect them **before** running any tool (`foamDictionary`/`paraview` write `~/.config`).

## Rebuild / verify

```
scripts/vm_prep/physics/xfce/build_xfce_image.sh            # ~25 min; needs KVM, qemu-img, genisoimage, outbound network
scripts/vm_prep/physics/verify_image.sh --port 5100          # cold start via osci, leak check, tune2fs, 36 checks, screenshot
```
