#!/usr/bin/env python3
"""
SmartOps pilot extractor
------------------------
Clones EVM protocol repos (shallow) and extracts DevOps-practice signals
from their files. Output: one JSON per repo + combined CSV + markdown report.

Design notes:
- Every probe returns a value AND the evidence (matched file paths / snippets),
  so a human can spot-check any cell. Un-auditable automation is useless for a paper.
- Probes are grouped by the D1..D10 dimensions in the research plan.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from collections import OrderedDict

ROOT = Path(__file__).resolve().parent
REPO_DIR = ROOT / "repos"
OUT_DIR = ROOT / "out"

# file extensions we are willing to read into memory
TEXT_EXT = {".sol", ".toml", ".yml", ".yaml", ".json", ".md", ".txt",
            ".ts", ".js", ".cfg", ".conf", ".sh", ".spec", ".config"}
SKIP_DIRS = {".git", "node_modules", "out", "cache", "artifacts", "typechain",
             "typechain-types", "coverage", ".venv", "venv", "dist", "build"}
MAX_FILE_BYTES = 400_000


# --------------------------------------------------------------------------
# repo index
# --------------------------------------------------------------------------

class Repo:
    def __init__(self, pid, category, url, path):
        self.pid = pid
        self.category = category
        self.url = url
        self.path = path
        self.files = []          # list of posix-relative paths
        self._cache = {}
        self._index()

    def _index(self):
        for dirpath, dirnames, filenames in os.walk(self.path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                p = Path(dirpath) / fn
                self.files.append(p.relative_to(self.path).as_posix())

    def text(self, relpath):
        """Read a file, cached. Returns '' if unreadable/binary/too big."""
        if relpath in self._cache:
            return self._cache[relpath]
        p = self.path / relpath
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                t = ""
            elif p.suffix.lower() not in TEXT_EXT and p.name not in (
                    "Makefile", "Dockerfile", ".gitmodules", "LICENSE"):
                t = ""
            else:
                t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            t = ""
        self._cache[relpath] = t
        return t

    def glob(self, pattern):
        """Regex match against relative paths."""
        rx = re.compile(pattern, re.I)
        return [f for f in self.files if rx.search(f)]

    def grep(self, pattern, path_filter=None, limit=6):
        """Regex search file contents. Returns list of (path, snippet)."""
        rx = re.compile(pattern, re.I | re.M)
        pf = re.compile(path_filter, re.I) if path_filter else None
        hits = []
        for f in self.files:
            if pf and not pf.search(f):
                continue
            t = self.text(f)
            if not t:
                continue
            m = rx.search(t)
            if m:
                snippet = t[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
                hits.append((f, snippet.strip()))
                if len(hits) >= limit:
                    break
        return hits


# --------------------------------------------------------------------------
# probe helpers
# --------------------------------------------------------------------------

def ev(hits):
    """Turn hits into compact evidence strings."""
    return [h[0] if isinstance(h, tuple) else h for h in hits]


def probe(result, dim, key, value, evidence):
    result[dim][key] = {"value": value, "evidence": evidence[:4]}


CI_PATHS = r"^\.github/workflows/"
CONFIG_PATHS = r"(Makefile|package\.json|justfile|foundry\.toml|\.config\.|config\.json|\.ya?ml$)"

ADOPTION = ["absent", "mentioned", "configured", "enforced_in_ci"]


def adoption_level(r, rx):
    """
    Three-level adoption scale instead of a boolean.

    enforced_in_ci  the tool actually runs in a CI workflow -> it gates merges
    configured      invoked from Makefile/package.json/justfile or has a config
                    file -> the team runs it, but nothing forces them to
    mentioned       appears only in source comments or docs (e.g. a
                    `slither-disable-next-line` pragma, or a README claim)
    absent          no trace

    The distinction matters: "we use Slither" in a README and "Slither blocks
    the merge" are very different DevOps maturity claims, and conflating them
    is how practice-adoption surveys end up over-reporting.
    """
    in_ci = r.grep(rx, path_filter=CI_PATHS, limit=2)
    if in_ci:
        return "enforced_in_ci", ev(in_ci)
    cfg = r.grep(rx, path_filter=CONFIG_PATHS, limit=2)
    if cfg:
        return "configured", ev(cfg)
    any_hit = r.grep(rx, limit=2)
    if any_hit:
        return "mentioned", ev(any_hit)
    return "absent", []


# --------------------------------------------------------------------------
# D1  build & toolchain
# --------------------------------------------------------------------------

def d1(r, res):
    d = "D1_build"
    foundry = r.glob(r"(^|/)foundry\.toml$")
    hardhat = r.glob(r"(^|/)hardhat\.config\.(ts|js|cjs)$")
    fw = []
    if foundry:
        fw.append("foundry")
    if hardhat:
        fw.append("hardhat")
    probe(res, d, "framework", "+".join(fw) or "none/other", ev(foundry + hardhat))

    # solc version pinning.  NOTE: an earlier version of this probe checked the
    # `^solc =` line without re.MULTILINE and reported every repo as
    # "floating-pragma".  3 of 5 pilot repos actually pin in foundry.toml.
    solc = r.grep(r"^\s*solc(_version)?\s*=", path_filter=r"foundry\.toml$")
    pragma_exact = r.grep(r"pragma\s+solidity\s+\d", path_filter=r"\.sol$")
    pragma_caret = r.grep(r"pragma\s+solidity\s+[\^>]", path_filter=r"\.sol$")
    if solc:
        pin = "pinned-in-config"
    elif pragma_exact and not pragma_caret:
        pin = "exact-pragma"
    else:
        pin = "floating-pragma"
    probe(res, d, "solc_pinning", pin,
          [s for _, s in solc[:1]] + ev(pragma_exact[:1] + pragma_caret[:1]))

    # dependency management
    dep = []
    if r.glob(r"^\.gitmodules$"):
        dep.append("git-submodule")
    if r.grep(r"^\s*\[dependencies\]", path_filter=r"foundry\.toml$"):
        dep.append("soldeer")
    if r.glob(r"^package\.json$"):
        dep.append("npm")
    probe(res, d, "dep_management", "+".join(dep) or "none", dep)

    lock = r.glob(r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb|soldeer\.lock)$")
    probe(res, d, "dep_lockfile", bool(lock), ev(lock))


# --------------------------------------------------------------------------
# D2  testing
# --------------------------------------------------------------------------

def d2(r, res):
    d = "D2_test"
    # Foundry convention is *.t.sol, but morpho-blue names them *Test.sol and
    # aave keeps them under tests/.  Counting only *.t.sol reported morpho-blue
    # as having zero tests, which is plainly false.  Count any .sol under a
    # test dir, plus any *.t.sol / *Test.sol anywhere.
    t_sol = sorted(set(r.glob(r"\.t\.sol$")
                       + r.glob(r"Test\.sol$")
                       + r.glob(r"^(test|tests)/.*\.sol$")))
    t_js = r.glob(r"^(test|tests)/.*\.(ts|js)$")
    probe(res, d, "test_files_sol", len(t_sol), t_sol[:3])
    probe(res, d, "test_files_js", len(t_js), t_js[:3])

    fork = r.grep(r"vm\.(createSelectFork|createFork)|--fork-url|forking\s*:")
    probe(res, d, "fork_testing", bool(fork), ev(fork))

    fuzz = r.grep(r"\[fuzz\]|forge-config:\s*[\w.]*fuzz|function\s+testFuzz|function\s+test_fuzz")
    probe(res, d, "fuzz_testing", bool(fuzz), ev(fuzz))

    # path-based signal added: aave keeps invariants in tests/invariants/ and
    # uses its own spec harness, so the function-name regex alone missed it.
    inv_paths = r.glob(r"invariant")
    inv = r.grep(r"\[invariant\]|function\s+invariant_|StdInvariant|targetContract\(")
    probe(res, d, "invariant_testing", bool(inv or inv_paths), ev(inv) + inv_paths[:2])

    # formal / symbolic, each on the adoption scale
    formal = {}
    for name, rx in (("certora", r"certoraRun|\bcertora\b"),
                     ("halmos", r"\bhalmos\b"),
                     ("kontrol", r"\bkontrol\b"),
                     ("medusa", r"\bmedusa\b"),
                     ("echidna", r"\bechidna\b")):
        lvl, e = adoption_level(r, rx)
        if lvl != "absent":
            formal[name] = lvl
    probe(res, d, "formal_symbolic",
          "+".join(f"{k}:{v}" for k, v in formal.items()) or "none", list(formal))

    cov = r.grep(r"forge coverage|solidity-coverage|codecov|lcov|run-coverage",
                 path_filter=r"(\.github/workflows/|Makefile|package\.json)")
    probe(res, d, "coverage_in_ci", bool(cov), ev(cov))


# --------------------------------------------------------------------------
# D3  static analysis
# --------------------------------------------------------------------------

def d3(r, res):
    d = "D3_static"
    # Each tool gets an adoption level, not a boolean.  In the pilot, aave-v3
    # only carries `slither-disable-next-line` comments in source -> "mentioned",
    # while comet runs run-slither.yaml -> "enforced_in_ci".  A boolean would
    # have scored those two identically.
    tools = OrderedDict()
    for name, rx in (("slither", r"\bslither\b"),
                     ("aderyn", r"\baderyn\b"),
                     ("semgrep", r"\bsemgrep\b"),
                     ("solhint", r"\bsolhint\b"),
                     ("mythx", r"\bmyth(ril|x)\b"),
                     ("4naly3er", r"4naly3er")):
        lvl, e = adoption_level(r, rx)
        if lvl != "absent":
            tools[name] = (lvl, e)
    probe(res, d, "tools",
          "+".join(f"{k}:{v[0]}" for k, v in tools.items()) or "none",
          [f"{k}={v[1][0] if v[1] else '?'}" for k, v in tools.items()][:4])

    enforced = [k for k, v in tools.items() if v[0] == "enforced_in_ci"]
    probe(res, d, "any_enforced_in_ci", bool(enforced), enforced)


# --------------------------------------------------------------------------
# D4  CI configuration
# --------------------------------------------------------------------------

def d4(r, res):
    d = "D4_ci"
    wf = r.glob(r"^\.github/workflows/.*\.(yml|yaml)$")
    probe(res, d, "workflow_count", len(wf), wf[:5])

    other_ci = r.glob(r"(\.circleci/|\.gitlab-ci\.yml|azure-pipelines)")
    probe(res, d, "other_ci", bool(other_ci), ev(other_ci))

    triggers = set()
    for f in wf:
        t = r.text(f)
        for k in ("pull_request", "push", "schedule", "workflow_dispatch"):
            if re.search(rf"^\s*{k}\s*:", t, re.M):
                triggers.add(k)
    probe(res, d, "triggers", "+".join(sorted(triggers)) or "none", sorted(triggers))

    # workflow filenames are highly informative on their own -- record them
    probe(res, d, "workflow_names",
          "; ".join(Path(f).name for f in wf[:14]), wf[:4])

    gas = (r.grep(r"gas[- ]?snapshot|\.gas-snapshot|forge snapshot|gas[- ]?profiler|gas[- ]?report")
           or r.glob(r"\.gas-snapshot$"))
    probe(res, d, "gas_regression", bool(gas), ev(gas))

    fmt = r.grep(r"forge fmt|prettier|eslint|solhint|lint|--check",
                 path_filter=CI_PATHS)
    probe(res, d, "format_gate", bool(fmt), ev(fmt))

    # A practice with no traditional-DevOps analogue: on-chain deployment and
    # governance actions driven from CI.  comet has deploy-market.yaml,
    # prepare-migration.yaml and enact-migration.yaml -- the governance stage
    # of the lifecycle, made executable.  Worth its own variable.
    gov_ci = [f for f in wf if re.search(
        r"(deploy|migrat|propos|enact|govern|upgrade)", Path(f).name, re.I)]
    probe(res, d, "deploy_or_governance_in_ci", bool(gov_ci),
          [Path(f).name for f in gov_ci][:4])


# --------------------------------------------------------------------------
# D5  release & deployment
# --------------------------------------------------------------------------

def d5(r, res):
    d = "D5_deploy"
    fs = r.glob(r"^(script|scripts)/.*\.s\.sol$")
    hd = r.grep(r"hardhat-deploy|@nomicfoundation/hardhat-ignition")
    style = []
    if fs:
        style.append("forge-script")
    if hd:
        style.append("hardhat-deploy/ignition")
    if not style and r.glob(r"^(script|scripts|deploy)/"):
        style.append("custom-script")
    probe(res, d, "deploy_style", "+".join(style) or "none", fs[:3] + ev(hd))

    arts = r.glob(r"^(deployments|broadcast|addresses)/")
    probe(res, d, "deploy_artifacts_committed", bool(arts), arts[:3])

    c2 = r.grep(r"CREATE2|create2|Create2Factory|CREATE3|deterministic")
    probe(res, d, "deterministic_deploy", bool(c2), ev(c2))

    verify = r.grep(r"--verify|etherscan|sourcify|verify-contract")
    probe(res, d, "verification_automated", bool(verify), ev(verify))

    multichain = r.grep(r"chainId|chain_id|rpc_endpoints|networks\s*:", path_filter=r"(foundry\.toml|hardhat\.config|\.env\.example)")
    probe(res, d, "multichain_config", bool(multichain), ev(multichain))


# --------------------------------------------------------------------------
# D6  upgradeability & governance
# --------------------------------------------------------------------------

def d6(r, res):
    d = "D6_upgrade"
    pat = OrderedDict()
    pat["uups"] = r.grep(r"UUPSUpgradeable|_authorizeUpgrade")
    pat["transparent"] = r.grep(r"TransparentUpgradeableProxy|ProxyAdmin")
    pat["beacon"] = r.grep(r"BeaconProxy|UpgradeableBeacon")
    pat["diamond"] = r.grep(r"diamondCut|IDiamond|LibDiamond")
    present = [k for k, v in pat.items() if v]
    probe(res, d, "proxy_pattern", "+".join(present) or "none/immutable",
          [f"{k}:{v[0][0]}" for k, v in pat.items() if v][:4])

    sl = r.grep(r"storage[- _]?layout|validateUpgrade|oz-upgrades|openzeppelin-upgrades")
    probe(res, d, "storage_layout_check", bool(sl), ev(sl))
    sl_ci = r.grep(r"storage[- _]?layout|validateUpgrade", path_filter=r"\.github/workflows/")
    probe(res, d, "storage_layout_in_ci", bool(sl_ci), ev(sl_ci))

    gov = []
    if r.grep(r"TimelockController|Timelock"):
        gov.append("timelock")
    if r.grep(r"GnosisSafe|Safe\{|safe-global|multisig|MultiSig"):
        gov.append("safe/multisig")
    if r.grep(r"Governor|GovernorBravo|propose\("):
        gov.append("governor")
    probe(res, d, "governance_mechanism", "+".join(gov) or "none", gov)

    sim = r.grep(r"simulate|proposal.*simulat|tenderly", path_filter=r"(script|scripts|test|\.github)")
    probe(res, d, "proposal_simulation", bool(sim), ev(sim))


# --------------------------------------------------------------------------
# D7  operability designed into the contracts
# --------------------------------------------------------------------------

def d7(r, res):
    d = "D7_operability"
    sol = r"\.sol$"
    pause = r.grep(r"Pausable|function\s+pause\s*\(|whenNotPaused", path_filter=sol)
    probe(res, d, "pause", bool(pause), ev(pause))

    guardian = r.grep(r"guardian|GUARDIAN_ROLE|emergencyAdmin|PAUSER_ROLE", path_filter=sol)
    probe(res, d, "guardian_role", bool(guardian), ev(guardian))

    ac = []
    if r.grep(r"AccessControl|hasRole\(", path_filter=sol):
        ac.append("role-based")
    if r.grep(r"onlyOwner|Ownable", path_filter=sol):
        ac.append("ownable")
    probe(res, d, "access_control", "+".join(ac) or "none", ac)

    bounds = r.grep(r"MAX_[A-Z_]+\s*=|require\(.*<=\s*MAX|_MAX_", path_filter=sol)
    probe(res, d, "param_bounds", bool(bounds), ev(bounds))

    emergency = r.grep(r"emergencyWithdraw|rescue|sweep|recoverERC20", path_filter=sol)
    probe(res, d, "emergency_exit", bool(emergency), ev(emergency))


# --------------------------------------------------------------------------
# D8  monitoring & incident response
# --------------------------------------------------------------------------

def d8(r, res):
    d = "D8_monitor"
    sec = r.glob(r"(^|/)SECURITY\.md$")
    probe(res, d, "security_md", bool(sec), ev(sec))

    bounty = r.grep(r"immunefi|bug bounty|hackerone")
    probe(res, d, "bug_bounty", bool(bounty), ev(bounty))

    mon = []
    for name, rx in (("forta", r"\bforta\b"), ("tenderly", r"\btenderly\b"),
                     ("oz-defender", r"defender|openzeppelin monitor"),
                     ("hypernative", r"hypernative")):
        if r.grep(rx):
            mon.append(name)
    probe(res, d, "monitoring_config", "+".join(mon) or "none", mon)

    rb = r.grep(r"runbook|incident response|post-?mortem|war ?room", path_filter=r"\.md$")
    probe(res, d, "incident_runbook", bool(rb), ev(rb))


# --------------------------------------------------------------------------
# D9  assurance / audits
# --------------------------------------------------------------------------

def d9(r, res):
    d = "D9_assurance"
    audit_files = r.glob(r"(^|/)audits?/")
    probe(res, d, "audits_dir", bool(audit_files), audit_files[:4])
    probe(res, d, "audit_file_count", len(audit_files), [])

    mention = r.grep(r"trail of bits|openzeppelin|spearbit|cantina|consensys|certora|code4rena|sherlock|quantstamp",
                     path_filter=r"\.md$")
    probe(res, d, "auditor_mentioned", bool(mention), ev(mention))

    contest = r.grep(r"code4rena|sherlock|cantina competition|audit contest")
    probe(res, d, "competitive_audit", bool(contest), ev(contest))


# --------------------------------------------------------------------------
# D10 documentation & process
# --------------------------------------------------------------------------

def d10(r, res):
    d = "D10_docs"
    probe(res, d, "contributing", bool(r.glob(r"(^|/)CONTRIBUTING")), ev(r.glob(r"(^|/)CONTRIBUTING")))
    probe(res, d, "changelog", bool(r.glob(r"(^|/)CHANGELOG")), ev(r.glob(r"(^|/)CHANGELOG")))
    adr = r.glob(r"(adr|decisions|rfcs?)/.*\.md$")
    probe(res, d, "adr", bool(adr), adr[:3])
    rel = r.grep(r"release process|deployment (guide|process|checklist)|how to deploy", path_filter=r"\.md$")
    probe(res, d, "release_process_doc", bool(rel), ev(rel))


PROBES = [d1, d2, d3, d4, d5, d6, d7, d8, d9, d10]
DIMS = ["D1_build", "D2_test", "D3_static", "D4_ci", "D5_deploy",
        "D6_upgrade", "D7_operability", "D8_monitor", "D9_assurance", "D10_docs"]


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def clone(pid, url, dest):
    if dest.exists():
        print(f"  [skip] {pid} already cloned")
        return True
    print(f"  [clone] {pid} <- {url}")
    cmd = ["git", "clone", "--depth", "1", "--quiet", url, str(dest)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        print(f"  [FAIL] {pid}: {p.stderr.strip()[:200]}")
        return False
    return True


def head_meta(path):
    def g(args):
        try:
            return subprocess.run(["git", "-C", str(path)] + args,
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            return ""
    return {"commit": g(["rev-parse", "HEAD"])[:10],
            "last_commit_date": g(["log", "-1", "--format=%cI"])}


def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = []
    for line in (ROOT / "repos.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        targets.append((parts[0], parts[1], parts[2]))

    all_results = []
    for pid, cat, url in targets:
        dest = REPO_DIR / pid
        if not clone(pid, url, dest):
            continue
        print(f"  [scan]  {pid}")
        r = Repo(pid, cat, url, dest)
        res = {d: {} for d in DIMS}
        for fn in PROBES:
            try:
                fn(r, res)
            except Exception as e:
                print(f"    !! probe {fn.__name__} failed on {pid}: {e}")
        record = {"protocol": pid, "category": cat, "url": url,
                  "file_count": len(r.files), **head_meta(dest), "dims": res}
        all_results.append(record)
        (OUT_DIR / f"{pid}.json").write_text(json.dumps(record, indent=2))

    (OUT_DIR / "all.json").write_text(json.dumps(all_results, indent=2))

    # flat CSV
    import csv
    keys = []
    for rec in all_results:
        for d in DIMS:
            for k in rec["dims"][d]:
                col = f"{d}.{k}"
                if col not in keys:
                    keys.append(col)
    with open(OUT_DIR / "matrix.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["protocol", "category"] + keys)
        for rec in all_results:
            row = [rec["protocol"], rec["category"]]
            for col in keys:
                d, k = col.split(".", 1)
                v = rec["dims"][d].get(k, {}).get("value", "")
                row.append(v)
            w.writerow(row)

    print(f"\nDone: {len(all_results)} repos -> {OUT_DIR}/matrix.csv")


if __name__ == "__main__":
    main()
