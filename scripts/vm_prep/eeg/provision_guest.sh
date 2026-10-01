#!/usr/bin/env bash
# Provision the ubuntu_eeglab guest: GNU Octave + signal/statistics packages + EEGLAB 2025.1.0 at /opt/eeglab.
# Mirrors docker/Dockerfile.octave of the EEGLAB CUA Benchmark. Run inside the booted OSWorld base
# (user `user`, sudo password `password`); long steps go through scripts/vm_prep/common/guest_run.py.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install --no-install-recommends -y octave octave-signal octave-statistics git ca-certificates
sudo git clone --depth 1 --branch 2025.1.0 --recurse-submodules --shallow-submodules https://github.com/sccn/eeglab.git /opt/eeglab
sudo rm -rf /opt/eeglab/.git
sudo chown -R user:user /opt/eeglab
# Make EEGLAB reachable from any Octave session the solver opens.
mkdir -p /home/user/.config/octave
grep -q "/opt/eeglab" /home/user/.octaverc 2>/dev/null || printf "addpath('/opt/eeglab');\n" >> /home/user/.octaverc
echo "EEGLAB_PATH=/opt/eeglab" | sudo tee -a /etc/environment >/dev/null
octave --no-gui --quiet --eval "addpath('/opt/eeglab'); eeglab nogui; disp(eeg_getversion())"
