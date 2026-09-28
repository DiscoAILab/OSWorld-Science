#!/usr/bin/env bash
# ubuntu_openfoam guest stage 1 — system side (runs inside the guest as `user`, uses sudo).
# Port of env/Dockerfile (osworld-openfoam-guest r3) onto the OSWorld Ubuntu 22.04 base:
# root partition growth, no unattended upgrades, ESI apt repository, OpenFOAM v2512, the r3 package
# list, profile.d files, passwordless sudo, the guest-server stdin patch and its environment.
set -euo pipefail
F=/home/user/.osci_physics_files          # unpacked files/ from the build context
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "--- passwordless sudo (OSWorld convention of the r3 container) ---"
echo password | sudo -S bash -c "echo 'user ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user"
sudo -n true

say "--- no unattended upgrades / update notifier during the build or at run time ---"
sudo systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>&1 | tail -1 || true
sudo systemctl mask apt-daily.service apt-daily-upgrade.service 2>&1 | tail -1 || true
sudo systemctl mask fstrim.timer 2>&1 | tail -1 || true        # never discard on a COW overlay (CIAO image lesson)
mkdir -p /home/user/.config/autostart
if [ -f /home/user/.config/autostart/update-notifier.desktop ]; then
  grep -q 'X-GNOME-Autostart-enabled=false' /home/user/.config/autostart/update-notifier.desktop || \
    echo 'X-GNOME-Autostart-enabled=false' >> /home/user/.config/autostart/update-notifier.desktop
fi
export DEBIAN_FRONTEND=noninteractive
APT="sudo -E apt-get -o DPkg::Lock::Timeout=300 -y -q"
# update-notifier pops "Software Updates Available to Download" on the desktop of an offline image (seen on the
# first build); the package goes, together with the GUI updater. apt/dpkg themselves stay.
$APT remove --purge update-notifier update-notifier-common update-manager update-manager-core 2>&1 | tail -1 || true
sudo rm -rf /var/lib/update-notifier; pkill -x update-notifier 2>/dev/null || true; rm -f /home/user/.config/autostart/update-notifier.desktop
for i in 1 2 3 4 5 6; do $APT update >/dev/null 2>&1 && break; say "  apt update retry $i"; sleep 20; done

say "--- root partition 29.5G -> whole 50G disk ---"
$APT install cloud-guest-utils 2>&1 | tail -1
ROOTDEV=$(findmnt -n -o SOURCE /); DISK=$(lsblk -no PKNAME "$ROOTDEV"); PART=$(echo "$ROOTDEV" | grep -o '[0-9]*$')
sudo growpart /dev/$DISK $PART || say "  (growpart: nothing to do)"
sudo resize2fs "$ROOTDEV" 2>&1 | tail -1 || true
df -h / | tail -1

say "--- ESI OpenFOAM apt repository (dl.openfoam.com, jammy) ---"
curl -sS --max-time 60 https://dl.openfoam.com/add-debian-repo.sh | sudo bash 2>&1 | tail -3
$APT update >/dev/null 2>&1

say "--- OpenFOAM v2512 (openfoam2512-default = dev + tutorials + mpi) ---"
$APT install openfoam2512-default 2>&1 | tail -2
ls -d /usr/lib/openfoam/openfoam2512 && dpkg-query -W -f='${Package} ${Version}\n' openfoam2512 openfoam2512-common openfoam2512-dev openfoam2512-tutorials

say "--- the r3 package list (what the base does not already have) ---"
$APT install --no-install-recommends \
    gnuplot-qt python3-pandas python3-scipy python3-matplotlib python3-tk python3-xlib \
    git vim nano less bc unzip zip bzip2 xz-utils file jq tree htop iproute2 iputils-ping time rsync openssh-client \
    xdotool wmctrl xclip scrot x11-xserver-utils x11-utils xdg-utils shared-mime-info desktop-file-utils libglib2.0-bin \
    libgl1 libglu1-mesa libgl1-mesa-dri libegl1 libopengl0 libxrender1 libxt6 libxcursor1 libxkbcommon-x11-0 \
    libxcb-xinerama0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-shape0 libxcb-xkb1 libxcb-xinput0 libxcb-util1 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-glx0 \
    libxi6 libsm6 libice6 libxrandr2 libxss1 fonts-dejavu-core 2>&1 | tail -2
sudo apt-get clean; sudo rm -rf /var/lib/apt/lists/*

say "--- /etc/profile.d: XDG pin, non-blocking tool defaults, OpenFOAM + ParaView for login shells ---"
sudo install -m 0644 $F/etc/00-osworld-xdg.sh   /etc/profile.d/00-osworld-xdg.sh
sudo install -m 0644 $F/etc/01-osworld-tools.sh /etc/profile.d/01-osworld-tools.sh
sudo tee /etc/profile.d/02-osworld-openfoam.sh >/dev/null <<'EOP'
# osworld-openfoam guest (VM edition): OpenFOAM v2512 environment and ParaView on PATH for every login shell
# (`bash -lc` through the guest server, ssh, the console); ~/.bashrc carries the same block for the
# non-login interactive shell that GNOME Terminal starts.
if [ -z "${WM_PROJECT_DIR:-}" ] && [ -f /usr/lib/openfoam/openfoam2512/etc/bashrc ]; then
    . /usr/lib/openfoam/openfoam2512/etc/bashrc
fi
case ":$PATH:" in *:/opt/paraview/bin:*) ;; *) export PATH="$PATH:/opt/paraview/bin" ;; esac
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
EOP
# image-level defaults for processes that never see a login shell (matches the Dockerfile's ENV line)
for kv in MPLBACKEND=Agg GNUPLOT_PAGER=cat LIBGL_ALWAYS_SOFTWARE=1; do
  k=${kv%%=*}; sudo sed -i "/^$k=/d" /etc/environment; echo "$kv" | sudo tee -a /etc/environment >/dev/null
done

say "--- guest server: stdin=/dev/null for /execute (r3 patch), pinned env for its service ---"
python3 - <<'PY'
import re, pathlib
p = pathlib.Path('/home/user/server/main.py'); s = p.read_text()
if 'stdin=subprocess.DEVNULL' not in s:
    n = 0
    def sub(m):
        global n; n += 1
        return m.group(1) + 'stdin=subprocess.DEVNULL,\n' + m.group(0)
    # only the two execute endpoints: the first two subprocess.run(...) blocks that read stdout/stderr PIPE with shell=shell
    s2 = re.sub(r'^(\s+)stdout=subprocess\.PIPE,\n\s+stderr=subprocess\.PIPE,\n\s+shell=shell,', sub, s, count=2, flags=re.M)
    assert n == 2, f'expected 2 execute blocks, patched {n}'
    p.write_text(s2); print('patched /execute and /execute_with_verification (stdin=DEVNULL)')
else:
    print('already patched')
PY
grep -c 'stdin=subprocess.DEVNULL' /home/user/server/main.py
sudo mkdir -p /etc/systemd/system/osworld.service.d
sudo tee /etc/systemd/system/osworld.service.d/10-openfoam.conf >/dev/null <<'EOU'
[Service]
# r3 container parity: matplotlib never picks TkAgg under /execute, gnuplot/git never wait on a pager,
# ParaView/OpenGL always software-rendered (the VM has no GPU).
Environment="MPLBACKEND=Agg" "GNUPLOT_PAGER=cat" "PAGER=cat" "LIBGL_ALWAYS_SOFTWARE=1"
EOU
sudo systemctl daemon-reload
say "stage 1 done"
