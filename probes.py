"""Variable registry and probes.

One function per variable, registered with @variable(...). A probe takes a Repo and
returns (value, evidence) where evidence is a list of {"path", "line", "snippet", ...}
dicts. It may raise NotApplicable("reason"). Any other exception is caught per
variable by the runner and recorded as status "error" -- one bad probe never
takes out the rest of a record.

Types
  adoption   absent < mentioned < configured < runs_in_ci   (levels.py)
  bool       True / False
  category   a '+'-joined token set ("foundry+hardhat") or a single label
  ratio      float

scope  core: measured on the protocol's core repo(s) only
       any:  strongest evidence across all of the protocol's repos
na     immutable | no_admin_role : not applicable, decided by the hand-labelled
       protocol context (protocols.csv), never by a probe. Probes may also raise
       NotApplicable themselves (e.g. "no_solidity" for a Vyper protocol).
gap    a scope_gaps tag: if protocols.csv says that part of the protocol lives
       outside the scanned repos, a negative result becomes "unknown", not "no".

D7 (operability in-contract) is retired; its IDs are not reused.
"""

import re
from collections import OrderedDict

from levels import adoption, ci_of, LEVELS, RANK, CONFIG_FILES
from repo import paths as P

STAGES = OrderedDict([
    ("D1_build", "before_launch"), ("D2_test", "before_launch"),
    ("D3_static", "before_launch"), ("D4_ci", "before_launch"),
    ("D5_deploy", "at_launch"), ("D6_upgrade", "governance"),
    ("D8_monitor", "after_launch"), ("D9_assurance", "assurance"),
    ("D10_docs", "cross_cutting"),
])

AUDIT_ORDER = ["none", "mentioned", "external_link", "in_repo"]


class NotApplicable(Exception):
    pass


class Var:
    def __init__(self, id, type, scope, merge, na, gap, order, desc, fn=None, sources=None):
        self.id, self.type, self.scope = id, type, scope
        self.dim, self.name = id.split(".", 1)
        self.stage = STAGES[self.dim]
        self.merge, self.na, self.gap, self.order = merge, na, gap, order
        self.desc, self.fn, self.sources = desc, fn, sources

    @property
    def negative(self):
        """The value that means 'practice not found' (None for ratios)."""
        if self.order:
            return self.order[0]
        return {"adoption": "absent", "bool": False, "category": "none"}.get(self.type)


VARS = OrderedDict()


def variable(id, type, scope="core", merge=None, na=None, gap=None, order=None, desc=""):
    def deco(fn):
        VARS[id] = Var(id, type, scope, merge, na, gap, order, desc, fn)
        return fn
    return deco


def derived(id, type, sources, desc, order=None):
    VARS[id] = Var(id, type, "derived", None, None, None, order, desc, sources=sources)


# ---------------------------------------------------------------------------
# shared path predicates
# ---------------------------------------------------------------------------

TESTDIR = r"(^|/)(test|tests)/"
MOCKISH = (r"(^|/)[^/]*(Mock|Harness)[^/]*\.(sol|ts|js|vy)$"
           r"|(^|/)(mocks?|harness(es)?)/")
NONSRC = (r"(^|/)(test|tests|testing|script|scripts|mocks?|vendor|vendored|lib|"
          r"node_modules|certora|echidna|medusa|docs?|examples?|audits?|deployments?|"
          r"interfaces?|tasks|forge|foundry)/"
          r"|\.(t|s)\.sol$|(^|/)I[A-Z]\w*\.sol$|(^|/)[^/]*(Mock|Harness|Test)[^/]*\.sol$")
TESTPATH = TESTDIR + r"|\.t\.sol$"
TEST_SIG = re.compile(
    r"function\s+(test|invariant|check|prove)\w*\s*\(|\b(it|describe|test)\s*\(|def\s+test_|@Test\b")

_test_dir_rx = re.compile(TESTDIR)
_mock_rx = re.compile(MOCKISH)
_nonsrc_rx = re.compile(NONSRC, re.I)
_testname_rx = re.compile(r"\.t\.sol$|Test\.sol$|\.(test|spec)\.(ts|js)$")


def is_test(f):
    return (f.endswith((".sol", ".ts", ".js", ".py", ".vy"))
            and (_test_dir_rx.search(f) or _testname_rx.search(f))
            and not _mock_rx.search(f))


def is_src(f):
    return f.endswith((".sol", ".vy")) and not _nonsrc_rx.search(f)


def test_files(r):
    return r.memo("test_files", lambda: [f for f in r.scoped if is_test(f)])


def src_files(r):
    return r.memo("src_files", lambda: [f for f in r.scoped if is_src(f)])


def loc(r, files):
    return sum(1 for f in files for l in r.text(f).splitlines() if l.strip())


def adopt(id, desc, **kw):
    """Register an adoption variable whose probe is a single adoption() call."""
    scope, na, gap = kw.pop("scope", "core"), kw.pop("na", None), kw.pop("gap", None)

    def fn(r):
        return adoption(r, **kw)
    VARS[id] = Var(id, "adoption", scope, None, na, gap, None, desc, fn)


def bool_var(id, desc, scope="core", na=None, gap=None):
    def deco(fn):
        VARS[id] = Var(id, "bool", scope, None, na, gap, None, desc, fn)
        return fn
    return deco


# ---------------------------------------------------------------------------
# D1  build & toolchain
# ---------------------------------------------------------------------------

