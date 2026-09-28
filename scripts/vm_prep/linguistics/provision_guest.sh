#!/usr/bin/env bash
#   scripts/vm_prep/linguistics/provision_guest.sh --port 5080
set -euo pipefail
PORT=5080
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
exec uv --directory "$REPO" run python "$HERE/praat_setup.py" --port "$PORT"
