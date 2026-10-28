#!/usr/bin/env python3
"""kdf-swaplab: repeatable live atomic swaps between two KDF nodes in Docker.

  swaplab.py wallet new NAME [--hd]
  swaplab.py wallet import NAME --seed-file FILE [--wallet-dir DIR] [--hd] [--overwrite]
  swaplab.py wallet addresses NAME [--flavor R] [--netid 8762] [--coins KMD,LTC]
  swaplab.py run SCENARIO.json [--dry-run] [--expect baseline|fixed] [--stop-anyway]
  swaplab.py status
  swaplab.py stop [SCENARIO.json | --all]
  swaplab.py recover SCENARIO.json UUID

A run writes out/<run-id>/ with SUMMARY.md (read this first), progress.log, results.json,
manifest.json, precondition.json, txs.json and per-node logs and diagnostics. See README.md
and docs/MANUAL.md.
"""
import argparse
import datetime
import fcntl
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from lib import electrum, nodes, scrub, txdecode, wallets  # noqa: E402
from lib.kdfrpc import (KdfError, buy, diagnostics, orderbook, setprice, swap_events,  # noqa: E402
                        swap_failures, swap_status, swap_summary, swap_txs, wait_for)

OUT = os.path.join(ROOT, "out")
PAYMENT_EVENTS = {"MakerPaymentSent", "TakerPaymentSent", "MakerPaymentReceived", "TakerPaymentReceived"}


# --------------------------------------------------------------------------- helpers
def rel(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def load_json(p):
    with open(rel(p)) as f:
        return json.load(f)


def now_id():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def harness_commit():
    try:
        c = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        d = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"], capture_output=True, text=True)
        return (c.stdout.strip() or "no-commit") + ("-dirty" if d.stdout.strip() else "")
    except OSError:
        return "unknown"


class Lock:
    def __enter__(self):
        os.makedirs(nodes.RUN, exist_ok=True)
        self.f = open(os.path.join(nodes.RUN, ".lock"), "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another swaplab.py command is running (run/.lock)")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def binaries():
    p = os.path.join(ROOT, "config", "binaries.json")
    if not os.path.exists(p):
        raise SystemExit("config/binaries.json missing: copy config/binaries.example.json and edit it")
    return load_json(p)


def binary_for(flavor, bins, verify=True):
    b = bins.get(flavor)
    if not b:
        raise SystemExit(f"flavor {flavor} is not configured in config/binaries.json")
    path = rel(b["path"])
    if not os.path.isfile(path):
        raise SystemExit(f"binary for {flavor} not found: {path}")
    if verify and b.get("sha256") and nodes.sha256(path) != b["sha256"]:
        raise SystemExit(f"binary for {flavor} has sha256 {nodes.sha256(path)}, config pins {b['sha256']}")
    return path


def build_nodes(sc, bins):
    coins_file = rel(sc["coins_file"])
    seed_ip = sc["seed"]["ip"]
    seed = nodes.Node("seed", sc["seed"]["flavor"], binary_for(sc["seed"]["flavor"], bins), sc["netid"],
                      seed_ip, sc["seed"]["host_port"], coins_file=coins_file,
                      extra={**sc.get("mm2_extra", {}), **sc["seed"].get("mm2_extra", {})})
    out = {"seed": seed}
    for role in ("maker", "taker"):
        s = sc[role]
        out[role] = nodes.Node(role, s["flavor"], binary_for(s["flavor"], bins), sc["netid"], s["ip"],
                               s["host_port"], wallet=s["wallet"], coins_file=coins_file, seednodes=[seed_ip],
                               extra={**sc.get("mm2_extra", {}), **s.get("mm2_extra", {})})
    return out


class Progress:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg):
        line = f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(self.path, "a") as f:
            f.write(line + "\n")


