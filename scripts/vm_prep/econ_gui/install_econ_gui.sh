#!/usr/bin/env bash
# ubuntu_econ_gui — runs as root inside the Ubuntu 24.04 build VM (build_image.sh uploads the context to /root/build_ctx).
# The GUI-only desktop the econ GUI-software tasks (ECON-040 … ECON-059) were developed and run on, ported from the
# stand-in Docker images in files/standin/ to a bootable VM in the way physics/xfce/install_xfce_openfoam.sh ports the
# OpenFOAM container:
#   * Ubuntu 24.04 + Xfce (Xorg + LightDM autologin into Xfce on :0, as for ubuntu_openfoam) with LibreOffice Calc 24.2,
#     eog, and the vendored OSWorld guest server (physics/files/guest_server) as a systemd service;
#   * JDemetra+ 3.9.0 desktop (Maven Central zip), GeoDa 1.22.1 (noble .deb), Portfolio Performance 0.89.0 (linux gtk
#     tarball) and NetLogo 7.0.4 (64-bit tarball), every download sha256-checked (build_image.sh fetches them);
#   * JDemetra+ and Portfolio Performance on a jlink'd Java 21 runtime without the compiler, jshell or attach modules;
#   * nothing on the desktop that runs arbitrary commands: no terminal emulator, no run dialog (xfce4-appfinder), no file
#     manager (Thunar's custom actions), no exo-open/preferred-applications chooser (it accepts a custom command), no
#     settings manager or keyboard-shortcut editor (custom shortcuts run commands), LibreOffice macros off, NetLogo
#     without the extensions that start external programs (py, sr) and without its headless/console launchers;
#   * the grader-side engines in root-only /opt/grader: jwsacruncher 3.9.0 (JDemetra+'s command-line engine) and the
#     Portfolio Performance reference classes (files/grader/PPRef*.java, compiled here against PP's own jars). The
#     tasks' evaluator commands reach them with `sudo -n`, so `user` keeps passwordless sudo, like every OSWorld image.
# The agent's action channel must also be limited to mouse and keyboard (`"gui_only": true` in these tasks); that is a
# runner-side check, see README.md.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
F=/root/build_ctx
say() { echo "[$(date +%H:%M:%S)] $*"; }
APT="apt-get -o DPkg::Lock::Timeout=300 -y -q"
must_not() { if bash -c "$1"; then say "FAILED: expected false: $1"; exit 1; fi; }   # (`! cmd` is exempt from set -e)
STANDIN=${STANDIN:-0}   # 1 = run inside a plain ubuntu:24.04 container to check the package steps (no systemd, no display)

say "--- 0. wait for cloud-init, then stop it from ever running again ---"
if [ "$STANDIN" = 0 ]; then
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
fi

say "--- 1. apt: Xorg/LightDM + Xfce without terminal, run dialog or file manager; LibreOffice Calc; python for the guest server ---"
for i in 1 2 3 4 5 6; do $APT update >/dev/null 2>&1 && break; say "  apt update retry $i"; sleep 15; done
[ "$STANDIN" = 0 ] && KMOD="linux-modules-extra-$(uname -r)" || KMOD=""
$APT install --no-install-recommends $KMOD \
  xserver-xorg xserver-xorg-video-all xserver-xorg-input-all xinit lightdm lightdm-gtk-greeter dbus-x11 at-spi2-core xauth \
  xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfconf mousepad eog \
  xdg-utils shared-mime-info desktop-file-utils libglib2.0-bin dconf-gsettings-backend gsettings-desktop-schemas \
  elementary-xfce-icon-theme greybird-gtk-theme adwaita-icon-theme fonts-dejavu fonts-liberation locales \
  libreoffice-calc libreoffice-gtk3 \
  python3 python3-pip python3-tk python3-xlib python3-pil python3-openpyxl python3-flask python3-lxml python3-requests python3-evdev \
  scrot xdotool wmctrl xclip x11-xserver-utils x11-utils \
  libgtk-3-0t64 libwebkit2gtk-4.1-0 libpcre2-32-0 libxtst6 libxrender1 libxi6 libgl1 libgl1-mesa-dri libglu1-mesa \
  sudo curl ca-certificates unzip procps psmisc file 2>&1 | tail -2
locale-gen en_US.UTF-8 >/dev/null
pip install --no-cache-dir --break-system-packages "pyautogui==0.9.54" pynput 2>&1 | tail -1
ln -sf /usr/bin/python3 /usr/local/bin/python

