#!/bin/sh
# Entry point of a kdf-swaplab node. /kdf is the node's run directory on the host (owned by
# the host user, mode 0700). The harness writes /kdf/MM2.json (0600) and /kdf/coins before the
# container starts; secrets are never passed through the environment. MM_LOG is set per run
# by the compose file, so every run gets its own log under /kdf/logs/.
set -eu
# An explicit command runs instead of the node, e.g. `docker run --rm <image> kdf --version`.
if [ "$#" -gt 0 ]; then exec "$@"; fi
cd /kdf
[ -f MM2.json ] || { echo "[swaplab] /kdf/MM2.json missing; start nodes with swaplab.py" >&2; exit 2; }
[ -f coins ] || { echo "[swaplab] /kdf/coins missing" >&2; exit 2; }
mkdir -p DB logs
export MM_CONF_PATH=/kdf/MM2.json MM_COINS_PATH=/kdf/coins
export MM_LOG="${MM_LOG:-/kdf/logs/kdf.log}"
echo "[swaplab] starting $(kdf --version 2>&1 | head -n1 || echo kdf), log ${MM_LOG}"
exec kdf
