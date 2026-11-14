#!/usr/bin/env bash
# Build the isolated R judge image of the hoosier_phonotactics_lab1 grader on this host
# and pin its id in the task package.
#
#   scripts/vm_prep/linguistics/build_r_judge.sh
#
# The grader never runs a submitted Analysis.R on the host: it replays it in a
# networkless, read-only container built from the task package's own
# grading/Dockerfile.rjudge (Ubuntu 22.04 by digest, r-base-core, r-cran-tidyverse).
# The package refuses any image but the one named by id in private/judge_image.json,
# and an image id is local to the Docker daemon that built it, so every grading host
# builds the image once and records its own id there. Reviews are bound to that file:
# rebuilding the image on a host invalidates reviews written against the old one.
# Needs Docker (rootless is fine) and outbound network for the build.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
DATA=$(uv --directory "$REPO" run python -c "from osworld_science.config import Settings; print(Settings.load().data_dir)")
PKG=$DATA/linguistics/private/reference_private/hoosier_phonotactics_lab1/package
[ -f "$PKG/grading/Dockerfile.rjudge" ] || { echo "task package not found under $PKG (download the linguistics data first)"; exit 1; }
if [ -z "${DOCKER_HOST:-}" ] && [ -S "/run/user/$(id -u)/docker.sock" ]; then
  export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
fi
TAG=osworld-science/hoosier-r-judge:local
docker build -f "$PKG/grading/Dockerfile.rjudge" -t "$TAG" "$PKG/grading"
ID=$(docker image inspect -f '{{.Id}}' "$TAG")
python3 - "$PKG/private/judge_image.json" "$ID" "$TAG" <<'PY'
import datetime, json, re, sys
path, image_id, tag = sys.argv[1:4]
assert re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), image_id
try:
    old = json.load(open(path))
except (OSError, ValueError):
    old = {}
new = {"image_id": image_id, "tag": tag, "base_digest": old.get("base_digest"),
       "built_utc": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
       "note": "Immutable local Docker image identity is mandatory. Tag is informational only. "
               "Pinned on this host by scripts/vm_prep/linguistics/build_r_judge.sh."}
json.dump(new, open(path, "w"), separators=(",", ":"))
print("pinned", image_id, "in", path)
PY
