#!/usr/bin/env bash
# Shared image builder: boot the pristine base as <snapshot>, run the domain's
# provisioner, bake to data/<domain>/vm/<snapshot>.qcow2.
#
#   scripts/vm_prep/common/build_image.sh <snapshot> <port> <provision-script> [extra provisioners…]
#
# Uses `osci vm` for the container lifecycle so the docker parameters live in
# exactly one place (src/osworld_science/vm/docker.py).
set -euo pipefail
SNAPSHOT=$1; PORT=$2; shift 2
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
VM_DIR=${OSCI_VM_DIR:-$REPO/vm}
BASE=$VM_DIR/base/Ubuntu.qcow2
[ -s "$BASE" ] || { echo "no base image at $BASE — run scripts/vm_prep/base/download_base.sh"; exit 1; }
cd "$REPO"
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "booting the pristine base as $SNAPSHOT on :$PORT (fresh container)"
uv run osci vm start --snapshot "$SNAPSHOT" --port "$PORT" --image "$BASE" --rebuild

for prov in "$@"; do
  say "provisioning: $prov"
  case "$prov" in
    *.py) uv run python "$prov" --port "$PORT" ;;
    *)    bash "$prov" --port "$PORT" ;;
  esac
done

say "what a solver's terminal sees:"
uv run osci vm check --snapshot "$SNAPSHOT" --port "$PORT"

say "baking to $VM_DIR/images/$SNAPSHOT.qcow2"
uv run osci vm bake --snapshot "$SNAPSHOT" --port "$PORT" --out "$VM_DIR/images/$SNAPSHOT.qcow2"
say "done. Cold-start check: uv run osci vm reset --snapshot $SNAPSHOT --port $PORT"
