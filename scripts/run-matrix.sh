#!/bin/bash
# Runs several scenarios one after another, unattended, and collects their summaries in one log.
#
#   scripts/run-matrix.sh [--wait-bug1 WALLET] SCENARIO...
#   e.g. scripts/run-matrix.sh --wait-bug1 W2 R-R G-R R-K K-R R-G
#
# --wait-bug1 WALLET  first wait (up to 4 h) until WALLET's KMD address holds a coin that carries
#                     a KMD reward (>= 10 KMD, older than one hour), so reward handling is tested.
# The matrix stops early if a run leaves containers running (a swap still active): the next run
# would refuse to start anyway, and the wallet must not be used twice.
# Log: out/matrix-<timestamp>.log (each run's SUMMARY.md is appended to it).
set -u
cd "$(dirname "$0")/.."
mkdir -p out
LOG="out/matrix-$(date +%Y%m%d-%H%M%S).log"
if [ "${1:-}" = "--wait-bug1" ]; then
  WALLET="$2"; shift 2
  echo "=== $(date +%H:%M:%S) waiting for a KMD reward-bearing coin in $WALLET" | tee -a "$LOG"
  python3 - "$WALLET" >> "$LOG" 2>&1 <<'PY' || { echo "=== precondition wait failed" | tee -a "$LOG"; exit 1; }
import json, subprocess, sys, time
sys.path.insert(0, ".")
from lib import electrum
out = subprocess.run(["./swaplab.py", "wallet", "addresses", sys.argv[1], "--coins", "KMD"],
                     capture_output=True, text=True).stdout
addr = [l.split()[1] for l in out.splitlines() if l.startswith("KMD:")][0]
servers = json.load(open("config/electrums.json"))["KMD"]
t0 = time.time()
while time.time() - t0 < 4 * 3600:
    try:
        s = electrum.utxo_snapshot(servers, addr, kmd=True)
        if s["bug1_precondition"]:
            print(time.strftime("%H:%M:%S"), "reward-bearing coin present:",
                  [(u["value"], u.get("age_minutes"), u.get("reward_allowed"), u.get("reward_pre_kip"))
                   for u in s["utxos"]])
            sys.exit(0)
    except Exception as e:  # noqa
        print("snapshot error", repr(e))
    time.sleep(120)
sys.exit(1)
PY
fi
for s in "$@"; do
  f="scenarios/$s.json"; [ -f "$s" ] && f="$s"
  echo "=== $(date +%H:%M:%S) $s" | tee -a "$LOG"
  ./swaplab.py run "$f" >> "$LOG" 2>&1
  echo "=== $s exit $?" | tee -a "$LOG"
  last=$(ls -dt out/*-"$(basename "$f" .json)" 2>/dev/null | head -1)
  [ -n "$last" ] && cat "$last/SUMMARY.md" >> "$LOG"
  if ./swaplab.py status | grep -q '^running:'; then
    echo "=== containers kept running after $s (active swap); matrix stopped" | tee -a "$LOG"
    break
  fi
done
echo "=== MATRIX DONE $(date +%H:%M:%S)" | tee -a "$LOG"