@variable("D1_build.framework", "category", desc="Build/test framework(s) with a config file")
def framework(r):
    found, ev = [], []
    for name, rx in (("foundry", r"(^|/)foundry\.toml$"),
                     ("hardhat", r"(^|/)hardhat\.config\.(ts|js|cjs|mjs)$"),
                     ("truffle", r"(^|/)truffle(-config)?\.js$"),
                     ("brownie", r"(^|/)brownie-config\.ya?ml$"),
                     ("ape", r"(^|/)ape-config\.ya?ml$"),
                     ("waffle", r"(^|/)\.waffle\.json$")):
        h = r.glob(rx)
        if h:
            found.append(name)
            ev.append({"path": h[0]})
    # Pre-Foundry toolchains have no config file of their own: they are named in
    # package.json instead (Uniswap v2 -> waffle, Compound v2 -> its own saddle).
    # Reporting "none" for a repo that plainly builds and tests was misleading.
    if not found:
        for name, rx in (("waffle", r"ethereum-waffle|waffle\s"),
                         ("saddle", r"eth-saddle|[\"']saddle[\"']|script/compile"),
                         ("dapptools", r"^\s*\w[\w-]*\s*:;?.*\bdapp\b[^\n]*\b(build|test)\b|\bhevm\s+dapp-test\b|\bdapp\s+(build|test)\b")):
            h = r.grep(rx, path_filter=r"(^|/)(package\.json|Makefile)$", limit=1)
            if h:
                found.append(name)
                ev += h
    return "+".join(found) or "none", ev


@variable("D1_build.solc_pinning", "category", merge="first",
          desc="Compiler version pinned in config, exact pragma, or floating pragma")
def solc_pinning(r):
    # Vyper protocols (Curve) have first-party source but no solc pragma at all:
    # test .sol specifically, and never report a pinning style with no evidence.
    if not any(f.endswith(".sol") for f in src_files(r)):
        raise NotApplicable("no_solidity")
    cfg = (r.grep(r"^\s*(solc|solc_version)\s*=", path_filter=r"(^|/)foundry\.toml$")
           or r.grep(r"(solidity|version)\s*:\s*[\"']=?\d+\.\d+\.\d+[\"']",
                     path_filter=r"(^|/)hardhat\.config\.\w+$"))
    if cfg:
        return "pinned-in-config", cfg[:1]
    caret = r.grep(r"pragma\s+solidity\s+[\^>~<]", path_filter=r"\.sol$", path_exclude=NONSRC)
    exact = r.grep(r"pragma\s+solidity\s+=?\d", path_filter=r"\.sol$", path_exclude=NONSRC)
    if exact and not caret:
        return "exact-pragma", exact[:1]
    if caret:
        return "floating-pragma", caret[:1]
    raise NotApplicable("no_solidity")


@variable("D1_build.dep_management", "category", desc="How third-party code is pulled in")
def dep_management(r):
    dep, ev = [], []
    if r.glob(r"^\.gitmodules$"):
        dep.append("git-submodule"); ev.append({"path": ".gitmodules"})
    sd = r.grep(r"^\s*\[(soldeer|dependencies)\]", path_filter=r"(^|/)foundry\.toml$")
    if sd:
        dep.append("soldeer"); ev += sd[:1]
    pj = r.glob(r"(^|/)package\.json$", exclude=r"node_modules")
    if pj:
        dep.append("npm"); ev.append({"path": pj[0]})
    return "+".join(dep) or "none", ev


@bool_var("D1_build.dep_lockfile", "Lockfile for JS or Solidity dependencies")
def dep_lockfile(r):
    lock = r.glob(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|"
                  r"soldeer\.lock|foundry\.lock)$", exclude=r"node_modules")
    return bool(lock), P(lock[:3])


# ---------------------------------------------------------------------------
# D2  testing
# ---------------------------------------------------------------------------

CI_RUNNER = (r"forge\s+test|forge\s+t\b|hardhat\s+test|\b(yarn|pnpm|bun)\s+(run\s+)?test\b|"
             r"\bnpm\s+(run\s+)?test\b|brownie\s+test|ape\s+test|\bmake\s+test|\bjust\s+test|"
             r"\bpytest\b|(foundry|hardhat|forge)[-_]test")


@bool_var("D2_test.suite_present", "Tests exist (test functions in non-mock test files)", gap="tests")
def suite_present(r):
    hit = []
    for f in test_files(r):
        if TEST_SIG.search(r.text(f)):
            hit.append(f)
            if len(hit) >= 3:
                break
    return bool(hit), P(hit)


SPLIT_REPO_SUBMODULES = 3   # at/above this, the implementation lives in other repos


@variable("D2_test.test_to_src_ratio", "ratio", merge="first", gap="tests",
          desc="Test LoC / source LoC (mocks, harnesses, interfaces, vendored code excluded)")
def test_to_src_ratio(r):
    src = src_files(r)
    if not src:
        raise NotApplicable("no_source")
    if r.first_party_submodules >= SPLIT_REPO_SUBMODULES:
        # the code under test is in the protocol's other repos, which a shallow
        # clone does not fetch, so this repo's source is not the denominator
        raise NotApplicable("source_in_submodules")
    t, s = loc(r, test_files(r)), loc(r, src)
    return round(t / s, 2) if s else 0.0, [
        {"snippet": f"{len(test_files(r))} test files, {t} LoC"},
        {"snippet": f"{len(src)} source files, {s} LoC"}]


def _test_paths_or_config():
    return TESTPATH + "|" + CONFIG_FILES


def _technique(id, desc, present_rx, present_paths, mention_rx, extra_present=None):
    """Registers an adoption variable; gap="tests" so a protocol whose tests are not
    public (scope_gaps=tests) reads as unknown rather than 'not done'."""
    def fn(r):
        def present():
            h = r.grep(present_rx, path_filter=present_paths, path_exclude=MOCKISH, limit=3)
            if extra_present:
                h += extra_present(r)
            return h
        return adoption(r, ci=CI_RUNNER, ci_needs_present=True, present=present,
                        mention=mention_rx, mention_paths=r"\.md$")
    VARS[id] = Var(id, "adoption", "core", None, None, "tests", None, desc, fn)


