#!/usr/bin/env bash
# Install the R / RKWard / LibreOffice desktop of the `ubuntu_rkward` snapshot into a running guest.
#
#   scripts/vm_prep/linguistics/provision_rkward.sh --port 5160
#
# This is also what `osci vm reset --snapshot ubuntu_rkward` runs when the booted
# image turns out not to have RKWard (i.e. you booted the pristine base).
set -euo pipefail
PORT=5160
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
exec uv --directory "$REPO" run python "$HERE/../common/guest_run.py" rkward_install "$HERE/rkward_guest.sh" --port "$PORT" --minutes 45
