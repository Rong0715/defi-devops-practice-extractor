#!/usr/bin/env python3
"""Suggest the core contracts repo for each candidate protocol.

    python3 tools/map_repos.py --top 200 > data/repo_candidates.csv

Combines two public sources, neither of which is sufficient alone:
  * DefiLlama gives the protocol's GitHub org(s), but the field is often empty.
  * Electric Capital's open-dev-data gives an ecosystem's repos, but an ecosystem
    holds everything the community wrote (Uniswap: 1426 repos), not the protocol's
    own contracts.

So: find the ecosystem, keep the repos under the protocol's own org, and rank what
is left by how much the name looks like contracts rather than an SDK, a front end,
a subgraph or docs. The output is a ranked suggestion **for human review**, not a
sample -- `suggested` is the top pick and `alternatives` are the runners-up.
"""

import argparse
import concurrent.futures as cf
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from odd_repos import load  # noqa: E402

# ecosystem names that do not match the DefiLlama name
ALIASES = {
    "sky lending": "Maker", "sky": "Maker", "makerdao": "Maker",
    "fluid lending": "InstaDApp", "fluid dex": "InstaDApp", "fluid": "InstaDApp",
    "euler v2": "Euler Finance", "eigencloud": "EigenLayer",
    "ether.fi stake": "ether.fi", "ether.fi liquid": "ether.fi",
    "compound v3": "Compound", "aave v3": "Aave", "uniswap v4": "Uniswap",
    "morpho blue": "Morpho", "curve dex": "Curve", "pendle v2": "Pendle",
    "stakewise v3": "StakeWise", "liquity v1": "Liquity", "liquity v2": "Liquity",
    "balancer v3": "Balancer", "spark": "Spark Protocol", "sparklend": "Spark Protocol",
}

GOOD = [(r"(^|[-_])(contracts?|core|protocol|monorepo)([-_]|$)", 6),
        (r"(^|[-_])v\d([-_]|$)", 3), (r"periphery", 2), (r"smart[-_]?contracts?", 6),
        (r"(^|[-_])(vault|pool|lend|swap|stak)\w*", 1)]
BAD = [(r"(^|[-_])(sdk|js|ts|py|rs|go|api|cli|bot|ui|app|web|www|frontend|front[-_]end|"
        r"interface|widget|docs?|website|blog|brand|assets|design|landing|analytics|"
        r"dashboard|subgraph|graph|indexer|keeper|scripts?|tools?|examples?|tutorial|"
        r"template|starter|test|mock|fork|archive|deprecated|legacy|awesome|list|spec|"
        r"audit|bounty|governance[-_]ui|snapshot|discord|telegram|action)([-_]|$)", -8)]


VENDOR = re.compile(r"(^|/)(node_modules|lib|vendor|deps)/")


def tree(url, tmp):
    """Treeless shallow clone -> the repo's file list. ~0.3s and ~100KB per repo,
    no API token needed. Names alone cannot tell a protocol's core contracts from
    a fork of someone else's; the file list can."""
    dst = f"{tmp}/{re.sub(r'\W+', '_', url)}"
    try:
        p = subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none",
                            "--no-checkout", "--quiet", url, dst],
                           capture_output=True, timeout=180)
        if p.returncode != 0:
            return None
        r = subprocess.run(["git", "-C", dst, "ls-tree", "-r", "HEAD", "--name-only"],
                           capture_output=True, text=True, timeout=180)
        return r.stdout.splitlines()
    except Exception:
        return None
    finally:
        shutil.rmtree(dst, ignore_errors=True)


def content_score(files):
    if files is None:
        return -100, "unreachable"
    sol = [f for f in files if f.endswith((".sol", ".vy")) and not VENDOR.search(f)]
    src = [f for f in sol if re.match(r"(src|contracts)/", f)]
    cfg = any(re.match(r"(foundry\.toml|hardhat\.config\.|truffle|brownie-config)", f) for f in files)
    tst = any(re.match(r"(test|tests)/", f) for f in files)
    s = min(len(src), 150) * .5 + min(len(sol), 150) * .2 + (25 if cfg else 0) + (15 if tst else 0)
    return (s - 60 if not sol else s), f"{len(src)}src/{len(sol)}sol cfg={int(cfg)} tests={int(tst)}"