_technique("D2_test.fork_testing",
           "Tests against a forked chain (technique in code; CI runs the test runner)",
           r"vm\.(createSelectFork|createFork|selectFork)|forking\s*:|--fork-url|"
           r"--fork-block-number|hardhat_reset",
           _test_paths_or_config(), r"fork(ed)?[- ](test|mode)|mainnet fork")

# Foundry fuzzes any test function that takes parameters; a `testFuzz_` prefix is
# only a convention (morpho-blue's tests are named testRepayMax(uint256 shares)).
_technique("D2_test.fuzz_testing",
           "Fuzz tests (Foundry parameterised tests, fast-check); CI runs the test runner",
           r"function\s+test\w*\s*\(\s*[^)\s]|function\s+test_?fuzz|forge-config:[^\n]*fuzz|"
           r"fast-check|fc\.assert|^\[(profile\.[\w-]+\.)?fuzz\]",
           TESTPATH + r"|(^|/)foundry\.toml$", r"\bfuzz(ing|z)?\b")

_technique("D2_test.invariant_testing",
           "Stateful invariant tests; CI runs the test runner",
           r"function\s+invariant\w*\s*\(|StdInvariant|targetContract\s*\(|targetSelector\s*\(|"
           r"^\[(profile\.[\w-]+\.)?invariant\]",
           TESTPATH + r"|(^|/)foundry\.toml$", r"\binvariant",
           extra_present=lambda r: P(r.glob(r"(^|/)invariants?/[^/]+\.(sol|ts|js)$")[:2]))

adopt("D2_test.coverage", "Code coverage measured", gap="tests",
      ci=r"forge\s+coverage|solidity-coverage|hardhat\s+coverage|codecov|coveralls|\blcov\b|"
         r"\bcoverage\b",
      cfg=r"forge\s+coverage|solidity-coverage|hardhat\s+coverage|\"coverage[\w:-]*\"\s*:|"
          r"codecov|\blcov\b|genhtml|solcover",
      cfg_paths=r"(^|/)(\.solcover\.js|\.?codecov\.ya?ml|\.?coveragerc)$",
      mention=r"forge\s+coverage|solidity-coverage|codecov|coveralls|\blcov\b",
      mention_paths=r"\.md$")

adopt("D2_test.formal_certora", "Certora Prover",
      ci=r"certoraRun|certora[-_]cli|certora[-_]run|certoraMutate|Certora/",
      cfg=r"certoraRun|certora[-_]cli",
      cfg_paths=r"(^|/)certora/.*\.(conf|spec)$|(^|/)\.certora",
      mention=r"certoraRun|certora[- ]prover", mention_paths=r"\.md$")
adopt("D2_test.formal_halmos", "Halmos symbolic testing",
      ci=r"\bhalmos\b", cfg_paths=r"(^|/)halmos\.toml$",
      cfg=r"\bhalmos\b", mention=r"\bhalmos\b", mention_paths=r"\.md$")
adopt("D2_test.formal_kontrol", "Kontrol (K framework) symbolic testing",
      ci=r"\bkontrol\b", cfg_paths=r"(^|/)kontrol\.toml$",
      cfg=r"\bkontrol\b", mention=r"\bkontrol\b", mention_paths=r"\.md$")
adopt("D2_test.fuzz_echidna", "Echidna property fuzzer",
      ci=r"\bechidna\b|crytic/echidna", cfg_paths=r"(^|/)echidna[\w.-]*\.ya?ml$|(^|/)echidna/",
      cfg=r"\bechidna\b", mention=r"\bechidna\b", mention_paths=r"\.md$")
adopt("D2_test.fuzz_medusa", "Medusa property fuzzer",
      ci=r"\bmedusa\b", cfg_paths=r"(^|/)medusa\.json$",
      cfg=r"\bmedusa\b", mention=r"\bmedusa\b", mention_paths=r"\.md$")

derived("D2_test.formal_any", "adoption",
        ["D2_test.formal_certora", "D2_test.formal_halmos", "D2_test.formal_kontrol"],
        "Strongest level among formal / symbolic tools")

# ---------------------------------------------------------------------------
# D3  static analysis
# ---------------------------------------------------------------------------

adopt("D3_static.slither", "Slither",
      ci=r"\bslither\b|crytic/slither-action",
      cfg_paths=r"(^|/)(slither\.config\.json|\.slither[\w.-]*)$")
adopt("D3_static.aderyn", "Aderyn",
      ci=r"\baderyn\b", cfg_paths=r"(^|/)aderyn\.(toml|json)$")
adopt("D3_static.semgrep", "Semgrep",
      ci=r"\bsemgrep\b",
      cfg_paths=r"(^|/)(\.semgrep(\.ya?ml)?|semgrep[\w.-]*\.ya?ml)$|(^|/)\.semgrep/")
adopt("D3_static.mythril", "Mythril / MythX",
      ci=r"\bmyth(ril|x)\b", cfg_paths=r"(^|/)\.mythx\.ya?ml$")
adopt("D3_static.four_naly3er", "4naly3er", ci=r"4naly3er")

derived("D3_static.any_analyzer", "adoption",
        ["D3_static.slither", "D3_static.aderyn", "D3_static.semgrep",
         "D3_static.mythril", "D3_static.four_naly3er"],
        "Strongest level among static analyzers (linters are under D4_ci.lint_gate)")

# ---------------------------------------------------------------------------
# D4  CI
# ---------------------------------------------------------------------------

@variable("D4_ci.provider", "category", desc="CI system(s) configured in the repo")
def ci_provider(r):
    ci = ci_of(r)
    out, ev = [], []
    for kind, label in (("workflow", "github-actions"), ("other", "other-ci")):
        f = [c["path"] for c in ci.files if c["kind"] == kind]
        if f:
            out.append(label); ev.append({"path": f[0]})
    return "+".join(out) or "none", ev


