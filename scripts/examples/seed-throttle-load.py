"""seed-throttle-load.py  --name N --bin KEY [--minutes 20] [--coins 10] [--upd chg|noop|none] [--partial] [--netem] [--churn] [--seedthrottle KBIT] [--throttle KBIT] [--rrestart MIN] [--gen]
Fault-injection example: a maker with many orders talks to a seed through a slow, lossy or restarted link; observers (all-pairs and partial) keep subscribing; memory, queues and sync errors are sampled every few seconds.
This is a runner on top of the harness libraries (lib/nodes.py), not a scenario file: the faults are injected with tc (policing, netem) and docker (restart) from inside the script.
Run from the repository root, one stack at a time; output goes to out/faults/<name>/.
 g  : G seed/relay (.20)
 m  : maker, R (binaries.json key --bin), wallet W1, K pairs (one order each)
 t  : G all-pairs subscriber (.22), runs `orderbook` on every pair every 60 s
 a,b: G partial subscribers (--partial), each `orderbook` on 3 different pairs of the maker only (.23/.24)
 c  : G churner (--churn), subscribes to half of the pairs, container restarted every 150 s (.25)
 --netem: tc netem on the maker container (delay 80ms 30ms, loss 3%, reorder 10%)
 --rrestart MIN: restart the maker container at minute MIN (orders restored from storage)."""
