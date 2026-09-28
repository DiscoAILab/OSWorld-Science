#!/usr/bin/env bash
# ubuntu_openfoam (Xfce edition) — runs as root inside the Ubuntu 24.04 build VM.
# A line-by-line port of env/Dockerfile (osworld-openfoam-guest:v2512-pv5.11.2 r3) to a bootable VM:
#   * the same Ubuntu 24.04 + the same dl.openfoam.com NOBLE packages, PINNED to 2512.0-1 (the exact build
#     inside opencfd/openfoam-default:2512 — simpleFoam md5 d66bb581… is asserted below),
#   * the same Xfce package list, ParaView 5.11.2 tarball, python packages, CLI tools, desktop files,
#     no-browser handler, guest server (vendored + r3 patches),
#   * what a VM needs instead of Xvfb/supervisord: Xorg + LightDM autologin into Xfce on :0, the guest
#     server as a systemd service, generic DHCP netplan, no cloud-init/snapd/unattended-upgrades afterwards.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
F=/root/build_ctx
say() { echo "[$(date +%H:%M:%S)] $*"; }
APT="apt-get -o DPkg::Lock::Timeout=300 -y -q"

say "--- 0. wait for cloud-init, then stop it from ever running again ---"
cloud-init status --wait >/dev/null 2>&1 || true
touch /etc/cloud/cloud-init.disabled
hostnamectl set-hostname osworld 2>/dev/null || echo osworld > /etc/hostname
grep -q 'osworld' /etc/hosts || echo '127.0.1.1 osworld' >> /etc/hosts
cat > /etc/netplan/01-osworld.yaml <<'EON'
# any NIC, DHCP (QEMU user-mode network of happysixd/osworld-docker or a plain qemu run)
network:
  version: 2
  ethernets:
    all-en:
      match: {name: "e*"}
      dhcp4: true
      dhcp6: false
EON
rm -f /etc/netplan/50-cloud-init.yaml; chmod 600 /etc/netplan/01-osworld.yaml

say "--- 1. apt: the Dockerfile package list + Xorg/LightDM + extra kernel modules (drm for the virtual GPU) ---"
for i in 1 2 3 4 5 6; do $APT update >/dev/null 2>&1 && break; say "  apt update retry $i"; sleep 15; done
$APT install --no-install-recommends \
  linux-modules-extra-$(uname -r) \
  xserver-xorg xserver-xorg-video-all xserver-xorg-input-all xinit lightdm lightdm-gtk-greeter dbus-x11 at-spi2-core xauth \
  xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfconf xfce4-terminal thunar mousepad \
  xfce4-appfinder xfce4-taskmanager ristretto atril tumbler tumbler-plugins-extra xdg-utils shared-mime-info desktop-file-utils libglib2.0-bin \
  elementary-xfce-icon-theme greybird-gtk-theme tango-icon-theme adwaita-icon-theme fonts-dejavu-core \
  python3 python3-venv python3-pip python3-dev python3-tk python3-pyatspi python3-xlib python3-gi gir1.2-atspi-2.0 \
  python3-numpy python3-scipy python3-matplotlib python3-pandas \
  scrot xdotool wmctrl xclip x11-xserver-utils x11-utils \
  libgl1 libglu1-mesa libgl1-mesa-dri libegl1 libopengl0 libxrender1 libxt6 libxcursor1 libxkbcommon-x11-0 \
  libxcb-xinerama0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-xkb1 libxcb-xinput0 libxcb-util1 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-glx0 \
  libxi6 libsm6 libice6 libxrandr2 libxss1 libdbus-1-3 libfontconfig1 libfreetype6 libglib2.0-0t64 libxext6 libx11-xcb1 \
  sudo curl ca-certificates less nano vim bc gnuplot-qt procps psmisc \
  git unzip zip bzip2 xz-utils file jq tree htop iproute2 iputils-ping time rsync openssh-client gnupg 2>&1 | tail -2