@bool_var("D4_ci.runs_on_pull_request", "A workflow runs on pull requests")
def ci_pr(r):
    ev = ci_of(r).any_trigger({"pull_request", "pull_request_target", "merge_group"})
    return bool(ev), ev


adopt("D4_ci.gas_regression", "Gas snapshots / gas reports tracked",
      ci=r"forge\s+snapshot|gas-snapshot|--gas-report|gas[- ]?profiler|snapshotGas|"
         r"check-snapshot|REPORT_GAS|gas-?diff|git\s+diff\s+--exit-code[^\n]*snapshot",
      cfg=r"forge\s+snapshot|gas-snapshot|gasReporter|hardhat-gas-reporter|REPORT_GAS|"
          r"gas[- ]?profiler|snapshotGas",
      cfg_paths=r"(^|/)\.gas-snapshot$|(^|/)snapshots?/[^/]+\.(json|snap|txt)$|(^|/)gas-snapshots?/",
      mention=r"gas[- ]?(snapshot|report|profiler)", mention_paths=r"\.md$")

adopt("D4_ci.lint_gate", "Solidity/JS linting or formatting (solhint, prettier, forge fmt, eslint)",
      ci=r"forge\s+fmt|prettier|eslint|solhint|\blint\b|\bfmt\b[^\n]*--check",
      cfg=r"^\[(profile\.[\w-]+\.)?fmt\]|solhint|prettier|eslint|forge\s+fmt",
      cfg_paths=r"(^|/)(\.solhint(\.json|rc)?|\.prettierrc[\w.]*|prettier\.config\.\w+|"
                r"\.eslintrc[\w.]*|eslint\.config\.\w+)$",
      mention=r"solhint|prettier|forge\s+fmt|eslint", mention_paths=r"\.md$")


@bool_var("D4_ci.deploy_or_governance_automation",
          "Deployment / migration / governance actions driven from a CI workflow")
def deploy_or_gov_ci(r):
    wf = [c["path"] for c in ci_of(r).files if c["kind"] == "workflow"
          and re.search(r"(deploy|migrat|propos|enact|govern|upgrade)", c["path"].rsplit("/", 1)[-1], re.I)]
    return bool(wf), P(wf[:4])


# ---------------------------------------------------------------------------
# D5  release & deployment
# ---------------------------------------------------------------------------

@variable("D5_deploy.deploy_style", "category", gap="deploy",
          desc="How deployments are scripted")
def deploy_style(r):
    style, ev = [], []
    tooling = r"(^|/)(certora|echidna|medusa|test|tests|node_modules|lib|vendor)/"
    fs = [f for f in r.glob(r"(^|/)(script|scripts)/.*\.sol$", exclude=tooling)
          if re.search(r"forge-std/Script|vm\.(start)?[bB]roadcast", r.text(f))]
    if fs:
        style.append("forge-script"); ev.append({"path": fs[0]})
    hd = r.grep(r"hardhat-deploy|@nomicfoundation/hardhat-ignition|hardhat-ignition",
                path_filter=r"(package\.json|hardhat\.config\.\w+)$")
    if hd:
        style.append("hardhat-deploy/ignition"); ev += hd[:1]
    if not style:
        cs = r.glob(r"(^|/)(script|scripts|deploy)/[^/]+\.(ts|js|sh|py)$", exclude=tooling)
        if cs:
            style.append("custom-script"); ev.append({"path": cs[0]})
    return "+".join(style) or "none", ev


@bool_var("D5_deploy.artifacts_committed", "Deployment addresses/broadcasts committed", gap="deploy")
def artifacts_committed(r):
    a = r.glob(r"(^|/)(deployments?|broadcast|addresses)/|(^|/)deployed[-_.\w]*\.json$|"
               r"(^|/)addresses?\.(json|md|ts)$")
    return bool(a), P(a[:3])


@bool_var("D5_deploy.deterministic_deploy", "CREATE2/CREATE3-style deterministic deployment",
          gap="deploy")
def deterministic_deploy(r):
    h = r.grep(r"CREATE2|CREATE3|CreateX|Create2Deployer|Create2Factory|deterministic[- ]deploy",
               path_filter=r"(^|/)(script|scripts|deploy|tasks)/|(^|/)(foundry\.toml|hardhat\.config\.\w+)$|"
                           r"(^|/)docs?/.*\.md$")
    return bool(h), h


adopt("D5_deploy.verification_automated", "Automated source verification (Etherscan/Sourcify)",
      gap="deploy",
      ci=r"--verify\b|sourcify|verify-contract|forge\s+verify|hardhat[- ]verify|etherscan-verify|verify:etherscan",
      cfg=r"--verify\b|etherscan|sourcify|forge\s+verify|hardhat[- ]verify",
      mention=r"etherscan|sourcify", mention_paths=r"\.md$")

_CHAIN = (r"mainnet|optimism|arbitrum|polygon|matic|base|bsc|bnb|avalanche|gnosis|linea|"
          r"scroll|zksync|mantle|blast|celo|fantom|metis|mode|zora")


@bool_var("D5_deploy.multichain", "Deployment config for two or more mainnet chains", gap="deploy")
def multichain(r):
    chains, ev = set(), []
    for f in r.glob(r"(^|/)(foundry\.toml|hardhat\.config\.\w+|\.env\.example)$"):
        for m in re.finditer(rf"\b({_CHAIN})\b", r.text(f), re.I):
            chains.add(m.group(1).lower())
        ev.append({"path": f})
    for d in r.glob(r"(^|/)(deployments?|broadcast)/[^/]+/"):
        m = re.search(rf"(?:deployments?|broadcast)/(?:[^/]*?)?({_CHAIN})", d, re.I)
        if m:
            chains.add(m.group(1).lower())
            ev.append({"path": d})
    return len(chains) >= 2, ev[:4] + [{"snippet": "chains: " + ",".join(sorted(chains))}]