say "--- 2. Java 21 runtime without compiler/jshell (jlink), then the PP reference classes, then the JDK goes again ---"
$APT install --no-install-recommends openjdk-21-jdk-headless binutils 2>&1 | tail -1
JMODS=$(java --list-modules | sed 's/@.*//' | grep -v -E '^(jdk\.compiler|jdk\.jshell|jdk\.jdeps|jdk\.javadoc|jdk\.jartool|jdk\.jlink|jdk\.jdi|jdk\.jdwp\.agent|jdk\.jpackage|jdk\.hotspot\.agent|jdk\.internal\.ed|jdk\.internal\.le|jdk\.internal\.opt|jdk\.editpad|jdk\.jstatd|jdk\.jconsole|java\.compiler|jdk\.attach|jdk\.jcmd|jdk\.internal\.jvmstat|jdk\.random)$' | paste -sd, -)
rm -rf /opt/jre-jd && jlink --add-modules "$JMODS" --strip-debug --no-header-files --no-man-pages --output /opt/jre-jd
must_not "/opt/jre-jd/bin/java --list-modules | grep -E 'jdk[.]compiler|jdk[.]jshell|jdk[.]attach'"

say "--- 3. Portfolio Performance 0.89.0 -> /opt/portfolio (launcher \`portfolio\`), reference classes -> /opt/grader/ppref ---"
echo "01ee66debf2f52fc6e6c3eb353ecdff9cb3629cedbe9d31b1f7c103b750b322c  $F/dl/PortfolioPerformance-0.89.0-linux.gtk.x86_64.tar.gz" | sha256sum -c -
rm -rf /opt/portfolio && tar -C /opt -xzf $F/dl/PortfolioPerformance-0.89.0-linux.gtk.x86_64.tar.gz
grep -q '^/opt/jre-jd/bin/java$' /opt/portfolio/PortfolioPerformance.ini || sed -i '1i -vm\n/opt/jre-jd/bin/java' /opt/portfolio/PortfolioPerformance.ini
ln -sf /opt/portfolio/PortfolioPerformance /usr/local/bin/portfolio
mkdir -p /opt/grader/ppref && javac -d /opt/grader/ppref -cp "/opt/portfolio/plugins/*" $F/grader/PPRef.java $F/grader/PPRef2.java $F/grader/PPRef3.java
ls /opt/grader/ppref/PPRef.class /opt/grader/ppref/PPRef2.class /opt/grader/ppref/PPRef3.class >/dev/null
$APT purge openjdk-21-jdk-headless openjdk-21-jre-headless 2>&1 | tail -1; $APT autoremove --purge 2>&1 | tail -1
must_not 'command -v javac || command -v jshell'

say "--- 4. JDemetra+ 3.9.0 desktop -> /opt/nbdemetra (launcher \`jdemetra\`), jwsacruncher 3.9.0 -> /opt/grader ---"
echo "11670b657d1a75d4a25bae6c7850bb5a606668aeff4cc383c3219a32a42b123a  $F/dl/jdplus-main-desktop-bin-3.9.0.zip" | sha256sum -c -
echo "4c23ccbcd6e610c6b77cb8e69b967f11730b88e85e002c192d0194e00e99fcda  $F/dl/jdplus-main-cli-bin-3.9.0.zip" | sha256sum -c -
rm -rf /opt/nbdemetra /opt/grader/jwsacruncher-3.9.0
(cd /opt && unzip -q $F/dl/jdplus-main-desktop-bin-3.9.0.zip) && (cd /opt/grader && unzip -q $F/dl/jdplus-main-cli-bin-3.9.0.zip)
chmod +x /opt/nbdemetra/bin/nbdemetra /opt/grader/jwsacruncher-3.9.0/bin/jwsacruncher
sed -i 's|^#*jdkhome=.*|jdkhome="/opt/jre-jd"|' /opt/nbdemetra/etc/nbdemetra.conf && grep -q '^jdkhome="/opt/jre-jd"' /opt/nbdemetra/etc/nbdemetra.conf
ln -sf /opt/nbdemetra/bin/nbdemetra /usr/local/bin/jdemetra

say "--- 5. GeoDa 1.22.1 (noble .deb; launcher /usr/bin/run_geoda.sh) ---"
echo "5ee442fd759515016d50d7c93c40e2d747fcca80848bd3c27a5c6e37a56cf989  $F/dl/geoda_1.22.1-1noble1_amd64.deb" | sha256sum -c -
$APT install --no-install-recommends $F/dl/geoda_1.22.1-1noble1_amd64.deb 2>&1 | tail -1
must_not 'ldd /usr/local/geoda/GeoDa | grep "not found"'
[ -x /usr/bin/run_geoda.sh ]

