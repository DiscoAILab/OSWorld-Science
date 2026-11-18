#!/usr/bin/env bash
# Build data/econ/vm/ubuntu_econ_gui.qcow2 — the GUI-only econ desktop (JDemetra+, GeoDa, Portfolio Performance,
# NetLogo, LibreOffice Calc on Xfce) — from the Ubuntu 24.04 cloud image. The same machinery as
# physics/xfce/build_xfce_image.sh: plain qemu-kvm on the host (no libvirt), ssh for provisioning, then flatten.
#
#   scripts/vm_prep/econ_gui/build_image.sh [--work DIR] [--ssh-port 2223] [--guest-port 5172]
#
# Steps: download + sha256-check the cloud image and the five installers → 50G build disk → cloud-init NoCloud seed
# (user/password, passwordless sudo, a throw-away ssh key) → boot under KVM (q35, UEFI, virtio, 8 vCPU / 8G) → upload
# the build context (files/, the installers, physics/files/{guest_server,bin,desktop}) + install_econ_gui.sh → run it
# → reboot into Xfce → guest/30_verify.sh through the guest server → clean run state, remove the build key, power off
# → qemu-img convert → md5 + manifest.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd)
WORK=$REPO/vm/build_econ_gui; SSHP=2223; GP=5172
while [ $# -gt 0 ]; do case "$1" in --work) WORK=$2; shift 2;; --ssh-port) SSHP=$2; shift 2;; --guest-port) GP=$2; shift 2;; *) shift;; esac; done
OUT=$REPO/data/econ/vm/ubuntu_econ_gui.qcow2; mkdir -p "$(dirname "$OUT")"
QEMU=${QEMU:-/usr/libexec/qemu-kvm}; [ -x "$QEMU" ] || QEMU=$(command -v qemu-system-x86_64)
OVMF_CODE=${OVMF_CODE:-/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd}; OVMF_VARS=${OVMF_VARS:-/usr/share/edk2/ovmf/OVMF_VARS.fd}
say() { echo "[$(date +%H:%M:%S)] $*"; }
mkdir -p "$WORK" && cd "$WORK"
S="ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -i $WORK/id_build -p $SSHP user@127.0.0.1"
SCP="scp -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $WORK/id_build -P $SSHP"

say "1. Ubuntu 24.04 cloud image"
[ -s noble-server-cloudimg-amd64.img ] || curl -sSL -o noble-server-cloudimg-amd64.img https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
curl -sSL -o SHA256SUMS https://cloud-images.ubuntu.com/noble/current/SHA256SUMS && grep noble-server-cloudimg-amd64.img SHA256SUMS | sha256sum -c -
rm -f build.qcow2 && qemu-img convert -O qcow2 noble-server-cloudimg-amd64.img build.qcow2 && qemu-img resize build.qcow2 50G
cp "$OVMF_VARS" ./OVMF_VARS.fd

say "2. cloud-init seed (user/password, passwordless sudo, throw-away build key)"
[ -f id_build ] || ssh-keygen -q -t ed25519 -N '' -f id_build -C osworld-build
mkdir -p seed
printf 'instance-id: osworld-econ-gui-build\nlocal-hostname: osworld\n' > seed/meta-data
cat > seed/user-data <<EOU
#cloud-config
hostname: osworld
manage_etc_hosts: true
users:
  - name: user
    gecos: user
    shell: /bin/bash
    groups: [sudo, adm, video, audio, plugdev]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    lock_passwd: false
    plain_text_passwd: password
    ssh_authorized_keys:
      - $(cat id_build.pub)
ssh_pwauth: true
chpasswd:
  expire: false
growpart:
  mode: auto
  devices: ['/']
runcmd:
  - [ sh, -c, "echo build-ready > /var/tmp/cloud-init.done" ]
EOU
genisoimage -quiet -output seed.iso -volid cidata -joliet -rock seed/user-data seed/meta-data

say "3. boot under KVM (ssh on :$SSHP, guest server on :$GP)"
rm -f qemu.pid
"$QEMU" -name osworld-build,process=osworld-build -enable-kvm -machine type=q35,accel=kvm -cpu host -smp 8 -m 8G \
  -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" -drive if=pflash,format=raw,file=OVMF_VARS.fd \
  -drive file=build.qcow2,if=virtio,format=qcow2,cache=writeback,discard=unmap \
  -drive file=seed.iso,media=cdrom,format=raw,readonly=on \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:$SSHP-:22,hostfwd=tcp:127.0.0.1:$GP-:5000 -device virtio-net-pci,netdev=n0 \
  -vga virtio -display none -device qemu-xhci -device usb-tablet -object rng-random,id=rng0,filename=/dev/urandom -device virtio-rng-pci,rng=rng0 \
  -serial file:serial.log -daemonize -pidfile qemu.pid
until $S 'test -f /var/tmp/cloud-init.done' 2>/dev/null; do kill -0 "$(cat qemu.pid)" 2>/dev/null || { echo "qemu died"; tail -20 serial.log; exit 1; }; sleep 5; done
say "   cloud-init done: $($S '. /etc/os-release; echo $PRETTY_NAME; uname -r')"

say "4. installers (sha256-checked), build context, run install_econ_gui.sh (20-40 min), stream its stages"
mkdir -p dl && while read -r sum name url; do
  [ -s "dl/$name" ] || curl -sSL --retry 5 -o "dl/$name" "$url"
  echo "$sum  dl/$name" | sha256sum -c -
done <<'EOD'
11670b657d1a75d4a25bae6c7850bb5a606668aeff4cc383c3219a32a42b123a jdplus-main-desktop-bin-3.9.0.zip https://repo1.maven.org/maven2/eu/europa/ec/joinup/sat/jdplus-main-desktop-bin/3.9.0/jdplus-main-desktop-bin-3.9.0.zip
4c23ccbcd6e610c6b77cb8e69b967f11730b88e85e002c192d0194e00e99fcda jdplus-main-cli-bin-3.9.0.zip https://repo1.maven.org/maven2/eu/europa/ec/joinup/sat/jdplus-main-cli-bin/3.9.0/jdplus-main-cli-bin-3.9.0.zip
5ee442fd759515016d50d7c93c40e2d747fcca80848bd3c27a5c6e37a56cf989 geoda_1.22.1-1noble1_amd64.deb https://github.com/GeoDaCenter/geoda/releases/download/v1.22.1/geoda_1.22.1-1noble1_amd64.deb
01ee66debf2f52fc6e6c3eb353ecdff9cb3629cedbe9d31b1f7c103b750b322c PortfolioPerformance-0.89.0-linux.gtk.x86_64.tar.gz https://github.com/portfolio-performance/portfolio/releases/download/0.89.0/PortfolioPerformance-0.89.0-linux.gtk.x86_64.tar.gz
75636b8469ea4995ec051cfbc91e525f171be84b8f959b86bc080fbff702dfbc NetLogo-7.0.4-64.tgz https://github.com/NetLogo/NetLogo/releases/download/v7.0.4/NetLogo-7.0.4-64.tgz
EOD
rm -rf ctx && mkdir -p ctx/physics && cp -r "$HERE/files/grader" "$HERE/files/registrymodifications.xcu" ctx/ && cp -r dl ctx/dl \
  && cp -r "$HERE/../physics/files/guest_server" "$HERE/../physics/files/bin" "$HERE/../physics/files/desktop" ctx/physics/
tar -C ctx -czf build_ctx.tgz . && $SCP build_ctx.tgz "$HERE/install_econ_gui.sh" user@127.0.0.1:/tmp/
$S 'sudo bash -c "rm -rf /root/build_ctx; mkdir -p /root/build_ctx && tar -C /root/build_ctx -xzf /tmp/build_ctx.tgz && rm -f /tmp/build_ctx.tgz; mv /tmp/install_econ_gui.sh /root/; chmod +x /root/install_econ_gui.sh; rm -f /root/install.rc; (setsid nohup bash -c \"bash /root/install_econ_gui.sh > /root/install.log 2>&1; echo \\\$? > /root/install.rc\" >/dev/null 2>&1 < /dev/null &)"'
last=0
until $S 'test -f /root/install.rc' 2>/dev/null; do
  n=$($S 'sudo grep -c "" /root/install.log' 2>/dev/null || echo 0)
  [ "$n" -gt "$last" ] && { $S "sudo sed -n '$((last+1)),\$p' /root/install.log" | grep -E '^\[|OK|Error|ERROR|FAILED' || true; last=$n; }
  sleep 15
done
rc=$($S 'sudo cat /root/install.rc'); [ "$rc" = 0 ] || { say "installer failed (rc=$rc)"; $S 'sudo tail -40 /root/install.log'; exit 1; }

say "5. reboot into the desktop and verify through the guest server"
$S 'sudo systemctl reboot' || true; sleep 20
for i in $(seq 1 90); do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:$GP/screenshot)" = 200 ] && break; sleep 5; done
PYTHONPATH=$REPO/src "$REPO/.venv/bin/python" - "$GP" "$HERE/guest/30_verify.sh" "$WORK/desktop_econ_gui.png" <<'PY'
import sys, pathlib
from osworld_science.guest.client import Guest
g = Guest(int(sys.argv[1]), host="127.0.0.1")
g.upload(pathlib.Path(sys.argv[2]), '/home/user/verify_econ_gui.sh')
r = g.execute(["bash", "-lc", "bash ~/verify_econ_gui.sh 2>&1; rc=$?; rm -f ~/verify_econ_gui.sh; echo exit=$rc"], 300)
out = (r.get("output") or "") + (r.get("error") or ""); print(out)
pathlib.Path(sys.argv[3]).write_bytes(g.screenshot() or b"")
sys.exit(0 if "verify: 0 failure" in out else 1)
PY

