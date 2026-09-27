"""Wallet secrets: generation, import, storage.

A wallet is secrets/<NAME>.json (mode 0600, directory 0700):
    {"passphrase": "<mnemonic or legacy passphrase>", "enable_hd": false,
     "wallet_dir": "secrets/<NAME>.db" or null}
The passphrase never goes into argv, environment variables, compose files or logs written by the
harness. It reaches KDF only through the per-node MM2.json (0600) in run/.
"""
import hashlib
import json
import os
import secrets as _rnd
import shutil
import stat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SECRETS = os.path.join(ROOT, "secrets")


def _wordlist():
    with open(os.path.join(HERE, "bip39_english.txt")) as f:
        words = f.read().split()
    assert len(words) == 2048
    return words


def new_mnemonic(strength_bits=256):
    """BIP39 mnemonic (24 words for 256 bits) from os.urandom via the secrets module."""
    ent = _rnd.token_bytes(strength_bits // 8)
    cs_bits = strength_bits // 32
    h = hashlib.sha256(ent).digest()
    bits = int.from_bytes(ent, "big") << cs_bits | (h[0] >> (8 - cs_bits))
    total = strength_bits + cs_bits
    words = _wordlist()
    return " ".join(words[(bits >> (total - 11 * (k + 1))) & 0x7FF] for k in range(total // 11))


def check_mnemonic(m):
    words = _wordlist()
    ws = m.split()
    if len(ws) not in (12, 15, 18, 21, 24) or any(w not in words for w in ws):
        return False
    bits = 0
    for w in ws:
        bits = bits << 11 | words.index(w)
    total = len(ws) * 11
    cs = total // 33
    ent = (bits >> cs).to_bytes((total - cs) // 8, "big")
    return (bits & ((1 << cs) - 1)) == hashlib.sha256(ent).digest()[0] >> (8 - cs)


def _ensure_dir():
    os.makedirs(SECRETS, exist_ok=True)
    os.chmod(SECRETS, 0o700)


def path(name):
    return os.path.join(SECRETS, f"{name}.json")


def exists(name):
    return os.path.exists(path(name))


def save(name, data, overwrite=False):
    _ensure_dir()
    p = path(name)
    if os.path.exists(p) and not overwrite:
        raise SystemExit(f"wallet {name} already exists ({p}); refusing to overwrite")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.chmod(p, 0o600)


def load(name):
    p = path(name)
    if not os.path.exists(p):
        raise SystemExit(f"wallet {name} not found: create it with `swaplab.py wallet new {name}` "
                         f"or `swaplab.py wallet import {name} --seed-file FILE`")
    mode = stat.S_IMODE(os.stat(p).st_mode)
    if mode & 0o077:
        raise SystemExit(f"{p} is readable by others (mode {oct(mode)}); run: chmod 600 {p}")
    with open(p) as f:
        return json.load(f)


def create(name, enable_hd=False):
    save(name, {"passphrase": new_mnemonic(), "enable_hd": enable_hd, "wallet_dir": None})


def import_(name, seed_file, wallet_dir=None, enable_hd=False, overwrite=False):
    with open(seed_file) as f:
        phrase = " ".join(f.read().split())
    if not phrase:
        raise SystemExit(f"{seed_file} is empty")
    data = {"passphrase": phrase, "enable_hd": enable_hd, "wallet_dir": None,
            "bip39_valid": check_mnemonic(phrase)}
    if wallet_dir:
        dst = os.path.join(SECRETS, f"{name}.db")
        if os.path.exists(dst):
            raise SystemExit(f"{dst} exists; remove it first if you really want to re-import")
        shutil.copytree(wallet_dir, dst)  # a copy: the original directory is never run
        os.chmod(dst, 0o700)
        data["wallet_dir"] = os.path.relpath(dst, ROOT)
    save(name, data, overwrite=overwrite)
    return data


def all_secret_strings():
    """Every secret value the harness knows about, for scrubbing."""
    out = []
    if os.path.isdir(SECRETS):
        for fn in os.listdir(SECRETS):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(SECRETS, fn)) as f:
                        d = json.load(f)
                    if d.get("passphrase"):
                        out.append(d["passphrase"])
                except (OSError, ValueError):
                    pass
    return out