say "--- 2. OpenFOAM v2512, noble build, pinned to 2512.0-1 (= opencfd/openfoam-default:2512) ---"
curl -sS --max-time 60 https://dl.openfoam.com/add-debian-repo.sh | bash 2>&1 | tail -2
$APT update >/dev/null 2>&1
$APT install openfoam2512-default=2512.0-1 openfoam2512-dev=2512.0-1 openfoam2512-tutorials=2512.0-1 \
             openfoam2512=2512.0-1 openfoam2512-common=2512.0-1 openfoam2512-tools=2512.0-1 openfoam2512-source=2512.0-1 2>&1 | tail -2
apt-mark hold openfoam2512-default openfoam2512-dev openfoam2512-tutorials openfoam2512 openfoam2512-common openfoam2512-tools openfoam2512-source >/dev/null
dpkg-query -W -f='${Package} ${Version}\n' openfoam2512 openfoam2512-common openfoam2512-dev openfoam2512-tutorials openfoam2512-tools openfoam2512-source
echo "d66bb581dd92389773d9a7583f36e555  /usr/lib/openfoam/openfoam2512/platforms/linux64GccDPInt32Opt/bin/simpleFoam" | md5sum -c -
echo "5715ef87e66f279f574e947236bfdca8  /usr/lib/openfoam/openfoam2512/platforms/linux64GccDPInt32Opt/lib/libfiniteVolume.so" | md5sum -c -

say "--- 3. ParaView 5.11.2 (official binary) -> /opt/paraview ---"
if [ ! -x /opt/paraview/bin/pvpython ]; then
  echo "36d2cb9a2b36ce3a57c154f81fc66233  $F/ParaView-5.11.2-MPI-Linux-Python3.9-x86_64.tar.gz" | md5sum -c -
  mkdir -p /opt/paraview && tar --strip-components=1 -C /opt/paraview -xzf $F/ParaView-5.11.2-MPI-Linux-Python3.9-x86_64.tar.gz
fi
ln -sf /opt/paraview/bin/paraview /usr/local/bin/paraview; ln -sf /opt/paraview/bin/pvpython /usr/local/bin/pvpython; ln -sf /opt/paraview/bin/pvbatch /usr/local/bin/pvbatch
cp -r /opt/paraview/share/icons/hicolor/. /usr/share/icons/hicolor/ && gtk-update-icon-cache -f -q /usr/share/icons/hicolor || true
rm -f $F/ParaView-5.11.2-MPI-Linux-Python3.9-x86_64.tar.gz

say "--- 4. user account (cloud-init made it; same password/sudo/dirs/.bashrc as the Dockerfile) ---"
id user; echo 'user:password' | chpasswd
echo 'user ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user
mkdir -p /home/user/work /home/user/Desktop /home/user/.config/ParaView
grep -q 'shell history instrumentation' /home/user/.bashrc || printf '%s\n' 'source /usr/lib/openfoam/openfoam2512/etc/bashrc' \
   'export LIBGL_ALWAYS_SOFTWARE=1' 'export PATH=$PATH:/opt/paraview/bin' '' \
   '# --- shell history instrumentation (same block harness/runner.py appends at setup; idempotent) ---' \
   'export HISTFILE="$HOME/.bash_history"' 'shopt -s histappend' 'export HISTTIMEFORMAT="%F %T "' \
   'export HISTSIZE=100000 HISTFILESIZE=200000' 'export PROMPT_COMMAND="history -a${PROMPT_COMMAND:+;$PROMPT_COMMAND}"' '' \
   '# xfce4-terminal starts a NON-login shell, so /etc/profile.d is not read there' \
   'export MPLBACKEND="${MPLBACKEND:-Agg}"' 'export GNUPLOT_PAGER="${GNUPLOT_PAGER:-cat}"' >> /home/user/.bashrc

