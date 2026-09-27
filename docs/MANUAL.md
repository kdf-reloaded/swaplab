# kdf-swaplab manual

This is the detailed manual. For a first run, [README.md](../README.md) is enough.

1. [What a run does](#what-a-run-does)
2. [Flavors, binaries and netids](#flavors-binaries-and-netids)
3. [Scenario files](#scenario-files)
4. [Wallets and secrets](#wallets-and-secrets)
5. [Funding](#funding)
6. [The private mesh](#the-private-mesh)
7. [Expectations](#expectations)
8. [Evidence](#evidence)
9. [Stuck swaps and recovery](#stuck-swaps-and-recovery)
10. [Commands](#commands)
11. [Troubleshooting](#troubleshooting)
12. [Design notes](#design-notes)

## What a run does

`./swaplab.py run scenarios/<name>.json [--dry-run]` works through these steps and records
each one in `out/<run-id>/progress.log`:

1. **Preflight**
   - Binaries exist and match their pinned SHA-256, if one is set.
   - Both wallets exist, and maker and taker use different wallets.
   - No other swaplab stack is running, so a wallet is never live in two nodes.
   - The host ports are free.
   - The Docker image `swaplab/kdf-runtime:debian12` exists. It is built from `docker/` if missing.
2. **Containers**
   - Compose project `swaplab-<scenario>` gets its own bridge network (`subnet`).
   - It runs three containers: `seed`, `maker` and `taker`.
   - Each container runs its flavor's binary with the node's run directory mounted at `/kdf`.
   - The seed starts first. Maker and taker start only after the seed's RPC answers: some
     flavors (Komodo 2.x) dial their seed nodes once at start-up and then throttle retries, so a
     node that started before its seed listened would never connect.
   - RPC is published on `127.0.0.1:<host_port>` only.
3. **RPC up.** The harness waits for `version` on all three nodes.
4. **Private mesh.** Maker and taker must be connected to peers, and every peer address must be
   inside the scenario's subnet. See [The private mesh](#the-private-mesh).
5. **Coins.** Every coin in `coins` is enabled on maker and taker through electrum, using
   `config/electrums.json`.
6. **Balances and preconditions**
   - The maker must hold `volume` of the base coin plus a fee buffer.
   - The taker must hold `volume × price` of the rel coin (+3 %) plus a fee buffer.
   - For KMD, the taker's coins are read from electrum, and the run records whether the
     "bug-1 precondition" holds. See [Expectations](#expectations).
   - Unfinished swaps on either node stop the run, unless `--allow-active` is given.
7. **Order.** The maker calls `setprice`. The harness waits until the order is in the taker's
   orderbook. `--dry-run` cancels the order here and stops.
8. **Swap**
   - The taker calls `buy` with `match_by: {"type": "Orders", "data": [<order uuid>]}`, so it
     can only match this one order.
   - The harness polls `my_swap_status` on both sides every 15 s and logs each new event.
   - It stops waiting when both sides report `Finished`, when `timeouts.swap` expires, or when
     one side failed before any payment and the other side did not finish within
     `timeouts.peer_failure_grace`.
9. **Export**
   - logs (secret lines removed), diagnostics, decoded transactions, `results.json`,
     `manifest.json`, `SUMMARY.md`
   - A final scan checks the whole evidence directory for secrets. If anything is found, the
     directory is renamed to `*.QUARANTINE` and the command exits with code 3.
10. **Stop**
    - The containers are stopped: first with the KDF `stop` RPC, then `docker compose down`.
    - **Exception:** if a swap is still active (for example waiting for a refund), the containers
      keep running so KDF can finish it. `SUMMARY.md` says so. Stop them later with
      `swaplab.py stop`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | done, and the expectation was met (or there was none) |
| 1 | expectation not met, or harness error |
| 2 | preflight refused; nothing was started |
| 3 | secret material found in the evidence |

## Flavors, binaries and netids

`config/binaries.json` (copy it from `config/binaries.example.json`) maps a flavor letter to a
binary:

```json
{
  "R": {"label": "KDF Reloaded <commit>", "path": "/abs/path/kdf", "sha256": "", "expect": "baseline"},
  "K": {"label": "Komodo 2.6.0-beta_475cdb49b", "path": "bin/kdf_475cdb4/kdf", "sha256": "cf7e95b9…"},
  "G": {"label": "GLEEC 3.0.0-beta_d56a7bc50", "path": "bin/kdf_d56a7bc/kdf", "sha256": "1fb2c0f7…"}
}
```

- **`path`**: absolute, or relative to the repository root. The binary is bind-mounted
  read-only into the container, so it must be a Linux x86-64 build that runs on Debian 12.
- **`sha256`** (optional): when set, a run refuses a binary with another hash. Every run records
  the actual hash in `manifest.json` anyway.
- **`expect`** (R only): which expectation set of the scenarios applies to this R build,
  `baseline` or `fixed`. Override it per run with `--expect`.
- Add more letters (for example `R2` for a second Reloaded build) and use them in scenarios.

Release binaries of K and G are published at
`https://github.com/GLEECBTC/komodo-defi-framework/releases` (formerly
`KomodoPlatform/komodo-defi-framework`). Each zip has a `.asc` signature. To check it without
touching your own keyring:

```sh
export GNUPGHOME=$(mktemp -d)
gpg --keyserver hkps://keys.openpgp.org --recv-keys FD9A772C7300F4C894D1A819FE50480862E6451C
gpg --verify kdf_475cdb4-linux-x86-64.zip.asc kdf_475cdb4-linux-x86-64.zip
```

Building KDF Reloaded yourself:

```sh
git clone https://github.com/kdf-reloaded/kdf && cd kdf
CARGO_TARGET_DIR=$PWD/target-private cargo build --release --bin kdf
# -> target-private/release/kdf
```

Which netid a flavor can use:

| Flavor | 8762 | 6133 | Notes |
|---|---|---|---|
| R (KDF Reloaded) | yes | yes | Accepts only these two netids. The fee address and rate follow the netid. |
| K (Komodo 2.6.0) | yes | not used | 1/777 fee (KMD 9/7770), KMD burns 25 % via OP_RETURN. Its fee rules are those of 8762 on any netid. |
| G (GLEEC 3.x) | no | yes | 8762 is deprecated in GLEEC. One fee address, 2 % (1 % with GLEEC), no burn. |

Two flavors can only trade if they agree on the DEX fee, so a netid only makes sense for the
flavors that implement its fee rules. R against G works only on 6133, and R against K only on 8762.

## Scenario files

One JSON file per test. Everything except secrets and binary paths is in it, so a scenario is a
complete, reviewable description of the test.

```json
{
  "name": "R-K",
  "description": "free text, shown in the manifest",
  "netid": 8762,
  "subnet": "10.87.62.0/24",
  "seed_mode": "private",
  "seed":  {"flavor": "R", "ip": "10.87.62.10", "host_port": 7800},
  "maker": {"flavor": "R", "wallet": "W1", "ip": "10.87.62.20", "host_port": 7801},
  "taker": {"flavor": "K", "wallet": "W2", "ip": "10.87.62.30", "host_port": 7802},
  "coins_file": "config/coins.json",
  "electrums": "config/electrums.json",
  "coins": ["KMD", "LTC"],
  "trade": {"base": "LTC", "rel": "KMD", "price": "8", "volume": "0.00125"},
  "fee_buffer": {"KMD": "0.001", "LTC": "0.001"},
  "timeouts": {"rpc": 120, "peers": 180, "order_visible": 180, "swap": 10800, "peer_failure_grace": 1200},
  "keep_running_if_active": true,
  "expect": {"baseline": [ ... ], "fixed": [ {"result": "success"} ]}
}
```

| Field | Meaning |
|---|---|
| `netid` | KDF network id for all three nodes |
| `subnet` | Private Docker network of this scenario. Pick one that is unused on your host. |
| `seed_mode` | `private` (default): the run aborts if a node has a peer outside `subnet`. `public` skips that check. |
| `seed` | The seed node (`i_am_seed`), which only relays. Any flavor that supports the netid. It has no wallet: it gets a random identity kept in its run dir. The shipped mixed scenarios use the upstream flavor (K or G) as seed, like the public networks; R-R uses R. |
| `maker`, `taker` | Flavor letter, wallet name (`secrets/<name>.json`), container IP inside `subnet`, host port for RPC |
| `coins` | Coins enabled on both nodes; entries must exist in `coins_file` and `electrums` |
| `trade` | The maker sells `volume` of `base` for `rel` at `price` (rel per base). The taker buys exactly that. The taker pays `volume × price` of `rel` plus the DEX fee in `rel`. |
| `fee_buffer` | Extra balance required on top of the trade amount, per coin (default 0.001) |
| `timeouts` | Seconds. `swap` is the longest a run waits for both sides to finish. |
| `keep_running_if_active` | Leave the containers up if a swap is still active at the end |
| `expect` | See [Expectations](#expectations) |

The default trade makes the **taker pay in KMD** (0.01 KMD for 0.00125 LTC). Both known KMD
defects of KDF Reloaded sit on the side that pays the taker fee in KMD. 0.01 KMD is also inside
the range where the netid-8762 burn split of KDF Reloaded and Komodo KDF used to differ.

`config/coins.json` holds the standard coin entries (from the `komodo-coins` repository), and
`config/electrums.json` their servers. Add a coin by adding its entry and its servers, then
naming it in a scenario.

## Wallets and secrets

A wallet is `secrets/<NAME>.json`, mode 0600, in a 0700 directory:

```json
{"passphrase": "<24 words>", "enable_hd": false, "wallet_dir": null}
```

**Creating and importing**
- `./swaplab.py wallet new W1` creates a fresh 256-bit BIP39 mnemonic (24 words). Nothing is
  printed. Back up `secrets/W1.json` if you fund it.
- `./swaplab.py wallet import W1 --seed-file phrase.txt` imports an existing seed phrase or
  legacy passphrase from a file. It is never taken as a command-line argument, so it does not
  end up in shell history or `ps`. Delete the file afterwards.
- `--wallet-dir DIR` also imports an existing KDF database directory, i.e. the directory that
  contains the `<rmd160-hash>/` folders, for example `~/.kdf/DB` or a wallet app's `DB`.
  - It is **copied** to `secrets/<NAME>.db/`, and the original is never opened by a node.
  - Each node run dir gets its own copy the first time it is created.
  - Use it when you need the existing swap history, for example to recover an old swap.

**Address modes**
- Wallets use KDF's legacy single-address ("iguana") mode by default. The same phrase gives the
  same address in R, K and G, which the harness relies on.
- `--hd` switches a wallet to HD mode (`enable_hd`), where the address differs. Wallet apps that
  run in HD mode show other addresses for the same phrase.

**How the passphrase is handled**
- It reaches KDF only through the node's `run/<node>/MM2.json`, written with mode 0600 and
  mounted into the container.
- It never goes through environment variables, command-line arguments, compose files or harness
  output.
- `allow_weak_password` is `true` because KDF applies its password policy to the passphrase
  too, and a BIP39 mnemonic never passes it.
- The RPC password is random, generated per node, and satisfies the policy.

**Never run the same wallet in two places at once.** For example, don't keep W1 open in a
desktop wallet while a scenario uses it. Two KDF instances with the same seed pick the same
coins, and one of them fails with double-spend errors. The harness enforces this only among its
own stacks.

**Secret protection**
- `.gitignore` excludes `secrets/`, `run/`, `out/`, `bin/` and `config/binaries.json`.
- `scripts/check-secrets.sh` scans staged files for:
  - forbidden paths
  - every known passphrase and RPC password
  - runs of 12 or more BIP39 words
  - `passphrase`/`rpc_password` fields with real values

  Install it as a pre-commit hook:
  ```sh
  ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
  scripts/check-secrets.sh --all     # check the whole working tree
  ```
- Exported logs have every line carrying secret material removed, and the whole evidence
  directory is scanned again before the run reports success.

## Funding

After `wallet addresses`, send:

| Wallet | Role | Coin | Per swap | For all five scenarios |
|---|---|---|---|---|
| W1 | maker | LTC (base) | 0.00125 + network fee | about 0.01 LTC |
| W2 | taker | KMD (rel) | 0.01 + DEX fee (0.0000116 on 8762, 0.0002 on 6133) + network fees | about 0.1 KMD |

`run` checks these amounts before it places an order and refuses to start if a wallet is short.

**Optional: exercising KMD rewards.** A KMD coin of **10 KMD or more** that is **older than one
hour** carries a KMD reward, which the next spend claims. Wallets that compute this reward
wrongly produce transactions the network rejects (`bad-txns-in-belowout`). To test that:
- send W2 one single transaction of at least 10 KMD
- wait an hour
- run a scenario where the taker is the implementation under test

The run records whether this precondition held (`bug1_precondition` in `results.json` and
`SUMMARY.md`). After a swap spends that coin, its change is new again, so wait another hour
before the next such test.

Getting coins back is an ordinary withdrawal from the wallet: import the phrase into any KDF
wallet app, or use a node's RPC.

## The private mesh

Each scenario runs its own seed node, and maker and taker connect only to it (`seednodes`).
Before any order is placed, the run checks that every peer of maker and taker is inside the
scenario's subnet. There are two reasons for this:
- **Nobody else can take the order.** The default price (8 KMD per LTC) is far below the
  market, so a public order would be a gift to any bot. In a private mesh only our taker can see
  it. The taker also matches it by uuid.
- **Results depend only on the two nodes under test.** Public peers can't influence them.

`seed_mode: "public"` disables the check. Only use it with a price that is not below the
market, and preferably with `seednodes` you control.

## Expectations

Each scenario states, per expectation set, what the swap should do. The set is chosen by `expect`
of flavor R in `config/binaries.json`, or by `--expect`. A set is a list of rules, and the first
rule that applies decides:

```json
"baseline": [
  {"if": "bug1_precondition", "result": "fail", "event": "TakerFeeSendFailed",
   "error_contains": "bad-txns-in-belowout", "why": "..."},
  {"result": "success"}
]
```

- **`if`** (optional): `bug1_precondition`. The rule applies only when the taker held a
  ≥10 KMD coin older than one hour.
- **`result`**: `success` means both sides finished without a failure event. `fail` means the
  swap failed.
- **`event`** and **`error_contains`**: for `fail`, text that must appear in the failure
  events. Case-insensitive.
- **`why`**: free text explaining the rule.

`SUMMARY.md` and `results.json` show the rule that applied and whether it was met.

The shipped expectations describe KDF Reloaded before and after the fix of two KMD defects. On
the `baseline` build:
- **KIP-0001 missing:** the build computes KMD rewards without the 2023 reduction, so a spend
  of a ≥10 KMD coin older than one hour is rejected.
- **Burn split:** on netid 8762, for taker fees between the KMD minimum and 4/3 of it, the build
  splits the fee 75/25 while Komodo KDF sends the minimum to the fee address and burns the rest.

## Evidence

`out/<YYYYmmdd-HHMMSS>-<scenario>[-dry]/`:

| File | Content |
|---|---|
| `SUMMARY.md` | One page: nodes and versions, stages reached, events of both sides, first failures, taker-fee outputs, expectation, result |
| `progress.log` | The run's own timeline |
| `results.json` | Machine-readable result: stages, versions, mesh, wallets (addresses, balances), swap summaries, failures, expectation |
| `manifest.json` | Harness commit, the scenario, per-node flavor, binary and SHA-256, run dir, IP, port, binary labels, coins file hash |
| `precondition.json` | Balances and, for KMD, the coins of the paying wallet with age, allowed reward and pre-2023 reward |
| `txs.json` | Every transaction recorded by the swap, plus transactions embedded in error messages, with decoded outputs (value, script kind) |
| `maker/`, `taker/`, `seed/` | `kdf.log` of this run (lines with secrets removed) and `diagnostics.json` (`my_orders`, `active_swaps`, `my_recent_swaps`, `my_swap_status`) |
| `scenario.json` | Copy of the scenario file used |

Run directories keep each node's full log history in `run/<node>/logs/<run-id>.log`, and its
database.

## Stuck swaps and recovery

A swap can stop after payments were sent, for example when the other side fails. KDF then refunds
automatically once the payment's locktime passes, which can take hours. It can only do that
while the node is running. So:
- **If a run ends with a swap still active,** the containers keep running. Check with
  `./swaplab.py status`.
- **If the containers were stopped anyway** (`--stop-anyway`, a reboot), restart the nodes and
  ask KDF to recover:
  ```sh
  ./swaplab.py recover scenarios/R-K.json <swap-uuid>
  ```
  This starts the scenario's nodes with their existing databases and enables the coins. Then it
  calls `recover_funds_of_swap` on both sides and leaves the nodes running. KDF also resumes
  unfinished swaps by itself when a node starts.
- **Never delete `run/`** while a swap may still be pending. The harness never deletes it.

## Commands

| Command | Purpose |
|---|---|
| `wallet new NAME [--hd]` | create a wallet with a fresh 24-word mnemonic |
| `wallet import NAME --seed-file F [--wallet-dir D] [--hd] [--overwrite]` | import a phrase (and optionally a KDF DB dir) |
| `wallet addresses NAME [--flavor R] [--netid 8762] [--coins KMD,LTC]` | start a one-off node without P2P and print addresses and balances |
| `run SCENARIO [--dry-run] [--expect baseline\|fixed] [--stop-anyway] [--allow-active]` | one scenario, end to end |
| `status` | running swaplab containers |
| `stop SCENARIO` / `stop --all` | stop containers gracefully |
| `recover SCENARIO UUID` | restart the nodes and call `recover_funds_of_swap` |

Only one swaplab command that starts nodes can run at a time (`run/.lock`).

To run all five scenarios one after another, unattended:

```sh
for s in R-R R-K K-R R-G G-R; do ./swaplab.py run scenarios/$s.json; done 2>&1 | tee out/matrix.log
```

## Troubleshooting

| Symptom | What to do |
|---|---|
| `host ports in use` | Another KDF uses 7800–7802 (or 7809 for `wallet addresses`). Change `host_port` in the scenario. |
| `other swaplab stacks are running` | A previous run kept its containers because a swap was active. Check `status` and wait, or `stop` after the swap has finished. |
| `Pool overlaps with other one on this host` | The scenario's `subnet` is used by another Docker network. Pick another one and adjust the three IPs. |
| Mesh check fails with peers outside the subnet | The flavor has built-in seed nodes it connects to regardless of `seednodes`. Keep `private` and report it, or use `public` with a market price. |
| `enabling KMD failed` | Electrum servers unreachable: check the container's internet access and `config/electrums.json`. |
| `insufficient balance` | Fund the address printed in the message, and wait for the confirmation. |
| A node exits at once | `run/<node>/logs/<run-id>.log` and `docker logs swaplab-<scenario>-<role>`. Typical causes: the binary does not accept the netid (G on 8762), or a broken `coins` entry. |
| Old swap blocks the run | `recover` it, or pass `--allow-active` if you know it is unrelated. |

## Design notes

- **Containers, not host processes.** Each node gets its own filesystem, IP address and P2P
  port, and the setup works the same on any Docker host. The image contains only Debian,
  CA certificates and `libudev1`. The binary is mounted, so switching builds never needs a
  rebuild.
- **One JSON per scenario.** A run is described completely by the scenario file plus the
  binaries it names. `manifest.json` records both, with hashes.
- **The harness never deletes wallet data.** Run directories persist, and the nodes stay up
  while swaps are active.
- **Origin.** The RPC client and swap helpers are adapted from the Rincoin test lab's KDF
  scenario (regtest). The regtest parts (mining, chain snapshots) were dropped.
