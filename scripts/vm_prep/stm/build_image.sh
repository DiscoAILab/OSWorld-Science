#!/usr/bin/env bash
# Build data/stm/vm/ubuntu_stm.qcow2: the OSWorld base + the STM-Bench console (see README.md in this directory).
#   STM_TASKS_JSON=<private tasks.json> scripts/vm_prep/stm/build_image.sh [port]      # default 5190
#   STM_SETUP_ARGS="--smoke"          also start the console once through stm-start, screenshot, then reset
#   STM_SETUP_ARGS="--no-tasks"       bake no task list (the seeds then reach the guest another way)
#   STM_COMMIT=<40 hex>               the pinned commit (pins.sh);
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
case " ${STM_SETUP_ARGS:-} " in
  *" --no-tasks "*) ;;
  *) : "${STM_TASKS_JSON:?the private task list: STM_TASKS_JSON=<file> (or STM_SETUP_ARGS=--no-tasks)}" ;;
esac
exec "$HERE/../common/build_image.sh" ubuntu_stm "${1:-5190}" "$HERE/provision_guest.sh"
