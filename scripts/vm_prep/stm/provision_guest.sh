#!/usr/bin/env bash
# Host-side provisioner of the ubuntu_stm guest, called by common/build_image.sh as `provision_guest.sh --port P`.
#   STM_TASKS_JSON=<private tasks.json> scripts/vm_prep/stm/provision_guest.sh --port 5190 [--smoke] [--no-tasks]
# Extra arguments go to stm_setup.py (--smoke, --no-tasks, --no-scrub); the build_image.sh wrapper passes them through
# STM_SETUP_ARGS. The private task list is an input of the build, never a file of this repository.
set -euo pipefail
PORT=5190
EXTRA=()
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) EXTRA+=("$1"); shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
# shellcheck disable=SC2086
exec uv --directory "$REPO" run python "$HERE/stm_setup.py" --port "$PORT" ${EXTRA[@]+"${EXTRA[@]}"} ${STM_SETUP_ARGS:-}