import sys, os, json, time, subprocess, threading, urllib.request, argparse, re, random
from concurrent.futures import ThreadPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from lib import nodes
from lib.kdfrpc import wait_for
import swaplab
ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True); ap.add_argument("--bin", default="R"); ap.add_argument("--minutes", type=int, default=20)
ap.add_argument("--coins", type=int, default=10); ap.add_argument("--upd", default="chg")
ap.add_argument("--partial", action="store_true"); ap.add_argument("--netem", action="store_true"); ap.add_argument("--churn", action="store_true")
ap.add_argument("--arrr", type=int, default=0); ap.add_argument("--arrr-period", type=int, default=15)
ap.add_argument("--k", type=int, default=0); ap.add_argument("--orderchurn", type=int, default=0)
ap.add_argument("--throttle", type=int, default=0); ap.add_argument("--gen", action="store_true")
ap.add_argument("--pausemaker", type=int, default=0); ap.add_argument("--afterstop", type=int, default=0)
ap.add_argument("--genorders", type=int, default=12); ap.add_argument("--genthreads", type=int, default=1); ap.add_argument("--obsleep", type=int, default=60)
ap.add_argument("--genstop", type=int, default=0); ap.add_argument("--mstop", action="store_true")
ap.add_argument("--perpair", type=int, default=1); ap.add_argument("--basecoins", type=int, default=0); ap.add_argument("--updpool", type=int, default=6)
ap.add_argument("--seedenv", action="append", default=[])
ap.add_argument("--seedflavor", default="G"); ap.add_argument("--burst1", type=int, default=0); ap.add_argument("--burst2", type=int, default=0)
ap.add_argument("--seedthrottle", type=int, default=0)
ap.add_argument("--mextra", default="{}")
ap.add_argument("--rrestart", type=int, default=0); ap.add_argument("--rustlog", default=None)
A = ap.parse_args()
EX = os.path.join(ROOT, "scenarios", "examples") + "/"
SP = os.path.join(ROOT, "out", "faults") + "/"
NET = 6133
FUNDED = ["RIN", "KMD", "DOGE", "LBC"]
ALL = FUNDED + ["LTC", "DINGO", "SHIC", "TRMP", "SYS", "NMC", "PIVX", "XMY"]
COINS = ALL[:A.coins]
PAIRS = [(b, r) for b in COINS for r in COINS if b != r]
PAIRS.sort(key=lambda p: (p[0] not in FUNDED, COINS.index(p[0]), COINS.index(p[1])))   # funded bases first
coins, els = EX + "coins.json", EX + "electrums.json"
els_j = json.load(open(els))
bins = swaplab.binaries(); bins["R"] = bins[A.bin]
SEEDIP = "10.99.4.20"
def mk(role, flavor, name, svc, ip, port, wallet=None, seeds=(), extra=None):
    n = nodes.Node(role, flavor, swaplab.binary_for(flavor, bins), NET, ip, port, wallet=wallet, coins_file=coins, seednodes=list(seeds), disable_p2p=False)
    n.name = name; n.dir = os.path.join(nodes.RUN, name); n.service = svc
    n.env = {"RUST_LOG": A.rustlog} if A.rustlog else {}
    n.prepare("kdf-swaplab-oom")
    cfg = n.mm2(); cfg.update(extra or {})
    fd = os.open(n.mm2_path, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(cfg, f)
    return n
SUBX = {"is_bootstrap_node": False, "i_am_seed": False, "seednodes": [SEEDIP], "disable_p2p": False}   # throwaway identity (role=seed), joins as an ordinary peer
for proj in ("swaplab-p-g1", "swaplab-oom"):
    nodes.sh("docker", "compose", "-p", proj, "down", "--timeout", "30", check=False)
gseed = mk("seed", A.seedflavor, "p-gseed", "g", SEEDIP, 7815)
gseed.env = dict(kv.split("=", 1) for kv in A.seedenv)
maker = mk("wallet", "R", "p-oomr", "m", "10.99.4.21", 7816, wallet="W1", seeds=[SEEDIP], extra={"pre_trade_spend_check": False, **json.loads(A.mextra)})
subs = {"t": mk("seed", "G", "p-oomt", "t", "10.99.4.22", 7817, extra=SUBX)}
if A.partial:
    subs["a"] = mk("seed", "G", "p-ooma", "a", "10.99.4.23", 7818, extra=SUBX)
    subs["b"] = mk("seed", "G", "p-oomb", "b", "10.99.4.24", 7819, extra=SUBX)
if A.churn:
    subs["c"] = mk("seed", "G", "p-oomc", "c", "10.99.4.25", 7820, extra=SUBX)
gen = None
if A.gen:
    gen = mk("wallet", "G", "p-oomgm", "gm", "10.99.4.26", 7821, wallet="W2", seeds=[SEEDIP])
    w2_was_up = "swaplab-p-w2-wallet" in subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    if w2_was_up: subprocess.run(["docker", "stop", "-t", "30", "swaplab-p-w2-wallet"], capture_output=True); print("stopped p-w2 (wallet W2 is used by the traffic generator)", flush=True)
gen2 = None
if A.burst2:
    gen2 = mk("wallet", "G", "p-oomgm2", "g2", "10.99.4.27", 7822, wallet="W1", seeds=[SEEDIP])
allnodes = [gseed, maker] + list(subs.values()) + ([gen] if gen else []) + ([gen2] if gen2 else [])
rid = swaplab.now_id() + "-oom"
st = nodes.Stack("swaplab-oom", "10.99.4.0/24", allnodes, rid)
st.write(); st.compose("up", "-d", "--force-recreate", "g")
wait_for("rpc", gseed.rpc().version, 120, 2)
st.compose("up", "-d", "--force-recreate", *[n.service for n in allnodes[1:] if n is not gen2])
for n in allnodes:
    if n is not gen2: wait_for("rpc", n.rpc().version, 120, 2)
print("run", A.name, "bin", A.bin, vars(A), flush=True)
def call(n, body, to=60):
    body = dict(body, userpass=n.mm2()["rpc_password"])
    r = urllib.request.Request(f"http://127.0.0.1:{n.host_port}/", data=json.dumps(body).encode())
    try: return json.loads(urllib.request.urlopen(r, timeout=to).read())
    except Exception as e: return {"error": str(e)}
def activate(n, cl):
    ok = [c for c in cl if "address" in call(n, {"method": "electrum", "coin": c, "servers": els_j[c]})]
    return ok
# partial subsets: three pairs each, different for a and b, taken from the maker's pair list (filled in after setprice)
us = []   # (uuid, base, rel, price)
def pairs_of_maker(): return [(b, r) for _, b, r, _ in us]
act = {}
for n in allnodes[:1] + [maker]: act[n.name] = activate(n, COINS)
print("seed/maker activated", act, flush=True)
# the maker dir persists between runs: cancel orders left over from earlier runs so every run starts with exactly K orders
pre = len(call(maker, {"method": "my_orders"}).get("result", {}).get("maker_orders", {}))
print("stale maker orders before cleanup:", pre, "cancel:", str(call(maker, {"method": "cancel_all_orders", "cancel_by": {"type": "All"}}))[:100], flush=True)
time.sleep(3)
print("maker orders after cleanup:", len(call(maker, {"method": "my_orders"}).get("result", {}).get("maker_orders", {})), flush=True)
# post the K orders
def setp(i_pair):
    i, (b, r) = i_pair
    x = call(maker, {"method": "setprice", "base": b, "rel": r, "price": str(1 + i / 1000), "volume": "0.1", "cancel_previous": False})
    return (b, r, x)
posted = []; failed = []
if A.basecoins: PAIRS = [p for p in PAIRS if p[0] in FUNDED[:A.basecoins]]
npairs = 0
def post_one(job):
    i, j, b, r = job
    pr = 1 + i / 1000 + j / 100000
    x = call(maker, {"method": "setprice", "base": b, "rel": r, "price": str(pr), "volume": os.environ.get("VOL", "0.01"), "cancel_previous": False})
    return (b, r, pr, x)
jobs = []
for i, (b, r) in enumerate(PAIRS):
    if A.k and i >= A.k: break
    for j in range(max(1, A.perpair)): jobs.append((i, j, b, r))
with ThreadPoolExecutor(6) as pp:
    for b, r, pr, x in pp.map(post_one, jobs):
        if "result" in x: us.append((x["result"]["uuid"], b, r, pr))
        else: failed.append((b, r, str(x.get("error"))[-100:]))
print(f"orders posted = {len(us)} over {len({(b, r) for _, b, r, _ in us})} pairs (of {len(jobs)} tried); first failures: {failed[:3]}", flush=True)
ARRRB = ["RIN", "KMD", "DOGE"][:A.arrr]
arrr_orders = {}
def v2(n, method, params, to=60): return call(n, {"mmrpc": "2.0", "method": method, "params": params}, to)
if A.arrr:
    import shutil
    zp = os.path.join(maker.dir, "zcash-params")
    if not os.path.exists(zp): shutil.copytree(os.environ.get("ZCASH_PARAMS", os.path.join(nodes.RUN, "p-w2", "zcash-params")), zp)   # a directory with the Zcash Sapling parameters
    lw = ["https://lightwalletd1.cryptoforge.cc:443", "https://lightwalletd2.cryptoforge.cc:443", "https://electrum1.cipig.net:9447", "https://electrum2.cipig.net:9447"]
    r = v2(maker, "task::enable_z_coin::init", {"ticker": "ARRR", "activation_params": {"mode": {"rpc": "Light", "rpc_data": {"light_wallet_d_servers": lw, "sync_params": {"height": 4182000}}}, "zcash_params_path": "/kdf/zcash-params"}})
    tid = r.get("result", {}).get("task_id"); print("ARRR init", str(r)[:120], flush=True)
    for _ in range(240):
        sj = json.dumps(v2(maker, "task::enable_z_coin::status", {"task_id": tid}))
        if '"status": "Ok"' in sj or '"status": "Error"' in sj: break
        time.sleep(5)
    print("ARRR activation:", sj[:200], flush=True)
    def post_arrr(b):
        x = call(maker, {"method": "setprice", "base": b, "rel": "ARRR", "price": "1", "volume": "0.1", "cancel_previous": False})
        return x["result"]["uuid"] if "result" in x else print("ARRR setprice FAIL", b, str(x)[:200], flush=True)
    for b in ARRRB: arrr_orders[b] = post_arrr(b)
mp = pairs_of_maker()
random.seed(7)
mp = sorted(set(mp))
subsets = {"t": mp}
if A.partial:
    pick = random.sample(mp, min(6, len(mp))); subsets["a"] = pick[:3]; subsets["b"] = pick[3:]
if A.churn: subsets["c"] = random.sample(mp, len(mp) // 2)
def subscribe(k):
    n = subs[k]; need = sorted({c for p in subsets[k] for c in p})
    ok = activate(n, need)
    for b, r in subsets[k]:
        if b in ok and r in ok: call(n, {"method": "orderbook", "base": b, "rel": r})
    return ok
for k in subs: print("sub", k, "pairs", len(subsets[k]), "coins ok", subscribe(k), flush=True)
def netem(on):
    if not on: return
    cmd = ["docker", "run", "--rm", "--cap-add", "NET_ADMIN", "--net", "container:swaplab-oom-m", "--entrypoint", "tc", "swaplab/netem:1",
           "qdisc", "replace", "dev", "eth0", "root", "netem", "delay", "80ms", "30ms", "loss", "3%", "reorder", "10%", "50%"]
    print("netem:", subprocess.run(cmd, capture_output=True, text=True).returncode, flush=True)
GP = [("RIN","KMD"),("KMD","RIN"),("DOGE","KMD"),("KMD","DOGE"),("RIN","DOGE"),("DOGE","RIN"),("LBC","RIN"),("RIN","LBC"),("LBC","KMD"),("KMD","LBC"),("LBC","DOGE"),("DOGE","LBC")]
gen_us = []
if gen:
    print("gen activated", activate(gen, FUNDED), flush=True)
    pre_g = len(call(gen, {"method": "my_orders"}).get("result", {}).get("maker_orders", {}))
    call(gen, {"method": "cancel_all_orders", "cancel_by": {"type": "All"}}); time.sleep(3)
    print("generator stale orders cancelled:", pre_g, "remaining:", len(call(gen, {"method": "my_orders"}).get("result", {}).get("maker_orders", {})), flush=True)
    for i in range(A.genorders):
        b, r = GP[i % len(GP)]
        x = call(gen, {"method": "setprice", "base": b, "rel": r, "price": str(2 + i / 100), "volume": "0.1", "cancel_previous": False})
        if "result" in x: gen_us.append((x["result"]["uuid"], 2 + i / 100))
        else: print("gen setprice FAIL", b, r, str(x.get("error"))[-100:], flush=True)
    print("gen orders", len(gen_us), flush=True)
def policing(kbit, ctr="swaplab-oom-m"):
    pre = ["docker", "run", "--rm", "--cap-add", "NET_ADMIN", "--net", "container:" + ctr, "--entrypoint", "tc", "swaplab/netem:1"]
    a = subprocess.run(pre + ["qdisc", "add", "dev", "eth0", "handle", "ffff:", "ingress"], capture_output=True, text=True)
    b = subprocess.run(pre + ["filter", "add", "dev", "eth0", "parent", "ffff:", "protocol", "ip", "prio", "1", "u32", "match", "u32", "0", "0", "police", "rate", f"{kbit}kbit", "burst", "16k", "drop"], capture_output=True, text=True)
    print("inbound policing on", ctr, ":", kbit, "kbit", a.returncode, b.returncode, b.stderr[:100], flush=True)
if A.throttle: policing(A.throttle)
if A.seedthrottle: policing(A.seedthrottle, "swaplab-oom-g")
netem(A.netem)
def rss(c):
    for l in subprocess.run(["docker", "top", c, "-eo", "pid,comm"], capture_output=True, text=True).stdout.split("\n"):
        p = l.split()
        if len(p) == 2 and p[1].startswith("kdf"):
            try:
                for x in open(f"/proc/{p[0]}/status"):
                    if x.startswith("VmRSS"): return int(x.split()[1]) // 1024
            except Exception: pass
    return -1
def pdirty(c):
    for l in subprocess.run(["docker", "top", c, "-eo", "pid,comm"], capture_output=True, text=True).stdout.split("\n"):
        p = l.split()
        if len(p) == 2 and p[1].startswith("kdf"):
            try:
                for x in open(f"/proc/{p[0]}/smaps_rollup"):
                    if x.startswith("Private_Dirty"): return int(x.split()[1]) // 1024
            except Exception: pass
    return -1
def cnt(path, pat):
    try: return int(subprocess.run(["grep", "-ciE", pat, path], capture_output=True, text=True).stdout.strip() or 0)
    except Exception: return -1
logs = {n.service: n.log_path(rid) for n in allnodes}
SUBLOGS = [k for k in logs if k not in ("g", "m", "gm")]
PATS = {"ISR": "InvalidStateRoot", "NPTR": "No pair trie root", "P2PERR": "P2P request error"}
def lastrx(path):
    try:
        for l in reversed(open(path, errors="replace").read().split("\n")):
            m = re.search(r"orderbook\.len=([\d.]+).*?received_messages\.count=([\d.]+)", l)
            if m: return int(float(m.group(1))), int(float(m.group(2)))
    except Exception: pass
    return -1, -1
stop = [False]; ops = [0]; rounds = [0]; sweep = [0]
def watcher():
    while not stop[0]:
        tot = 0
        for b, r in subsets["t"]:
            tot += len(call(subs["t"], {"method": "orderbook", "base": b, "rel": r}).get("asks", []))
        sweep[0] = tot
        for k in subs:
            if k in ("a", "b"):
                for b, r in subsets[k]: call(subs[k], {"method": "orderbook", "base": b, "rel": r})
        t_end = time.time() + A.obsleep
        while time.time() < t_end:
            if stop[0]: return
            time.sleep(0.5)
def churner():
    while not stop[0]:
        for _ in range(150):
            if stop[0]: return
            time.sleep(1)
        subprocess.run(["docker", "restart", "-t", "10", "swaplab-oom-c"], capture_output=True)
        try: wait_for("rpc", subs["c"].rpc().version, 120, 2); subscribe("c"); print("churner reconnected", flush=True)
        except Exception as e: print("churner err", e, flush=True)
def rrestart():
    time.sleep(A.rrestart * 60 - 30)
    if stop[0]: return
    subprocess.run(["docker", "restart", "-t", "20", "swaplab-oom-m"], capture_output=True)
    try:
        wait_for("rpc", maker.rpc().version, 180, 2); activate(maker, COINS)
        time.sleep(10)
        print("maker restarted; my_orders:", len(call(maker, {"method": "my_orders"}).get("result", {}).get("maker_orders", {})), flush=True)
    except Exception as e: print("restart err", e, flush=True)
arrr_cycles = [0]
def arrrchurn():
    while not stop[0]:
        for _ in range(A.arrr_period):
            if stop[0]: return
            time.sleep(1)
        for b in ARRRB:
            u = arrr_orders.get(b)
            if u: call(maker, {"method": "cancel_order", "uuid": u})
            x = call(maker, {"method": "setprice", "base": b, "rel": "ARRR", "price": "1", "volume": "0.1", "cancel_previous": False})
            arrr_orders[b] = x["result"]["uuid"] if "result" in x else None
        arrr_cycles[0] += 1
_helpers = set()
def ssq(ctr, peer_prefix, col):
    h = "sq-" + ctr
    if h not in _helpers:
        subprocess.run(["docker", "rm", "-f", h], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--name", h, "--net", "container:" + ctr, "--entrypoint", "sleep", "swaplab/netem:1", "infinity"], capture_output=True)
        _helpers.add(h)
    r = subprocess.run(["docker", "exec", h, "ss", "-tn"], capture_output=True, text=True).stdout
    tot = 0
    for l in r.split("\n"):
        p = l.split()
        if len(p) >= 5 and p[0] == "ESTAB" and p[4].startswith(peer_prefix):
            try: tot += int(p[col])
            except ValueError: pass
    return tot
def sendq(): return ssq("swaplab-oom-g", "10.99.4.21:", 2)
os.makedirs(SP + f"{A.name}", exist_ok=True)
_pids = {}
def kpid(c):
    if c in _pids and os.path.exists(f"/proc/{_pids[c]}"): return _pids[c]
    for l in subprocess.run(["docker", "top", c, "-eo", "pid,comm"], capture_output=True, text=True).stdout.split("\n"):
        p = l.split()
        if len(p) == 2 and p[1].startswith("kdf"): _pids[c] = p[0]; return p[0]
    return None
def cputicks(c):
    p = kpid(c)
    try:
        f = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split(); return int(f[11]) + int(f[12])
    except Exception: return None
def sampler():
    t0 = time.time(); f = open(SP + f"{A.name}/sampler.csv", "w"); f.write("sec,g_rss,m_rss,sendq_seed_to_maker,recvq_maker_from_seed,genops,g_privdirty,sendq_maker_to_seed,recvq_seed_from_maker,g_cpu_pct,m_cpu_pct\n")
    last = {"g": (time.time(), cputicks("swaplab-oom-g")), "m": (time.time(), cputicks("swaplab-oom-m"))}
    def cpupct(k):
        tk = cputicks("swaplab-oom-" + k); now = time.time(); lt, ltk = last[k]; last[k] = (now, tk)
        if tk is None or ltk is None or now <= lt: return -1
        return round((tk - ltk) / (now - lt))
    while not stop[0]:
        f.write(f"{int(time.time() - t0)},{rss('swaplab-oom-g')},{rss('swaplab-oom-m')},{sendq()},{ssq('swaplab-oom-m', '10.99.4.20:', 1)},{genops[0]},{pdirty('swaplab-oom-g')},{ssq('swaplab-oom-m', '10.99.4.20:', 2)},{ssq('swaplab-oom-g', '10.99.4.21:', 1)},{cpupct('g')},{cpupct('m')}\n"); f.flush()
        time.sleep(8)
genops = [0]; genoff = [False]
gpool = ThreadPoolExecutor(max(1, A.genthreads))
def genload():
    while not stop[0]:
        if genoff[0]:
            time.sleep(1); continue
        s = time.time()
        def g1(up):
            u, p = up
            call(gen, {"method": "update_maker_order", "uuid": u, "new_price": str(p * (1 + 0.0001 * random.randint(1, 97)))}, to=30); genops[0] += 1
        list(gpool.map(g1, gen_us))
def ordchurn():
    while not stop[0]:
        for _ in range(A.orderchurn):
            if stop[0]: return
            time.sleep(1)
        for i, (uuid, b, r, p) in enumerate(list(us)):
            call(maker, {"method": "cancel_order", "uuid": uuid})
            x = call(maker, {"method": "setprice", "base": b, "rel": r, "price": str(p), "volume": "0.1", "cancel_previous": False})
            if "result" in x: us[i] = (x["result"]["uuid"], b, r, p)
        print("order churn cycle done", flush=True)
def pausemaker():
    time.sleep(A.pausemaker * 60)
    if stop[0]: return
    print("docker pause maker 60 s", flush=True)
    subprocess.run(["docker", "pause", "swaplab-oom-m"], capture_output=True); time.sleep(60)
    subprocess.run(["docker", "unpause", "swaplab-oom-m"], capture_output=True); print("maker unpaused", flush=True)
T0 = [None]
def bursts():
    while T0[0] is None: time.sleep(1)
    def waitmin(x):
        while time.time() < T0[0] + x * 60:
            if stop[0]: return False
            time.sleep(1)
        return True
    if A.burst1 and waitmin(A.burst1):
        print("burst1 (same pubkey) start", flush=True); t_end = time.time() + 60; n_up = 0
        while time.time() < t_end:
            for u, p in gen_us[:5]:
                call(gen, {"method": "update_maker_order", "uuid": u, "new_price": str(p * (1 + 0.0001 * random.randint(1, 97)))}); n_up += 1
            time.sleep(5)
        print("burst1 done, updates:", n_up, flush=True)
    if A.burst2 and waitmin(A.burst2):
        print("burst2 (different pubkey) start", flush=True)
        st.compose("up", "-d", "--force-recreate", "g2"); wait_for("rpc", gen2.rpc().version, 120, 2); activate(gen2, FUNDED)
        g2u = []
        for b, r in GP[:3]:
            x = call(gen2, {"method": "setprice", "base": b, "rel": r, "price": "3.0", "volume": "0.1", "cancel_previous": False})
            if "result" in x: g2u.append(x["result"]["uuid"])
        t_end = time.time() + 60; n_up = 0
        while time.time() < t_end:
            for u in g2u: call(gen2, {"method": "update_maker_order", "uuid": u, "new_price": str(3.0 * (1 + 0.0001 * random.randint(1, 97)))}); n_up += 1
            time.sleep(5)
        print("burst2 done, orders:", len(g2u), "updates:", n_up, flush=True)
pool = ThreadPoolExecutor(A.updpool)
def one(u):
    uuid, b, r, p = u
    x = call(maker, {"method": "update_maker_order", "uuid": uuid, "new_price": str(p * (1 + 0.0001 * (random.randint(1, 97)))) if A.upd == "chg" else str(p)}, to=30)
    ops[0] += 1
def loadthread():
    while not stop[0]:
        s = time.time()
        if A.upd == "chg" or (A.upd == "noop" and int(s) % 3 == 0):
            list(pool.map(one, us)); rounds[0] += 1
        d = 1 - (time.time() - s)
        if d > 0: time.sleep(d)
for f in [watcher, loadthread] + ([churner] if A.churn else []) + ([arrrchurn] if A.arrr else []) + ([genload] if gen else []) + ([ordchurn] if A.orderchurn else []) + ([pausemaker] if A.pausemaker else []) + [sampler] + ([bursts] if (A.burst1 or A.burst2) else []) + ([rrestart] if A.rrestart else []):
    threading.Thread(target=f, daemon=True).start()
hdr = "min | RSS g m t [a b c] | G(seed) dISR dNPTR dP2PERR | subs(all) dISR dNPTR dP2PERR | maker dP2PERR | Grx(msg/5min) Gbook | sweepAsks | ops rounds"
print(hdr, flush=True)
prev = {}
T0[0] = time.time()
mfile = open(SP + f"{A.name}/metrics.jsonl", "w")
peak = {"seed": 0, "maker": 0}
mfile2 = open(SP + f"{A.name}/metrics-maker.jsonl", "w")
def _rx(x):
    return max([int(mm.get("value", 0)) for mm in x.get("metrics", []) if mm.get("key") == "p2p.received_messages.count" and isinstance(mm.get("value"), (int, float))] or [0])
def seed_metrics():
    t = int(time.time() - T0[0])
    if A.seedflavor == "R":
        x = call(gseed, {"method": "metrics"}, to=20)
        mfile.write(json.dumps({"t": t, "metrics": x}) + "\n"); mfile.flush(); peak["seed"] = max(peak["seed"], _rx(x))
    if not A.mstop:
        y = call(maker, {"method": "metrics"}, to=20)   # R maker (G has no such method: error is stored as is)
        mfile2.write(json.dumps({"t": t, "metrics": y}) + "\n"); mfile2.flush(); peak["maker"] = max(peak["maker"], _rx(y))
def tot(keys, pat): return sum(max(cnt(logs[k], pat), 0) for k in keys)
SUBK = list(subs)
if A.mstop:
    subprocess.run(["docker", "stop", "-t", "20", "swaplab-oom-m"], capture_output=True); print("maker stopped at start (G-only mesh)", flush=True)
for m in range(1, A.minutes + 1):
    if A.genstop and m == A.genstop + 1: genoff[0] = True; print("generator stopped at minute", A.genstop, flush=True)
    time.sleep(60)
    seed_metrics()
    g = [cnt(logs["g"], p) for p in PATS.values()]
    s_ = [tot(SUBK, p) for p in PATS.values()]
    mm = cnt(logs["m"], PATS["P2PERR"])
    cur = (g, s_, mm)
    pg, ps, pm = prev.get("v", ([0, 0, 0], [0, 0, 0], 0))
    rs = [rss("swaplab-oom-" + k) for k in ["g", "m", "t"] + [k for k in "abc" if k in subs]]
    print(m, "|", *rs, "PD", pdirty("swaplab-oom-g"), "|", "|", *[a - b for a, b in zip(g, pg)], "|", *[a - b for a, b in zip(s_, ps)], "|", mm - pm, "|",
          lastrx(logs["g"])[1], lastrx(logs["g"])[0], "|", sweep[0], "|", ops[0], rounds[0], "arrr_cycles", arrr_cycles[0], flush=True)
    prev["v"] = cur
if A.afterstop:
    subprocess.run(["docker", "stop", "-t", "30", "swaplab-oom-m"], capture_output=True)
    print("maker stopped; seed RSS after stop (every 20 s):", flush=True)
    for i in range(A.afterstop // 20):
        time.sleep(20); print("  +%ds seed %s MB" % ((i + 1) * 20, rss("swaplab-oom-g")), flush=True)
stop[0] = True; time.sleep(2)
if gen and w2_was_up: subprocess.run(["docker", "start", "swaplab-p-w2-wallet"], capture_output=True); print("restarted p-w2", flush=True)
out = SP + f"{A.name}"
for k, p in logs.items(): subprocess.run(["cp", p, f"{out}/{k}.log"])
print("totals G", dict(zip(PATS, [cnt(logs["g"], p) for p in PATS.values()])), "subs", dict(zip(PATS, [tot(SUBK, p) for p in PATS.values()])),
      "maker P2PERR", cnt(logs["m"], PATS["P2PERR"]), flush=True)
def smaps_top(c, tag):
    pid = None
    for l in subprocess.run(["docker", "top", c, "-eo", "pid,comm"], capture_output=True, text=True).stdout.split("\n"):
        p = l.split()
        if len(p) == 2 and p[1].startswith("kdf"): pid = p[0]
    if not pid: print("SMAPS", tag, "no pid", flush=True); return
    maps = []; cur = None
    try:
        for l in open(f"/proc/{pid}/smaps"):
            f = l.split()
            if "-" in f[0] and len(f) >= 5 and ":" not in f[0]:
                a, b = f[0].split("-"); cur = {"range": f[0], "perm": f[1], "path": f[5] if len(f) > 5 else "", "size": (int(b, 16) - int(a, 16)) // 1024, "rss": 0, "pd": 0}; maps.append(cur)
            elif cur and l.startswith("Rss:"): cur["rss"] = int(f[1])
            elif cur and l.startswith("Private_Dirty:"): cur["pd"] = int(f[1])
    except Exception as e: print("SMAPS", tag, "error", e, flush=True); return
    anon = [m for m in maps if m["path"] in ("", "[heap]")]
    top = sorted(anon, key=lambda m: -m["rss"])[:5]
    tot_anon = sum(m["rss"] for m in anon)
    print(f"SMAPS {tag}: anon Rss total {tot_anon // 1024} MB of {sum(m['rss'] for m in maps) // 1024} MB; top5 anon (range perm size_kB rss_kB private_dirty_kB): " +
          "; ".join(f"{m['range']} {m['perm']} {m['size']} {m['rss']} {m['pd']}" for m in top), flush=True)
def probe(tag):
    tot = 0; combos = set(); pks = set(); per = []
    for b_, r_ in GP:
        x = call(gseed, {"method": "orderbook", "base": b_, "rel": r_}, to=60)
        n = 0
        for side in ("asks", "bids"):
            for it in x.get(side, []):
                n += 1; pk = it.get("pubkey"); pks.add(pk); combos.add((pk, tuple(sorted((b_, r_)))))
        tot += n; per.append(n)
    m = call(gseed, {"method": "metrics"}, to=30)
    ol = [mm.get("value") for mm in m.get("metrics", []) if mm.get("key") == "orderbook.len"]
    print(f"PROBE {tag}: orderbook RPC asks+bids over the generator's {len(GP)} pairs = {tot} (per pair {per}); distinct pubkeys {len(pks)}; distinct (pubkey,pair) {len(combos)}; metrics orderbook.len {ol}; generator live orders {len(gen_us)}", flush=True)
def trim_seed():
    probe("before trim"); smaps_top("swaplab-oom-g", "before trim")
    sh = "for p in /proc/[0-9]*; do [ \"$(cat $p/comm 2>/dev/null)\" = kdf ] && echo ${p#/proc/}; done | head -1"
    pid = subprocess.run(["docker", "run", "--rm", "--user", "0", "--pid", "container:swaplab-oom-g", "--entrypoint", "sh", "swaplab/gdb:1", "-c", sh], capture_output=True, text=True).stdout.strip()
    before = rss("swaplab-oom-g"); pd0 = pdirty("swaplab-oom-g")
    r = subprocess.run(["docker", "run", "--rm", "--user", "0", "--pid", "container:swaplab-oom-g", "--cap-add", "SYS_PTRACE", "--security-opt", "seccomp=unconfined", "-v", os.path.abspath(gseed.binary) + ":/usr/local/bin/kdf:ro", "--entrypoint", "gdb", "swaplab/gdb:1",
                        "-batch", "-ex", f"set sysroot /proc/{pid}/root", "-ex", f"attach {pid}", "-ex", "call (int)malloc_trim(0)", "-ex", "detach"], capture_output=True, text=True)
    time.sleep(3)
    print("malloc_trim on seed pid", pid, "RSS before", before, "after", rss("swaplab-oom-g"), "MB; Private_Dirty before", pd0, "after", pdirty("swaplab-oom-g"), "|", (r.stdout + r.stderr).strip().replace("\n", " ")[-200:], flush=True)
    probe("after trim"); smaps_top("swaplab-oom-g", "after trim")
if A.seedflavor == "R": trim_seed()
else: smaps_top("swaplab-oom-g", "G seed end of tail (no trim)")
try:
    keys = set()
    for l in open(SP + f"{A.name}/metrics-maker.jsonl"):
        for mm in json.loads(l)["metrics"].get("metrics", []): keys.add(mm.get("key"))
    print("MAKER METRIC KEYS:", sorted(k for k in keys if k and not k.startswith("rpc_client")), flush=True)
except Exception as e: print("maker metric keys error", e, flush=True)
try:
    series = {}
    for l in open(SP + f"{A.name}/metrics-maker.jsonl"):
        j = json.loads(l)
        for mm in j["metrics"].get("metrics", []):
            k = mm.get("key", "")
            if k.startswith(("p2p.outbox", "p2p.send_queue", "p2p.relay")) or "signals" in k:
                series.setdefault(k, []).append((j["t"], mm.get("value")))
    for k, v in sorted(series.items()): print("MAKER SERIES", k, [(t, x) for t, x in v], flush=True)
except Exception as e: print("maker series error", e, flush=True)
print("PEAK p2p.received_messages.count per 60 s window seen by the per-minute samples: seed", peak["seed"], "maker", peak["maker"], flush=True)
if A.seedenv:
    subprocess.run(["docker", "stop", "-t", "60", "swaplab-oom-g"], capture_output=True)
    lg = subprocess.run(["docker", "logs", "swaplab-oom-g"], capture_output=True, text=True)
    open(out + "/seed-stdout-stderr.txt", "w").write(lg.stdout + "\n--- stderr ---\n" + lg.stderr); print("seed stats saved,", len(lg.stdout) + len(lg.stderr), "bytes", flush=True)
for h in list(_helpers): subprocess.run(["docker", "rm", "-f", h], capture_output=True)
nodes.sh("docker", "compose", "-p", "swaplab-oom", "down", "--timeout", "30", check=False)
