#!/usr/bin/env bash
# Build data/physics/vm/ubuntu_openfoam.qcow2 from the pristine base: start → provision → check → bake.
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_openfoam "${1:-5100}" "$HERE/provision_guest.sh"