adopt("D5_deploy.abi_release",
      "ABIs / artifacts released for integrators (release workflow or committed ABI folder)",
      ci=r"abis?[:_-]?extract|extract[-_ ]?abis?|abi[-_]?exporter|export[-_ ]?abis?|"
         r"forge\s+inspect[^\n]*\babi\b|\bABIs?\.zip|abi[-_]?release|publish[-_ ]?abis?",
      ci_extra={"release", "schedule"},
      cfg=r"abis?[:_-]?extract|hardhat-abi-exporter|abiExporter|forge\s+inspect[^\n]*\babi\b",
      cfg_paths=r"(^|/)abis?/[^/]+\.json$", mention=False)

adopt("D5_deploy.onchain_state_verification",
      "Post-deploy or recurring check that on-chain state matches the expected config",
      gap="deploy",
      ci=r"state[-_ ]?mate|verify[-_ ]?state|verify[-_ ]?deploy(ment)?|check[-_ ]?deploy(ment)?|"
         r"post[-_ ]?deploy|validate[-_ ]?(deploy(ment)?|config|state)|verifyDeployment|"
         r"on-?chain[-_ ]?(check|verif)",
      ci_extra={"schedule"},
      cfg=r"state[-_ ]?mate|verifyDeployment|post[-_ ]?deploy(ment)?[-_ ]?(check|verif)",
      cfg_paths=r"(^|/)(verify|check|validate|assert)[-_]?(deploy(ment)?|state|config|onchain|params)"
                r"[\w.-]*\.(s\.sol|sol|ts|js|sh|py|ya?ml|json|toml)$",
      mention=r"state-mate|verify[- ]deployment|post-deploy(ment)? (check|verif)",
      mention_paths=r"\.md$")

# ---------------------------------------------------------------------------
# D6  upgradeability & governance
# ---------------------------------------------------------------------------

_SRC_NOT_TEST = TESTDIR + "|" + MOCKISH


@variable("D6_upgrade.proxy_pattern", "category",
          desc="Proxy pattern in first-party contract source (tests, mocks, vendored code excluded)")
def proxy_pattern(r):
    pats = OrderedDict([
        ("uups", r"UUPSUpgradeable|_authorizeUpgrade"),
        ("transparent", r"TransparentUpgradeableProxy|\bProxyAdmin\b"),
        ("beacon", r"BeaconProxy|UpgradeableBeacon"),
        ("diamond", r"diamondCut|IDiamond|LibDiamond"),
    ])
    found, ev = [], []
    for k, rx in pats.items():
        h = r.grep(rx, path_filter=r"\.(sol|vy)$", path_exclude=_SRC_NOT_TEST, limit=1)
        if h:
            found.append(k); ev += h
    custom = [f for f in r.glob(r"(^|/)[^/]*Proxy[^/]*\.sol$", exclude=_SRC_NOT_TEST + "|" + r"(^|/)(vendor|lib|node_modules)/")
              if "delegatecall" in r.text(f)]
    if custom and not found:
        found.append("custom-proxy"); ev.append({"path": custom[0]})
    return "+".join(found) or "none", ev


adopt("D6_upgrade.upgrade_safety_check",
      "Upgrade-safety / storage-layout tooling (OZ upgrades plugin, slither-check-upgradeability, "
      "forge inspect storage)",
      na="immutable",
      ci=r"slither-check-upgradeability|forge\s+inspect[^\n]*storage|storage[-_ ]?layout|"
         r"\bvalidateUpgrade\b|\bvalidateImplementation\b|Upgrades\.(validate|upgrade|deploy)|"
         r"hardhat-upgrades|upgrades-core|foundry-upgrades|@openzeppelin/upgrades|upgrade-safety",
      cfg=r"slither-check-upgradeability|forge\s+inspect[^\n]*storage|\bvalidateUpgrade\b|"
          r"\bvalidateImplementation\b|Upgrades\.(validate|upgrade)|hardhat-upgrades|upgrades-core|"
          r"foundry-upgrades|@openzeppelin/upgrades|upgrade-safety",
      cfg_where=CONFIG_FILES + "|" + TESTPATH,
      mention=r"storage[-_ ]?layout|storageLayout")


@bool_var("D6_upgrade.proposal_simulation",
          "Governance proposals / payloads simulated on a fork before execution", na="no_admin_role",
          gap="governance")
def proposal_simulation(r):
    marker = re.compile(r"createSelectFork|createFork|selectFork|--fork-url|tenderly|hardhat_reset|\bsimulate\w*\s*\(", re.I)
    ctx = re.compile(r"propos(al|e)|governor|timelock|\baip\b|payload|castVote|enact|migration", re.I)
    hits = []
    for f in r.scoped:
        if not re.search(r"(^|/)(script|scripts|test|tests|tasks|forge)/", f):
            continue
        if not f.endswith((".sol", ".ts", ".js", ".sh", ".py")):
            continue
        if re.search(r"(^|/)(vendor|lib|node_modules|mocks?)/", f) or _mock_rx.search(f):
            continue
        t = r.text(f)
        m = marker.search(t)
        if m and (ctx.search(t) or ctx.search(f)):
            hits.append({"path": f, "line": t.count("\n", 0, m.start()) + 1,
                         "snippet": t[max(0, m.start() - 40):m.end() + 40].replace("\n", " ").strip()})
            if len(hits) >= 4:
                break
    return bool(hits), hits