def peers_ok(node_rpc, subnet):
    """(ok, peers): every connected peer address must be inside our private subnet."""
    peers = node_rpc.peers()
    net = ipaddress.ip_network(subnet)
    outside = []
    for pid, addrs in peers.items():
        for a in addrs if isinstance(addrs, list) else [addrs]:
            m = re.search(r"/ip4/([0-9.]+)/", str(a))
            if m and ipaddress.ip_address(m.group(1)) not in net:
                outside.append(a)
    return (bool(peers) and not outside), {"peers": peers, "outside_subnet": outside}


def _task_wait(node_rpc, status_method, task_id, done_when, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = node_rpc.v2(status_method, {"task_id": task_id}, raw=True)
        st = (s.get("result") or {}).get("status")
        if st in ("Ok", "Error"):
            return s
        time.sleep(3)
    raise RuntimeError(f"{status_method} timed out")


def enable_special(node_rpc, coin, spec):
    """Activation that is not a plain electrum one. spec['kind']: zcoin | sia | evm | token (token
    coins are activated together with their platform and only read here)."""
    kind = spec["kind"]
    if kind == "zcoin":
        servers = spec["light_wallet_d_servers"]
        r = node_rpc.v2("task::enable_z_coin::init", {"ticker": coin, "activation_params": {
            "mode": {"rpc": "Light", "rpc_data": {"light_wallet_d_servers": servers,
                                                  "sync_params": spec["sync_params"],
                                                  **({"electrum_servers": spec["electrum_servers"]} if spec.get("electrum_servers") else {})}},
            "zcash_params_path": "/kdf/zcash-params"}}, raw=True)
        if "result" not in r:
            raise RuntimeError(f"{node_rpc.name}: {coin} activation init rejected: {str(r)[:500]}")
        s = _task_wait(node_rpc, "task::enable_z_coin::status", r["result"]["task_id"], None)
        if s["result"]["status"] != "Ok":
            raise RuntimeError(f"{node_rpc.name}: enabling {coin} failed: {s}")
    elif kind == "sia":
        r = node_rpc.v2("task::enable_sia::init", {"ticker": coin, "activation_params": {
            "client_conf": {"server_url": spec["server_url"]}}}, raw=True)
        s = _task_wait(node_rpc, "task::enable_sia::status", r["result"]["task_id"], None)
        if s["result"]["status"] != "Ok":
            raise RuntimeError(f"{node_rpc.name}: enabling {coin} failed: {s}")
    elif kind == "evm":
        r = node_rpc.v2("enable_eth_with_tokens", {
            "ticker": coin, "mm2": 1, "swap_contract_address": spec["swap_contract_address"],
            "fallback_swap_contract": spec["fallback_swap_contract"],
            "nodes": [{"url": u} for u in spec["nodes"]],
            "erc20_tokens_requests": [{"ticker": t} for t in spec.get("tokens", [])],
            "get_balances": False}, raw=True)
        if r.get("error"):
            raise RuntimeError(f"{node_rpc.name}: enabling {coin} failed: {r['error']}")
    # kind == "token": nothing to do here
    b = node_rpc.legacy("my_balance", raw=True, coin=coin)
    if b.get("error"):
        raise RuntimeError(f"{node_rpc.name}: balance of {coin} failed: {b['error']}")
    return {"address": b.get("address"), "balance": b.get("balance"), "unspendable": b.get("unspendable_balance")}


def enable_coins(node_rpc, coins, electrums, special=None):
    res = {}
    special = special or {}
    for c in coins:
        if c in special and special[c]["kind"] != "electrum":
            res[c] = enable_special(node_rpc, c, special[c])
            continue
        r = node_rpc.electrum(c, electrums[c], raw=True)
        if r.get("error"):
            raise RuntimeError(f"{node_rpc.name}: enabling {c} failed: {r['error']}")
        res[c] = {"address": r.get("address"), "balance": r.get("balance"),
                  "unspendable": r.get("unspendable_balance")}
    return res


def unfinished_swaps(node_rpc):
    """uuids in active_swaps whose status is not Finished (a finished swap can stay listed as
    active for a moment after its last event)."""
    out = []
    for u in node_rpc.legacy("active_swaps", raw=True).get("uuids", []) or []:
        try:
            if "Finished" not in swap_events(swap_status(node_rpc, u)):
                out.append(u)
        except (KdfError, OSError, ValueError):
            out.append(u)
    return out


def find_hex_txs(text):
    """Raw transactions embedded in an error string (KDF puts the rejected tx hex there)."""
    return [h for h in re.findall(r"\b([0-9a-f]{200,})\b", text or "") if len(h) % 2 == 0]


def match_expectation(rules, outcome, bug1):
    for rule in rules:
        if rule.get("if") == "bug1_precondition" and not bug1:
            continue
        if rule["result"] == "success":
            return rule, outcome["success"]
        if outcome["success"]:
            return rule, False
        blob = json.dumps(outcome["failures"]).lower()
        ok = ((not rule.get("event") or rule["event"].lower() in blob)
              and (not rule.get("error_contains") or rule["error_contains"].lower() in blob))
        return rule, ok
    return None, None


# --------------------------------------------------------------------------- run
def cmd_run(a):
    sc = load_json(a.scenario)
    bins = binaries()
    expect_key = a.expect or bins.get("R", {}).get("expect", "baseline")
    run_id = f"{now_id()}-{sc['name']}" + ("-dry" if a.dry_run else "")
    out = os.path.join(OUT, run_id)
    os.makedirs(out, mode=0o700)
    log = Progress(os.path.join(out, "progress.log"))
    stages = {k: False for k in ("preflight", "containers_started", "rpc_up", "private_mesh", "coins_enabled",
                                 "balances_ok", "order_visible", "swap_started", "swap_finished")}
    result = {"run_id": run_id, "scenario": sc["name"], "netid": sc["netid"], "expect_set": expect_key,
              "dry_run": a.dry_run, "stages": stages, "started_at": datetime.datetime.now().isoformat()}
    ns, stack, uuid, order, keep_running = None, None, None, None, False
    statuses = {"maker": None, "taker": None}
    try:
        with Lock():
            # ---- preflight
            ns = build_nodes(sc, bins)
            electrums = load_json(sc["electrums"])
            for role in ("maker", "taker"):
                wallets.load(sc[role]["wallet"])
            if sc["maker"]["wallet"] == sc["taker"]["wallet"]:
                raise SystemExit("maker and taker must use different wallets")
            others = [p for p in nodes.running_projects() if p != f"swaplab-{sc['name'].lower()}"]
            if others:
                raise SystemExit(f"other swaplab stacks are running ({others}); stop them first "
                                 f"(a wallet must never be live in two nodes)")
            busy = [n.host_port for n in ns.values() if not nodes.port_free(n.host_port)]
            if busy:
                raise SystemExit(f"host ports in use: {busy}")
            nodes.ensure_image()
            stages["preflight"] = True
            log(f"run {run_id}: {sc['name']} netid {sc['netid']}, expectations '{expect_key}'")

            # ---- containers
            gui = f"kdf-swaplab-{run_id}"
            for n in ns.values():
                n.prepare(gui)
            stack = nodes.Stack(f"swaplab-{sc['name'].lower()}", sc["subnet"], list(ns.values()), run_id)
            stack.up(wait_seed=lambda: wait_for("seed rpc", ns["seed"].rpc().version, sc["timeouts"]["rpc"], 2))
            stages["containers_started"] = True
            log("containers started: " + ", ".join(f"{r}={n.flavor}" for r, n in ns.items()))

            rpcs = {r: n.rpc() for r, n in ns.items()}
            versions = {}
            for r, k in rpcs.items():
                versions[r] = wait_for(f"{r} rpc", k.version, sc["timeouts"]["rpc"], 2)
            stages["rpc_up"] = True
            result["versions"] = versions
            log("rpc up: " + ", ".join(f"{r} {v}" for r, v in versions.items()))

            # ---- private mesh
            mesh = {}
            for r in ("maker", "taker"):
                def chk(r=r):
                    ok, info = peers_ok(rpcs[r], sc["subnet"])
                    mesh[r] = info
                    return ok
                try:
                    wait_for(f"{r} connected to the private seed", chk, sc["timeouts"]["peers"], 3)
                except TimeoutError:
                    if sc.get("seed_mode") == "private" and mesh.get(r, {}).get("outside_subnet"):
                        raise RuntimeError(f"{r} has peers outside the private subnet: "
                                           f"{mesh[r]['outside_subnet'][:5]}; refusing to place orders")
                    raise
            stages["private_mesh"] = True
            result["mesh"] = mesh
            log("private mesh ok: " + ", ".join(f"{r} peers {len(m['peers'])}" for r, m in mesh.items()))

            # ---- coins
            enabled = {r: enable_coins(rpcs[r], sc["coins"], electrums, sc.get("special")) for r in ("maker", "taker")}
            stages["coins_enabled"] = True
            result["wallets"] = enabled
            log("coins enabled: " + "; ".join(
                f"{r} " + ", ".join(f"{c} {v['address']} bal {v['balance']}" for c, v in e.items())
                for r, e in enabled.items()))

            t = sc["trade"]
            base, relc = t["base"], t["rel"]
            buf = sc.get("fee_buffer", {})
            need_maker = float(t["volume"]) + float(buf.get(base, "0.001"))
            need_taker = float(t["volume"]) * float(t["price"]) * 1.03 + float(buf.get(relc, "0.001"))
            short = []
            if float(enabled["maker"][base]["balance"]) < need_maker:
                short.append(f"maker {sc['maker']['wallet']} needs >= {need_maker:.8f} {base} at "
                             f"{enabled['maker'][base]['address']}")
            if float(enabled["taker"][relc]["balance"]) < need_taker:
                short.append(f"taker {sc['taker']['wallet']} needs >= {need_taker:.8f} {relc} at "
                             f"{enabled['taker'][relc]['address']}")
            pre = {"balances": enabled}
            for r, coin in (("taker", relc), ("maker", base)):
                if coin == "KMD":
                    try:
                        pre[f"{r}_kmd_utxos"] = electrum.utxo_snapshot(electrums["KMD"],
                                                                     enabled[r]["KMD"]["address"], kmd=True)
                    except Exception as e:  # noqa
                        pre[f"{r}_kmd_utxos"] = {"error": repr(e)}
            bug1 = bool(pre.get("taker_kmd_utxos", {}).get("bug1_precondition"))
            pre["bug1_precondition_taker"] = bug1
            result["bug1_precondition"] = bug1
            with open(os.path.join(out, "precondition.json"), "w") as f:
                json.dump(pre, f, indent=1)
            log(f"bug-1 precondition on the taker's KMD (a >=10 KMD coin whose reward a wallet signing now would claim): {bug1}")
            if short and not a.dry_run:
                raise RuntimeError("insufficient balance: " + "; ".join(short))
            if short:
                log("dry run: balances would be insufficient for a live run: " + "; ".join(short))
                log("dry run: KDF refuses orders without funds, so the run ends before the order step")
                return finish(a, sc, ns, stack, out, result, statuses, uuid, keep_running=False, log=log)
            stages["balances_ok"] = not short
            active = {r: unfinished_swaps(rpcs[r]) for r in ("maker", "taker")}
            if any(active.values()) and not a.allow_active:
                raise RuntimeError(f"unfinished swaps exist: {active}; let them finish or use recover")

            # ---- order
            order = setprice(rpcs["maker"], base, relc, t["price"], t["volume"], **t.get("setprice_extra", {}))
            log(f"maker order {order}: sells {t['volume']} {base} at {t['price']} {relc}")
            zhtlc_ob = {"err": None}

            def _visible():
                try:
                    return any(o.get("uuid") == order
                               for o in orderbook(rpcs["taker"], base, relc).get("asks", []))
                except KdfError as e:
                    if "not supported for ZHTLC" in str(e):
                        zhtlc_ob["err"] = str(e)
                        return True  # the orderbook RPC itself refuses ZHTLC pairs: recorded, not retried
                    raise
            wait_for("order visible on the taker", _visible, sc["timeouts"]["order_visible"], 3)
            if zhtlc_ob["err"]:
                log("FINDING: the taker's orderbook RPC fails for this pair: " + zhtlc_ob["err"])
                result["orderbook_error"] = zhtlc_ob["err"]
                time.sleep(20)  # no way to see the order; give gossip time, then take it by uuid
            try:  # raw evidence of what the taker's orderbook / best_orders answer for this pair
                ob_raw = {"orderbook": rpcs["taker"].legacy("orderbook", raw=True, base=base, rel=relc),
                          "best_orders_buy_" + base: rpcs["taker"].legacy("best_orders", raw=True, coin=base, action="buy", volume=t["volume"]),
                          "best_orders_sell_" + relc: rpcs["taker"].legacy("best_orders", raw=True, coin=relc, action="sell", volume="0.001"),
                          "best_orders_buy_" + relc: rpcs["taker"].legacy("best_orders", raw=True, coin=relc, action="buy", volume="0.001")}
                with open(os.path.join(out, "orderbook-raw.json"), "w") as fh:
                    json.dump(ob_raw, fh, indent=1)
            except Exception as e:  # noqa
                log(f"orderbook-raw capture failed: {e}")
            stages["order_visible"] = True
            log("order visible on the taker" if not zhtlc_ob["err"] else "order not checkable on the taker; taking it by uuid")
            if a.dry_run:
                rpcs["maker"].legacy("cancel_order", raw=True, uuid=order)
                log("dry run: order cancelled, no funds moved")
                return finish(a, sc, ns, stack, out, result, statuses, uuid, keep_running=False, log=log)

            # ---- swap
            uuid = buy(rpcs["taker"], base, relc, t["price"], t["volume"],
                       match_by={"type": "Orders", "data": [order]})
            result["uuid"] = uuid
            log(f"taker buy -> swap {uuid}")
            seen = {"maker": 0, "taker": 0}
            t0, peer_fail_at = time.time(), None
            while True:
                for r in ("maker", "taker"):
                    try:
                        statuses[r] = swap_status(rpcs[r], uuid)
                    except (KdfError, OSError, ValueError):
                        continue
                    evs = statuses[r].get("events", [])
                    for e in evs[seen[r]:]:
                        typ, data = e["event"]["type"], e["event"].get("data")
                        err = data.get("error") if isinstance(data, dict) else None
                        log(f"{r}: {typ}" + (f" ERROR {str(err)[:300]}" if err else ""))
                    seen[r] = len(evs)
                sums = {r: swap_summary(s) for r, s in statuses.items()}
                if all(s["started"] for s in sums.values()):
                    stages["swap_started"] = True
                if all(s["finished"] for s in sums.values()):
                    break
                evs_all = set(sums["maker"]["events"]) | set(sums["taker"]["events"])
                if any(s["finished"] and s["failures"] for s in sums.values()) and not evs_all & PAYMENT_EVENTS:
                    peer_fail_at = peer_fail_at or time.time()
                    if time.time() - peer_fail_at > sc["timeouts"].get("peer_failure_grace", 1200):
                        log("one side failed before any payment; the other side did not finish within the grace "
                            "period, stopping the wait (no swap funds are locked)")
                        break
                if time.time() - t0 > sc["timeouts"]["swap"]:
                    log("swap timeout reached")
                    break
                time.sleep(15)
            stages["swap_finished"] = all(swap_summary(s)["finished"] for s in statuses.values())
            left = {r: unfinished_swaps(rpcs[r]) for r in ("maker", "taker")}
            keep_running = any(left.values()) and sc.get("keep_running_if_active", True) and not a.stop_anyway
            return finish(a, sc, ns, stack, out, result, statuses, uuid, keep_running, log, bug1=bug1,
                          expect=sc["expect"].get(expect_key, []))
    except (Exception, SystemExit) as e:  # noqa
        result["error"] = str(e)[:3000]
        if isinstance(e, SystemExit) and not stages["containers_started"]:
            print(f"ERROR: {e}", file=sys.stderr)
            shutil.rmtree(out, ignore_errors=True)
            return 2
        log(f"ERROR: {str(e)[:1000]}")
        return finish(a, sc, ns, stack, out, result, statuses, uuid, keep_running, log)


def finish(a, sc, ns, stack, out, result, statuses, uuid, keep_running, log, bug1=False, expect=None):
    """Export evidence, check expectations, write SUMMARY.md, stop the stack."""
    run_id = result["run_id"]
    secrets = wallets.all_secret_strings()
    if ns:
        for n in ns.values():
            secrets += n.secrets()
    secrets = [s for s in secrets if s]
    txs = []
    if ns:
        for r, n in ns.items():
            d = os.path.join(out, r)
            os.makedirs(d, exist_ok=True)
            src = n.log_path(run_id)
            if os.path.exists(src):
                with open(src, errors="replace") as f:
                    text = f.read()
                with open(os.path.join(d, "kdf.log"), "w") as f:
                    f.write(scrub.redact_lines(text, secrets))
            if r != "seed" and result["stages"].get("rpc_up"):
                try:
                    with open(os.path.join(d, "diagnostics.json"), "w") as f:
                        json.dump(diagnostics(n.rpc(), uuid), f, indent=1)
                except Exception as e:  # noqa
                    log(f"diagnostics of {r} failed: {e!r}")
    outcome = {"success": False, "failures": []}
    for r, st in statuses.items():
        if not st:
            continue
        for t in swap_txs(st):
            txs.append({"side": r, **t, "outputs": txdecode.outputs(t["tx_hex"])})
        for ev, data in swap_failures(st):
            outcome["failures"].append({"side": r, "event": ev, "data": data})
            for h in find_hex_txs(json.dumps(data)):
                txs.append({"side": r, "event": ev + " (tx from error)", "tx_hex": h,
                            "outputs": txdecode.outputs(h)})
    sums = {r: swap_summary(s) for r, s in statuses.items() if s}
    outcome["success"] = bool(sums) and all(s["success"] for s in sums.values()) and len(sums) == 2
    result["summaries"] = sums
    result["outcome"] = outcome
    with open(os.path.join(out, "txs.json"), "w") as f:
        json.dump(txs, f, indent=1)
    rule, ok = (None, None)
    if expect and uuid:
        rule, ok = match_expectation(expect, outcome, bug1)
    result["expectation"] = {"rule": rule, "met": ok}
    result["kept_running"] = keep_running
    result["finished_at"] = datetime.datetime.now().isoformat()
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(result, f, indent=1)
    manifest = {"run_id": run_id, "harness": harness_commit(), "scenario": sc,
                "nodes": {r: n.describe() for r, n in (ns or {}).items()},
                "binaries": {k: {kk: vv for kk, vv in v.items() if kk != "path"}
                             for k, v in binaries().items() if not k.startswith("_")},
                "coins_file_sha256": nodes.sha256(rel(sc["coins_file"]))}
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    shutil.copyfile(rel(a.scenario), os.path.join(out, "scenario.json"))
    write_summary(out, sc, result, txs, rule, ok)
    if stack and not keep_running:
        stack.down()
        log("containers stopped")
    elif stack:
        log("containers KEPT RUNNING: a swap is still active (refund or spend pending); "
            f"check later with `swaplab.py status`, stop with `swaplab.py stop {a.scenario}`")
    leaks = scrub.find_leaks([os.path.join(dp, fn) for dp, _, fns in os.walk(out) for fn in fns], secrets)
    if leaks:
        q = out + ".QUARANTINE"
        os.rename(out, q)
        os.chmod(q, 0o700)
        print(f"SECRET MATERIAL FOUND in evidence, moved to {q}: {leaks}", file=sys.stderr)
        return 3
    print(f"\nevidence: {os.path.relpath(out, ROOT)}/SUMMARY.md")
    return 0 if (ok is not False and "error" not in result) else 1


def write_summary(out, sc, result, txs, rule, ok):
    st = result["stages"]
    L = [f"# {sc['name']} - run {result['run_id']}", ""]
    if result.get("dry_run"):
        L.append("Dry run: no order was matched, no funds moved.")
    v = result.get("versions", {})
    L.append("Nodes: " + ", ".join(f"{r} {sc[r]['flavor'] if r != 'seed' else sc['seed']['flavor']} "
                                   f"({v.get(r, '?')})" for r in ("maker", "taker", "seed")))
    L.append(f"Netid {sc['netid']}; trade: taker buys {sc['trade']['volume']} {sc['trade']['base']} "
             f"at {sc['trade']['price']} {sc['trade']['rel']}; expectations '{result['expect_set']}'")
    L.append("Stages: " + " ".join(("[x] " if v_ else "[ ] ") + k for k, v_ in st.items()))
    L.append(f"Bug-1 precondition (taker has a >=10 KMD coin whose reward a wallet signing now would claim): {result.get('bug1_precondition')}")
    if result.get("uuid"):
        L.append(f"Swap uuid: {result['uuid']}")
    for r, s in result.get("summaries", {}).items():
        L.append(f"- {r}: " + " > ".join(s["events"]))
    for f_ in result.get("outcome", {}).get("failures", [])[:3]:
        err = f_["data"].get("error") if isinstance(f_["data"], dict) else f_["data"]
        err = re.sub(r"[0-9a-f]{200,}", "<tx hex>", str(err))
        L.append(f"- FAILURE {f_['side']} {f_['event']}: {err[:400]}")
    fee_txs = [t for t in txs if "TakerFee" in t["event"] and isinstance(t["outputs"], list)]
    for t in fee_txs[:1]:
        L.append("Taker fee tx outputs: " + ", ".join(f"#{o['n']} {o['value']} {o['kind']}" for o in t["outputs"]))
    if result.get("error"):
        L.append(f"Harness error: {result['error'][:400]}")
    if rule is not None:
        L.append(f"Expected: {rule.get('result')} {rule.get('event', '')} {rule.get('error_contains', '')}".rstrip()
                 + f" -> {'MET' if ok else 'NOT MET'}")
    L.append("Result: " + ("SUCCESS" if result.get("outcome", {}).get("success") else
                           ("DRY RUN" if result.get("dry_run") and not result.get("error") else "FAILED")))
    if result.get("kept_running"):
        L.append("Containers are still running because a swap is active (refund/spend pending).")
    L.append("")
    with open(os.path.join(out, "SUMMARY.md"), "w") as f:
        f.write("\n".join(L))


# --------------------------------------------------------------------------- other commands
def cmd_wallet(a):
    if a.action == "new":
        wallets.create(a.name, enable_hd=a.hd)
        print(f"wallet {a.name} created in secrets/{a.name}.json (0600). Back it up if you fund it.")
        print(f"next: ./swaplab.py wallet addresses {a.name}")
    elif a.action == "import":
        if not a.seed_file:
            raise SystemExit("--seed-file is required (the phrase is never taken from the command line)")
        d = wallets.import_(a.name, a.seed_file, a.wallet_dir, enable_hd=a.hd, overwrite=a.overwrite)
        print(f"wallet {a.name} imported (BIP39 checksum valid: {d['bip39_valid']}, "
              f"wallet dir copied: {bool(d['wallet_dir'])}). You may now delete {a.seed_file}.")
    elif a.action == "addresses":
        return wallet_addresses(a)
    return 0


def wallet_addresses(a):
    bins = binaries()
    coins = a.coins.split(",")
    electrums = load_json(a.electrums_file)
    with Lock():
        n = nodes.Node("wallet", a.flavor, binary_for(a.flavor, bins), a.netid, "10.99.0.20", 7809,
                       wallet=a.name, coins_file=rel(a.coins_file), disable_p2p=True)
        n.name = f"addr-{a.flavor}-{a.name}"
        n.dir = os.path.join(nodes.RUN, n.name)
        nodes.ensure_image()
        n.prepare("kdf-swaplab-addresses")
        run_id = now_id() + "-addresses"
        st = nodes.Stack(f"swaplab-addr-{a.name.lower()}", "10.99.0.0/24", [n], run_id)
        st.up()
        try:
            k = n.rpc()
            wait_for("rpc", k.version, 120, 2)
            for c in coins:
                r = k.electrum(c, electrums[c], raw=True)
                if r.get("error"):
                    print(f"{c}: ERROR {str(r['error'])[:300]}")
                else:
                    print(f"{c}: {r.get('address')}  balance {r.get('balance')}  unspendable "
                          f"{r.get('unspendable_balance')}")
        finally:
            st.down()
    return 0


def cmd_status(a):
    projects = nodes.running_projects()
    if not projects:
        print("no swaplab containers running")
    for p in projects:
        print(f"running: {p}")
        print(nodes.sh("docker", "ps", "--filter", f"label=com.docker.compose.project={p}",
                       "--format", "  {{.Names}}\t{{.Status}}\t{{.Ports}}", check=False).rstrip())
    return 0


def cmd_stop(a):
    if a.all:
        for p in nodes.running_projects():
            nodes.sh("docker", "compose", "-p", p, "down", "--timeout", "30", check=False)
            print(f"stopped {p}")
        return 0
    sc = load_json(a.scenario)
    ns = build_nodes(sc, binaries())
    nodes.Stack(f"swaplab-{sc['name'].lower()}", sc["subnet"], list(ns.values()), "stop").down()
    print(f"stopped swaplab-{sc['name'].lower()}")
    return 0


def cmd_recover(a):
    sc = load_json(a.scenario)
    bins = binaries()
    electrums = load_json(sc["electrums"])
    with Lock():
        ns = build_nodes(sc, bins)
        run_id = now_id() + "-recover"
        for n in ns.values():
            n.prepare(f"kdf-swaplab-{run_id}")
        st = nodes.Stack(f"swaplab-{sc['name'].lower()}", sc["subnet"], list(ns.values()), run_id)
        st.up(wait_seed=lambda: wait_for("seed rpc", ns["seed"].rpc().version, 120, 2))
        for r in ("maker", "taker"):
            k = ns[r].rpc()
            wait_for(f"{r} rpc", k.version, 120, 2)
            enable_coins(k, sc["coins"], electrums, sc.get("special"))
            res = k.legacy("recover_funds_of_swap", raw=True, params={"uuid": a.uuid})
            print(f"{r}: {json.dumps(res)[:1500]}")
        print("containers left running so pending refunds can complete; stop with "
              f"`swaplab.py stop {a.scenario}`")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wallet")
    w.add_argument("action", choices=["new", "import", "addresses"])
    w.add_argument("name")
    w.add_argument("--seed-file")
    w.add_argument("--wallet-dir")
    w.add_argument("--hd", action="store_true", help="HD (BIP44) mode instead of the legacy single-address mode")
    w.add_argument("--overwrite", action="store_true")
    w.add_argument("--flavor", default="R")
    w.add_argument("--netid", type=int, default=8762)
    w.add_argument("--coins", default="KMD,LTC")
    w.add_argument("--coins-file", default="config/coins.json")
    w.add_argument("--electrums-file", default="config/electrums.json")
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--dry-run", action="store_true", help="stop after the order is visible; no funds move")
    r.add_argument("--expect", choices=["baseline", "fixed"])
    r.add_argument("--stop-anyway", action="store_true", help="stop containers even if a swap is still active")
    r.add_argument("--allow-active", action="store_true", help="run although older swaps are unfinished")
    sub.add_parser("status")
    s = sub.add_parser("stop")
    s.add_argument("scenario", nargs="?")
    s.add_argument("--all", action="store_true")
    rc = sub.add_parser("recover")
    rc.add_argument("scenario")
    rc.add_argument("uuid")
    a = ap.parse_args()
    if a.cmd == "stop" and not (a.scenario or a.all):
        ap.error("stop needs a scenario or --all")
    return {"wallet": cmd_wallet, "run": cmd_run, "status": cmd_status, "stop": cmd_stop,
            "recover": cmd_recover}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
