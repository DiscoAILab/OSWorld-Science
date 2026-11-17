#!/usr/bin/env bash
# Build the grader image of the stm tasks on this host: stmbench-grader:<first 12 hex digits of the commit>.
#
#   scripts/vm_prep/stm/build_grader.sh            # the commit pinned in pins.sh (or $STM_COMMIT)
#
# The grader replays an exported episode with `python -m stmbench.cli grade` (see deploy/osworld/README.md of the
# repository) inside a networkless container; data/stm/private/reference_private/<id>/gt.json names the image tag.
# The image is built from deploy/osworld/grader/Dockerfile of the SAME commit the console in the guest runs.
# Like linguistics/build_r_judge.sh it needs a container engine (Docker, rootless is fine; podman works too:
# CONTAINER_CLI=podman) and outbound network for the build. Source of the tree, first that applies:
#   STM_SRC_DIR=<checkout>   a local checkout (its HEAD must start with the commit)
#   STM_SRC_TARBALL=<file>   `git archive` of the commit (taken on trust: $STM_COMMIT must name it)
#   otherwise                fetch the commit from $STM_REPO_URL into a temporary directory.
# Other knobs: PYTHON_IMAGE=python:3.13-slim@sha256:<digest> fixes the base image; ENGINE_ARGS="--storage-driver vfs"
# style extra engine arguments are placed before the engine's subcommand.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$HERE/pins.sh"
CLI=${CONTAINER_CLI:-docker}
if [ "$CLI" = docker ] && [ -z "${DOCKER_HOST:-}" ] && [ -S "/run/user/$(id -u)/docker.sock" ]; then
  export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
fi
# shellcheck disable=SC2206
ENGINE=($CLI ${ENGINE_ARGS:-})

case "$STM_COMMIT" in *[!0-9a-f]*|"") echo "STM_COMMIT is not a hex commit"; exit 1;; esac
[ "${#STM_COMMIT}" -ge 12 ] || { echo "STM_COMMIT needs at least 12 hex digits"; exit 1; }
if [ "${STM_COMMIT//0/}" = "" ]; then echo "STM_COMMIT is still the placeholder (pins.sh)"; exit 1; fi
TAG=stmbench-grader:${STM_COMMIT:0:12}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
SRC=$WORK/src
mkdir -p "$SRC"
if [ -n "${STM_SRC_DIR:-}" ]; then
  HEAD=$(git -C "$STM_SRC_DIR" rev-parse HEAD)
  case "$HEAD" in "${STM_COMMIT}"*) ;; *) echo "$STM_SRC_DIR is at ${HEAD:0:12}, not ${STM_COMMIT:0:12}"; exit 1;; esac
  git -C "$STM_SRC_DIR" archive "$HEAD" | tar -x -C "$SRC"
elif [ -n "${STM_SRC_TARBALL:-}" ]; then
  tar -x -f "$STM_SRC_TARBALL" -C "$SRC"
else
  git -C "$WORK" init -q fetched
  git -C "$WORK/fetched" fetch -q --depth 1 "$STM_REPO_URL" "$STM_COMMIT"
  HEAD=$(git -C "$WORK/fetched" rev-parse FETCH_HEAD)
  case "$HEAD" in "${STM_COMMIT}"*) ;; *) echo "fetched ${HEAD:0:12}, not ${STM_COMMIT:0:12}"; exit 1;; esac
  git -C "$WORK/fetched" archive "$HEAD" | tar -x -C "$SRC"
fi
for f in deploy/osworld/grader/Dockerfile deploy/osworld/grader/requirements.txt pyproject.toml; do
  [ -f "$SRC/$f" ] || { echo "the commit has no $f"; exit 1; }
done

ARGS=()
[ -z "${PYTHON_IMAGE:-}" ] || ARGS+=(--build-arg "PYTHON_IMAGE=$PYTHON_IMAGE")
"${ENGINE[@]}" build ${ARGS[@]+"${ARGS[@]}"} -f "$SRC/deploy/osworld/grader/Dockerfile" -t "$TAG" "$SRC"

# The image's user is `grader`, uid 10001 (the adapter reads the export as that user: files 0644, directories 0755).
# Checked from the image's metadata and passwd file, with --user 0 for the two small runs, so that the check also
# works on a rootless engine whose user namespace maps a single uid (it cannot start a container as uid 10001).
CFG_USER=$("${ENGINE[@]}" image inspect -f '{{.Config.User}}' "$TAG")
[ "$CFG_USER" = grader ] || { echo "the image's user is '$CFG_USER', not grader"; exit 1; }
PW=$("${ENGINE[@]}" run --rm --network none --user 0 --entrypoint getent "$TAG" passwd grader)
case "$PW" in grader:x:10001:*) ;; *) echo "grader is not uid 10001 in the image: $PW"; exit 1;; esac
"${ENGINE[@]}" run --rm --network none --user 0 "$TAG" grade --help >/dev/null
ID=$("${ENGINE[@]}" image inspect -f '{{.Id}}' "$TAG")
echo "built $TAG ($ID)"
echo "put \"grader_image\": \"$TAG\" into data/stm/private/reference_private/<id>/gt.json"
