#!/usr/bin/env python3
"""Check each protocol's repo against the contracts that actually hold its funds.

    python3 tools/verify_mapping.py                # every protocol in data/protocols.csv
    python3 tools/verify_mapping.py aave-v3 cap    # only these

Independent evidence, so a wrong repo cannot confirm itself:
  1. DefiLlama's TVL adapter for the protocol (github.com/DefiLlama/DefiLlama-Adapters) names the
     contracts it reads TVL from. Addresses that appear in 3+ protocols' adapters are shared assets
     (WETH, USDC, stETH ...) and are skipped.
  2. Etherscan returns the verified source name of each address on Ethereum mainnet, following a
     proxy to its implementation.
  3. The repo is confirmed when it declares one of those contracts under a *distinctive* name
     (declared in at most MAX_SHARED of the sample's repos, so `ERC20` or `Pool` alone proves nothing).

Verdicts (data/mapping_check.csv, committed; raw Etherscan answers cached in out/etherscan/):
  confirmed    a distinctive deployed contract is declared in the repo
  weak         only generic names matched
  no_match     verified deployed contracts found, none declared in the repo -> check by hand
  no_evidence  the adapter names no verified Ethereum contract (API-driven adapters) -> check by hand

Needs ETHERSCAN_API_KEY in the environment or in .env (git-ignored). The key is sent only to
Etherscan and never printed or written to disk by this tool.
"""
import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from extract import clone_dir, load_csv  # noqa: E402
from repo import NOISE, Repo  # noqa: E402

ADAPTERS = ROOT / "repos" / ".defillama-adapters"
ADAPTERS_URL = "https://github.com/DefiLlama/DefiLlama-Adapters"
CACHE = ROOT / "out" / "etherscan" / "v2"
ETHERSCAN = "https://api.etherscan.io/v2/api"
MAX_ADDRESSES = 40      # per protocol; adapters list their core contracts first
SHARED_ASSET = 3        # an address in this many adapters is an asset, not the protocol's contract
MAX_SHARED = 3          # a contract name declared in more repos than this is not distinctive
ADDR = re.compile(r"0x[0-9a-fA-F]{40}\b")
DECL = re.compile(r"^\s*(?:abstract\s+)?(?:contract|library)\s+([A-Za-z_]\w*)", re.M)
IFACE = re.compile(r"(^|/)interfaces?/|(^|/)I[A-Z]\w*\.sol$")
VYDEF = re.compile(r"^def\s+(\w+)\s*\(", re.M)
MIN_DEFS = 10           # a Vyper contract is matched by its function names; ERC20-sized ones are too generic
MIN_JACCARD = 0.7
VENDOR = re.compile(r"(^|/)(lib|node_modules|@[\w-]+|openzeppelin[\w-]*|forge-std|solmate|solady|"
                    r"test|tests|mocks?)/", re.I)