say "6. clean run state, drop the build key, power off, flatten"
$S 'sudo bash -c "rm -rf /home/user/work/* /home/user/assets/* /home/user/.bash_history /home/user/.lesshst /home/user/.viminfo /home/user/.python_history /home/user/.nbdemetra /home/user/.cache/nbdemetra /home/user/.PortfolioPerformance /home/user/.java/.userPrefs /home/user/.cache/thumbnails /home/user/.cache/sessions /home/user/.local/share/recently-used.xbel /home/user/.config/libreoffice/4/user/registrymodifications.xcu.bak; mkdir -p /home/user/work /home/user/assets; chown -R user:user /home/user; rm -rf /root/build_ctx /root/install.log* /root/install.rc /root/.bash_history /var/lib/cloud/instances/* /var/log/cloud-init*.log /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /home/user/.ssh/authorized_keys; journalctl --rotate; journalctl --vacuum-time=1s; sync" >/dev/null 2>&1; sudo systemctl poweroff' || true
for i in $(seq 1 60); do kill -0 "$(cat qemu.pid)" 2>/dev/null || break; sleep 3; done
qemu-img convert -O qcow2 -m 8 build.qcow2 "$OUT.new" && ! qemu-img info "$OUT.new" | grep -qi backing && mv "$OUT.new" "$OUT"
md5=$(md5sum "$OUT" | cut -d' ' -f1); echo "$md5  ubuntu_econ_gui.qcow2" > "$OUT.md5"
printf '{\n  "snapshot": "ubuntu_econ_gui",\n  "image": "ubuntu_econ_gui.qcow2",\n  "md5": "%s",\n  "bytes": %s,\n  "built": "%s",\n  "base": "noble-server-cloudimg-amd64.img + install_econ_gui.sh"\n}\n' "$md5" "$(stat -c %s "$OUT")" "$(date +%FT%T)" > "${OUT%.qcow2}.manifest.json"
say "done: $OUT  md5 $md5"
