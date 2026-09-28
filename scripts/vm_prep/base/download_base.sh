#!/usr/bin/env bash
# Download the pristine OSWorld Ubuntu image (the one every snapshot is built from).
#
#   scripts/vm_prep/base/download_base.sh [dest-dir]     # default $OSCI_VM_DIR/base or ./vm/base
#
# Source: https://huggingface.co/datasets/xlangai/ubuntu_osworld (Ubuntu.qcow2.zip, ~24 GB unpacked).
# The image is never modified: the runtime mounts it read-only and refuses images with a backing file.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
DEST=${1:-${OSCI_VM_DIR:-$REPO/vm}/base}
URL=${OSWORLD_UBUNTU_URL:-https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip}
if [ -n "${HF_ENDPOINT:-}" ] && echo "$HF_ENDPOINT" | grep -q hf-mirror.com; then
  URL=${URL/huggingface.co/hf-mirror.com}
fi
mkdir -p "$DEST"
if [ -s "$DEST/Ubuntu.qcow2" ]; then
  echo "already present: $DEST/Ubuntu.qcow2"; exit 0
fi
echo "downloading $URL -> $DEST/Ubuntu.qcow2.zip (resumable)"
curl -L -C - --retry 10 --retry-delay 5 -o "$DEST/Ubuntu.qcow2.zip" "$URL"
echo "unzipping"
( cd "$DEST" && unzip -o Ubuntu.qcow2.zip && rm -f Ubuntu.qcow2.zip )
qemu-img info "$DEST/Ubuntu.qcow2" | head -4
if qemu-img info "$DEST/Ubuntu.qcow2" | grep -qi "backing file"; then
  echo "!! downloaded image has a backing file — unexpected"; exit 1
fi
md5sum "$DEST/Ubuntu.qcow2" | tee "$DEST/Ubuntu.qcow2.md5"
echo "done"
