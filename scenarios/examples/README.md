# Example scenarios

Examples of what kdf-swaplab can do beyond the five basic `scenarios/*.json`. They are examples:
the binaries, wallets and funds are yours to provide, and a few of the coins are small projects
whose servers may be gone when you read this. Copy one, adapt it, run it:

```sh
./swaplab.py run scenarios/examples/doge-rin.json --dry-run     # starts the nodes, trades nothing
./swaplab.py run scenarios/examples/doge-rin.json
```

All of them use the shared files in this directory instead of `config/coins.json`:
- `coins.json`: coin definitions (UTXO coins, a shielded coin, EVM platform coins and tokens)
- `coins-header-layouts.json`: the same, plus explicit block-header layouts for MEWC and RIC
- `electrums.json`: electrum servers for the UTXO coins

`W1` and `W2` are the wallet names from `secrets/`. Real coins move: keep the wallets small. The
amounts below are what one swap needs, plus fees; the harness checks the balances before it
starts.

## Scenario files (run with `swaplab.py run`)

| File | What it shows | Maker, taker, seed | Trade (maker sells) | Needs |
|---|---|---|---|---|
| `rin-kmd-r-k.json` | Mixed flavors: Reloaded maker, Komodo 2.6 taker and seed. A coin pair that is not the default LTC/KMD. | R, K, K (netid 8762) | 5 RIN for 0.325 KMD | W1: RIN; W2: KMD |
| `g-g-rin-kmd.json` | The same implementation (GLEEC 3.x) on all three nodes of the GLEEC network, to see one build alone before you compare it with others. | G, G, G (netid 6133) | 5 RIN for 0.325 KMD | W1: RIN; W2: KMD |
| `doge-rin.json` | A legacy UTXO coin with tiny amounts: how to choose a trade size above both dust limits. | R, R, R | 0.3 DOGE for 15 RIN | W1: DOGE; W2: RIN |
| `rxd-rin.json` | A coin with its own signature-hash rule (Radiant): signing of HTLC payment and spend. | R, R, R | 100 RXD for 5 RIN | W1: RXD; W2: RIN |
| `nmc-rin-no-spend-check.json` | A merged-mining (AuxPoW) coin, and `mm2_extra`: extra keys written into MM2.json of every node (here the pre-trade spend check is switched off). | R, R, R | 0.001 NMC for 0.5 RIN | W1: NMC; W2: RIN |
| `mewc-rin-header-layout.json` | A scenario with its own coins file: the block-header layout is declared in the coin entry. | R, R, R | 100 MEWC for 5 RIN | W1: MEWC; W2: RIN |
| `dingo-rin-trading-v2.json` | A coin with a fixed-fee transaction policy, swapped with the second trading protocol (`use_trading_proto_v2` through `mm2_extra`). | R, R, R | 20 DINGO for 20 RIN | W1: DINGO; W2: RIN |
| `arrr-rin-shielded.json` | A shielded (ZHTLC) coin: the `special` section activates ARRR in light mode against public lightwalletd servers; a slow first sync is normal. Needs a Reloaded build for the ARRR leg. | R, R, R | 0.0005 ARRR for 0.5 RIN | W2: ARRR; W1: RIN |
| `usdc-plg20-rin-token.json` | An EVM token: the `special` section activates the platform coin (POL) together with the token. Both wallets need some POL for gas. | R, R, R | 0.5 USDC (Polygon) for 5 RIN | W2: USDC, POL; W1: RIN, POL |

Each scenario also demonstrates the `expect` block (here simply "success"); see
[the manual](../../docs/MANUAL.md#expectations) for failing expectations, which is how a known
defect is pinned down before and after its fix.

Things a scenario file can do that these examples use:
- `flavor` per node: any mix of R, K and G on one netid
- `mm2_extra`: extra MM2.json keys for all nodes, or per node (`seed`/`maker`/`taker`)
- `special`: activation of coins that are not electrum coins (`zcoin`, `evm` with tokens)
- `coins_file` / `electrums`: a coin set per scenario
- `trade`: any base/rel, price and volume

## Faults: slow, lossy, restarted or killed nodes

The scenario format describes a clean swap and has no fault section. Faults are injected by
scripts; see [scripts/examples/](../../scripts/examples/README.md).

A plan that combines these scenarios and the fault scripts into a release gate is in
[docs/example-test-plan.md](../../docs/example-test-plan.md).
