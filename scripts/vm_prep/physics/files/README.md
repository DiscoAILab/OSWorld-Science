# Guest image `osworld-openfoam-guest:v2512-pv5.11.2` (revision r3, 2026-09-12)

Native-container Xfce desktop for the OSWorld-Science OpenFOAM benchmark (docs/DESIGN.md §2). One container
per task; the harness talks to the OSWorld guest server on `<container_ip>:5000` over an `--internal` network.

## What is in it

| Layer | Content |
|---|---|
| Base | `opencfd/openfoam-default:2512` — Ubuntu 24.04.3, OpenFOAM ESI **v2512** (`/usr/lib/openfoam/openfoam2512`, 549 tutorials, Open MPI 4.1.6, gcc 13, wmake) |
| Desktop | Xfce 4.18 on **Xvfb :0 1920x1080x24** (`-ac`), xfwm4, xfce4-panel, xfdesktop, Thunar, mousepad, xfce4-terminal, appfinder, taskmanager; elementary-xfce + Greybird themes (icons render); x11vnc :5900 + noVNC :8006 for humans |
| ParaView | **5.11.2** official binary in `/opt/paraview` (`paraview`, `pvpython`, `pvbatch` on PATH, software GL). Menu/desktop/dock launcher; `.foam` / `.OpenFOAM` MIME (`application/x-openfoam`) opens ParaView from Thunar; Welcome dialog pre-dismissed for `user` (`~/.config/ParaView/ParaView5.11.2.ini`) |
| Plotting / Python | gnuplot 6.0 (**gnuplot-qt**: qt, wxt, x11, png, pngcairo, svg, pdfcairo, dumb — `foamMonitor` works); system python3 3.12 with numpy, **scipy, matplotlib, pandas**, pyautogui 0.9.54, Pillow, Xlib, tkinter. `MPLBACKEND=Agg` everywhere (r3) so `plt.show()` never blocks a headless caller |
| Viewers | ristretto (images), atril (PDF), **tumbler + tumbler-plugins-extra** (thumbnails; r3), xdg-utils (`xdg-open`), `gio`; defaults in `/etc/xdg/mimeapps.list`. Note: GLib reports 0-byte files as `application/x-zerosize` before glob matching, so that type is also mapped to ParaView (the `.foam` stub is empty by convention); any other empty file double-clicked in Thunar therefore opens ParaView too. `application/x-vtk` (r3: `*.vtk *.vtu *.vtp *.vti *.vtm *.vtr *.vts *.pvtu *.pvd`) is mapped to ParaView too, so `foamToVTK` output does not open in Mousepad. Thunar right-click also has "Open with ParaView" for `*.foam`/VTK files (`/etc/xdg/Thunar/uca.xml`) |
| CLI | git, vim, nano, less, jq, tree, htop, file, unzip/zip, bzip2, xz, `ip`, `ping`, GNU `time`, bc, curl, rsync, ssh client, xdotool, wmctrl, xclip, scrot |
| Guest server | `/home/user/server` — vendored xlang-ai/OSWorld `desktop_env/server` (see `guest_server/ORIGIN.md`, `PATCHES.md`); Flask on :5000 as `user`; `python` -> `/usr/bin/python3` |
| Accounts | `user` / `password`, passwordless sudo, `~/work` for task files; `.bashrc` sources the OpenFOAM env and the shell-history instrumentation (`HISTFILE`, `histappend`, `HISTTIMEFORMAT`, `PROMPT_COMMAND=history -a`) |
| Process tree | `entrypoint.sh` (runtime dirs, xauth cookie for :0) -> supervisord: xvfb, xfce (dbus-launch startxfce4), x11vnc, novnc, guest_server |

Deliberately absent: web browser, mail client, LibreOffice, CJK fonts, cmake/gdb/strace. `xfce4-web-browser`,
`xfce4-mail-reader`, `xfce4-session-logout` (Log Out) and `x11vnc` menu entries are hidden (`NoDisplay=true`) and
the panel's Web Browser launcher was replaced by ParaView, so the agent never meets a dead launcher.

