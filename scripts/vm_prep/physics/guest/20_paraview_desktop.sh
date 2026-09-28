#!/usr/bin/env bash
# ubuntu_openfoam guest stage 2 — ParaView 5.11.2 and the desktop side of env/Dockerfile (r3), GNOME edition.
set -euo pipefail
F=/home/user/.osci_physics_files
TAR=/home/user/paraview.tar.gz
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "--- ParaView 5.11.2 official binary -> /opt/paraview ---"
[ -s "$TAR" ] || { echo "missing $TAR"; exit 1; }
echo "36d2cb9a2b36ce3a57c154f81fc66233  $TAR" | md5sum -c -
sudo rm -rf /opt/paraview && sudo mkdir -p /opt/paraview
sudo tar --strip-components=1 -C /opt/paraview -xzf "$TAR"
rm -f "$TAR"
for b in paraview pvpython pvbatch pvserver; do sudo ln -sf /opt/paraview/bin/$b /usr/local/bin/$b; done
sudo cp -r /opt/paraview/share/icons/hicolor/. /usr/share/icons/hicolor/ && sudo gtk-update-icon-cache -f -q /usr/share/icons/hicolor || true
/opt/paraview/bin/pvpython -c "import paraview; print('pvpython ok', paraview.__version__ if hasattr(paraview,'__version__') else '')" 2>&1 | tail -1

say "--- menu entry, MIME types (.foam / VTK / empty stub -> ParaView), URL schemes -> no-browser ---"
sudo install -m 0644 $F/desktop/paraview.desktop /usr/share/applications/paraview.desktop
sudo install -m 0644 $F/desktop/openfoam-mime.xml /usr/share/mime/packages/openfoam.xml
sudo install -m 0755 $F/bin/osworld-no-browser /usr/local/bin/osworld-no-browser
sudo install -m 0644 $F/desktop/osworld-no-browser.desktop /usr/share/applications/osworld-no-browser.desktop
sudo update-mime-database /usr/share/mime >/dev/null 2>&1
sudo update-desktop-database /usr/share/applications >/dev/null 2>&1
mkdir -p /home/user/.config /home/user/.local/share/applications
python3 - <<'PY'
# user-level mimeapps.list: our associations first in [Default Applications]; the base's VLC/other lines stay.
import pathlib, re
p = pathlib.Path('/home/user/.config/mimeapps.list')
ours = {
 'application/x-openfoam': 'paraview.desktop', 'application/x-vtk': 'paraview.desktop',
 'application/vnd.kitware.paraview.state': 'paraview.desktop', 'application/x-zerosize': 'paraview.desktop',
 'x-scheme-handler/http': 'osworld-no-browser.desktop', 'x-scheme-handler/https': 'osworld-no-browser.desktop',
 'x-scheme-handler/ftp': 'osworld-no-browser.desktop', 'x-scheme-handler/mailto': 'osworld-no-browser.desktop',
 'x-scheme-handler/about': 'osworld-no-browser.desktop',
 'image/png': 'org.gnome.eog.desktop', 'image/jpeg': 'org.gnome.eog.desktop', 'image/svg+xml': 'org.gnome.eog.desktop',
 'application/pdf': 'org.gnome.Evince.desktop', 'text/plain': 'org.gnome.gedit.desktop',
}
text = p.read_text() if p.exists() else ''
lines = [l for l in text.splitlines() if not any(l.startswith(k + '=') for k in ours)]
if '[Default Applications]' not in lines:
    lines.insert(0, '[Default Applications]')
i = lines.index('[Default Applications]') + 1
for k, v in ours.items():
    lines.insert(i, f'{k}={v}'); i += 1
p.write_text('\n'.join(lines) + '\n'); print('mimeapps.list updated')
PY
xdg-settings set default-web-browser osworld-no-browser.desktop 2>/dev/null || true
xdg-mime query default application/x-openfoam; xdg-mime query default x-scheme-handler/https

say "--- hide the dead ends of an offline image (browsers, mail, store, help), keep them installed ---"
for d in google-chrome.desktop firefox_firefox.desktop firefox.desktop thunderbird.desktop snap-store_ubuntu-software.desktop \
         snap-store_snap-store.desktop yelp.desktop update-manager.desktop software-properties-gtk.desktop \
         software-properties-drivers.desktop gnome-initial-setup.desktop; do
  printf '[Desktop Entry]\nType=Application\nName=%s\nHidden=true\nNoDisplay=true\n' "${d%.desktop}" > /home/user/.local/share/applications/$d
done
update-desktop-database /home/user/.local/share/applications >/dev/null 2>&1 || true

say "--- GNOME dash: Terminal, Files, ParaView, gedit, VS Code (no browser launcher) ---"
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}
gsettings set org.gnome.shell favorite-apps "['org.gnome.Terminal.desktop', 'org.gnome.Nautilus.desktop', 'paraview.desktop', 'org.gnome.gedit.desktop', 'code.desktop']"
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.shell.extensions.ding show-home false 2>/dev/null || true
gsettings get org.gnome.shell favorite-apps

say "--- ParaView first run: welcome dialog off; desktop shortcuts; ~/work ---"
mkdir -p /home/user/.config/ParaView /home/user/work /home/user/Desktop
install -m 0644 $F/desktop/ParaView5.11.2.ini /home/user/.config/ParaView/ParaView5.11.2.ini
cp /usr/share/applications/paraview.desktop /home/user/Desktop/ParaView.desktop
cp /usr/share/applications/org.gnome.Terminal.desktop /home/user/Desktop/Terminal.desktop
chmod +x /home/user/Desktop/*.desktop
for f in /home/user/Desktop/*.desktop; do gio set "$f" metadata::trusted true 2>/dev/null || true; done

say "--- ~/.bashrc: OpenFOAM env, ParaView, history instrumentation, non-blocking defaults (top of file, before the interactive guard) ---"
python3 - <<'PY'
import pathlib
p = pathlib.Path('/home/user/.bashrc'); s = p.read_text()
block = """# --- osworld-openfoam guest (VM edition; same content as the r3 container's ~/.bashrc block) ---
[ -f /usr/lib/openfoam/openfoam2512/etc/bashrc ] && [ -z "${WM_PROJECT_DIR:-}" ] && source /usr/lib/openfoam/openfoam2512/etc/bashrc
export LIBGL_ALWAYS_SOFTWARE=1
case ":$PATH:" in *:/opt/paraview/bin:*) ;; *) export PATH=$PATH:/opt/paraview/bin ;; esac
# shell history instrumentation (same block harness/runner.py appends at setup; idempotent)
export HISTFILE="$HOME/.bash_history"
shopt -s histappend
export HISTTIMEFORMAT="%F %T "
export HISTSIZE=100000 HISTFILESIZE=200000
export PROMPT_COMMAND="history -a${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
export MPLBACKEND="${MPLBACKEND:-Agg}"
export GNUPLOT_PAGER="${GNUPLOT_PAGER:-cat}"
# --- end osworld-openfoam block ---
"""
if 'osworld-openfoam guest' not in s:
    p.write_text(block + s); print('.bashrc block prepended')
else:
    print('.bashrc already has the block')
PY
say "stage 2 done"