say "--- 6. NetLogo 7.0.4 -> /opt/netlogo (launcher \`netlogo\`), without py/sr extensions and command-line launchers ---"
echo "75636b8469ea4995ec051cfbc91e525f171be84b8f959b86bc080fbff702dfbc  $F/dl/NetLogo-7.0.4-64.tgz" | sha256sum -c -
rm -rf /opt/netlogo "/opt/NetLogo 7.0.4" && tar -C /opt -xzf $F/dl/NetLogo-7.0.4-64.tgz && mv "/opt/NetLogo 7.0.4" /opt/netlogo
rm -rf /opt/netlogo/extensions/.bundled/py /opt/netlogo/extensions/.bundled/sr "/opt/netlogo/Mathematica Link"
mkdir -p /opt/grader/netlogo-cli && mv /opt/netlogo/netlogo-headless.sh /opt/netlogo/netlogo-gui.sh /opt/netlogo/NetLogo_Console \
  /opt/netlogo/behaviorsearch /opt/netlogo/bin/BehaviorSearch /opt/grader/netlogo-cli/
ln -sf /opt/netlogo/bin/NetLogo /usr/local/bin/netlogo

say "--- 7. menu entries for the four programs; grader directory root-only ---"
mk_desktop() { printf '[Desktop Entry]\nName=%s\nExec=%s\nIcon=%s\nType=Application\nTerminal=false\nCategories=Office;Science;\n' "$1" "$2" "$3" > /usr/share/applications/$4.desktop; }
mk_desktop "JDemetra+" "/opt/nbdemetra/bin/nbdemetra" "/opt/nbdemetra/nbdemetra.png" jdemetra
mk_desktop "Portfolio Performance" "/opt/portfolio/PortfolioPerformance" "/opt/portfolio/icon.xpm" portfolio
mk_desktop "NetLogo" "/opt/netlogo/bin/NetLogo %f" "/opt/netlogo/lib/NetLogo.png" netlogo
chown -R root:root /opt/grader && chmod 700 /opt/grader

say "--- 8. GUI-only desktop: remove every program that runs a command line from the desktop ---"
$APT purge xfce4-terminal xfce4-appfinder thunar gnome-terminal xterm 2>&1 | tail -1 || true
rm -f /usr/bin/x-terminal-emulator /usr/bin/uxterm /usr/bin/lxterm /usr/bin/xdg-terminal /usr/bin/exo-open /usr/bin/xfce4-mime-helper \
      /usr/bin/xfce4-mime-settings /usr/bin/xfce4-settings-manager /usr/bin/xfce4-keyboard-settings /usr/bin/xfce4-panel-profiles /usr/bin/xfce4-run
for d in /usr/share/applications/xfce4-*-settings.desktop /usr/share/applications/xfce-settings-manager.desktop \
         /usr/share/applications/xfce4-web-browser.desktop /usr/share/applications/xfce4-mail-reader.desktop \
         /usr/share/applications/xfce4-file-manager.desktop /usr/share/applications/xfce4-terminal-emulator.desktop \
         /usr/share/applications/xfce4-run.desktop /usr/share/applications/xfce4-session-logout.desktop; do
  [ -f "$d" ] && sed -i '/^\[Desktop Entry\]/a NoDisplay=true' "$d"
done
mkdir -p /etc/xdg/xfce4/kiosk
printf '[xfce4-panel]\nCustomizePanel=NONE\n[xfce4-session]\nCustomizeSplash=NONE\nCustomizeChooser=NONE\nCustomizeLogout=NONE\nCustomizeCompatibility=NONE\nCustomizeSecurity=NONE\n' > /etc/xdg/xfce4/kiosk/kioskrc
# no keyboard shortcut starts a command (Xfce's defaults bind the terminal, appfinder and a run dialog)
mkdir -p /etc/xdg/xfce4/xfconf/xfce-perchannel-xml
printf '<?xml version="1.0" encoding="UTF-8"?>\n<channel name="xfce4-keyboard-shortcuts" version="1.0">\n  <property name="commands" type="empty">\n    <property name="default" type="empty"/>\n    <property name="custom" type="empty">\n      <property name="override" type="bool" value="true"/>\n    </property>\n  </property>\n</channel>\n' \
  > /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-keyboard-shortcuts.xml
