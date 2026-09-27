"""KDF RPC client and swap helpers.

Adapted from rincoin-testlab scenarios/03-kdf-forkid/kdflab.py (2026-09-22): the client takes
a URL and an RPC password instead of a fixed lab node name, and nothing here mines blocks or
knows about regtest. Works with every KDF flavor used by the harness (Reloaded, Komodo 2.x,
GLEEC 3.x): only legacy methods and a few mmrpc 2.0 calls that all of them implement.
"""
import json
import time
import urllib.error
import urllib.request


class KdfError(Exception):
    def __init__(self, err, http=None):
        super().__init__(json.dumps(err)[:2000] if not isinstance(err, str) else err[:2000])
        self.err, self.http = err, http


class Kdf:
    """legacy() and v2() return parsed JSON. raw=True never raises, so an error body can be
    recorded verbatim; otherwise a body with "error" raises KdfError."""

    def __init__(self, name, url, rpc_password):
        self.name, self.url, self._pw = name, url, rpc_password

    def _post(self, body, raw=False, timeout=120):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text, http = r.read().decode(), r.status
        except urllib.error.HTTPError as e:
            text, http = e.read().decode(), e.code
        try:
            res = json.loads(text)
        except ValueError:
            res = {"error": text.strip(), "non_json": True}
        if raw:
            return res
        if isinstance(res, dict) and res.get("error") is not None:
            raise KdfError(res, http)
        return res

    def legacy(self, method, raw=False, timeout=120, **params):
        return self._post({"userpass": self._pw, "method": method, **params}, raw=raw, timeout=timeout)

    def v2(self, method, params=None, raw=False, timeout=120):
        res = self._post({"userpass": self._pw, "mmrpc": "2.0", "method": method,
                          "params": params or {}}, raw=raw, timeout=timeout)
        return res if raw else res.get("result", res)

    # ------------------------------------------------------------------ convenience
    def version(self):
        return self.legacy("version", timeout=10)["result"]

    def peers(self):
        """{peer_id: [multiaddr, ...]}. Flavors name the method differently."""
        for m in ("get_directly_connected_peers", "get_peers_info"):
            r = self.legacy(m, raw=True, timeout=20)
            if isinstance(r, dict) and isinstance(r.get("result"), dict):
                return r["result"]
        return {}

    def electrum(self, coin, servers, tx_history=False, raw=False, **extra):
        return self.legacy("electrum", raw=raw, timeout=180, coin=coin, servers=servers,
                           tx_history=tx_history, **extra)

    def balance(self, coin):
        return self.legacy("my_balance", coin=coin)

    def stop(self):
        return self.legacy("stop", raw=True, timeout=10)


def wait_for(what, fn, timeout=600, every=1):
    """Poll fn until it returns something truthy; exceptions count as 'not yet'."""
    t0, v = time.time(), None
    while True:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:  # noqa
            v = e
        if time.time() - t0 > timeout:
            raise TimeoutError(f"{what}: {v!r}"[:1000])
        time.sleep(every)


# ---------------------------------------------------------------------------- swaps
def setprice(node, base, rel, price, volume, **extra):
    """Maker order: sells `base` for `rel`. Returns the order uuid."""
    return node.legacy("setprice", base=base, rel=rel, price=str(price), volume=str(volume),
                       **extra)["result"]["uuid"]


def buy(node, base, rel, price, volume, **extra):
    """Taker order: buys `base` with `rel`. Returns the swap uuid."""
    return node.legacy("buy", base=base, rel=rel, price=str(price), volume=str(volume),
                       **extra)["result"]["uuid"]


def orderbook(node, base, rel):
    return node.legacy("orderbook", base=base, rel=rel)


def swap_status(node, uuid):
    return node.legacy("my_swap_status", params={"uuid": uuid})["result"]


def swap_events(status):
    return [e["event"]["type"] for e in (status or {}).get("events", [])]


def swap_failures(status):
    errs = set((status or {}).get("error_events", []))
    return [(e["event"]["type"], e["event"].get("data"))
            for e in (status or {}).get("events", [])
            if "Failed" in e["event"]["type"] or e["event"]["type"] in errs]


def swap_summary(status):
    ev = swap_events(status)
    failures = swap_failures(status)
    return {"uuid": (status or {}).get("uuid"), "type": (status or {}).get("type"), "events": ev,
            "started": "Started" in ev, "negotiated": "Negotiated" in ev,
            "finished": "Finished" in ev, "success": "Finished" in ev and not failures,
            "failures": failures,
            "maker_coin": (status or {}).get("maker_coin"), "taker_coin": (status or {}).get("taker_coin"),
            "maker_amount": (status or {}).get("maker_amount"),
            "taker_amount": (status or {}).get("taker_amount"),
            "mm_version": (status or {}).get("mm_version")}


def swap_txs(status):
    """Every transaction the swap recorded, as (event, tx_hash, tx_hex)."""
    out = []
    for e in (status or {}).get("events", []):
        d = e["event"].get("data") or {}
        if isinstance(d, dict) and d.get("tx_hex"):
            out.append({"event": e["event"]["type"], "tx_hash": d.get("tx_hash"), "tx_hex": d["tx_hex"]})
    return out


def diagnostics(node, uuid=None):
    """Orders, active and recent swaps (and the swap itself) as raw bodies."""
    d = {}
    for m in ("my_orders", "active_swaps", "my_recent_swaps"):
        try:
            d[m] = node.legacy(m, raw=True, timeout=30)
        except Exception as e:  # noqa
            d[m] = repr(e)
    if uuid:
        try:
            d["my_swap_status"] = node.legacy("my_swap_status", raw=True, timeout=30, params={"uuid": uuid})
        except Exception as e:  # noqa
            d["my_swap_status"] = repr(e)
    return d
