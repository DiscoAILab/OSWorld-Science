#!/usr/bin/env bash
# Run only in the disposable OSWorld guest. No task data or answers are installed.
# Uses the official pinned 5.7.2 Linux binary installer.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
UPDATED=0
for attempt in $(seq 1 60); do
  if echo password | sudo -S apt-get -o DPkg::Lock::Timeout=300 update -qq; then
    UPDATED=1
    break
  fi
  echo "Waiting for guest package manager/network ($attempt/60)"
  sleep 5
done
[ "$UPDATED" -eq 1 ] || { echo 'Guest apt update did not complete'; exit 1; }
echo password | sudo -S apt-get -o DPkg::Lock::Timeout=300 install -y \
  curl ca-certificates tar libgl1 libegl1 libopengl0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xinerama0 libxcb-xkb1 libdbus-1-3 libfontconfig1 wmctrl gedit patchelf
mkdir -p /home/user/profex-install
cd /home/user/profex-install
test -s profex.tar.gz
sha256sum profex.tar.gz > archive.sha256
tar xzf profex.tar.gz
mapfile -t INSTALLERS < <(find . -type f -name 'Profex-5.7.2-linux-x86_64.bin')
[ "${#INSTALLERS[@]}" -eq 1 ] || { echo 'Expected exactly one 5.7.2 installer'; exit 1; }
chmod +x "${INSTALLERS[0]}"
# Stop on unsupported CLI rather than silently guessing interactive button positions.
QT_QPA_PLATFORM=offscreen "${INSTALLERS[0]}" --help > installer-help.txt
grep -q 'accept-licenses' installer-help.txt
mkdir -p /tmp/profex-offline-repo
printf '%s\n' '<?xml version="1.0"?><Updates></Updates>' > /tmp/profex-offline-repo/Updates.xml
echo password | sudo -S env QT_QPA_PLATFORM=offscreen "${INSTALLERS[0]}" \
  --root /opt/Profex5 --set-temp-repository file:///tmp/profex-offline-repo \
  --accept-licenses --default-answer --confirm-command install
test -x /opt/Profex5/profex.sh
test -n "$(find /opt/Profex5 -type f -iname '*.str' -print -quit)"
test -n "$(find /opt/Profex5 -type f -iname 'bgmn*' -print -quit)"
python3 - <<'PY'
import xml.etree.ElementTree as E
packages = {p.findtext('Name'): p.findtext('Version')
            for p in E.parse('/opt/Profex5/components.xml').getroot().findall('Package')}
assert packages['Profex.Application'] == '5.7.2', packages
assert packages['BGMNwin.Application'] == '4.2.23d', packages
PY
echo password | sudo -S mkdir -p /opt/Profex5/compat
echo password | sudo -S tar xf runtime.tar -C /opt/Profex5/compat
for binary in /opt/Profex5/bin/profex /opt/Profex5/bin/profexsc \
    /opt/Profex5/bin/profexst /opt/Profex5/bin/profexed /opt/Profex5/bin/profexwp; do
  echo password | sudo -S patchelf --set-interpreter /opt/Profex5/compat/ld-linux-x86-64.so.2 \
    --force-rpath --set-rpath /opt/Profex5/compat:/opt/Profex5/lib:/opt/Profex5 "$binary"
done
echo password | sudo -S chown -R user:user /opt/Profex5/Structures
gsettings set org.gnome.desktop.notifications show-banners false
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false
echo password | sudo -S ln -sf /opt/Profex5/profex.sh /usr/local/bin/profex
cat > /tmp/profex.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Profex
Comment=X-ray powder diffraction phase identification
Exec=/usr/local/bin/profex %F
Terminal=false
Categories=Science;Education;
DESKTOP
echo password | sudo -S install -m 644 /tmp/profex.desktop /usr/share/applications/profex.desktop
echo password | sudo -S install -m 644 archive.sha256 /opt/Profex5/.osci-archive.sha256
mkdir -p /home/user/.config/doebelin.org
mkdir -p /home/user/.local/share/doebelin.org/Profex5/{Devices,Structures,Presets}
cat > /home/user/.config/doebelin.org/Profex5.conf <<'INI'
[bgmnProject]
bgmnInstallation=1
bgmnExec=/opt/Profex5/BGMNwin/bgmn
makegeqExec=/opt/Profex5/BGMNwin/makegeq
geometExec=/opt/Profex5/BGMNwin/geomet
teilExec=/opt/Profex5/BGMNwin/teil
eflechExec=/opt/Profex5/BGMNwin/eflech
deviceDatabase=/opt/Profex5/Devices, /home/user/.local/share/doebelin.org/Profex5/Devices
structureDatabaseIndexed=/opt/Profex5/Structures, /home/user/.local/share/doebelin.org/Profex5/Structures
presetDirectory=/opt/Profex5/Presets, /home/user/.local/share/doebelin.org/Profex5/Presets

[config]
indexHkl=-1
useLocalCodDb=false
INI
# These key names are from the official 5.7.2 source. Build the stock index
# before baking so the solver does not spend its budget in first-run dialogs.
DISPLAY=:0 setsid nohup /usr/local/bin/profex > /home/user/profex-index.log 2>&1 < /dev/null &
INDEXED=0
for attempt in $(seq 1 180); do
  sleep 5
  WINDOWS=$(DISPLAY=:0 wmctrl -lx)
  if grep -q 'Profex 5.7.2' <<< "$WINDOWS" && \
     ! grep -q 'Updating reference structure database' <<< "$WINDOWS" && \
     [ -s /home/user/.local/share/doebelin.org/Profex5/hklbufferV5.db3 ]; then
    INDEXED=1
    break
  fi
  echo "Building stock structure index ($attempt/180)"
done
[ "$INDEXED" -eq 1 ] || { echo 'Profex stock indexing failed or timed out'; exit 1; }
DISPLAY=:0 wmctrl -x -c profex.Profex5
sleep 3
echo password | sudo -S touch /opt/Profex5/.osci-install-ok
rm -rf /home/user/profex-install /tmp/profex.desktop /tmp/profex-offline-repo /home/user/profex-index.log
echo 'PROFEX_INSTALL_OK: pinned 5.7.2, private runtime, BGMN configured, stock index initialized; task GUI review still required.'