# URLs (in-app help links) go to a no-op handler: no browser, no chooser dialog
install -m 0755 $F/physics/bin/osworld-no-browser /usr/local/bin/osworld-no-browser
install -m 0644 $F/physics/desktop/osworld-no-browser.desktop /usr/share/applications/osworld-no-browser.desktop
cat > /etc/xdg/mimeapps.list <<'EOM'
[Default Applications]
image/png=org.gnome.eog.desktop
image/jpeg=org.gnome.eog.desktop
x-scheme-handler/http=osworld-no-browser.desktop
x-scheme-handler/https=osworld-no-browser.desktop
x-scheme-handler/ftp=osworld-no-browser.desktop
x-scheme-handler/mailto=osworld-no-browser.desktop
x-scheme-handler/about=osworld-no-browser.desktop
EOM
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
must_not 'command -v xfce4-terminal || command -v thunar || command -v xfce4-appfinder || command -v exo-open || command -v gnome-terminal || command -v xterm'

say "--- 9. user account: LibreOffice profile (no first-run dialogs, macros off), work dirs ---"
id user >/dev/null 2>&1 || useradd -m -s /bin/bash user
echo 'user:password' | chpasswd
echo 'user ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user
mkdir -p /home/user/work /home/user/assets /home/user/Desktop /home/user/Documents /home/user/.config/libreoffice/4/user
install -m 0644 $F/registrymodifications.xcu /home/user/.config/libreoffice/4/user/registrymodifications.xcu
chown -R user:user /home/user

say "--- 10. guest server (vendored OSWorld server, physics/files/guest_server) as a systemd service ---"
rm -rf /home/user/server && cp -r $F/physics/guest_server /home/user/server && rm -rf /home/user/server/__pycache__ && chown -R user:user /home/user/server
if [ "$STANDIN" = 0 ]; then
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
Environment="XDG_RUNTIME_DIR=/run/user/1000" "XDG_SESSION_TYPE=x11" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" "PAGER=cat"
ExecStartPre=/bin/bash -c 'for i in $(seq 1 120); do [ -S /tmp/.X11-unix/X0 ] && [ -s /home/user/.Xauthority ] && exit 0; sleep 1; done; exit 1'
ExecStart=/usr/bin/python3 /home/user/server/run_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOU
systemctl daemon-reload && systemctl enable osworld.service >/dev/null 2>&1

say "--- 11. display: LightDM autologin -> Xfce on :0, 1920x1080, no blanking ---"
mkdir -p /etc/lightdm/lightdm.conf.d /etc/X11/xorg.conf.d /etc/xdg/autostart
printf '[Seat:*]\nautologin-user=user\nautologin-user-timeout=0\nautologin-session=xfce\nuser-session=xfce\nxserver-command=X -s 0 -dpms -nolisten tcp\n' > /etc/lightdm/lightdm.conf.d/50-osworld.conf
printf 'Section "Monitor"\n    Identifier "Virtual-1"\n    Option "PreferredMode" "1920x1080"\nEndSection\nSection "Screen"\n    Identifier "Screen0"\n    Monitor "Virtual-1"\n    DefaultDepth 24\n    SubSection "Display"\n        Depth 24\n        Modes "1920x1080"\n    EndSubSection\nEndSection\n' > /etc/X11/xorg.conf.d/10-osworld-screen.conf
cat > /etc/xdg/autostart/osworld-screen.desktop <<'EOD'
[Desktop Entry]
Type=Application
Name=osworld screen setup
Exec=sh -c 'xset s off -dpms; xrandr -s 1920x1080 2>/dev/null || xrandr --output "$(xrandr | awk "/ connected/{print \$1; exit}")" --mode 1920x1080 2>/dev/null; xfconf-query -c xfwm4 -p /general/use_compositing -s false 2>/dev/null; true'
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOD
systemctl set-default graphical.target >/dev/null 2>&1; systemctl enable lightdm >/dev/null 2>&1 || true

say "--- 12. hygiene: no updates, no fstrim, no snapd, apt clean ---"
systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl mask apt-daily.service apt-daily-upgrade.service fstrim.timer 2>/dev/null || true
fi
$APT purge unattended-upgrades snapd command-not-found python3-commandnotfound 2>&1 | tail -1 || true
rm -rf /snap /var/snap /var/lib/snapd /root/snap /var/lib/command-not-found 2>/dev/null || true
apt-get clean; rm -rf /var/lib/apt/lists/* /root/.cache

say "--- versions ---"
/opt/jre-jd/bin/java -version 2>&1 | head -1
grep -o 'currentVersion=[^ ]*' /opt/nbdemetra/etc/nbdemetra.conf 2>/dev/null || ls /opt/nbdemetra/platform >/dev/null
dpkg-query -W -f='${Package} ${Version}\n' geoda libreoffice-calc
ls /opt/portfolio/plugins | grep -m1 'name.abuchen.portfolio_'
grep -m1 -o 'NetLogo 7[.0-9]*' /opt/netlogo/readme.md || true
. /etc/os-release; echo "$PRETTY_NAME"
touch /root/.install_done
say "install done — reboot into the desktop next"
