# kdf-swaplab

Repeatable **live** atomic swaps between two [Komodo DeFi Framework](https://github.com/GLEECBTC/komodo-defi-framework)
(KDF) nodes, each in its own Docker container, on **real mainnet coins**. One command runs a
whole swap: the harness
- starts the two nodes and a private seed node,
- checks that they see only each other,
- enables the coins and places the maker order,
- lets the taker take exactly that order,
- follows the swap to its end and exports every log with a one-page `SUMMARY.md`.

It was written to compare KDF implementations against each other. The three flavors are
independent third-party products, each under its own license and none of them part of this
repository; swaplab treats them as equals and any of them can be the build under test:
- **R**: KDF Reloaded.
- **K**: Komodo KDF 2.6.0-beta, netid 8762.
- **G**: GLEEC KDF 3.x, netid 6133.

A scenario named `X-Y` means X is the maker and Y the taker:

| Scenario | Netid | Maker | Taker |
|---|---|---|---|
| `scenarios/R-R.json` | 8762 | Reloaded | Reloaded |
| `scenarios/R-K.json` | 8762 | Reloaded | Komodo 2.6.0 |
| `scenarios/K-R.json` | 8762 | Komodo 2.6.0 | Reloaded |
| `scenarios/R-G.json` | 6133 | Reloaded | GLEEC 3.x |
| `scenarios/G-R.json` | 6133 | GLEEC 3.x | Reloaded |

Any other pair of binaries, netid or coin pair is just another scenario file.
[scenarios/examples/](scenarios/examples/README.md) shows more: other coin kinds (legacy, merged-mining,
shielded, EVM token) and extra node settings. [scripts/examples/](scripts/examples/README.md) has
fault-injection runners (slow, lossy, restarted or killed nodes).

> **Real money.** Swaps move real coins and pay real DEX and network fees, and a failed swap
> can lock funds until its refund time, which can be hours. Use dedicated, small wallets. Read
> [docs/MANUAL.md](docs/MANUAL.md) before your first live run.

## Requirements

- Linux x86-64 with Docker Engine and the compose plugin (`docker compose version`).
- Python 3.9 or newer. Only the standard library is used.
- Internet access for the containers, which talk to public electrum servers.
- A Linux x86-64 KDF binary for every flavor you use.

## Quickstart

```sh
git clone <this repository> kdf-swaplab && cd kdf-swaplab

# 1. Binaries: download the release zips (K and G) and point the config at them
mkdir -p bin && cd bin
gh release download v2.6.0-beta -R GLEECBTC/komodo-defi-framework -p 'kdf_475cdb4-linux-x86-64.zip'
gh release download v3.0.0-beta -R GLEECBTC/komodo-defi-framework -p 'kdf_d56a7bc-linux-x86-64.zip'
unzip -q kdf_475cdb4-linux-x86-64.zip -d kdf_475cdb4 && unzip -q kdf_d56a7bc-linux-x86-64.zip -d kdf_d56a7bc
cd ..
cp config/binaries.example.json config/binaries.json
$EDITOR config/binaries.json          # set R's path to your KDF Reloaded build

# 2. Two wallets: W1 is the maker, W2 the taker (a fresh 24-word seed each, stored in secrets/)
./swaplab.py wallet new W1
./swaplab.py wallet new W2
#    ... or import existing ones (the phrase is read from a file, never from the command line):
#    ./swaplab.py wallet import W1 --seed-file /path/to/phrase.txt

# 3. Addresses to fund
./swaplab.py wallet addresses W1 --flavor K
./swaplab.py wallet addresses W2 --flavor K

# 4. Check that everything starts and the nodes find each other. Nothing is traded.
./swaplab.py run scenarios/R-R.json --dry-run

# 5. Fund: W1 needs the base coin (default LTC), W2 the rel coin (default KMD); see the manual

# 6. A live swap, then read the summary
./swaplab.py run scenarios/R-R.json
cat out/<run-id>/SUMMARY.md
```

Default amounts per swap: the taker buys 0.00125 LTC for 0.01 KMD. W1 should hold about
0.01 LTC and W2 about 0.1 KMD for all five scenarios, plus a buffer.
[docs/MANUAL.md](docs/MANUAL.md#funding) explains the exact needs and the
extra ≥10 KMD coin used to test KMD rewards.

## Where things are

| Path | What it is | In git |
|---|---|---|
| `swaplab.py` | the command line | yes |
| `scenarios/*.json` | one self-contained file per test | yes |
| `scenarios/examples/` | more scenarios, with their own README | yes |
| `scripts/examples/` | fault-injection runners (throttled, lossy, restarted or killed nodes) | yes |
| `LICENSE` | Apache License 2.0 | yes |
| `CLAUDE.md` | how an agent or developer uses swaplab to test a KDF change | yes |
| `config/coins.json`, `config/electrums.json` | coin definitions and electrum servers | yes |
| `config/binaries.example.json` | template for `config/binaries.json` | yes |
| `config/binaries.json` | your binary paths | **no** |
| `secrets/` | wallet seeds (0600 files, 0700 directory) | **no** |
| `run/` | one directory per node: MM2.json (holds the seed), database, logs | **no** |
| `out/` | exported evidence of every run | **no** |
| `bin/` | downloaded binaries | **no** |

Before committing anything, run `scripts/check-secrets.sh`. Better, install it as a
pre-commit hook: `ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit`.

## More

[docs/MANUAL.md](docs/MANUAL.md) covers:
- how a run works, step by step
- every field of a scenario file
- flavors and netids
- wallets and secrets, and funding
- the private mesh
- expectations
- evidence
- recovering a stuck swap
- troubleshooting

[docs/example-test-plan.md](docs/example-test-plan.md) is an example of a release-gate plan built from the
scenarios and scripts.

## License

Apache License 2.0, see [LICENSE](LICENSE).
