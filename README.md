# DeFi DevOps Practice Extractor

Measures which DevOps practices Ethereum DeFi protocols use, by reading their public
repositories. Every cell in the output carries the evidence (file, line, snippet, and a
permalink pinned to the scanned commit) that produced it, so any number can be checked by hand.

Standard library only (Python 3.11+) plus `git`. Sampling and the static site are described below.

## Pipeline

```text
data/protocols.csv + data/repos.csv
        │  extract.py: shallow clone → probe every variable → merge repos → apply N/A rules
        ▼
out/                      (gitignored)  <protocol>.json  all.json  matrix.csv  matrix_long.csv
                                        variables.csv  run.json (skips, clone failures, cell-status counts)
        │  tools/build_site_data.py
        ▼
site/data/data.js         (committed)   → open site/index.html
```

### Quickstart

Three commands: extract, build the page's data, serve it.

```bash
python3 extract.py --jobs 8        # 1. clone + scan every protocol in data/protocols.csv
python3 tools/build_site_data.py   # 2. out/all.json -> site/data/data.js
python3 tools/serve.py             # 3. http://127.0.0.1:8000
```

Step 1 is the slow one on a cold start: it shallow-clones each repo (46 protocols ≈ 3.3 GB, a few
minutes on a fast connection). Repos and results are cached, so afterwards the loop is just:

```bash
python3 extract.py --no-clone && python3 tools/build_site_data.py   # ~30s for 46 protocols
```

then reload the page. `tools/serve.py` sends `no-store`, so a plain reload always shows the
current build.

Other flags:

```bash
python3 extract.py aave-v3 morpho-blue   # only these protocols
python3 extract.py --resume              # skip protocols already written to out/ (crash-safe reruns)
python3 -m unittest discover tests       # 17 unit tests for the CI engine and merge rules
open site/index.html                     # works straight from disk too; deployable as-is to GitHub Pages
```

## Input

The current sample is **46 protocols** (see [data/protocols.csv](data/protocols.csv)), drawn from the
candidate pool `tools/rank_candidates.py` produces. Every repo URL was verified to exist and checked
by hand against its file listing before being added.

- [data/protocols.csv](data/protocols.csv): one row per **subject** (one version of a codebase, e.g. `aave-v3`).
  Context labels are **hand labels**: `upgradeability` (`immutable` / `upgradeable` / `mixed`, judged on the
  contracts that hold user funds), `has_admin_role`, `launch_year`, `scope_gaps`, `audit_links`.
  `label_status=draft` marks labels that are pre-filled and still need review.
- [data/repos.csv](data/repos.csv): one or more repos per protocol, each with a `role` (`core`, `periphery`,
  `governance`, `deploy`), an optional `subpath` for monorepos, and `include`.

## Code map

| File | Role |
| --- | --- |
| [repo.py](repo.py) | `Repo`: file index, `glob(regex)` on paths, `grep(regex)` on contents → `{path, line, snippet}`. Prunes vendored code, lockfiles, audit dumps, and git submodules. Optional `subpath`. |
| [levels.py](levels.py) | `adoption()` and the CI engine: parses workflow triggers, follows `make` / `yarn` / `npm` / `just` targets up to three hops, honours `continue-on-error` and swallowed exit codes. |
| [probes.py](probes.py) | The variable registry: one function per variable, with type, scope, N/A rule, and gap tag. |
| [aggregate.py](aggregate.py) | Runs variables per repo, merges across repos, applies N/A / unknown / error, computes roll-ups. |
| [outputs.py](outputs.py) | JSON / CSV writers and commit-pinned evidence permalinks. |
| [extract.py](extract.py) | CLI driver. |
| [tools/build_site_data.py](tools/build_site_data.py) | `out/all.json` → `site/data/data.js`. |
| [tools/fetch_defillama.py](tools/fetch_defillama.py) | One-time DefiLlama snapshot → `data/sources/defillama_<date>.json`. |
| [tools/rank_candidates.py](tools/rank_candidates.py) | Ranks Ethereum protocols by TVL into keep / review / drop, with a reason per row. |
| [tools/odd_repos.py](tools/odd_repos.py) | Replays Electric Capital's open-dev-data migration DSL to map a protocol to its repos. |
| [tools/map_repos.py](tools/map_repos.py) | Suggests a protocol's core contracts repo (shortlist for human review, not an answer). |
| [tools/serve.py](tools/serve.py) | Local no-cache dev server for `site/`. |
| [site/](site/) | Static page (vanilla HTML/JS/CSS): ranking, said-vs-run, lifecycle stages, upgradeable-vs-immutable, toolchain by cohort, heatmap explorer with evidence drill-down, methods. |

## Variables

49 variables in the dimensions D1–D6 and D8–D10, grouped by lifecycle stage: D1–D4 before launch, D5 at launch,
D6 governance, D8 after launch, D9 assurance, D10 cross-cutting. **D7 (operability in-contract) is retired**;
its IDs are not reused. The full list with types and scopes is written to `out/variables.csv`.

Each variable carries a plain-English `how_measured` line describing what its probe actually looks
for. It is written next to the probe in [probes.py](probes.py) (`MEASURED`), exported to
`out/variables.csv`, and shown on the site as hover text on every practice name. An import-time check
fails if a variable has no entry, so the wording cannot drift from the registry.

Types: **adoption** (four levels), **bool**, **category**, **ratio** (test LoC ÷ source LoC; mocks,
harnesses, interfaces and vendored code excluded).

### Adoption levels

