"""Secret detection for exported evidence and for commits.

find_leaks() reports, for a set of files, every place where
  - a known secret value (wallet passphrases, RPC passwords, seed-node passphrase) appears,
  - any four consecutive words of a known mnemonic appear,
  - a run of 12 or more BIP39 words appears (an unknown mnemonic),
  - a JSON "passphrase"/"rpc_password"/"userpass" field carries a real value.
"""
import functools
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER = re.compile(r"^(<[^>]*>|\*+|REDACTED|CHANGE_ME|)$", re.I)
FIELD = re.compile(r'"(passphrase|rpc_password|userpass|wallet_password)"\s*:\s*"([^"]*)"')


@functools.lru_cache(maxsize=1)
def _words():
    with open(os.path.join(HERE, "bip39_english.txt")) as f:
        return frozenset(f.read().split())


def _bip39_runs(text, words, min_run=12):
    run, hits = 0, []
    for tok in re.findall(r"[a-z]+|[^a-z\s]+", text):
        if tok in words:
            run += 1
            if run == min_run:
                hits.append("run of %d+ BIP39 words" % min_run)
        else:
            run = 0
    return hits


def text_leaks(text, secrets):
    leaks = []
    for s in secrets:
        if not s:
            continue
        if s in text:
            leaks.append("known secret value")
        ws = s.split()
        if len(ws) >= 4:
            for k in range(len(ws) - 3):
                if " ".join(ws[k:k + 4]) in text:
                    leaks.append("4 consecutive words of a known mnemonic")
                    break
    for m in FIELD.finditer(text):
        if not PLACEHOLDER.match(m.group(2)):
            leaks.append(f'"{m.group(1)}" field with a value')
    leaks += _bip39_runs(text.lower(), _words())
    return sorted(set(leaks))


def find_leaks(paths, secrets):
    out = {}
    for p in paths:
        try:
            with open(p, errors="replace") as f:
                t = f.read()
        except OSError:
            continue
        leaks = text_leaks(t, secrets)
        if leaks:
            out[p] = leaks
    return out


def redact_lines(text, secrets):
    """Drop every line carrying a secret (used for KDF logs before export)."""
    keep = []
    for line in text.splitlines(keepends=True):
        if text_leaks(line, secrets):
            keep.append("[swaplab: line removed, it contained secret material]\n")
        else:
            keep.append(line)
    return "".join(keep)
