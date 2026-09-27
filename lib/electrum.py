"""Minimal Electrum client (TCP or SSL) plus the KMD reward (interest) rules.

Used only for evidence and preflight: which coins a wallet holds, how old they are and how much
KMD reward the chain allows for them. The KDF nodes talk to electrum on their own.
"""
import hashlib
import json
import socket
import ssl
import time

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class Electrum:
    def __init__(self, url, protocol="TCP", timeout=20):
        host, port = url.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        if protocol.upper() == "SSL":
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # many coin servers self-sign
            sock = ctx.wrap_socket(sock, server_hostname=host)
        self.f = sock.makefile("rwb")
        self.sock, self._id = sock, 0
        self.call("server.version", ["kdf-swaplab", "1.4"])

    def call(self, method, params):
        self._id += 1
        self.f.write((json.dumps({"id": self._id, "method": method, "params": params}) + "\n").encode())
        self.f.flush()
        r = json.loads(self.f.readline())
        if r.get("error"):
            raise RuntimeError(f"{method}: {r['error']}")
        return r["result"]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def connect_any(servers):
    last = None
    for s in servers:
        try:
            return Electrum(s["url"], s.get("protocol", "TCP"))
        except Exception as e:  # noqa
            last = e
    raise RuntimeError(f"no electrum server reachable: {last!r}")


def p2pkh_scripthash(address):
    n = 0
    for c in address:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes(25, "big")
    spk = b"\x76\xa9\x14" + raw[-24:-4] + b"\x88\xac"
    return hashlib.sha256(spk).digest()[::-1].hex()


# KMD active user reward, as komodo_interest.h after KIP-0001 (and as GLEEC/Komodo KDF compute it)
KMD_S7_HEIGHT = 3_484_958
KMD_ENDOFERA = 7_777_777


def kmd_reward(height, value, lock_time, now, kip0001=True):
    """Satoshis of reward the rules allow for one coin, or 0. kip0001=False gives the pre-2023
    5 % formula, i.e. what a wallet missing the KIP-0001 cut would claim."""
    if value < 1_000_000_000 or lock_time < 500_000_000 or not height or height >= KMD_ENDOFERA:
        return 0
    minutes = (now - lock_time) // 60
    if minutes < 60:
        return 0
    minutes = min(minutes, 525_600)
    if height >= 1_000_000:
        minutes = min(minutes, 31 * 24 * 60)
    minutes -= 59
    r = (value // 10_512_000) * minutes
    if kip0001 and height >= KMD_S7_HEIGHT:
        r //= 500
    return r


def utxo_snapshot(servers, address, kmd=False):
    """Coins of a P2PKH address with age; for KMD also allowed vs pre-KIP reward."""
    el = connect_any(servers)
    try:
        tip = el.call("blockchain.headers.subscribe", [])
        tip_h = tip.get("height") or tip.get("block_height")
        out = []
        for u in el.call("blockchain.scripthash.listunspent", [p2pkh_scripthash(address)]):
            row = {"tx_hash": u["tx_hash"], "tx_pos": u["tx_pos"], "height": u["height"], "value": u["value"]}
            if kmd and u["height"] > 0:
                t = el.call("blockchain.transaction.get", [u["tx_hash"], True])
                now = int(time.time())
                row.update(locktime=t.get("locktime"), blocktime=t.get("blocktime"),
                           reward_allowed=kmd_reward(u["height"], u["value"], t.get("locktime", 0), now),
                           reward_pre_kip=kmd_reward(u["height"], u["value"], t.get("locktime", 0), now,
                                                     kip0001=False),
                           age_minutes=(now - (t.get("blocktime") or now)) // 60)
            out.append(row)
        return {"address": address, "tip": tip_h, "utxos": out,
                "bug1_precondition": any(r.get("reward_pre_kip", 0) > r.get("reward_allowed", 0)
                                         for r in out)}
    finally:
        el.close()