Because URLs can still be reached from inside applications (help menus, `xdg-open`), r3 makes URL opening fail
*quietly* instead of raising Xfce's untitled "Failed to execute default Web Browser. Input/output error." modal:

- `/usr/local/bin/osworld-no-browser` prints one line to stderr and exits 1;
- it is the `http`/`https`/`ftp`/`mailto`/`about` scheme handler in `/etc/xdg/mimeapps.list`
  (`osworld-no-browser.desktop`), which covers GIO / `gtk_show_uri` / in-app "Read Online" links;
- `/etc/xdg/xfce4/helpers.rc` points `WebBrowser` and `MailReader` at `osworld-no-browser-xfce-helper`, a wrapper
  that calls the above and then **exits 0 on purpose**: `xfce4-mime-helper` waits for the helper process and
  raises that modal (and makes `exo-open` block until it is dismissed) if the helper exits non-zero. Consequence:
  `xdg-open https://…` exits **0** while printing the explanatory line — nothing appears on screen.

## Known limitations

- **No network** at run time (`docker network create --internal`): `apt`, `pip`, `curl` fail; `apt-get update`
  prints warnings but exits 0. Everything the agent needs must be baked in.
- `--cpus` alone does not change `nproc`; the harness adds `--cpuset-cpus` (harness/env.py `pick_cpuset`) so
  `nproc` matches the requested core count. Manual `docker run` should pass both.
- pyautogui types `<` as `>` (X11 keycode 94 mapping; operator-side patch). `xdotool type` is correct.
- GUI programs must be started via `POST /setup/launch`; `/execute` blocks until the process exits (120 s cap).
  Since r3 the child's stdin is `/dev/null` (`guest_server/PATCHES.md`), so commands that read stdin return
  immediately instead of hanging for the full 120 s; non-interactive shells also get `PAGER=cat`
  (interactive terminals keep a real pager). gnuplot 6.0.0 honours `PAGER`, not `GNUPLOT_PAGER`.
- `pvbatch`/`pvpython` rendering needs `DISPLAY=:0` (`docker exec` sessions do not have it by default).
- The base image's `/etc/profile.d/openfoam-99run.sh` redirects `XDG_*_HOME` to `/tmp/.home.$USER` when
  `DISPLAY` is set; `/etc/profile.d/00-osworld-xdg.sh` pins them back to `$HOME` first, so all config lives
  in `~/.config`.
- Xvfb runs with `-ac`; the xauth cookie only exists to silence python-xlib. Not a security boundary.
- The Xfce panel config also references pulseaudio/power-manager plugins that are not installed (harmless).

## Known quirks

Not bugs — behaviours an agent (or a task author) can trip over. Keep them in mind when writing task
statements and operator hints.

- **Any empty file double-clicks into ParaView (~25 s cold start).** GLib reports every 0-byte regular file as
  `application/x-zerosize` *before* glob matching, and the canonical `.foam` stub is empty, so that type must be
  mapped to ParaView. Side effect: `touch notes.txt`, `: > empty.csv`, and Thunar's "Create Text Document…"
  produce files whose double-click starts ParaView and opens them with the delimited-text reader (empty
  SpreadSheetView after Apply, no error message). Do not rely on the double-click behaviour of empty files.
- **ParaView exposes no AT-SPI nodes.** `GET /accessibility` returns a usable GTK tree for the Xfce side
  (panel, Thunar, dialogs) but ParaView's Qt5 window contributes no application node. Irrelevant while the
  operator observes by screenshot (`agent/ours_agent_loop.py`, `observation_type="screenshot"`); if the
  observation mode ever switches to a11y, half the interface will be invisible and ParaView would need an
  AT-SPI bridge (`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` or equivalent).
