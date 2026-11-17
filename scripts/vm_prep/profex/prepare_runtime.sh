#!/usr/bin/env bash
# Extract only glibc/GCC runtime libraries from a pinned Ubuntu 24 image.
# No GPU is used or required; the desktop remains the OSWorld Ubuntu 22 guest.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
CACHE="$REPO/.cache/profex"
IMAGE=nvidia/cuda@sha256:f8ef28f579ea42a44b415d2c5d46f788e6a9b395c6c83f2929416e1fc192c143
mkdir -p "$CACHE"
if [ ! -s "$CACHE/profex-ubuntu24-runtime.tar" ]; then
  docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
  docker run --rm --network none --user "$(id -u):$(id -g)" \
    --entrypoint /bin/sh -v "$CACHE:/out" "$IMAGE" -c '
      tar -chf /out/profex-ubuntu24-runtime.tar -C /usr/lib/x86_64-linux-gnu \
        ld-linux-x86-64.so.2 libc.so.6 libm.so.6 libpthread.so.0 libdl.so.2 \
        librt.so.1 libresolv.so.2 libstdc++.so.6 libgcc_s.so.1
      dpkg-query -W libc6 libstdc++6 libgcc-s1 > /out/runtime-packages.txt
    '
fi
sha256sum "$CACHE/profex-ubuntu24-runtime.tar"