@variable("D6_upgrade.governance_mechanism", "category", na="no_admin_role", gap="governance",
          desc="Governance / admin mechanism visible in first-party code or docs")
def governance_mechanism(r):
    pats = OrderedDict([
        ("timelock", r"TimelockController|\bTimelock(er)?\b"),
        ("safe/multisig", r"GnosisSafe|safe-global|\bmulti-?sig\b"),
        ("governor", r"\bGovernor(Bravo|Alpha)?\b|castVote"),
    ])
    found, ev = [], []
    for k, rx in pats.items():
        h = r.grep(rx, path_filter=r"\.(sol|vy|md|ts|js)$", path_exclude=_SRC_NOT_TEST, limit=1)
        if h:
            found.append(k); ev += h
    return "+".join(found) or "none", ev


# ---------------------------------------------------------------------------
# D8  monitoring & incident response
# ---------------------------------------------------------------------------

@bool_var("D8_monitor.security_policy", "SECURITY.md / security.txt", scope="any")
def security_policy(r):
    s = r.glob(r"(^|/)(\.github/)?SECURITY\.md$|(^|/)security\.txt$")
    return bool(s), P(s[:2])


@bool_var("D8_monitor.bug_bounty", "Bug bounty referenced in README/SECURITY/docs", scope="any")
def bug_bounty(r):
    h = r.grep(r"immunefi|bug[- ]?bounty|hackerone|hackenproof|cantina\.xyz/bounties",
               path_filter=r"(^|/)(README|SECURITY|BUGBOUNTY|BUG[-_]BOUNTY|CONTRIBUTING)[^/]*\.md$|"
                           r"(^|/)docs?/.*\.md$")
    return bool(h), h


@variable("D8_monitor.monitoring_tools", "category", scope="any",
          desc="Monitoring/alerting tools referenced in public repo files "
               "(weak: many mentions are development-time use)")
def monitoring_tools(r):
    pats = OrderedDict([
        ("forta", r"\bforta\b|forta-agent"),
        ("tenderly", r"tenderly"),
        ("oz-defender", r"openzeppelin[/ -]defender|defender-(client|sdk|relay)|OpenZeppelin (Defender|Monitor)"),
        ("hypernative", r"hypernative"),
        ("alerting", r"pagerduty|opsgenie|alertmanager|grafana"),
    ])
    found, ev = [], []
    for k, rx in pats.items():
        h = r.grep(rx, path_exclude=r"(^|/)(test|tests)/", limit=1)
        if h:
            found.append(k); ev += h
    return "+".join(found) or "none", ev


@bool_var("D8_monitor.incident_runbook", "Runbook / incident-response / post-mortem docs", scope="any")
def incident_runbook(r):
    f = r.glob(r"(^|/)(runbooks?|incident[-_ ]?(response|management)?|post-?mortems?|playbooks?)[^/]*(/|\.md$)")
    if f:
        return True, P(f[:3])
    h = r.grep(r"^#{1,4}\s.*(runbook|incident response|post-?mortem)", path_filter=r"\.md$")
    return bool(h), h


# ---------------------------------------------------------------------------
# D9  assurance
# ---------------------------------------------------------------------------

_AUDITORS = (r"trail of bits|trailofbits|spearbit|cantina|consensys diligence|diligence|code4rena|"
             r"sherlock|quantstamp|mixbytes|chainsecurity|abdk|peckshield|halborn|sigma ?prime|"
             r"statemind|ackee|zellic|pashov|certora|openzeppelin|hacken|omniscia|iosiro|dedaub|"
             r"runtime verification|least authority|nethermind|oxorio|stermi|blackthorn|ottersec")
_AUDIT_URL = (r"https?://[^\s)>\]\"']*(audits?|security[-_]?review|code4rena\.com/reports|"
              r"cantina\.xyz/(competitions|portfolio)|sherlock\.xyz|spearbit\.com|"
              r"consensys\.io/diligence|diligence\.consensys|certora\.com/reports|"
              r"chainsecurity\.com/security-audit|github\.com/trailofbits/publications|"
              r"mixbytes\.io/audits|abdk\.consulting|statemind\.io|zellic\.io|halborn\.com)"
              r"[^\s)>\]\"']*")


@variable("D9_assurance.audit_evidence", "category", scope="any", order=AUDIT_ORDER, gap="audits",
          desc="Strongest audit evidence: report in repo > link to a report hosted elsewhere > "
               "auditor named next to 'audit' in docs")
def audit_evidence(r):
    inrepo = r.glob(r"(^|/)(audits?|security[-_ ]?reviews?)/.*\.(pdf|md|txt|html)$|"
                    r"(^|/)[^/]*audit[^/]*\.pdf$")
    if inrepo:
        return "in_repo", P(inrepo[:4])
    ext = r.grep(_AUDIT_URL, path_filter=r"\.(md|txt)$", exclude=None)
    if ext:
        return "external_link", ext[:4]
    men = r.grep(rf"^(?=[^\n]*\baudit)[^\n]*\b(?:{_AUDITORS})\b", path_filter=r"\.md$",
                 path_exclude=r"(^|/)(node_modules|vendor|lib)/", exclude=None)
    if men:
        return "mentioned", men[:3]
    return "none", []


@bool_var("D9_assurance.competitive_audit", "Audit competition (Code4rena, Sherlock, Cantina, CodeHawks)",
          scope="any", gap="audits")
def competitive_audit(r):
    f = r.glob(r"(^|/)(audits?|security)[^\n]*(competition|contest|code4rena|sherlock|codehawks)|"
               r"(competition|contest|code4rena|c4-)[^/]*\.(pdf|md)$")
    if f:
        return True, P(f[:3])
    h = r.grep(r"code4rena\.com/reports|audits\.sherlock\.xyz|cantina\.xyz/competitions|"
               r"codehawks|audit (competition|contest)", path_filter=r"\.md$", exclude=None)
    return bool(h), h


