#!/usr/bin/env bash
# Build data/physics/vm/ubuntu_openfoam.qcow2 — the r3 container as a VM — from the Ubuntu 24.04 cloud image.
# Runs plain qemu-kvm on the host (no libvirt), ssh for provisioning, then flattens the disk.
#
#   scripts/vm_prep/physics/xfce/build_xfce_image.sh [--work DIR] [--ssh-port 2222] [--guest-port 5102]
#
# Steps: download + sha256-check the cloud image → 50G build disk → cloud-init NoCloud seed (user/password,
# passwordless sudo, a throw-away ssh key) → boot under KVM (q35, UEFI, virtio, 8 vCPU / 8G) → upload
# ../files (the r3 build context) + install_xfce_openfoam.sh → run it → reboot into Xfce → 30_verify_xfce.sh
# through the guest server → clean run state, remove the build key, power off → qemu-img convert → md5 + manifest.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../../.." && pwd)
WORK=$REPO/vm/build_xfce; SSHP=2222; GP=5102
while [ $# -gt 0 ]; do case "$1" in --work) WORK=$2; shift 2;; --ssh-port) SSHP=$2; shift 2;; --guest-port) GP=$2; shift 2;; *) shift;; esac; done
OUT=$REPO/data/physics/vm/ubuntu_openfoam.qcow2
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
printf 'instance-id: osworld-openfoam-build\nlocal-hostname: osworld\n' > seed/meta-data
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

say "4. upload the r3 build context + installer, run it (10-20 min), stream its stages"
tar -C "$HERE/.." -czf build_ctx.tgz files && $SCP build_ctx.tgz "$HERE/install_xfce_openfoam.sh" user@127.0.0.1:/tmp/
$S 'sudo bash -c "rm -rf /root/build_ctx; mkdir -p /root/build_ctx && tar -C /root/build_ctx --strip-components=1 -xzf /tmp/build_ctx.tgz && rm -f /root/build_ctx/Dockerfile /root/build_ctx/README.md /root/build_ctx/entrypoint.sh /root/build_ctx/supervisord.conf /tmp/build_ctx.tgz; mv /tmp/install_xfce_openfoam.sh /root/; chmod +x /root/install_xfce_openfoam.sh; rm -f /root/install.rc; (setsid nohup bash -c \"bash /root/install_xfce_openfoam.sh > /root/install.log 2>&1; echo \\\$? > /root/install.rc\" >/dev/null 2>&1 < /dev/null &)"'
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
PYTHONPATH=$REPO/src "$REPO/.venv/bin/python" - "$GP" "$HERE/../guest/30_verify_xfce.sh" "$WORK/desktop_xfce.png" <<'PY'
import sys, pathlib
from osworld_science.guest.client import Guest
g = Guest(int(sys.argv[1]), host="127.0.0.1")
g.upload(pathlib.Path(sys.argv[2]), '/home/user/verify_openfoam.sh')
r = g.execute(["bash", "-lc", "bash ~/verify_openfoam.sh 2>&1; rc=$?; rm -f ~/verify_openfoam.sh; echo exit=$rc"], 300)
out = (r.get("output") or "") + (r.get("error") or ""); print(out)
pathlib.Path(sys.argv[3]).write_bytes(g.screenshot() or b"")
sys.exit(0 if "verify: 0 failure" in out else 1)
PY

say "6. clean run state, drop the build key, power off, flatten"
$S 'sudo bash -c "rm -rf /home/user/work/* /home/user/.bash_history /home/user/.lesshst /home/user/.viminfo /home/user/.python_history /home/user/.cache/ParaView /home/user/.cache/thumbnails /home/user/.cache/sessions /home/user/.config/ParaView/ParaView-UserSettings.json /home/user/.local/share/recently-used.xbel /tmp/cav; mkdir -p /home/user/work /home/user/.config/ParaView; printf \"[General]\\nGeneralSettings.ShowWelcomeDialog=0\\n\" > /home/user/.config/ParaView/ParaView5.11.2.ini; chown -R user:user /home/user; rm -rf /root/build_ctx /root/install.log* /root/install.rc /root/.bash_history /var/lib/cloud/instances/* /var/log/cloud-init*.log /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /home/user/.ssh/authorized_keys; journalctl --rotate; journalctl --vacuum-time=1s; sync" >/dev/null 2>&1; sudo systemctl poweroff' || true
for i in $(seq 1 60); do kill -0 "$(cat qemu.pid)" 2>/dev/null || break; sleep 3; done
qemu-img convert -O qcow2 -m 8 build.qcow2 "$OUT.new" && ! qemu-img info "$OUT.new" | grep -qi backing && mv "$OUT.new" "$OUT"
md5=$(md5sum "$OUT" | cut -d' ' -f1); echo "$md5  ubuntu_openfoam.qcow2" > "$OUT.md5"
printf '{\n  "snapshot": "ubuntu_openfoam",\n  "image": "ubuntu_openfoam.qcow2",\n  "md5": "%s",\n  "bytes": %s,\n  "built": "%s",\n  "base": "noble-server-cloudimg-amd64.img + install_xfce_openfoam.sh"\n}\n' "$md5" "$(stat -c %s "$OUT")" "$(date +%FT%T)" > "${OUT%.qcow2}.manifest.json"
say "done: $OUT  md5 $md5   — now: scripts/vm_prep/physics/verify_image.sh --port 5100"
