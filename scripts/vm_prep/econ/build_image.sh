#!/usr/bin/env bash
# Build data/econ/vm/ubuntu_econ.qcow2: OSWorld base + gretl + LibreOffice Calc.
#   scripts/vm_prep/econ/build_image.sh [port]     # default 5160
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_econ "${1:-5160}" "$HERE/provision_guest.sh"