| Level | Meaning |
| --- | --- |
| `runs_in_ci` | A workflow triggered by push / pull request / merge queue invokes it, directly or through a make / yarn / just target, and a failure can fail the build. |
| `configured` | A config file, script entry, or the practice's own code exists; or it runs only on a schedule, by hand, or as an advisory step (`continue-on-error: true` on the step or its job, or a swallowed exit code such as `\|\| true`). |
| `mentioned` | Appears only in docs or comments. |
| `absent` | No trace. |

Only *command positions* are read as invocations: `run:` (including its block scalar) and `uses:`.
Env vars, `with:` arguments, artifact paths and cache keys are data -- matching a tool name there
used to turn an `ETHERSCAN_KEY` secret into "automated verification". If no command names the tool
but the **workflow file name** does (`verify-state.yml`, `run-slither.yaml`), that counts, flagged
`by_filename` in the evidence; a command always wins over a name, so a workflow whose only matching
step is `|| true` stays advisory.

`runs_in_ci` does **not** claim the check is *required to merge*. That is a branch-protection setting held
by GitHub, invisible in the repo. Workflow `paths:` filters are not evaluated. When CI delegates to a reusable
workflow hosted in another repo (`uses: org/repo/.github/workflows/x.yml@ref`), the practice is inferred from that
workflow's file name and the evidence is flagged `external_workflow`.

### Cell status

Each cell has a `status` separate from its `value`:

| status | meaning | in adoption rates |
| --- | --- | --- |
| `ok` | the value is meaningful (a negative is a real "not found") | counted |
| `na` | does not apply. From hand labels: `immutable` (upgrade checks), `no_admin_role` (governance). From probes: `no_solidity` (a Vyper protocol such as Curve has no solc pragma), `source_in_submodules` (the implementation lives in the protocol's other repos, which a shallow clone does not fetch -- 3+ first-party submodules, as in Maple) | excluded |
| `unknown` | nothing found, and `scope_gaps` says part of the protocol lives outside the scanned repos | excluded |
| `error` | a probe raised; the negative can't be trusted | excluded |

N/A comes from the hand-labelled protocol context, never from a probe.

### Multi-repo protocols

Variables declare a scope. `core` variables (build, tests, CI, static analysis) are measured on the core repo(s).
`any` variables (audits, governance, deployment, monitoring, ABI release) take the strongest evidence across all
of the protocol's repos. `per_repo` values are kept in the JSON so the source of each result stays visible.

## Sampling

```bash
python3 tools/fetch_defillama.py        # once: data/sources/defillama_<date>.json (committed; the date is the snapshot)
python3 tools/rank_candidates.py        # data/candidates.csv: rank, verdict (keep / review / drop) and a reason for every row
```

DefiLlama's `/protocols` returns *current* values, so the download date is the snapshot date; committing the
file is what makes the sample reproducible. Things the live data taught us:

- The fork field is `forkedFromIds`; `forkedFrom` is almost never set.
- `openSource` is empty for nearly every protocol, so closed-source protocols must be removed by hand.
- `github` is often missing on the protocol itself (it sits on the parent).
- The raw Ethereum top 100 is roughly a third CEXs and bridges, so the category filter matters.

### Mapping a protocol to its repo

This is the hard part, and it is **not** reliably automatable. `tools/odd_repos.py` replays
Electric Capital's open-dev-data taxonomy (a migration DSL, not TOML) and `tools/map_repos.py`
ranks each ecosystem's repos by name, by content (a treeless `--filter=blob:none` clone gives the
file list for ~0.3 s and ~100 KB, with no API token), and by version/org match.

Measured against 20 repos that had been chosen by hand:

| Method | Picks the right repo first | In its top 3 |
| --- | --- | --- |
| Name heuristics | 6/20 | 12/20 |
| + repo content | 9/20 | 16/20 |
| + version and org match | 10/20 | 18/20 |

An ecosystem holds everything its community wrote (Uniswap: 1426 repos), so name-only ranking
promotes forks such as `morpho-org/openzeppelin-contracts`. At ~50 % top-1 the shortlist is a
review aid, not a sample: picking automatically would measure roughly half the protocols against
the wrong code. Hence every repo in `data/repos.csv` was confirmed by hand.

`candidates.csv` is a starting point for hand review, not the sample: every removal keeps its reason so the
exclusion log is complete. Chosen protocols then go into `data/protocols.csv` and `data/repos.csv`.

## Known limits

- Public files only: "not found" is not "not done". Post-launch practices (monitoring, runbooks) are the least visible.
- A public repo may not hold the whole protocol. Pendle, Kelp and Renzo ship contracts but no tests, so their test variables read as "not found"; tag a protocol `scope_gaps=tests` to record that as unknown instead.
- One commit per repo (shallow clone); no git history, no on-chain data. The data model leaves room for both.
- Workflow `paths:` / `branches:` filters are not evaluated, so a job that only runs for certain paths still counts as running in CI.
- Extractor accuracy against hand labels has **not yet been measured**; treat every number as unvalidated.
- `monitoring_tools` is weak (many mentions are development-time use) and should be flagged or dropped after validation.

## Credits and data licence

Protocol metadata and TVL come from [DefiLlama](https://defillama.com). Protocol-to-repository
mapping is helped by [Electric Capital's open-dev-data](https://github.com/electric-capital/open-dev-data),
whose data is **CC BY 4.0** — credit Electric Capital in anything published from it.

## Repo hygiene

Repos are cloned once per URL, so protocols that share a monorepo do not clone it twice. Each
protocol's JSON is written as it finishes, so an interrupted run resumes with `--resume`.

`repos/` and `out/` are generated and gitignored. Committed: code, `data/`, `tests/`, and `site/` including
`site/data/data.js` (GitHub Pages needs it).
