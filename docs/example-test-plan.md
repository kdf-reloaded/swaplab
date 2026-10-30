# Example test plan: a release gate for a KDF build

A plan like this answers one question: *is this build good enough to ship?* It lists what is
run, with which funds, what counts as a pass, and what is expected to fail for reasons that are
not the build's fault. Copy it, strike what you do not need, fill in your own amounts. The
scenario files and scripts it refers to are in [scenarios/examples/](../scenarios/examples/README.md)
and [scripts/examples/](../scripts/examples/README.md).

## 1. Fixtures

| Item | Choice |
|---|---|
| Build under test (R) | one commit, built in its own worktree and target directory, release profile, SHA-256 pinned in `config/binaries.json` |
| Reference builds | the upstream builds you compare against (K, G), pinned the same way |
| Flavor pairs (maker-taker) | R-R, R-K, K-R, R-G, G-R |
| Wallets | two small, dedicated wallets: one live in at most one node at a time |
| Counter coin | one cheap coin that every cell pairs with (the examples use RIN) |
| Amounts | the smallest amount that clears both dust limits and both fee floors; a hard cap per swap and per wallet |
| Evidence | one `out/<run-id>/` per swap; one consolidated table (cell, flavors, direction, result, uuid, spend txids, notes) per plan run |

Each cell runs in both directions: wallet A sells X for the counter coin, then wallet B sells the
counter coin for X. Both wallets therefore need both coins, plus fee reserve.

## 2. Pass criterion and pre-declared exceptions

**Pass:** the swap reaches Finished on both sides, both payments are spent, the balance changes
equal the traded amount minus fees, and the evidence is complete (payment, spend and fee
transactions listed).

Declare exceptions *before* the run so the final table can be built mechanically. Typical ones:
1. A leg that a reference build cannot do (for example a shielded coin on a build that the network
   rejects): recorded as expected, with the exact message.
2. A coin that one of the builds does not support: recorded as N/A with the activation error.
3. Behavior known from an earlier analysis of the reference build: recorded as known, with a
   reference, not as a failure.
4. A failure caused by lab conditions (server down, RPC error): one automatic rerun; a second
   failure counts.

## 3. Cells

| Tier | Flavors | Coins | Purpose |
|---|---|---|---|
| T1 gate | R-R | every coin class the build supports | the build alone |
| T2 interop | R-K, K-R, R-G, G-R | coins every flavor knows | the build against references, both roles |
| T3 interop, rest | as T2 | the remaining classes, as the references support them | wider coverage; cut first when time is short |
| T4 specials | R | wallet-only checks for coins that cannot be traded here | activation, balance, address, withdraw build, restart persistence |

Coin classes worth one cell each, with an example file: legacy UTXO (`doge-rin.json`),
merged-mining (`nmc-rin-no-spend-check.json`), special signature rule (`rxd-rin.json`), header
layout declared in the coin entry (`mewc-rin-header-layout.json`), fixed-fee policy and second
trading protocol (`dingo-rin-trading-v2.json`), shielded (`arrr-rin-shielded.json`), EVM token
(`usdc-plg20-rin-token.json`), mixed flavors (`rin-kmd-r-k.json`), reference alone
(`g-g-rin-kmd.json`).

If time is short: run T1 completely, T2 with one coin per class, skip T3. State the cut line in
the report.

## 4. Separate rows

| Row | What | Pass |
|---|---|---|
| Refund gate | a shortened lock time (a test-only build feature), the taker killed after the maker payment is sent, the maker left alone until its refund; control run on the previous build | refund published after the lock time and the chain's median time; the control fails with the node's own rejection text |
| Recovery regression | recovery of stuck swaps (too early, after the lock, repeated, wrong seed) through `recover_funds_of_swap` | every answer within seconds, with the documented text; nothing broadcast when it must not be |
| Soak | hundreds of maker orders over many pairs with continuous price updates for an hour plus a quiet tail, observers subscribed (`scripts/examples/seed-throttle-load.py`) | no orderbook wipe, no sync errors, seed memory returns to baseline, no connection closed by the maker |
| Throttled soak | the same with the seed's inbound link policed to a low rate | books stay complete; flow control engages before any connection is closed |
| Order posting timing | a loop of `setprice` calls per base coin, `my_balance` before each, per-call median, p90 and maximum | not slower than the previous build on the same link and pairs |
| Trading floors | the smallest volume a maker advertises and a taker accepts, on the build and on the references; one order at the floor must match, one just below must be refused; then one swap at the floor | floors agree within documented rounding; refusal text recorded |
| Degraded-mode divergence | a maker that loses one of its two seeds keeps creating orders on new pairs; an observer of another flavor polls its orderbook (`scripts/examples/relay-kill-degraded.py`) | no sync errors on the observer; new pairs visible within a round; recovery after the fault ends |
| Taker refund claims spent payment | the taker is cut from its own coin's servers after its payment is sent while the maker spends it; the taker is restored before, after and across a restart of the refund path | the taker claims the maker's coins and finishes; the control build ends in a failed refund and needs manual recovery |

A row that has no script yet is run by hand against a persistent node; write the steps and the
observed texts into the evidence directory either way.

## 5. Procedure per swap

1. Preflight: no other stack running (`./swaplab.py status`), long-running nodes using the wallet
   stopped, balances sufficient.
2. `./swaplab.py run <scenario>.json`
3. Read `SUMMARY.md` first. Copy the result into the consolidated table. Keep full logs for
   failures; keep summary, results and transaction list for passes.
4. Between cells, wait for pending confirmations when the next cell uses the same coin's change.
5. Write the expectation (pass or exception id) per cell before the run.

## 6. Budget

Add up the swaps (cells x 2 directions, plus rows) and the minutes each takes (10 to 20 for
UTXO coins, longer for shielded coins and refunds). Serial execution is forced by shared wallets.
Decide the cut line and the spending caps before the first swap, not during.
