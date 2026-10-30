"""relay-kill-degraded.py  --name N --bin KEY [--variant base|v1|v2] [--trip kill|throttle] [--rounds 6]
Fault-injection example: a maker on a chain of relays loses one of its two seeds (or its link is throttled) and trips into degraded mode while it keeps creating orders on new pairs; an observer node of another flavor shows whether its orderbook converges.
Chain  M -> K1 -> K2 -> O   (variant v1: K2 replaced by an R seed; v2: M gets maker_update_coalescing=false)
 k1 : K relay (.20), the only seed of K2 and of M's first seed entry
 k2 : K relay (.21), seednodes [K1]            (variant v1: R seed, role seed)
 x  : second relay (.22), seednodes [K1]       (killed to trip degraded mode; K flavor)
 o  : K observer, non-relay (.23), seednodes [K2]
 m  : R maker, wallet W1 (.24), non-relay, seednodes [K1, X]
Steps: 1 control (pair A, 3 orders, wait for O, 3 keep-alive rounds without errors)
       2 trip (kill X <30 s after an order create/update on M; or tc police on M's link)
       3 create orders on NEW pairs B and C, keep updating prices every 5 s
       4 watch ROUNDS keep-alive rounds (default 6 x 40 s) - per round: errors on O and K2, O's book vs M's per pair, M p2p.outbox.degraded
       5 cancel B and C on M            6 restart M (errors should stop next round)
Output: out/faults/<name>/s8.log plus the node logs. Stack name swaplab-s8, subnet 10.99.5.0/24 (never run next to another stack)."""
import sys, os, json, time, subprocess, urllib.request, argparse, re, threading
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from lib import nodes
from lib.kdfrpc import wait_for
import swaplab
ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True); ap.add_argument("--bin", default="R")
ap.add_argument("--variant", default="base"); ap.add_argument("--trip", default="kill")
ap.add_argument("--rounds", type=int, default=6); ap.add_argument("--round-secs", type=int, default=40)
ap.add_argument("--throttle", type=int, default=100); ap.add_argument("--mextra", default="{}")
A = ap.parse_args()
EX = os.path.join(ROOT, "scenarios", "examples") + "/"
SP = os.path.join(ROOT, "out", "faults") + "/"
OUT = SP + f"{A.name}/"; os.makedirs(OUT, exist_ok=True)
NET = 8762                      # K netid; R and K share it in the harness
coins, els = EX + "coins.json", EX + "electrums.json"; els_j = json.load(open(els))
bins = swaplab.binaries(); bins["R"] = bins[A.bin]
IPS = {"k1": "10.99.5.20", "k2": "10.99.5.21", "x": "10.99.5.22", "o": "10.99.5.23", "m": "10.99.5.24"}
def mk(role, flavor, name, svc, ip, port, wallet=None, extra=None):
    n = nodes.Node(role, flavor, swaplab.binary_for(flavor, bins), NET, ip, port, wallet=wallet, coins_file=coins, seednodes=[], disable_p2p=False)
    n.name = name; n.dir = os.path.join(nodes.RUN, name); n.service = svc; n.env = {}
    n.prepare("kdf-swaplab-s8")
    cfg = n.mm2(); cfg.update(extra or {})
    fd = os.open(n.mm2_path, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(cfg, f)
    return n
RELAY = lambda seeds: {"is_bootstrap_node": False, "i_am_seed": True, "seednodes": seeds, "disable_p2p": False}
for proj in ("swaplab-p-g1", "swaplab-s8"):
    nodes.sh("docker", "compose", "-p", proj, "down", "--timeout", "30", check=False)
k1 = mk("seed", "K", "p-s8k1", "k1", IPS["k1"], 7831)
k2 = mk("seed", "R" if A.variant == "v1" else "K", "p-s8k2", "k2", IPS["k2"], 7832, extra=RELAY([IPS["k1"]]))
x = mk("seed", "K", "p-s8x", "x", IPS["x"], 7833, extra=RELAY([IPS["k1"]]))
o = mk("seed", "K", "p-s8o", "o", IPS["o"], 7834, extra={"is_bootstrap_node": False, "i_am_seed": False, "seednodes": [IPS["k2"]], "disable_p2p": False})
mex = {"pre_trade_spend_check": False, "seednodes": [IPS["k1"], IPS["x"]], "i_am_seed": False, "is_bootstrap_node": False}
if A.variant == "v2": mex["maker_update_coalescing"] = False
mex.update(json.loads(A.mextra))
m = mk("wallet", "R", "p-s8m", "m", IPS["m"], 7835, wallet="W1", extra=mex)
allnodes = [k1, k2, x, o, m]
rid = swaplab.now_id() + "-s8"
st = nodes.Stack("swaplab-s8", "10.99.5.0/24", allnodes, rid)
st.write(); st.compose("up", "-d", "--force-recreate", "k1")
wait_for("rpc", k1.rpc().version, 120, 2)
st.compose("up", "-d", "--force-recreate", "k2", "x"); [wait_for("rpc", n.rpc().version, 120, 2) for n in (k2, x)]
st.compose("up", "-d", "--force-recreate", "o", "m"); [wait_for("rpc", n.rpc().version, 120, 2) for n in (o, m)]
def call(n, body, to=60):
    body = dict(body, userpass=n.mm2()["rpc_password"])
    r = urllib.request.Request(f"http://127.0.0.1:{n.host_port}/", data=json.dumps(body).encode())
    try: return json.loads(urllib.request.urlopen(r, timeout=to).read())
    except Exception as e: return {"error": str(e)}
def activate(n, cl): return [c for c in cl if "address" in call(n, {"method": "electrum", "coin": c, "servers": els_j[c]})]
FUND = ["RIN", "KMD", "DOGE", "LBC"]
print("activated m", activate(m, FUND), "o", activate(o, FUND), flush=True)
call(m, {"method": "cancel_all_orders", "cancel_by": {"type": "All"}}); time.sleep(3)
logs = {n.service: n.log_path(rid) for n in allnodes}
PATS = {"NPTR": "No pair trie root", "ISR": "InvalidStateRoot", "REQERR": "P2P request error|Error on send request", "CLOSE": "a close signal came"}
def cnt(path, pat):
    try: return int(subprocess.run(["grep", "-ciE", pat, path], capture_output=True, text=True).stdout.strip() or 0)
    except Exception: return -1
last = {}
def row(label, pairs):
    """per-round record: log counters (delta since previous row) on o, k2, m; O's book vs M's per pair; M degraded gauge."""
    d = {}
    for svc in ("o", "k2", "m", "k1"):
        for k, p in PATS.items():
            c = cnt(logs[svc], p); d[f"{svc}.{k}"] = c - last.get((svc, k), 0); last[(svc, k)] = c
    mets = call(m, {"method": "metrics"}); deg = None
    try: deg = [mm for mm in mets["metrics"] if mm.get("key") == "p2p.outbox.degraded"][0]["value"]
    except Exception: pass
    books = {}
    for b, r in pairs:
        ob = call(o, {"method": "orderbook", "base": b, "rel": r}, to=30)
        mb = call(m, {"method": "my_orders"}, to=30)
        books[f"{b}/{r}"] = len(ob.get("asks", [])) if "asks" in ob else str(ob.get("error"))[:40]
    line = f"{time.strftime('%H:%M:%S')} {label} degraded={deg} errs={ {k: v for k, v in d.items() if v} } O_book={books}"
    print(line, flush=True); open(OUT + "s8.log", "a").write(line + "\n")
def setp(b, r, price, vol="0.01"): return call(m, {"method": "setprice", "base": b, "rel": r, "price": str(price), "volume": vol, "cancel_previous": False})
def kill_x(): print("kill X", subprocess.run(["docker", "stop", "-t", "1", "swaplab-s8-x"], capture_output=True, text=True).returncode, flush=True)
def policing(kbit):
    pre = ["docker", "run", "--rm", "--cap-add", "NET_ADMIN", "--net", "container:swaplab-s8-m", "--entrypoint", "tc", "swaplab/netem:1"]
    subprocess.run(pre + ["qdisc", "add", "dev", "eth0", "handle", "ffff:", "ingress"], capture_output=True, text=True)
    r = subprocess.run(pre + ["filter", "add", "dev", "eth0", "parent", "ffff:", "protocol", "ip", "prio", "1", "u32", "match", "u32", "0", "0", "police", "rate", f"{kbit}kbit", "burst", "16k", "drop"], capture_output=True, text=True)
    print("policing M inbound", kbit, "kbit", r.returncode, flush=True)
# step 1: control, pair A = RIN/KMD with 3 orders
PA = ("RIN", "KMD"); uA = [setp(*PA, 1 + i / 100) for i in range(3)]
call(o, {"method": "orderbook", "base": PA[0], "rel": PA[1]})
for i in range(3):
    time.sleep(A.round_secs); row(f"control{i + 1}", [PA])
# step 2: trip
newB, newC = ("KMD", "RIN"), ("DOGE", "RIN")
setp(*PA, 1.5)
if A.trip == "kill": kill_x()
else: policing(A.throttle)
time.sleep(10); row("tripped", [PA])
# step 3-4: orders on new pairs + price updates every 5 s while watching rounds
ub = [setp(*newB, 1 + i / 100) for i in range(3)]; uc = [setp(*newC, 1 + i / 100) for i in range(3)]
stop = [False]
def bot():
    n = 0
    while not stop[0]:
        n += 1
        for u in (ub + uc):
            if "result" in u: call(m, {"method": "update_maker_order", "uuid": u["result"]["uuid"], "new_price": str(1 + (n % 50) / 1000 + 0.2)})
        time.sleep(5)
t = threading.Thread(target=bot, daemon=True); t.start()
for i in range(A.rounds):
    time.sleep(A.round_secs); row(f"degraded{i + 1}", [PA, newB, newC])
# step 5: cancel B and C
stop[0] = True; t.join(timeout=10)
for u in ub + uc:
    if "result" in u: call(m, {"method": "cancel_order", "uuid": u["result"]["uuid"]})
for i in range(2):
    time.sleep(A.round_secs); row(f"cancelled{i + 1}", [PA, newB, newC])
# step 6: restart M
subprocess.run(["docker", "restart", "-t", "5", "swaplab-s8-m"], capture_output=True, text=True); wait_for("rpc", m.rpc().version, 120, 2)
for i in range(2):
    time.sleep(A.round_secs); row(f"restarted{i + 1}", [PA, newB, newC])
print("done; logs", logs, flush=True)