derived("D9_assurance.audited_publicly", "bool", ["D9_assurance.audit_evidence"],
        "An audit report is in the repo or linked from it")

# ---------------------------------------------------------------------------
# D10 documentation & process
# ---------------------------------------------------------------------------

@bool_var("D10_docs.contributing", "CONTRIBUTING guide", scope="any")
def contributing(r):
    f = r.glob(r"(^|/)CONTRIBUTING")
    return bool(f), P(f[:1])


@bool_var("D10_docs.changelog", "CHANGELOG", scope="any")
def changelog(r):
    f = r.glob(r"(^|/)CHANGELOG")
    return bool(f), P(f[:1])


@bool_var("D10_docs.adr", "Architecture decision records / RFCs", scope="any")
def adr(r):
    f = r.glob(r"(^|/)(adrs?|decisions|rfcs?)/.*\.md$")
    return bool(f), P(f[:3])


@bool_var("D10_docs.release_process_doc", "Written release / deployment process", scope="any")
def release_process_doc(r):
    h = r.grep(r"release process|deployment (guide|process|checklist)|how to deploy",
               path_filter=r"\.md$")
    return bool(h), h


# ---------------------------------------------------------------------------
# How each variable is measured, in plain English, for the site's hover text.
# These describe what the probe above actually looks for -- keep them in step
# with the code; the completeness check below fails the import if one is missing.
# ---------------------------------------------------------------------------

_CI_NOTE = ("Runs in CI when a push/pull-request workflow invokes it, following "
            "make/yarn/just targets; a step whose failure is ignored counts as configured.")

