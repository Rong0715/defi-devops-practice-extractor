#!/usr/bin/env python3
"""Check that the sample, the hand-review log and the repo mapping agree.

    python3 tools/check_sample.py

Errors (exit 1) -- the sample is not what the documents say it is:
  * a keep/review candidate ranked above the last included one has no decision in data/review.csv
  * an `include` in review.csv is missing from protocols.csv, or a protocol is not an `include`
  * an `exclude` has no reason
  * a protocol has no core repo, or repos.csv names a protocol that does not exist

Warnings -- a repo may be the wrong codebase (how `cap` once pointed at an unrelated 2021 project):
  * the repo's GitHub owner is not among the orgs DefiLlama lists for the protocol
  * the scanned commit is more than a year older than the protocol's DefiLlama listing
    (needs out/<id>.json from a previous extract run)
  * two protocols scan the same repo and subpath
  * tools/verify_mapping.py could not tie the repo to the contracts that hold the protocol's funds
    (verdict no_match / no_evidence in data/mapping_check.csv)
"""
import csv
import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from extract import load_csv  # noqa: E402

STALE_DAYS = 365


def owner(url):
    return re.sub(r"^https?://[^/]+/", "", url).split("/")[0].lower()


def main():
    protocols = load_csv(ROOT / "data" / "protocols.csv")
    repos = load_csv(ROOT / "data" / "repos.csv")
    review = load_csv(ROOT / "data" / "review.csv")
    cands = list(csv.DictReader(open(ROOT / "data" / "candidates.csv")))
    snap = sorted((ROOT / "data" / "sources").glob("defillama_2*.json"))[-1]
    dl = {p["slug"]: p for p in json.loads(snap.read_text())["protocols"]}

    errors, warns = [], []
    by_slug = {p["defillama_slug"]: p for p in protocols}
    decided = {r["slug"]: r for r in review}
    rank = {c["slug"]: int(c["raw_rank"]) for c in cands}

    # -- the review log is complete down to the cutoff --------------------------
    missing_pool = [s for s in by_slug if s not in rank]
    for s in missing_pool:
        errors.append(f"{by_slug[s]['protocol_id']}: slug {s} is not in data/candidates.csv (outside the pool)")
    cutoff = max((rank[s] for s in by_slug if s in rank), default=0)
    for c in cands:
        if c["verdict"] != "drop" and int(c["raw_rank"]) <= cutoff and c["slug"] not in decided:
            errors.append(f"rank {c['raw_rank']} {c['slug']}: no decision in data/review.csv")
    for r in review:
        if r["decision"] == "include":
            if r["slug"] not in by_slug:
                errors.append(f"review.csv includes {r['slug']} but protocols.csv does not")
        elif r["decision"] == "exclude":
            if not r["reason"].strip():
                errors.append(f"review.csv excludes {r['slug']} without a reason")
        else:
            errors.append(f"review.csv {r['slug']}: decision must be include or exclude, not {r['decision']!r}")
    for s, p in by_slug.items():
        if decided.get(s, {}).get("decision") != "include":
            errors.append(f"{p['protocol_id']}: in protocols.csv but not an include in review.csv")

    # -- repo mapping -------------------------------------------------------------
    pids = {p["protocol_id"] for p in protocols}
    by_pid = defaultdict(list)
    for r in repos:
        if r["protocol_id"] not in pids:
            errors.append(f"repos.csv row for unknown protocol {r['protocol_id']}")
        if (r.get("include") or "1").strip() != "0":
            by_pid[r["protocol_id"]].append(r)
    seen = defaultdict(list)
    for p in protocols:
        pid = p["protocol_id"]
        rows = by_pid.get(pid, [])
        if not any(r["role"] == "core" for r in rows):
            errors.append(f"{pid}: no core repo in repos.csv")
        orgs = {o.lower() for o in dl.get(p["defillama_slug"], {}).get("github", [])}
        for r in rows:
            seen[(r["url"].rstrip("/").lower(), r.get("subpath", ""))].append(pid)
            if orgs and r["role"] == "core" and owner(r["url"]) not in orgs:
                warns.append(f"{pid}: repo owner {owner(r['url'])} is not a DefiLlama org ({', '.join(sorted(orgs))})")
        listed = dl.get(p["defillama_slug"], {}).get("listedAt")
        rec = ROOT / "out" / f"{pid}.json"
        if listed and rec.exists():
            core = next((m for m in json.loads(rec.read_text())["repos"] if m["role"] == "core"), {})
            if core.get("commit_date"):
                last = datetime.datetime.fromisoformat(core["commit_date"])
                lst = datetime.datetime.fromtimestamp(listed, datetime.timezone.utc)
                if (lst - last).days > STALE_DAYS:
                    warns.append(f"{pid}: last commit {last.date()} is {(lst - last).days} days before "
                                 f"the DefiLlama listing ({lst.date()})")
    mc = ROOT / "data" / "mapping_check.csv"
    checked = {r["protocol_id"]: r for r in load_csv(mc)} if mc.exists() else {}
    mr = ROOT / "data" / "mapping_review.csv"
    reviewed = {r["protocol_id"]: r for r in load_csv(mr)} if mr.exists() else {}
    for pid, r in reviewed.items():
        if pid not in pids:
            errors.append(f"mapping_review.csv names unknown protocol {pid}")
        elif r["decision"] not in ("confirmed", "doubtful"):
            errors.append(f"mapping_review.csv {pid}: decision must be confirmed or doubtful")
        elif r["decision"] == "doubtful":
            warns.append(f"{pid}: repo flagged doubtful by hand ({r['note'] or 'no note'})")
    for pid in sorted(pids):
        r = checked.get(pid)
        if r and r["verdict"] in ("no_match", "no_evidence") and pid not in reviewed:
            warns.append(f"{pid}: deployed contracts not tied to the repo ({r['verdict']}; "
                         f"{r['verified']} verified, unmatched: {r['unmatched'] or '-'})")
    if checked and pids - set(checked):
        warns.append(f"{len(pids - set(checked))} protocol(s) not yet in data/mapping_check.csv")
    for (url, sub), who in seen.items():
        if len(who) > 1:
            warns.append(f"{', '.join(who)} scan the same repo {url}{' ' + sub if sub else ''}")

    n_parents = len({p["parent_id"] or p["protocol_id"] for p in protocols})
    if checked:
        from collections import Counter
        c = Counter(r["verdict"] for pid, r in checked.items() if pid in pids)
        print("mapping check: " + ", ".join(f"{v} {c[v]}" for v in ("confirmed", "weak", "no_match", "no_evidence"))
              + f"; reviewed by hand: {len(reviewed)}")
    print(f"{len(protocols)} subjects, {n_parents} parents; review log covers ranks 1-{cutoff} "
          f"({sum(r['decision'] == 'include' for r in review)} include, "
          f"{sum(r['decision'] == 'exclude' for r in review)} exclude)")
    for w in warns:
        print("  warn:", w)
    for e in errors:
        print("  ERROR:", e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
