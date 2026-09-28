#!/usr/bin/env bash
# Build data/linguistics/vm/ubuntu_linguistics.qcow2: base + Praat 6.2.09 (with sendpraat).
#   scripts/vm_prep/linguistics/build_image.sh [port]     # default 5080
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_linguistics "${1:-5080}" "$HERE/provision_guest.sh"
