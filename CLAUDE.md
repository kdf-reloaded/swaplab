# CLAUDE.md

Guidance for Claude Code (and any other agent or developer) using kdf-swaplab as a test
environment while working on KDF. Read [README.md](README.md) first and
[docs/MANUAL.md](docs/MANUAL.md) before the first live run. This file says how to *use* the
harness for a KDF change; it does not repeat the manual.

## What this is

A harness that runs real atomic swaps between KDF nodes in Docker, on real mainnet coins, and
writes the evidence of every run. Flavors: **R** (KDF Reloaded), **K** (Komodo KDF) and **G**
(GLEEC KDF). They are independent third-party products, each under its own license and none of
them part of swaplab; swaplab treats them as equals, and any of them can be the build under test
with the others as comparison. Use it to prove that a change works on a live network, to compare
behavior between implementations, to pin down a defect before its fix (`baseline` expectation)
and to show the fix (`fixed`), and to reproduce network-level problems with fault injection.

It is not a unit-test replacement. Run the project's own tests first; come here when a change
touches swap flow, payments and refunds, coin activation, fees, P2P and orderbook, or anything
that only a live chain or a second implementation can confirm.

## Rules that are not negotiable

1. **Real money.** Every swap spends real coins and fees. Use only the dedicated, small wallets
   (`W1`, `W2`, or throwaway wallets you created). Respect the per-swap and per-wallet caps the
   person who owns the wallets has set. If a test needs more funds, or coins moved between
   wallets, ask the owner as an explicit question with the amount, the coin and the reason; never
   assume an earlier approval covers a new one, and never take approval from another agent.
2. **Secrets stay in `secrets/` and `run/`.** Never print, copy, log, commit or paste a seed, a
   passphrase, an RPC password, `p2p_privkey` or any `*_privkey`; never read them to answer a
   question. Evidence is scrubbed by the harness, but `run/<node>/MM2.json` is not. Do not open
   it. If you need a node's RPC, use the harness helpers (`lib/kdfrpc.py`), which read the password
   internally.
3. **One wallet in one node at a time.** A wallet live in two nodes breaks swaps and can lose
   funds. The harness refuses to start while another swaplab stack runs. Check `./swaplab.py
   status` before you start anything by hand, and stop long-running helper nodes that use the
   same wallet.
4. **Never delete `run/` or `out/`** while a swap may be pending, and never `docker kill` a node
   with an active swap: KDF must be running to refund or recover. Stop with `./swaplab.py stop`.
