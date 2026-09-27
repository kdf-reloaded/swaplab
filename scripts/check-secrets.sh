#!/bin/sh
# Refuses a commit that would carry secret material. Checks the files staged for commit (or,
# with --all, every tracked and untracked non-ignored file) for:
#   - paths that must never be committed (secrets/, run/, out/, bin/, config/binaries.json)
#   - any wallet passphrase or RPC password known to this checkout
#   - runs of 12+ BIP39 words, 4+ consecutive words of a known mnemonic
#   - "passphrase" / "rpc_password" / "userpass" JSON fields with a real value
# Install as a pre-commit hook:  ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
set -eu
cd "$(git rev-parse --show-toplevel)"
if [ "${1:-}" = "--all" ]; then
  files=$(git ls-files --cached --others --exclude-standard)
else
  files=$(git diff --cached --name-only --diff-filter=ACMR)
fi
[ -n "$files" ] || { echo "check-secrets: nothing to check"; exit 0; }
printf '%s\n' "$files" | python3 -c '
import os, re, sys
sys.path.insert(0, ".")
from lib import scrub, wallets
bad = re.compile(r"^(secrets|run|out|bin)/|^config/binaries\.json$")
known = wallets.all_secret_strings()
for root, _, fns in os.walk("run"):
    for fn in fns:
        if fn in ("MM2.json", "seed-identity"):
            try:
                import json
                p = os.path.join(root, fn)
                t = open(p).read()
                if fn == "MM2.json":
                    d = json.loads(t); known += [d.get("passphrase"), d.get("rpc_password")]
                else:
                    known.append(t.strip())
            except Exception:
                pass
known = [k for k in known if k]
fail = 0
for f in sys.stdin.read().split("\n"):
    if not f:
        continue
    if bad.search(f):
        print(f"check-secrets: {f}: path must never be committed"); fail = 1; continue
    if f == "lib/bip39_english.txt" or not os.path.isfile(f):
        continue
    leaks = scrub.find_leaks([f], known)
    for p, why in leaks.items():
        print(f"check-secrets: {p}: {why}"); fail = 1
if fail:
    print("check-secrets: REFUSED"); sys.exit(1)
print("check-secrets: clean")
'