say "--- 5. /etc/profile.d and image-level environment ---"
install -m 0644 $F/etc/00-osworld-xdg.sh /etc/profile.d/00-osworld-xdg.sh
install -m 0644 $F/etc/01-osworld-tools.sh /etc/profile.d/01-osworld-tools.sh
cat > /etc/profile.d/02-osworld-openfoam.sh <<'EOP'
# osworld-openfoam guest (VM edition): OpenFOAM v2512 + ParaView for every login shell (bash -lc via the guest server)
if [ -z "${WM_PROJECT_DIR:-}" ] && [ -f /usr/lib/openfoam/openfoam2512/etc/bashrc ]; then . /usr/lib/openfoam/openfoam2512/etc/bashrc; fi
case ":$PATH:" in *:/opt/paraview/bin:*) ;; *) export PATH="$PATH:/opt/paraview/bin" ;; esac
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
EOP
for kv in MPLBACKEND=Agg GNUPLOT_PAGER=cat LIBGL_ALWAYS_SOFTWARE=1; do k=${kv%%=*}; sed -i "/^$k=/d" /etc/environment; echo "$kv" >> /etc/environment; done

say "--- 6. guest server (vendored xlang-ai/OSWorld server with the r2/r3 patches) as a systemd service ---"
rm -rf /home/user/server && cp -r $F/guest_server /home/user/server && rm -rf /home/user/server/__pycache__
# flask/lxml/requests/pillow/python-xlib come from the noble archive (pip would try to replace debian's blinker and
# fail: "Cannot uninstall blinker 1.7.0, RECORD file not found"); pip only for what the archive does not pin.
$APT install --no-install-recommends python3-flask python3-lxml python3-requests python3-pil python3-xlib 2>&1 | tail -1
pip install --no-cache-dir --break-system-packages "pyautogui==0.9.54" pynput 2>&1 | tail -1
# (pyautogui cannot be imported without an X display; its version comes from the package metadata)
python3 -c "import importlib.metadata as m, flask, lxml, requests, PIL, Xlib; print('pyautogui', m.version('pyautogui'), 'pynput', m.version('pynput'), 'flask', flask.__version__, 'xlib', Xlib.__version__)"
ln -sf /usr/bin/python3 /usr/local/bin/python
cat > /etc/systemd/system/osworld.service <<'EOU'
[Unit]
Description=OSWorld guest server (:5000) on the Xfce display :0
After=graphical.target lightdm.service
Wants=lightdm.service

[Service]
Type=simple
User=user
Group=user
WorkingDirectory=/home/user
Environment="DISPLAY=:0" "HOME=/home/user" "USER=user" "LOGNAME=user" "SHELL=/bin/bash" "XAUTHORITY=/home/user/.Xauthority"
Environment="XDG_RUNTIME_DIR=/run/user/1000" "XDG_SESSION_TYPE=x11" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
Environment="LIBGL_ALWAYS_SOFTWARE=1" "MPLBACKEND=Agg" "GNUPLOT_PAGER=cat" "PAGER=cat"
# wait for the X server and the user's Xauthority that LightDM's autologin writes
ExecStartPre=/bin/bash -c 'for i in $(seq 1 120); do [ -S /tmp/.X11-unix/X0 ] && [ -s /home/user/.Xauthority ] && exit 0; sleep 1; done; exit 1'
ExecStart=/bin/bash -lc 'source /usr/lib/openfoam/openfoam2512/etc/bashrc; export PATH=$PATH:/opt/paraview/bin; exec /usr/bin/python3 /home/user/server/run_server.py'
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOU
systemctl daemon-reload && systemctl enable osworld.service >/dev/null 2>&1

