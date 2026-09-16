# DeFi DevOps Practice Extractor

A small research tool that measures which DevOps practices EVM smart-contract
protocols actually use, by reading their public repositories.

Given a list of protocol repos, it shallow-clones each one, runs a fixed set of
probes over the file tree and file contents, and emits a machine-readable
practice matrix — one JSON per protocol plus a combined CSV — where **every
extracted value carries the evidence (file paths / matched snippets) that
produced it**, so any cell can be spot-checked by hand.

## How it works

1. **Input** — [repos.txt](repos.txt) is the sample definition: one whitespace-separated
   line per protocol, `protocol_id  category  github_url`. Blank lines and `#`
   comments are ignored.

2. **Clone** — each repo is cloned into `repos/<protocol_id>` with
   `git clone --depth 1`. Already-cloned repos are skipped, so reruns are cheap.
   The commit SHA and last-commit date are recorded with the results, which is
   what makes a run reproducible against a moving target.

3. **Index** — a `Repo` object walks the tree once and keeps the relative paths.
   Build output and vendored code (`node_modules`, `out`, `cache`, `artifacts`,
   `typechain`, `coverage`, `dist`, `build`, virtualenvs) are pruned, and file
   reads are limited to known text extensions under 400 KB and cached, so
   dozens of probes can share one pass over the repo.

4. **Probe** — two primitives do all the work: `glob(regex)` matches against
   file *paths*, `grep(regex, path_filter)` matches against file *contents* and
   returns `(path, snippet)` pairs. Probes are composed from these.

5. **Output** — results are written to `out/`:
   - `out/<protocol_id>.json` — full record with values *and* evidence
   - `out/all.json` — every record combined
   - `out/matrix.csv` — flat protocol × variable matrix (values only), for stats

## The dimensions

Probes are grouped into ten dimensions, one function per dimension:

| Dim | Area | Examples of what is extracted |
| --- | --- | --- |
| D1 | Build & toolchain | Foundry/Hardhat, solc version pinning, dependency manager, lockfile |
| D2 | Testing | Solidity/JS test file counts, fork tests, fuzzing, invariants, formal & symbolic tools, coverage in CI |
| D3 | Static analysis | Slither, Aderyn, Semgrep, Solhint, Mythril/MythX, 4naly3er |
| D4 | CI configuration | Workflow count & names, triggers, gas-regression gate, format/lint gate, deploy-or-governance workflows |
| D5 | Release & deployment | Deploy script style, committed deployment artifacts, deterministic (CREATE2/CREATE3) deploys, automated verification, multichain config |
| D6 | Upgradeability & governance | Proxy pattern (UUPS/transparent/beacon/diamond), storage-layout checks, timelock/multisig/governor, proposal simulation |
| D7 | Operability in-contract | Pause switch, guardian role, access control model, parameter bounds, emergency exits |
| D8 | Monitoring & incident response | `SECURITY.md`, bug bounty, monitoring stacks (Forta, Tenderly, OZ Defender, Hypernative), runbooks |
| D9 | Assurance | Audit directories and counts, named auditors, competitive audits |
| D10 | Documentation & process | `CONTRIBUTING`, `CHANGELOG`, ADRs/RFCs, release-process docs |

## Adoption levels instead of booleans

Tool-adoption probes (D3, and the formal/symbolic probe in D2) do not return
true/false. `adoption_level()` resolves each tool to one of four ordered levels
by *where* the match is found:

- `enforced_in_ci` — the tool runs inside a `.github/workflows/` file, so it
  gates merges
- `configured` — invoked from a `Makefile`, `package.json`, `justfile`,
  `foundry.toml` or a config file: the team runs it, but nothing forces them to
- `mentioned` — appears only in source comments or docs, e.g. a
  `slither-disable-next-line` pragma or a README claim
- `absent` — no trace

The distinction is the point. "We use Slither" in a README and "Slither blocks
the merge" are very different maturity claims, and collapsing them into one
boolean is how practice-adoption surveys end up over-reporting. In the pilot
sample this separated repos that only carry Slither suppression comments from
one that runs a dedicated Slither workflow.

Two other deliberate choices in the same spirit:

- **Naming conventions vary, so path-based signals back up content-based ones.**
  Counting only `*.t.sol` reported one repo as having zero tests; the test probe
  now also counts `*Test.sol` and any `.sol` under `test/`. Likewise the
  invariant probe accepts an `invariants/` directory, because one protocol uses
  its own spec harness rather than Foundry's `invariant_` prefix.
- **Deploy and governance actions driven from CI get their own variable.** This
  practice has no traditional-DevOps analogue: workflows named
  `deploy-market`, `prepare-migration`, `enact-migration` make the governance
  stage of the lifecycle executable, so it is recorded separately rather than
  folded into a generic CI score.

Probes are also individually fault-isolated: a probe that raises is reported and
skipped, and the rest of the record is still produced.

## Usage

Requires Python 3 (standard library only) and `git`.

```bash
python3 extract.py
```

Reads `repos.txt` from the script directory, clones into `repos/`, writes to
`out/`. To change the sample, edit `repos.txt`. Both `repos/` and `out/` are
generated and therefore gitignored — this repository tracks only the extractor
and the sample definition.
