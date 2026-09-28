#!/usr/bin/env bash
# Install the statistics toolchain into a running guest, in user space.
#
#   scripts/vm_prep/stat/provision_guest.sh --port 5040
#
# micromamba → /home/user/statenv from statenv.lock (302 conda-forge packages
# pinned to build+hash: R 4.4.3, sqlite3 3.53.4, shiny, plotly, sf, readxl,
# jsonlite, ggplot2, dplyr, tidyr, scales, copula 1.1-6, glmnet, fastglm,
# png/jpeg/magick, the recommended set, ImageMagick 7). The lock matters:
# stat_sim_slope_v1 is graded on R's RNG stream to 1e-9, so "R 4.4, whatever
# solves today" is drift, not a specification. Without the lock the script
# falls back to a version-pinned solve.
#
# This is also what `osci vm reset --snapshot ubuntu_stat` runs when the
# booted image turns out not to have R (i.e. you booted the pristine base).
set -o pipefail
PORT=5040
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
LOCK=$HERE/statenv.lock
HOST=http://localhost:$PORT

exec_guest() {   # exec_guest <command string> [timeout]
  curl -s -X POST "$HOST/execute" -H 'Content-Type: application/json' --max-time "${2:-120}" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"command":["bash","-lc",sys.argv[1]]}))' "$1")"
}
out() { python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("output") or "")+(d.get("error") or ""))' 2>/dev/null; }
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "guest check"
exec_guest 'echo alive; . /etc/os-release; echo $PRETTY_NAME' | out || { echo "no guest on :$PORT"; exit 1; }

INSTALLER=$(cat <<'GUEST'
set -e
PREFIX=/home/user/statenv
LOG=/home/user/provision.log
exec >"$LOG" 2>&1
echo "=== started $(date) ==="
if [ ! -x /home/user/bin/micromamba ]; then
  echo "--- fetching micromamba ---"
  mkdir -p /home/user/bin
  cd /home/user
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj bin/micromamba
fi
export MAMBA_ROOT_PREFIX=/home/user/mamba
MM=/home/user/bin/micromamba
if [ -s /home/user/statenv.lock ]; then
  echo "--- installing from the explicit lock ---"
  "$MM" create -y -p "$PREFIX" --file /home/user/statenv.lock
else
  echo "--- no lock file; solving against conda-forge ---"
  "$MM" create -y -p "$PREFIX" -c conda-forge \
    r-base=4.4.3 sqlite=3.53.4 r-jsonlite r-readxl r-shiny r-plotly r-sf \
    r-ggplot2 r-dplyr r-tidyr r-scales r-copula=1.1_6 r-glmnet r-magick
fi
echo "--- exposing on PATH ---"
mkdir -p /home/user/.local/bin
for b in R Rscript sqlite3; do ln -sf "$PREFIX/bin/$b" "/home/user/.local/bin/$b"; done
for rc in /home/user/.profile /home/user/.bashrc; do
  touch "$rc"
  grep -q 'statenv/bin' "$rc" || cat >>"$rc" <<'EOF2'

# statistics toolchain (R, sqlite3) — added by OSWorld-Science provisioning
export PATH="/home/user/statenv/bin:/home/user/.local/bin:$PATH"
EOF2
done
echo "--- versions ---"
"$PREFIX/bin/R" --version | head -1
"$PREFIX/bin/sqlite3" --version
"$PREFIX/bin/Rscript" -e 'for (p in c("shiny","plotly","sf","readxl","jsonlite","ggplot2","dplyr","copula")) cat(sprintf("%-10s %s\n", p, as.character(packageVersion(p)))); stopifnot(as.character(packageVersion("copula")) == "1.1.6")'
rm -f /home/user/statenv.lock
"$MM" clean -a -y >/dev/null 2>&1 || true
echo "=== done $(date) ==="
touch /home/user/provision.done
GUEST
)

if [ -s "$LOCK" ]; then
  say "uploading the environment lock ($(wc -l <"$LOCK") lines)"
  uv --directory "$REPO" run python - "$LOCK" "$PORT" <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[0]).resolve().parents[0]))
from osworld_science.guest.client import Guest
Guest(int(sys.argv[2])).upload(pathlib.Path(sys.argv[1]), "/home/user/statenv.lock")
print("uploaded")
PY
else
  say "no statenv.lock — will solve against conda-forge instead"
  exec_guest "rm -f /home/user/statenv.lock" | out
fi

say "writing installer into the guest"
B64=$(printf '%s' "$INSTALLER" | base64 -w0)
exec_guest "rm -f /home/user/provision.done /home/user/provision.log; \
            printf %s '$B64' | base64 -d > /home/user/provision.sh; chmod +x /home/user/provision.sh; wc -l /home/user/provision.sh" | out
say "launching it detached (conda solves take much longer than /execute allows)"
exec_guest 'setsid nohup bash /home/user/provision.sh >/dev/null 2>&1 < /dev/null & echo launched' | out

say "polling for completion (up to 45 min)"
DONE=0
for i in $(seq 1 270); do
  sleep 10
  if exec_guest 'test -f /home/user/provision.done && echo FLAG' 30 | out | grep -q FLAG; then
    DONE=1; say "installer finished after ~$((i * 10 / 60)) min"; break
  fi
  if [ $((i % 6)) = 0 ]; then say "  … $(exec_guest 'tail -1 /home/user/provision.log 2>/dev/null | cut -c1-90' 30 | out)"; fi
done
if [ "$DONE" != 1 ]; then
  say "!! poll loop exhausted — this is a timeout, not a success"
  exec_guest 'tail -30 /home/user/provision.log' | out; exit 1
fi
say "installed:"; exec_guest 'tail -15 /home/user/provision.log' | out
exec_guest 'rm -f /home/user/provision.sh /home/user/provision.log /home/user/provision.done' | out
say "verifying the way a solver's terminal would see it"
exec_guest 'command -v R Rscript sqlite3' | out