MEASURED = {
 "D1_build.framework":
   "Looks for a build config file: foundry.toml, hardhat.config.*, truffle-config.js, "
   "brownie-config.yaml, ape-config.yaml, .waffle.json. If none exists, falls back to "
   "package.json / Makefile naming ethereum-waffle, eth-saddle or dapptools.",
 "D1_build.solc_pinning":
   "'Pinned in config' if foundry.toml sets solc = or the Hardhat config names an exact "
   "version. Otherwise reads the pragma in first-party .sol files: exact if none use ^ ~ > <, "
   "floating if any do. Not applicable when there is no Solidity (e.g. Vyper).",
 "D1_build.dep_management":
   "Reports which dependency mechanisms exist: a .gitmodules file (git submodules), a "
   "[soldeer]/[dependencies] table in foundry.toml, and a package.json (npm).",
 "D1_build.dep_lockfile":
   "Looks for a committed lockfile: package-lock.json, yarn.lock, pnpm-lock.yaml, bun.lockb, "
   "soldeer.lock or foundry.lock.",
 "D2_test.suite_present":
   "Counts files under test/ or named *.t.sol, *Test.sol, *.test.ts (mocks and harnesses "
   "excluded) that actually contain a test function signature.",
 "D2_test.test_to_src_ratio":
   "Non-blank lines of test code divided by lines of first-party source (.sol/.vy). Excludes "
   "mocks, harnesses, interfaces, scripts and vendored code. Not applicable when the "
   "implementation sits in the protocol's own git submodules, which a shallow clone omits.",
 "D2_test.fork_testing":
   "Looks in test files and build config for vm.createSelectFork / createFork / selectFork, "
   "a forking: block, --fork-url or hardhat_reset. " + _CI_NOTE,
 "D2_test.fuzz_testing":
   "Foundry fuzzes any test function that takes parameters, so a test signature with arguments "
   "counts, as do testFuzz_ names, a [fuzz] profile and fast-check in JS. " + _CI_NOTE,
 "D2_test.invariant_testing":
   "Looks for invariant_ test functions, StdInvariant, targetContract/targetSelector, an "
   "[invariant] profile, or an invariants/ directory. " + _CI_NOTE,
 "D2_test.coverage":
   "Looks for forge coverage, solidity-coverage, hardhat coverage, codecov, coveralls or lcov, "
   "or a .solcover.js / codecov.yml config. " + _CI_NOTE,
 "D2_test.formal_certora":
   "Looks for certoraRun or certora-cli, or a certora/ directory holding .conf or .spec "
   "files. " + _CI_NOTE,
 "D2_test.formal_halmos":
   "Looks for halmos on a command line or a halmos.toml config. " + _CI_NOTE,
 "D2_test.formal_kontrol":
   "Looks for kontrol on a command line or a kontrol.toml config. " + _CI_NOTE,
 "D2_test.fuzz_echidna":
   "Looks for echidna (or crytic/echidna) on a command line, an echidna*.yaml config or an "
   "echidna/ directory. " + _CI_NOTE,
 "D2_test.fuzz_medusa":
   "Looks for medusa on a command line or a medusa.json config. " + _CI_NOTE,
 "D2_test.formal_any":
   "Roll-up: the strongest level reached by Certora, Halmos or Kontrol.",
 "D3_static.slither":
   "Looks for slither or the crytic/slither-action, or a slither.config.json. " + _CI_NOTE,
 "D3_static.aderyn":
   "Looks for aderyn on a command line or an aderyn.toml/json config. " + _CI_NOTE,
 "D3_static.semgrep":
   "Looks for semgrep on a command line or a .semgrep/ directory or semgrep*.yaml rule "
   "file. " + _CI_NOTE,
 "D3_static.mythril":
   "Looks for mythril or mythx on a command line, or a .mythx.yaml config. " + _CI_NOTE,
 "D3_static.four_naly3er":
   "Looks for the 4naly3er report generator. " + _CI_NOTE,
 "D3_static.any_analyzer":
   "Roll-up: the strongest level reached by Slither, Aderyn, Semgrep, Mythril or 4naly3er. "
   "Linters are counted separately, under the lint gate.",
 "D4_ci.provider":
   "github-actions if .github/workflows holds any workflow; other-ci for a CircleCI, GitLab, "
   "Azure Pipelines or Travis config.",
 "D4_ci.runs_on_pull_request":
   "True when at least one workflow declares a pull_request, pull_request_target or "
   "merge_group trigger. Path filters on the trigger are not evaluated.",
 "D4_ci.gas_regression":
   "Looks for forge snapshot, --gas-report, a gas profiler, snapshotGas or a committed "
   ".gas-snapshot / snapshots directory. " + _CI_NOTE,
 "D4_ci.lint_gate":
   "Looks for forge fmt, prettier, eslint or solhint, or their config files "
   "(.solhint.json, .prettierrc, .eslintrc, an [fmt] profile). " + _CI_NOTE,
 "D4_ci.deploy_or_governance_automation":
   "True when a workflow's file name contains deploy, migrat, propos, enact, govern or "
   "upgrade: on-chain actions driven from CI, which has no traditional DevOps analogue.",
 "D5_deploy.deploy_style":
   "forge-script when a file under script/ imports forge-std/Script or calls vm.broadcast; "
   "hardhat-deploy/ignition when named in package.json or the Hardhat config; otherwise "
   "custom-script if a script/ or deploy/ directory holds .ts/.js/.sh/.py.",
 "D5_deploy.artifacts_committed":
   "True when deployment output is committed: a deployments/, broadcast/ or addresses/ "
   "directory, or a deployed*.json / addresses.json file.",
 "D5_deploy.deterministic_deploy":
   "Looks for CREATE2, CREATE3, CreateX or a Create2 deployer in deploy scripts, tasks, build "
   "config or docs.",
 "D5_deploy.verification_automated":
   "Looks for --verify, forge verify, hardhat-verify, verify-contract or sourcify. An "
   "Etherscan API key on its own does not count. " + _CI_NOTE,
 "D5_deploy.multichain":
   "True when two or more mainnet chain names appear in foundry.toml, the Hardhat config or "
   ".env.example, or as per-chain deployment directories.",
 "D5_deploy.abi_release":
   "Looks for ABI extraction or publishing (abis:extract, abi-exporter, forge inspect abi, an "
   "ABIs archive) or a committed abi/ directory. A release-triggered workflow counts as CI.",
 "D5_deploy.onchain_state_verification":
   "Looks for a check that deployed on-chain state matches the intended config: state-mate, "
   "verify-state, verify/check-deployment, post-deploy validation. A scheduled workflow counts "
   "as CI. Distinct from Etherscan source verification.",
 "D6_upgrade.proxy_pattern":
   "Reads first-party contract source only (tests, mocks and vendored code excluded) for "
   "UUPSUpgradeable/_authorizeUpgrade, TransparentUpgradeableProxy/ProxyAdmin, BeaconProxy or "
   "diamondCut; failing those, a *Proxy*.sol that delegatecalls.",
 "D6_upgrade.upgrade_safety_check":
   "Looks for tooling that checks an upgrade is storage-safe: slither-check-upgradeability, "
   "forge inspect storage, OpenZeppelin's upgrades plugins, Upgrades.validate. " + _CI_NOTE +
   " Not applicable to immutable protocols.",
 "D6_upgrade.proposal_simulation":
   "True when a file under script/, test/ or tasks/ both uses a fork cheatcode, Tenderly or a "
   "simulate*() call and mentions proposals, governor, timelock, AIP, payload or migration. "
   "Not applicable when the protocol has no admin role.",
 "D6_upgrade.governance_mechanism":
   "Looks in first-party code and docs for TimelockController/Timelock, Gnosis Safe or "
   "multisig, and Governor/castVote. Not applicable when the protocol has no admin role.",
 "D8_monitor.security_policy":
   "True when a SECURITY.md (repo root or .github/) or security.txt exists.",
 "D8_monitor.bug_bounty":
   "Looks for Immunefi, HackerOne, HackenProof or the words 'bug bounty' in README, SECURITY, "
   "CONTRIBUTING or docs markdown.",
 "D8_monitor.monitoring_tools":
   "Names Forta, Tenderly, OpenZeppelin Defender, Hypernative or an alerting stack (PagerDuty, "
   "Opsgenie, Grafana) referenced outside tests. Weak signal: many mentions are development-time "
   "use, and most real monitoring is not public.",
 "D8_monitor.incident_runbook":
   "Looks for a runbook, incident-response, post-mortem or playbook file or directory, or a "
   "markdown heading naming one.",
 "D9_assurance.audit_evidence":
   "in_repo when an audits/ directory holds reports; external_link when a markdown file links "
   "to a report hosted elsewhere (DefiLlama's audit links also count); mentioned when an auditor "
   "is named on a line about an audit.",
 "D9_assurance.competitive_audit":
   "Looks for Code4rena, Sherlock, Cantina competitions or CodeHawks in audit file names or docs.",
 "D9_assurance.audited_publicly":
   "Roll-up: true when an audit report is in the repository or linked from it.",
 "D10_docs.contributing": "True when a CONTRIBUTING file exists.",
 "D10_docs.changelog": "True when a CHANGELOG file exists.",
 "D10_docs.adr":
   "True when an adr/, decisions/ or rfcs/ directory holds markdown.",
 "D10_docs.release_process_doc":
   "Looks in markdown for 'release process', a deployment guide/process/checklist, or "
   "'how to deploy'.",
}

_missing = set(VARS) - set(MEASURED)
if _missing:                      # keep the hover text in step with the registry
    raise RuntimeError(f"probes.MEASURED is missing: {sorted(_missing)}")
for _v in VARS.values():
    _v.measured = MEASURED[_v.id]