say "--- 7. no browser / no mailer, desktop files, MIME, panel, ParaView first-run ---"
install -m 0755 $F/bin/osworld-no-browser /usr/local/bin/osworld-no-browser
install -m 0755 $F/bin/osworld-no-browser-xfce-helper /usr/local/bin/osworld-no-browser-xfce-helper
install -m 0644 $F/desktop/osworld-no-browser.desktop /usr/share/applications/osworld-no-browser.desktop
mkdir -p /usr/share/xfce4/helpers /etc/xdg/xfce4/xfconf/xfce-perchannel-xml /etc/xdg/Thunar /etc/xdg/xfce4/panel
install -m 0644 $F/desktop/xfce4-helper-no-browser.desktop /usr/share/xfce4/helpers/osworld-no-browser.desktop
install -m 0644 $F/desktop/xfce4-helper-no-mailer.desktop /usr/share/xfce4/helpers/osworld-no-mailer.desktop
install -m 0644 $F/desktop/xfce4-helpers.rc /etc/xdg/xfce4/helpers.rc
install -m 0644 $F/desktop/ristretto.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/ristretto.xml
install -m 0644 $F/desktop/paraview.desktop /usr/share/applications/paraview.desktop
install -m 0644 $F/desktop/openfoam-mime.xml /usr/share/mime/packages/openfoam.xml
install -m 0644 $F/desktop/mimeapps.list /etc/xdg/mimeapps.list
install -m 0644 $F/desktop/thunar-uca.xml /etc/xdg/Thunar/uca.xml
install -m 0644 $F/desktop/xfce4-panel-default.xml /etc/xdg/xfce4/panel/default.xml
install -m 0644 $F/desktop/xfce-terminal.desktop /home/user/Desktop/Terminal.desktop
install -m 0644 $F/desktop/ParaView5.11.2.ini /home/user/.config/ParaView/ParaView5.11.2.ini
update-mime-database /usr/share/mime >/dev/null 2>&1; update-desktop-database /usr/share/applications >/dev/null 2>&1
for d in xfce4-web-browser xfce4-mail-reader xfce4-session-logout; do
  [ -f /usr/share/applications/$d.desktop ] && sed -i '/^\[Desktop Entry\]/a NoDisplay=true' /usr/share/applications/$d.desktop
done
cp /usr/share/applications/paraview.desktop /home/user/Desktop/ParaView.desktop
chmod +x /home/user/Desktop/*.desktop

say "--- 8. display: LightDM autologin -> Xfce on :0, 1920x1080, no blanking ---"
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-osworld.conf <<'EOL'
[Seat:*]
autologin-user=user
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
xserver-command=X -s 0 -dpms -nolisten tcp
EOL
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-osworld-screen.conf <<'EOX'
# QEMU virtual GPU (bochs/std VGA or virtio): ask for 1920x1080 like the r3 container's Xvfb screen
Section "Monitor"
    Identifier "Virtual-1"
    Option "PreferredMode" "1920x1080"
EndSection
Section "Screen"
    Identifier "Screen0"
    Monitor "Virtual-1"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080"
    EndSubSection
EndSection
EOX
mkdir -p /etc/xdg/autostart
cat > /etc/xdg/autostart/osworld-screen.desktop <<'EOD'
[Desktop Entry]
Type=Application
Name=osworld screen setup
Exec=sh -c 'xset s off -dpms; xrandr -s 1920x1080 2>/dev/null || xrandr --output "$(xrandr | awk "/ connected/{print \$1; exit}")" --mode 1920x1080 2>/dev/null; true'
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOD
systemctl set-default graphical.target >/dev/null 2>&1; systemctl enable lightdm >/dev/null 2>&1 || true

say "--- 9. hygiene: no updates, no fstrim, no snapd, apt clean ---"
systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl mask apt-daily.service apt-daily-upgrade.service fstrim.timer 2>/dev/null || true
$APT purge unattended-upgrades snapd 2>&1 | tail -1 || true; rm -rf /snap /var/snap /var/lib/snapd /root/snap 2>/dev/null || true
# r3 parity: the opencfd base has no command-not-found, so a typo says "bash: e: command not found" and never
# "can be installed with: sudo apt install …" (a dead-end suggestion on an offline image)
$APT purge command-not-found python3-commandnotfound 2>&1 | tail -1 || true; rm -rf /var/lib/command-not-found /etc/apt/apt.conf.d/50command-not-found
# r3 parity: the first terminal shows the "To run a command as administrator…" sudo hint (no ~/.sudo_as_admin_successful)
rm -f /home/user/.sudo_as_admin_successful
apt-get clean; rm -rf /var/lib/apt/lists/* /root/.cache
chown -R user:user /home/user
say "--- versions ---"
set +u; . /usr/lib/openfoam/openfoam2512/etc/bashrc; set -u; echo "OpenFOAM $WM_PROJECT_VERSION  gcc $(gcc -dumpfullversion)"; /opt/paraview/bin/pvpython -c "import paraview; print('ParaView', paraview.__version__)"
. /etc/os-release; echo "$PRETTY_NAME  kernel $(uname -r)"
touch /root/.install_done
say "install done — reboot into the desktop next"
