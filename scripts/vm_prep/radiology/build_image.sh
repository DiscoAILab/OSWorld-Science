#!/usr/bin/env bash
# Build data/biomed/vm/ubuntu_radiology.qcow2: base + Weasis 4.7.3 + 3D Slicer 5.12.3 (+ first-run config).
#   scripts/vm_prep/radiology/build_image.sh [port]     # default 5050
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_radiology "${1:-5050}" "$HERE/provision_guest.sh"