- **v2512's cavity tutorial ships `system/decomposeParDict` with `numberOfSubdomains 9`** (`method
  hierarchical`). A documented `decomposePar + mpirun -np 2 icoFoam -parallel` recipe therefore fails on a
  2-core container with `"system/decomposeParDict" specifies 9 processors but job was started with 2 ranks`.
  Any such recipe must override the count *and* the `hierarchical` coeffs (their product must match), verified
  on r3 with a 2-core container:

  ```bash
  foamDictionary system/decomposeParDict -entry numberOfSubdomains -set 2
  foamDictionary system/decomposeParDict -entry coeffs/n -set '(2 1 1)'
  decomposePar -force && mpirun --oversubscribe -np 2 icoFoam -parallel && reconstructPar -latestTime
  ```
- `htop` and `free` show the **host's** CPU/memory (no cgroup awareness), even though `nproc` is correct when
  the container is started with `--cpuset-cpus`. An agent planning parallelism from `htop` will over-subscribe.
- `pyautogui.doubleClick()` with the default `interval=0` is often seen by Thunar as a single select; use
  `interval>=0.15` or "select + Enter". A correct MIME association can look broken otherwise.

## Rebuild

```bash
# needs /opt/dlami/nvme/openfoam/downloads/ParaView-5.11.2-MPI-Linux-Python3.9-x86_64.tar.gz (584 MB)
env/build.sh osworld-openfoam-guest:v2512-pv5.11.2-rN       # build under a NEW tag first
docker builder prune -af                                     # root disk is small; drop build cache
```
**Root disk budget (38 GB, ~13 GB free with one guest image around):** one full build needs ~9 GB — ~4.7 GB of
new snapshots plus about as much BuildKit cache. `docker builder prune -af` between two attempts is a trap: it
forces every `RUN` to re-execute, so the second build needs the full 9 GB again and dies half-way through
unpacking ParaView with `no space left on device` (leaving the new tag pointing at a broken image). Either keep
the cache until you are done, or `docker rmi` the previous rN first. Package additions belong in the *late* apt
layer (after the ParaView step) so the 1.85 GB ParaView layer stays cached.
Then verify (below), re-tag (`docker tag osworld-openfoam-guest:v2512-pv5.11.2-rN osworld-openfoam-guest:v2512-pv5.11.2`)
and only afterwards `docker rmi` the superseded image id. Other agents may hold containers on the old id.
The build uses BuildKit (`RUN --mount=type=bind` for the ParaView tarball, one 1.85 GB layer instead of two).

## Verify

```bash
python3 harness/tests/smoke_env.py --image osworld-openfoam-guest:v2512-pv5.11.2-rN   # all 9 PASS
G=$(docker run -d --network of-internal --cpus 4 --cpuset-cpus 0-3 --shm-size 2g osworld-openfoam-guest:v2512-pv5.11.2-rN)
IP=$(docker inspect $G --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); sleep 15
X() { curl -s -X POST -H 'Content-Type: application/json' -d "{\"command\":[\"bash\",\"-lc\",\"$1\"]}" http://$IP:5000/execute; echo; }
X 'nproc; python -c "import scipy, matplotlib, pandas; print(scipy.__version__)"; gnuplot -e "set term" 2>&1 | grep -E "^ *(qt|x11) "'
X 'python3 -c "import matplotlib; print(matplotlib.get_backend())"'                 # agg
X 'python3 -c "
import matplotlib.pyplot as plt; plt.plot([0,1]); plt.savefig(\"/tmp/a.png\"); plt.show(); print(\"ok\")"'  # returns at once
X 'xdg-open https://example.com; echo rc=$?'                                        # rc=0, one stderr line, no window
X 'timeout 5 cat; echo rc=$?'                                                       # rc=0 (stdin is /dev/null)
X 'dpkg -l | grep -c tumbler; xdg-mime query default application/x-vtk'             # 4; paraview.desktop
X 'python -c "import pyautogui; print(pyautogui.position())" 2>&1'            # no Xlib.xauth warning
X 'python3 -c "import socket; s=socket.socket(); s.settimeout(3); print(s.connect_ex((\"172.18.0.1\",22)))"'   # != 0 (host hardened)
curl -s -o /dev/null -w '%{http_code}\n' http://$IP:5000/accessibility           # 200
curl -s http://$IP:5000/screenshot -o /tmp/shot.png                              # icons present, no globe in dock
docker rm -f $G
```
