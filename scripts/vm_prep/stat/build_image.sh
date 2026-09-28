#!/usr/bin/env bash
# Build data/stat/vm/ubuntu_stat.qcow2: base + statenv (R 4.4.3 toolchain from statenv.lock)
# + root-partition growth, RStudio, Python stats packages, ImageMagick.
#
#   scripts/vm_prep/stat/build_image.sh [port]      # default 5040
#
# No SAS: the legacy image carried a licensed host install that cannot be
# redistributed. The Firefox profile for SAS OnDemand
# is likewise a local, unpublished artefact (restore_firefox_profile.py).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_stat "${1:-5040}" \
  "$HERE/extras_guest.sh" "$HERE/provision_guest.sh"
