# Example scripts: fault injection

Two runners that use the harness libraries (`lib/nodes.py`) directly and inject faults with
`tc` and `docker`, which a scenario file cannot express.

The scenario format ([scenarios/examples/](../../scripts/examples/README.md)) describes a clean swap. Slow, lossy, restarted or killed nodes are not part
of it; they are done by two scripts that use the harness libraries directly and inject the
faults with `tc` and `docker`. Run them from the repository root. One stack at a time; output
goes to `out/faults/<name>/`. Both need the helper image `swaplab/netem:1` (build instructions below) and a funded wallet W1
(the maker).

| Script | Fault | What it measures |
|---|---|---|
| `seed-throttle-load.py` | Seed link policed to a low rate (`--seedthrottle KBIT`), maker link throttled (`--throttle`) or lossy and jittery (`--netem`: delay 80 ms, 30 ms jitter, 3 % loss, 10 % reorder), maker restarted mid-run (`--rrestart MIN`), maker paused for 60 s (`--pausemaker MIN`), order churn (`--orderchurn SEC`), subscriber that joins and leaves (`--churn`), a second order source (`--gen`, `--burst2`) | A maker with many orders (`--coins`, `--k`, `--perpair`) updates prices for `--minutes` while observers subscribe. Every few seconds: memory of seed and maker, send queues, CPU, the count of sync errors and orderbook sizes on each observer. At the end a summary with peaks. |
| `relay-kill-degraded.py` (not yet run end to end) | The maker has two seeds and one is killed (`--trip kill`), or its link is throttled (`--trip throttle`), while it keeps creating orders on new pairs | A chain of relays maker, relay, relay, observer, with Komodo nodes as relays and observer. `--variant v1` makes the middle relay a Reloaded node, `--variant v2` sets `maker_update_coalescing` to false on the maker (a key only newer Reloaded builds know). Per round: errors on the observer and the middle relay, the observer's orderbook against the maker's own orders, the maker's degraded-mode gauge. |

Typical calls:

```sh
# 600 orders on 20 pairs, seed inbound limited to 100 kbit/s, 20 minutes
VOL=1 ./scripts/examples/seed-throttle-load.py --name throttled --bin R --minutes 20 \
    --coins 8 --basecoins 4 --k 20 --perpair 30 --partial --seedthrottle 100

# lossy maker link and one maker restart at minute 10
./scripts/examples/seed-throttle-load.py --name lossy --bin R --minutes 20 --netem --rrestart 10

# a maker that loses one of its two seeds
./scripts/examples/relay-kill-degraded.py --name kill-x --bin R --trip kill --rounds 6
```

`--bin` is a key of `config/binaries.json` (the build of the maker). `--seedflavor` of the first
script selects the flavor of the seed (G by default). The scripts are examples, not a stable
interface: read the top of each file before you rely on a flag.

Build the helper images once (they need `swaplab/kdf-runtime:debian12` from `docker/Dockerfile`):

```sh
docker build -t swaplab/netem:1 -f scripts/examples/Dockerfile.netem docker/
docker build -t swaplab/gdb:1   -f scripts/examples/Dockerfile.gdb docker/     # optional, malloc_trim step only
```
