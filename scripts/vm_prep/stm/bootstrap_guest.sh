#!/usr/bin/env bash
# In-guest bootstrap of the stm snapshot, run through common/guest_run.py as the agent's account (`user`) by stm_setup.py.
# It fetches the pinned STM-Bench commit, then runs that commit's own deploy/osworld/guest/install.sh, which does the
# real work (packages, account, Python, venv, files, directories, service). Inputs, uploaded by stm_setup.py to
# /tmp/stm-prov (mode 0700): install.env (the pins, from pins.sh) and tasks.json (the private task list).
# Nothing is printed from either file. The private copy is shredded at the end.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
P=/tmp/stm-prov
PW=${SUDO_PASSWORD:-password}
S() { echo "$PW" | sudo -S -E "$@"; }
trap 'shred -u -z "$P/tasks.json" 2>/dev/null || true; rm -rf "$P/repo" "$P/install.env"' EXIT

# shellcheck disable=SC1091
. "$P/install.env"
updated=0
for _ in $(seq 1 30); do
  if S apt-get -o DPkg::Lock::Timeout=180 update && S apt-get -o DPkg::Lock::Timeout=180 install --no-install-recommends -y git ca-certificates; then
    updated=1
    break
  fi
  sleep 5
done
[ "$updated" = 1 ]
rm -rf "$P/repo"; mkdir -p "$P/repo"
git -C "$P/repo" init -q
git -C "$P/repo" fetch -q --depth 1 "$STM_REPO_URL" "$STM_COMMIT"
HEAD=$(git -C "$P/repo" rev-parse FETCH_HEAD)
case "$HEAD" in "${STM_COMMIT}"*) ;; *) echo "fetched ${HEAD:0:12}, not ${STM_COMMIT:0:12}"; exit 1;; esac
mkdir -p "$P/src"
git -C "$P/repo" archive "$HEAD" deploy/osworld/guest | tar -x -C "$P/src"
STM_SKIP_APT_UPDATE=1 STM_TASKS_JSON="$P/tasks.json" STM_INSTALL_ENV="$P/install.env" S bash "$P/src/deploy/osworld/guest/install.sh"
rm -rf "$P/src"
gsettings set org.gnome.desktop.notifications show-banners false || true
gsettings set org.gnome.desktop.session idle-delay 0 || true
gsettings set org.gnome.desktop.screensaver lock-enabled false || true
echo "bootstrap done: ${HEAD:0:12}"