def version_bonus(proto_name, url):
    """'Balancer V3' must not map to balancer-v2-monorepo."""
    m = re.search(r"\bv(\d+)\b", proto_name, re.I)
    if not m:
        return 0
    got = set(re.findall(r"(?:^|[-_])v(\d+)(?:[-_]|$)", url.rsplit("/", 1)[-1].lower()))
    if m.group(1) in got:
        return 40
    return -40 if got else 0


def score(url, owner_ok):
    name = url.rstrip("/").rsplit("/", 1)[-1].lower()
    s = 10 if owner_ok else 0
    for rx, w in GOOD + BAD:
        if re.search(rx, name):
            s += w
    return s - 0.02 * len(name)


def pick_eco(name, slug, ecos_lower):
    for key in (name.lower(), slug.lower().replace("-", " ")):
        if key in ALIASES and ALIASES[key].lower() in ecos_lower:
            return ecos_lower[ALIASES[key].lower()]
        if key in ecos_lower:
            return ecos_lower[key]
    # strip a trailing version token ("Aave V3" -> "Aave") and retry
    base = re.sub(r"\s+(v\d+(\.\d+)?|core|finance|protocol|lending|stake|dex)$", "",
                  name, flags=re.I).strip().lower()
    return ecos_lower.get(base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--candidates", default=str(ROOT / "data" / "candidates.csv"))
    ap.add_argument("--shortlist", type=int, default=3, help="candidates to keep per protocol")
    ap.add_argument("--probe", type=int, default=10, help="candidates to content-probe per protocol")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp()

    t, _ = load()
    ecos_lower = {e.lower(): e for e in t.repos}
    rows = [r for r in csv.DictReader(open(a.candidates)) if r["verdict"] != "drop"][:a.top]

    w = csv.DictWriter(sys.stdout, fieldnames=[
        "protocol_id", "name", "slug", "category", "eth_tvl_usd", "verdict",
        "ecosystem", "eco_repos", "owner", "suggested", "confidence", "evidence", "alternatives"])
    w.writeheader()
    for r in rows:
        eco = pick_eco(r["name"], r["slug"], ecos_lower)
        orgs = [o.strip().lower() for o in (r.get("github_orgs") or "").split(";") if o.strip()]
        urls = list(t.repos_for(eco)) if eco else []
        gh = [u for u in urls if "github.com/" in u.lower()]
        owners = Counter(u.lower().split("github.com/")[1].split("/")[0] for u in gh)
        # the protocol's own org: what DefiLlama says, else the ecosystem's dominant owner
        own = [o for o in orgs if o in owners] or ([owners.most_common(1)[0][0]] if owners else [])
        cand = sorted(
            [u for u in gh if u.lower().split("github.com/")[1].split("/")[0] in own],
            key=lambda u: -(score(u, True) + version_bonus(r["name"], u)))[:a.probe]
        with cf.ThreadPoolExecutor(max_workers=10) as ex:
            scored = list(ex.map(lambda u: (u,) + content_score(tree(u, tmp)), cand))
        scored.sort(key=lambda x: -(x[1] + version_bonus(r["name"], x[0])))
        top = [x for x in scored if x[1] > -100][:a.shortlist]
        conf = "none" if not top else ("high" if top[0][1] >= 60 and len(top) == 1 or
                                       (len(top) > 1 and top[0][1] - top[1][1] >= 25) else "medium")
        w.writerow({**{k: r[k] for k in ("name", "slug", "category", "eth_tvl_usd", "verdict")},
                    "protocol_id": re.sub(r"[^a-z0-9]+", "-", r["name"].lower()).strip("-"),
                    "ecosystem": eco or "", "eco_repos": len(gh), "owner": ";".join(own),
                    "suggested": top[0][0] if top else "", "confidence": conf,
                    "evidence": top[0][2] if top else "",
                    "alternatives": " | ".join(f"{x[0]} ({x[2]})" for x in top[1:])})


if __name__ == "__main__":
    main()
