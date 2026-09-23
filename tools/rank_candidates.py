#!/usr/bin/env python3
"""Rank Ethereum protocols by Ethereum TVL and pre-filter them into a candidate list.

    python3 tools/rank_candidates.py [data/sources/defillama_<date>.json] [--top 300]

Writes data/candidates.csv. Nothing is dropped silently: every row carries a verdict and a reason.
  drop    automatic, with a reason (fork, deprecated, not a DeFi protocol by category)
  review  needs a human (a sibling version of a higher-ranked protocol, or a borderline category)
  keep    passed the automatic rules; still needs its repos mapped and a hand check for open source
The list is a starting point for hand review, not the sample.
"""
import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# not on-chain DeFi protocols with their own contracts
NOT_DEFI = {"CEX", "Bridge", "Canonical Bridge", "Chain", "Risk Curators", "Onchain Capital Allocator",
            "Wallets", "Services", "Analytics", "NFT Marketplace", "Gaming", "Prediction Market",
            "Anchor", "Infrastructure", "Oracle", "Ponzi", "Privacy"}
BORDERLINE = {"RWA", "Basis Trading", "Yield", "Yield Aggregator", "Staking Pool", "Restaking",
              "Liquidity manager", "Leveraged Farming", "Options", "Derivatives", "Indexes"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot", nargs="?")
    ap.add_argument("--top", type=int, default=300)
    a = ap.parse_args()
    snap = Path(a.snapshot) if a.snapshot else sorted((ROOT / "data" / "sources").glob("defillama_*.json"))[-1]
    data = json.loads(snap.read_text())
    prots = data["protocols"]
    by_id = {str(p["id"]): p for p in prots}

    rows, seen_parent = [], {}
    for i, p in enumerate(prots[:a.top], 1):
        verdict, reason = "keep", ""
        forks = p.get("forkedFromIds") or p.get("forkedFrom")
        cat = p.get("category", "")
        if forks:
            names = [by_id[str(f)]["name"] for f in forks if str(f) in by_id] or [str(f) for f in forks]
            verdict, reason = "drop", "fork of " + ", ".join(names)[:60]
        elif p.get("deprecated"):
            verdict, reason = "drop", "deprecated"
        elif cat in NOT_DEFI:
            verdict, reason = "drop", f"category {cat} is not a DeFi protocol with its own contracts"
        elif cat in BORDERLINE:
            verdict, reason = "review", f"borderline category: {cat}"
        parent = p.get("parentProtocol", "")
        if verdict != "drop" and parent:
            if parent in seen_parent:
                verdict = "review"
                reason = (reason + "; " if reason else "") + f"sibling version of {seen_parent[parent]} (separate codebase?)"
            else:
                seen_parent[parent] = p["name"]
        rows.append({"raw_rank": i, "name": p["name"], "slug": p["slug"], "parent": parent,
                     "category": cat, "eth_tvl_usd": p["eth_tvl_usd"], "verdict": verdict, "reason": reason,
                     "github_orgs": ";".join(p.get("github", [])),
                     "audit_links": ";".join(p.get("audit_links", [])), "listed_at": p.get("listedAt", "")})
    out = ROOT / "data" / "candidates.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    c = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("keep", "review", "drop")}
    print(f"snapshot {data['snapshot_date']}: top {len(rows)} Ethereum protocols -> {c}  ({out})")


if __name__ == "__main__":
    main()
