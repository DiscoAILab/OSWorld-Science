#!/usr/bin/env bash
# Install the radiology toolchain into a running guest (also what `osci vm reset`
# runs when the tools probe fails): Weasis, then 3D Slicer, then Slicer's
# first-run settings.
#   scripts/vm_prep/radiology/provision_guest.sh --port 5050 [--slicer-tarball FILE]
set -euo pipefail
PORT=5050; TARBALL=""
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; --slicer-tarball) TARBALL=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PY="uv --directory $REPO run python"
has() { curl -s -X POST "http://localhost:$PORT/execute" -H 'Content-Type: application/json' --max-time 60 \
  -d "{\"command\":[\"bash\",\"-lc\",\"$1\"]}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("output") or "").strip())' 2>/dev/null; }
if [ -z "$(has 'ls /opt/weasis/bin/Weasis 2>/dev/null')" ]; then
  $PY "$HERE/weasis_setup.py" --port "$PORT"
else echo "Weasis present"; fi
if [ -z "$(has 'ls /opt/Slicer/Slicer 2>/dev/null')" ]; then
  if [ -n "$TARBALL" ]; then $PY "$HERE/slicer_setup.py" --port "$PORT" --tarball "$TARBALL"
  else $PY "$HERE/slicer_setup.py" --port "$PORT"; fi
  $PY "$HERE/../common/guest_run.py" slicer_firstrun "$HERE/slicer_firstrun.sh" --port "$PORT" --minutes 10
else echo "3D Slicer present"; fi