def api_key():
    key = os.environ.get("ETHERSCAN_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        m = re.search(r"^ETHERSCAN_API_KEY\s*=\s*(\S+)", env.read_text(), re.M)
        key = m and m.group(1).strip("'\"")
    if not key:
        sys.exit("ETHERSCAN_API_KEY is not set (environment or .env)")
    return key


# -- DefiLlama adapters --------------------------------------------------------------

def modules(protocols):
    """(slug -> adapter module path, slug -> token address), from a dated cache of DefiLlama's
    live /protocols list."""
    cached = sorted((ROOT / "data" / "sources").glob("defillama_modules_*.json"))
    if cached:
        c = json.loads(cached[-1].read_text())
        if "tokens" in c:
            return c["modules"], c["tokens"]
    req = urllib.request.Request("https://api.llama.fi/protocols",
                                 headers={"User-Agent": "defi-devops-extractor"})
    live = json.load(urllib.request.urlopen(req, timeout=120))
    by_slug = {p["slug"]: p for p in live}
    by_id = {str(p.get("id")): p for p in live}
    snap = sorted((ROOT / "data" / "sources").glob("defillama_2*.json"))[-1]
    ids = {p["slug"]: str(p.get("id")) for p in json.loads(snap.read_text())["protocols"]}
    out, tokens = {}, {}
    for p in protocols:
        s = p["defillama_slug"]
        hit = by_slug.get(s) or by_id.get(ids.get(s, "")) or {}
        out[s] = hit.get("module") or ""
        a = str(hit.get("address") or "").lower()
        tokens[s] = a if ADDR.fullmatch(a) else ""      # skips "chain:0x..." non-Ethereum tokens
    today = datetime.date.today().isoformat()
    (ROOT / "data" / "sources" / f"defillama_modules_{today}.json").write_text(json.dumps(
        {"snapshot_date": today, "source": "https://api.llama.fi/protocols", "modules": out,
         "tokens": tokens}, indent=1, sort_keys=True))
    return out, tokens


def adapter_paths(module):
    return f"projects/{module.split('/')[0]}" if "/" in module else f"projects/{module}"


def sync_adapters(mods):
    if not (ADAPTERS / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse",
                        ADAPTERS_URL, str(ADAPTERS)], check=True)
    paths = sorted({adapter_paths(m) for m in mods if m}) + ["registries"]
    subprocess.run(["git", "-C", str(ADAPTERS), "sparse-checkout", "set", "--no-cone", *paths],
                   check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(ADAPTERS), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def registry_block(name):
    """Many adapters now live as one entry in a shared registries/*.js file (all Liquity forks
    in registries/liquity.js). Return only this protocol's entry, never its neighbours'."""
    key = re.compile(r"^(\s*)['\"]?" + re.escape(name) + r"['\"]?\s*:\s*[\[{]")
    for f in sorted((ADAPTERS / "registries").glob("*.js")):
        text = f.read_text(errors="ignore")
        lines = text.splitlines()
        # entries often go through a file-level constant or helper (ethereum: balV2Chain(...),
        # which reads balancerV2Vault): follow such names one hop, within this file only
        defs, cur = {}, None
        for l in lines:
            m = re.match(r"(?:const|let|var|function|async function)\s+(\w+)", l)
            if m:
                cur = m.group(1)
                defs[cur] = [l]
            elif cur and l[:1] in (" ", "\t", "}", ")", "]"):
                defs[cur].append(l)
            else:
                cur = None
        defs = {k: "\n".join(v) for k, v in defs.items() if k != "configs"}
        for i, l in enumerate(lines):
            m = key.match(l)
            if not m:
                continue
            ind, out = len(m.group(1)), [l]
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                    break
                out.append(nxt)
            block = "\n".join(out)
            for _ in range(2):
                block += "\n" + "\n".join(v for k, v in defs.items()
                                           if re.search(rf"\b{k}\b", block) and v not in block)
            return block
    return ""


def adapter_addresses(module):
    p = ADAPTERS / adapter_paths(module)
    files = sorted(p.rglob("*")) if p.is_dir() else [p] if p.exists() else []
    texts = [f.read_text(errors="ignore") for f in files
             if f.is_file() and f.suffix in (".js", ".ts", ".json")]
    if not texts:
        texts = [registry_block(module.split("/")[0].removesuffix(".js"))]
    seen = []
    for t in texts:
        for a in ADDR.findall(t):
            a = a.lower()
            if a not in seen and int(a, 16) > 0xffff:   # skip 0x0 / precompile-style placeholders
                seen.append(a)
    return seen


# -- Etherscan ---------------------------------------------------------------------

class Etherscan:
    def __init__(self, key):
        self.key, self.last = key, 0.0
        CACHE.mkdir(parents=True, exist_ok=True)

    def source(self, addr):
        """{'name', 'files', 'impl'} for a verified contract, or None. Cached per address."""
        c = CACHE / f"{addr}.json"
        if c.exists():
            return json.loads(c.read_text())
        q = urllib.parse.urlencode({"chainid": 1, "module": "contract", "action": "getsourcecode",
                                    "address": addr, "apikey": self.key})
        r, err = {}, ""
        for attempt in range(5):
            time.sleep(max(0, 0.26 - (time.time() - self.last)))   # free tier: 5 calls/s
            self.last = time.time()
            try:
                r = json.load(urllib.request.urlopen(f"{ETHERSCAN}?{q}", timeout=30))
            except Exception as e:                                  # never echo the URL (holds the key)
                r, err = {}, type(e).__name__
                time.sleep(1 + attempt)
                continue
            if r.get("status") == "1" and isinstance(r.get("result"), list):
                break
            err = str(r.get("result") or r.get("message"))[:80]
            if "invalid api key" in err.lower():
                sys.exit("Etherscan rejected the API key")
            if "rate limit" not in err.lower():
                break
            time.sleep(1 + attempt)
        if r.get("status") != "1":
            print(f"    etherscan failed for {addr}: {err}")   # not cached: retried next run
            return None
        res = r["result"][0] if r["result"] else {}
        out = None
        if res.get("SourceCode"):
            src = res["SourceCode"]
            files = source_files(src)
            out = {"name": res.get("ContractName", ""), "files": files,
                   "impl": (res.get("Implementation") or "").lower() if res.get("Proxy") == "1" else "",
                   # a Vyper contract is identified by its functions ("Vyper_contract" is no name)
                   "defs": sorted(set(VYDEF.findall(src))) if "vyper" in res.get("CompilerVersion", "").lower() else []}
        c.write_text(json.dumps(out))
        return out


def source_files(src):
    """File paths of a verified multi-file source; [] for a flattened single file."""
    s = src.strip()
    if s.startswith("{{"):
        s = s[1:-1]
    if s.startswith("{"):
        try:
            j = json.loads(s)
            return sorted((j.get("sources") or j).keys())
        except Exception:
            return []
    return []


# -- repos ---------------------------------------------------------------------------

def suffix(path):
    """Last two path components: 'protocol/pool/Pool.sol' -> 'pool/Pool.sol'."""
    return "/".join(path.split("/")[-2:])


def index(repo, idx):
    """Add a repo's own (non-vendored, non-test) contracts to idx: declared names, file-path
    suffixes, and each Vyper file's set of function names."""
    for f in repo.scoped:
        if VENDOR.search(f) or NOISE.search(f) or not f.endswith((".sol", ".vy")):
            continue
        idx["paths"].add(suffix(f))
        if f.endswith(".vy"):
            idx["names"].add(Path(f).stem)
            defs = frozenset(VYDEF.findall(repo.text(f)))
            if len(defs) >= MIN_DEFS:
                idx["vy"].append((Path(f).stem, defs))
        else:
            idx["names"].update(DECL.findall(repo.text(f)))


def vyper_match(defs, idx):
    defs = set(defs)
    if len(defs) < MIN_DEFS:
        return None
    best = max(((len(defs & d) / len(defs | d), stem) for stem, d in idx["vy"]), default=(0, None))
    return best[1] if best[0] >= MIN_JACCARD else None


def resolve(eth, addr):
    """Verified source of an address, or of its implementation when it is a proxy."""
    s = eth.source(addr)
    if s and s.get("impl"):
        impl = eth.source(s["impl"])
        if impl:
            s = dict(impl, proxy=s["name"])
    return s if s and s.get("name") else None


def classify(s, me, spread, pspread):
    """(label, 'hit' | 'generic' | None): is this deployed contract declared in the repo?"""
    vy = vyper_match(s.get("defs", []), me)
    if vy:
        return f"{vy}.vy", "hit"
    name = s["name"]
    if name in me["names"] and spread[name] <= MAX_SHARED:
        return name, "hit"
    # interfaces are copied into integrators' repos all the time: never evidence on their own
    first_party = [suffix(f) for f in s.get("files", []) if not VENDOR.search(f) and not IFACE.search(f)]
    by_path = next((x for x in first_party if x in me["paths"] and pspread[x] <= MAX_SHARED), None)
    if by_path:
        return f"{name} ({by_path})", "hit"
    return name, ("generic" if name in me["names"] else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("protocols", nargs="*")
    args = ap.parse_args()
    protocols = load_csv(ROOT / "data" / "protocols.csv")
    repo_rows = defaultdict(list)
    for r in load_csv(ROOT / "data" / "repos.csv"):
        if (r.get("include") or "1").strip() != "0":
            repo_rows[r["protocol_id"]].append(r)

    mods, tokens = modules(protocols)
    commit = sync_adapters(mods.values())
    print(f"DefiLlama adapters @ {commit[:10]}")
    eth = Etherscan(api_key())

    # every repo's contracts, to know which names and paths are distinctive across the sample
    idx = {}
    for p in protocols:
        i = {"names": set(), "paths": set(), "vy": []}
        for row in repo_rows[p["protocol_id"]]:
            d = clone_dir(row)
            if (d / ".git").exists():
                index(Repo(row["repo_id"], row["url"], d, row["role"], row.get("subpath", "")), i)
        idx[p["protocol_id"]] = i
    spread = Counter(n for i in idx.values() for n in i["names"])
    pspread = Counter(x for i in idx.values() for x in i["paths"])

    addrs = {p["protocol_id"]: adapter_addresses(mods.get(p["defillama_slug"], ""))
             if mods.get(p["defillama_slug"]) else [] for p in protocols}
    shared = Counter(a for lst in addrs.values() for a in set(lst))

    wanted = set(args.protocols) or {p["protocol_id"] for p in protocols}
    rows = []
    for p in protocols:
        pid = p["protocol_id"]
        if pid not in wanted:
            continue
        # the protocol's own addresses first; ones shared with other adapters (assets like WETH, but
        # also contracts others build on, like Balancer's Vault) after them
        own = ([a for a in addrs[pid] if shared[a] < SHARED_ASSET]
               + [a for a in addrs[pid] if shared[a] >= SHARED_ASSET])[:MAX_ADDRESSES]
        tok = tokens.get(p["defillama_slug"], "")
        verified, hits, generic = {}, [], []

        def judge(a, via=""):
            s = resolve(eth, a)
            if not s:
                return
            label, kind = classify(s, idx[pid], spread, pspread)
            verified[a] = label
            if kind:
                (hits if kind == "hit" else generic).append(label + via)

        for a in own:
            judge(a)
        if not hits and tok and tok not in own:
            # adapters that fetch their contracts from an API name no address; fall back to the
            # protocol's token (for liquid staking and stablecoins it is the core contract)
            own.append(tok)
            judge(tok, " (token)")
        hits, generic = sorted(set(hits)), sorted(set(generic) - set(hits))
        verdict = ("confirmed" if hits else "weak" if generic else
                   "no_match" if verified else "no_evidence")
        unmatched = sorted({v for v in verified.values() if not any(h.startswith(v) for h in hits + generic)})
        rows.append({"protocol_id": pid, "verdict": verdict,
                     "adapter": mods.get(p["defillama_slug"], ""), "addresses": len(own),
                     "verified": len(verified), "matched": ";".join(hits),
                     "generic_matched": ";".join(generic), "unmatched": ";".join(unmatched[:8])})
        print(f"  {pid:<22} {verdict:<11} {len(verified):>2}/{len(own):<2} verified"
              f"{'  ' + ', '.join(hits[:4]) if hits else ''}")

    out = ROOT / "data" / "mapping_check.csv"
    prev = {r["protocol_id"]: r for r in load_csv(out)} if out.exists() and args.protocols else {}
    prev.update({r["protocol_id"]: r for r in rows})
    order = [p["protocol_id"] for p in protocols if p["protocol_id"] in prev]
    with open(out, "w", newline="") as fh:
        fh.write(f"# tools/verify_mapping.py; DefiLlama-Adapters @ {commit}; Etherscan chain 1; "
                 f"{datetime.date.today().isoformat()}\n")
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(prev[k] for k in order)
    c = Counter(prev[k]["verdict"] for k in order)
    print(f"\n{dict(c)} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
