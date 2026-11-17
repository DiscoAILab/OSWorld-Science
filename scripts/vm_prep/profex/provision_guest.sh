#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
exec "$REPO/.venv/bin/python" "$HERE/profex_setup.py" "$@"
