#!/usr/bin/env bash
# Build ubuntu_rkward.qcow2: base + R, RKWard, tidyverse, Praat, LibreOffice Writer/Calc, Evince.
#   scripts/vm_prep/linguistics/build_rkward_image.sh [port]     # default 5160
# The result lands in vm/images/; move it (with its .md5 and manifest) to data/linguistics/vm/.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_rkward "${1:-5160}" "$HERE/provision_rkward.sh"
