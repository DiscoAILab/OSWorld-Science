#!/usr/bin/env bash
# Capture the guest's Firefox profile after a successful manual SAS sign-in.
#
#   ./snapshot_firefox_profile.sh [port]        # default 5040
#
# Do this once, by hand, whenever the SAS session expires:
#   1  open the guest desktop at http://localhost:<PORT+3006>
#   2  sign in to SAS OnDemand in Firefox, tick "save password"
#   3  click "Clear my saved tabs" so the next launch opens an empty Studio
#   4  run this
# The archive is gitignored — it holds a live session and a saved password.
set -o pipefail
PORT=${1:-5040}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PY="uv --directory $REPO run python"

$PY - "$PORT" "$HERE" <<'PYEOF'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[2]).parents[2] / "src"))
from osworld_science.guest.client import Guest
g = Guest(int(sys.argv[1]))
g.execute(["bash", "-lc",
           "pkill -x firefox; sleep 8; echo closed"], 120)
r = g.execute(["bash", "-lc",
    "cd /home/user/snap/firefox/common/.mozilla && "
    "tar czf /home/user/ff_profile.tgz firefox && stat -c%s /home/user/ff_profile.tgz"], 300)
size = (r.get("output") or "").strip()
dest = pathlib.Path(sys.argv[2]) / "firefox_profile.tgz"
ok = g.pull("/home/user/ff_profile.tgz", dest)
print(f"{'saved' if ok else 'FAILED'} -> {dest} ({dest.stat().st_size:,} bytes)"
      if ok else "pull failed")
PYEOF
