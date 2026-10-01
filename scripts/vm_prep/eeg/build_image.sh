#!/usr/bin/env bash
# Build data/eeg/vm/ubuntu_eeglab.qcow2: base + GNU Octave (signal, statistics) + EEGLAB 2025.1.0 at /opt/eeglab.
#   scripts/vm_prep/eeg/build_image.sh [port]     # default 5150
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../common/build_image.sh" ubuntu_eeglab "${1:-5150}" "$HERE/provision_guest.sh"