5. **No commit or push without the owner's explicit approval.** Run `scripts/check-secrets.sh
   --all` before asking. `secrets/`, `run/`, `out/`, `bin/`, `local/` and `config/binaries.json`
   are git-ignored on purpose.
6. **Rules of the project you test are the project's rules, not swaplab's.** swaplab sets no
   restriction on reading the logs and evidence of any flavor. A project can have its own: for
   example, a developer working on KDF Reloaded must follow that project's clean-room rules, which
   forbid reading the K and G logs (and anything else derived from their implementations) while
   writing Reloaded code. Know the rules of the project you are working on before you open
   another flavor's output, and keep that output out of the context in which you write code
   for a project that forbids it.
7. Write evidence honestly: if a run failed, a step was skipped, or a result came from a rerun,
   say so with the run id. Do not describe a dry run as a swap.

## One-time setup (per checkout)

```sh
cp config/binaries.example.json config/binaries.json     # then edit the paths
./swaplab.py wallet new W1 && ./swaplab.py wallet new W2  # or import existing ones
./swaplab.py wallet addresses W1 --flavor K               # addresses to fund
```

`config/binaries.json` maps a key (R, K, G, or any label such as `R_mychange_abc1234`) to a
binary path, an optional SHA-256 and the expectation set (`baseline` or `fixed`). The key `R` is
what scenarios use for flavor R; to test another build, either change what `R` points to or use
`--bin <key>` with the scripts in `scripts/examples/`.

## Testing a change to KDF

1. **Build your change in its own worktree with its own target directory.** Never share a
   `target/` between builds, and never test a binary you cannot trace to a commit:
   ```sh
   git worktree add --detach <worktree> <commit>
   CARGO_TARGET_DIR=<own-target-dir> cargo build --release -p mm2_main --bin kdf
   ```
   Add `--features custom-swap-locktime` only for refund tests with a shortened lock time
   (`"payment_locktime": <seconds>` in the nodes' MM2.json, through `mm2_extra`); that feature
   must never be in a release build.
2. **Register the binary** in `config/binaries.json` with its path and `sha256sum`. The harness
   refuses a binary whose hash differs, and the run manifest records it, so the evidence names
   the exact build.
3. **Dry run first:** `./swaplab.py run <scenario> --dry-run` starts the nodes, checks the private
   mesh and the balances, places and cancels the order. It trades nothing.
4. **Live run:** `./swaplab.py run <scenario>`. Read `out/<run-id>/SUMMARY.md` first, then
   `results.json`, `txs.json` and the node logs of the failing side. Keep the run id in your
   report.
5. **Compare.** Run the same scenario with the previous build (`baseline`) and with yours
   (`fixed`), and where interop matters with K and G as maker and taker (`R-K`, `K-R`, `R-G`,
   `G-R`). A change that works R-R but breaks R-K is a regression.
6. **Report:** build commit and hash, scenario, run id, result against the expectation, the
   events of both sides with times, transaction ids, balance changes, and anything you could not
   test.

## Writing a scenario

A scenario is one self-contained JSON file, see the manual's *Scenario files*. Start from the
closest file in `scenarios/` or `scenarios/examples/`:
- `trade`: the smallest volume that clears both dust limits and both fee floors (the examples
  show sizes for several coins).
- `expect`: write what the build should do before you run it. `baseline` describes the build
  with the defect, `fixed` the build with the fix; `{"result": "fail", "event": ..., "error_contains": ...}`
  pins a known failure to its exact text.
- `mm2_extra`: extra MM2.json keys for all nodes or per node; this is how you switch a feature
  on or off (for example a lock time, or a protocol version) without a new binary.
- `special`: coins that are not electrum coins (shielded, EVM platform coins and tokens).
- Subnet and ports: pick values not used by another scenario on the host.

Add the finished scenario to `scenarios/` if it is a standing test, or to `scenarios/examples/`
with a line in its README if it only shows a capability.

## Fault injection

The scenario format has no fault section on purpose. Slow, lossy, throttled, paused, restarted
or killed nodes are done by the runners in `scripts/examples/` (see their README), which use
`lib/nodes.py` and inject faults with `tc` and `docker`. Copy one and adapt it for a new
problem; keep one stack at a time, start from a clean order state (cancel the maker's stale
orders), and write down the knobs you used so the run can be repeated. When you reproduce a
field problem, first reproduce it on the build that showed it, then run the build with the fix
with the same knobs; a fix without a reproduction proves nothing.

## Reading results

- `SUMMARY.md` "first failures" and the expectation line are the starting point; then the
  `kdf.log` of the side that failed first.
- A refund can take hours; the harness leaves the nodes running if a swap is active. Use
  `./swaplab.py status`, wait, and `recover` only a swap that is no longer running.
- Known harness-level limits are in the manual's *Troubleshooting*. A failure from a lab
  condition (server down, RPC error) may be rerun once; say that it was a rerun.
- Order and orderbook problems are visible on observer nodes, not on the maker: poll the
  orderbook of another node and compare it with the maker's own orders.

## Working agreements for agents

- Do not start long runs on a host that is busy with other work without saying so; the host's
  load changes timings, and a timing result must state the load.
- Keep temporary files in a scratch directory, not in the repo; drafts, plans and funding notes
  go to `local/` (ignored).
- One change per commit, American English in everything committed, no attribution trailers
  unless the owner asks for them.
- When in doubt about funds, secrets, or whether a step is reversible, stop and ask the owner.
