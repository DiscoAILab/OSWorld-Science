#!/usr/bin/env bash
#   scripts/vm_prep/econ/provision_guest.sh --port 5160
set -euo pipefail
PORT=5160
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
exec uv --directory "$REPO" run python "$HERE/econ_setup.py" --port "$PORT"
